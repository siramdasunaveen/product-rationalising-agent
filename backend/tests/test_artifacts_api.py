import copy

import pytest
from sqlalchemy import select

from app.db.models import AuditEvent, OutboxEvent
from app.seed.telco import CANONICAL_KEY, TELCO_CANONICAL
from app.services.artifact_seed import seed_artifacts

API = "/api/rationalisation/registries"

SCHEMA = {
    "entities": [{"name": "Product", "keyFields": ["productId"], "fields": [
        {"name": "productId", "type": "string", "required": True},
        {"name": "status", "type": "enum", "values": ["ACTIVE", "RETIRED"]},
    ]}],
}


def lookup(*pairs, schema="DEMO-SCHEMA"):
    return {"target": {"schema": schema, "entity": "Product", "field": "status"},
            "entries": [{"source": s, "target": t} for s, t in pairs]}


@pytest.fixture
def h(login, tenant):
    """Auth headers per persona, all in the same fresh tenant."""
    return {role: login(role, tenant) for role in ("agent_admin", "data_steward", "portfolio_manager", "viewer")}


def create(client, headers, kind="canonical-schemas", key="DEMO-SCHEMA", content=None, **extra):
    body = {"key": key, "name": "Demo schema", "content": content if content is not None else SCHEMA} | extra
    return client.post(f"{API}/{kind}", json=body, headers=headers)


def publish(client, headers, kind, v):
    return client.post(f"{API}/{kind}/{v['artifact_id']}/versions/{v['version']}/publish",
                       json={"lock_version": v["lock_version"]}, headers=headers)


def url(kind, v, suffix=""):
    return f"{API}/{kind}/{v['artifact_id']}/versions/{v['version']}{suffix}"


def published_schema(client, h, key="DEMO-SCHEMA", content=None):
    v = create(client, h["agent_admin"], key=key, content=content).json()
    r = publish(client, h["agent_admin"], "canonical-schemas", v)
    assert r.status_code == 200, r.text
    return r.json()


# ---- lifecycle through the API ----

def test_create_draft(client, h):
    r = create(client, h["data_steward"], change_note="first cut")
    assert r.status_code == 201, r.text
    v = r.json()
    assert (v["version"], v["status"], v["ref"]) == ("1.0", "DRAFT", "DEMO-SCHEMA@1.0")
    assert v["digest"].startswith("sha256:") and v["digest_verified"] is True
    assert v["selectable"] is False and v["parent_version"] is None and v["lock_version"] == 1


def test_full_lifecycle_publish_clone_supersede(client, h, tenant, db):
    admin = h["agent_admin"]
    v1 = published_schema(client, h)
    assert v1["status"] == "PUBLISHED" and v1["selectable"] and v1["published_by"]

    # edits to a published version are refused; clone instead
    r = client.patch(url("canonical-schemas", v1), json={"lock_version": v1["lock_version"], "content": {}},
                     headers=admin)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_editable"

    r = client.post(url("canonical-schemas", v1, "/clone"), json={"bump": "minor"}, headers=h["data_steward"])
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert (v2["version"], v2["status"], v2["parent_version"]) == ("1.1", "DRAFT", "1.0")
    assert v2["digest"] == v1["digest"]  # same content -> same digest

    changed = copy.deepcopy(SCHEMA)
    changed["entities"][0]["fields"][1]["values"].append("PLANNED")
    r = client.patch(url("canonical-schemas", v2), json={"lock_version": v2["lock_version"], "content": changed},
                     headers=h["data_steward"])
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["digest"] != v1["digest"] and v2["lock_version"] == 2

    r = publish(client, admin, "canonical-schemas", v2)
    assert r.status_code == 200, r.text
    detail = client.get(f"{API}/canonical-schemas/{v1['artifact_id']}", headers=admin).json()
    assert detail["published_version"] == "1.1"
    assert [(x["version"], x["status"]) for x in detail["versions"]] == [("1.1", "PUBLISHED"), ("1.0", "SUPERSEDED")]

    # the superseded version is still readable for pinned runs, unchanged
    old = client.get(url("canonical-schemas", v1), headers=admin).json()
    assert old["digest"] == v1["digest"] and old["content"] == SCHEMA and old["digest_verified"]

    actions = list(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == tenant)
                              .order_by(AuditEvent.seq)))
    assert [a for a in actions if a.startswith("artifact.")] == [
        "artifact.created", "artifact.published", "artifact.version_created", "artifact.draft_updated",
        "artifact.published", "artifact.superseded"]
    events = list(db.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == tenant)
                             .order_by(OutboxEvent.id)))
    assert events == ["ArtifactVersionCreated", "ArtifactVersionPublished", "ArtifactVersionCreated",
                      "ArtifactVersionPublished", "ArtifactVersionSuperseded"]


def test_major_bump_and_one_draft_at_a_time(client, h):
    v1 = published_schema(client, h)
    r = client.post(url("canonical-schemas", v1, "/clone"), json={"bump": "major"}, headers=h["agent_admin"])
    assert r.json()["version"] == "2.0"
    r = client.post(url("canonical-schemas", v1, "/clone"), json={"bump": "minor"}, headers=h["agent_admin"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "draft_exists"


def test_publish_refuses_invalid_content(client, h):
    v = create(client, h["data_steward"], content={"entities": []}).json()  # drafts may be incomplete
    r = client.post(url("canonical-schemas", v, "/validate"), headers=h["data_steward"])
    assert r.status_code == 200 and r.json()["valid"] is False and r.json()["errors"][0]["field"] == "entities"
    r = publish(client, h["agent_admin"], "canonical-schemas", v)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed"


def test_lock_version_conflict(client, h):
    v = create(client, h["data_steward"]).json()
    ok = client.patch(url("canonical-schemas", v), json={"lock_version": 1, "change_note": "a"},
                      headers=h["data_steward"])
    assert ok.status_code == 200
    stale = client.patch(url("canonical-schemas", v), json={"lock_version": 1, "change_note": "b"},
                         headers=h["data_steward"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"
    assert publish(client, h["agent_admin"], "canonical-schemas", v).status_code == 409


def test_create_validation_and_duplicate_key(client, h):
    r = create(client, h["data_steward"], key="bad key", content={"price": 1.5}, name="x",
               valid_from="2027-01-02", valid_to="2027-01-01")
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["detail"]["errors"]} == {"key", "name", "content", "valid_to"}
    assert create(client, h["data_steward"]).status_code == 201
    r = create(client, h["data_steward"], kind="lookups", content=lookup(("a", "ACTIVE")))  # same key, other kind
    assert r.status_code == 409 and r.json()["detail"]["code"] == "key_taken"


def test_deprecate_and_retire_need_a_reason(client, h):
    v = published_schema(client, h)
    r = client.post(url("canonical-schemas", v, "/deprecate"), json={"lock_version": v["lock_version"], "reason": ""},
                    headers=h["agent_admin"])
    assert r.status_code == 422
    r = client.post(url("canonical-schemas", v, "/deprecate"),
                    json={"lock_version": v["lock_version"], "reason": "Replaced by vendor model"},
                    headers=h["agent_admin"])
    assert r.status_code == 200 and r.json()["status"] == "DEPRECATED" and not r.json()["selectable"]
    v = r.json()
    r = client.post(url("canonical-schemas", v, "/retire"),
                    json={"lock_version": v["lock_version"], "reason": "No longer used"}, headers=h["agent_admin"])
    assert r.status_code == 200 and r.json()["status"] == "RETIRED"
    v = r.json()
    r = client.post(url("canonical-schemas", v, "/deprecate"),
                    json={"lock_version": v["lock_version"], "reason": "again"}, headers=h["agent_admin"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invalid_transition"


def test_discard_draft(client, h):
    v1 = published_schema(client, h)
    v2 = client.post(url("canonical-schemas", v1, "/clone"), json={}, headers=h["data_steward"]).json()
    r = client.delete(url("canonical-schemas", v2), params={"lock_version": v2["lock_version"]},
                      headers=h["data_steward"])
    assert r.status_code == 204
    assert client.get(url("canonical-schemas", v2), headers=h["data_steward"]).status_code == 404
    r = client.delete(url("canonical-schemas", v1), params={"lock_version": v1["lock_version"]},
                      headers=h["data_steward"])
    assert r.status_code == 409  # published versions are never deleted

    only = create(client, h["data_steward"], key="SCRATCH-SCHEMA").json()
    client.delete(url("canonical-schemas", only), params={"lock_version": 1}, headers=h["data_steward"])
    assert client.get(f"{API}/canonical-schemas/{only['artifact_id']}", headers=h["data_steward"]).status_code == 404


# ---- resolve (what runs pin) ----

def test_resolve(client, h):
    admin = h["agent_admin"]
    v1 = published_schema(client, h)
    r = client.get(f"{API}/canonical-schemas/resolve", params={"ref": "DEMO-SCHEMA"}, headers=admin)
    assert r.status_code == 200
    assert (r.json()["ref"], r.json()["digest"], r.json()["selectable"]) == ("DEMO-SCHEMA@1.0", v1["digest"], True)

    v2 = client.post(url("canonical-schemas", v1, "/clone"), json={}, headers=admin).json()
    r = client.get(f"{API}/canonical-schemas/resolve", params={"ref": "DEMO-SCHEMA@1.1"}, headers=admin)
    assert r.status_code == 404  # drafts cannot be referenced
    publish(client, admin, "canonical-schemas", v2)

    assert client.get(f"{API}/canonical-schemas/resolve", params={"ref": "DEMO-SCHEMA"},
                      headers=admin).json()["ref"] == "DEMO-SCHEMA@1.1"
    pinned = client.get(f"{API}/canonical-schemas/resolve", params={"ref": "DEMO-SCHEMA@1.0"}, headers=admin).json()
    assert (pinned["status"], pinned["selectable"], pinned["digest"]) == ("SUPERSEDED", False, v1["digest"])
    assert client.get(f"{API}/lookups/resolve", params={"ref": "DEMO-SCHEMA"}, headers=admin).status_code == 404
    assert client.get(f"{API}/canonical-schemas/resolve", params={"ref": "X@latest"}, headers=admin).status_code == 422


def test_future_valid_from_is_not_selectable(client, h):
    v = create(client, h["agent_admin"], valid_from="2099-01-01").json()
    assert publish(client, h["agent_admin"], "canonical-schemas", v).json()["selectable"] is False
    r = client.get(f"{API}/canonical-schemas/resolve", params={"ref": "DEMO-SCHEMA"}, headers=h["agent_admin"])
    assert r.status_code == 404


# ---- lookups and crosswalks are checked against the published canonical schema ----

def test_lookup_needs_published_target_schema(client, h):
    v = create(client, h["agent_admin"], kind="lookups", key="DEMO-STATUS", content=lookup(("Live", "ACTIVE"))).json()
    r = publish(client, h["agent_admin"], "lookups", v)
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["field"] == "target.schema"
    published_schema(client, h)
    assert publish(client, h["agent_admin"], "lookups", v).status_code == 200


def test_lookup_target_values_must_exist(client, h):
    published_schema(client, h)
    v = create(client, h["agent_admin"], kind="lookups", key="DEMO-STATUS",
               content=lookup(("Live", "ACTIVE"), ("Pending", "PENDING"))).json()
    r = publish(client, h["agent_admin"], "lookups", v)
    assert r.status_code == 422
    assert r.json()["detail"]["errors"] == [
        {"field": "entries[1].target", "message": "PENDING is not an allowed value of status"}]


def test_crosswalk_one_key_one_entity_blocks_publish(client, h):
    published_schema(client, h)
    content = {"target": {"schema": "DEMO-SCHEMA", "entity": "Product"}, "entries": [
        {"sourceSystem": "SALESFORCE", "sourceKey": "01t1", "canonicalId": "PRD-1"},
        {"sourceSystem": "SALESFORCE", "sourceKey": "01t1", "canonicalId": "PRD-2"}]}
    v = create(client, h["data_steward"], kind="crosswalks", key="SF-PRODUCTS", content=content).json()
    r = publish(client, h["agent_admin"], "crosswalks", v)
    assert r.status_code == 422 and "more than one entity" in r.json()["detail"]["errors"][0]["message"]


# ---- permissions and tenancy ----

def test_permissions(client, h):
    assert create(client, h["portfolio_manager"]).status_code == 403
    assert create(client, h["viewer"]).status_code == 403
    assert client.get(f"{API}/canonical-schemas", headers=h["viewer"]).status_code == 403
    v = create(client, h["data_steward"]).json()
    assert client.get(url("canonical-schemas", v), headers=h["portfolio_manager"]).status_code == 200
    assert publish(client, h["data_steward"], "canonical-schemas", v).status_code == 403  # steward cannot publish
    assert publish(client, h["agent_admin"], "canonical-schemas", v).status_code == 200


def test_tenant_isolation(client, h, login):
    v = create(client, h["agent_admin"]).json()
    other = login("agent_admin", "t-other-tenant")
    assert client.get(url("canonical-schemas", v), headers=other).status_code == 404
    assert client.get(f"{API}/canonical-schemas", headers=other).json()["total"] == 0
    assert publish(client, other, "canonical-schemas", v).status_code == 404
    # the same key is free in another tenant
    assert create(client, other, key=f"DEMO-SCHEMA-{v['artifact_id'][:4].upper()}").status_code == 201


def test_kind_in_path_must_match(client, h):
    v = create(client, h["agent_admin"]).json()
    assert client.get(url("lookups", v), headers=h["agent_admin"]).status_code == 404


def test_content_schema_endpoint(client, h):
    r = client.get(f"{API}/transforms/content-schema", headers=h["viewer"])
    assert r.status_code == 403
    r = client.get(f"{API}/transforms/content-schema", headers=h["data_steward"])
    assert r.status_code == 200 and "steps" in r.json()["properties"]


# ---- seed ----

def test_seed_is_published_and_idempotent(client, h, tenant, session_factory, platform):
    with session_factory() as s:
        first = seed_artifacts(s, platform.audit, tenant)
    assert f"{CANONICAL_KEY}@1.0" in first["created"] and len(first["created"]) == 21
    with session_factory() as s:
        second = seed_artifacts(s, platform.audit, tenant)
    assert second["created"] == [] and len(second["existing"]) == 21

    admin = h["agent_admin"]
    ref = client.get(f"{API}/canonical-schemas/resolve", params={"ref": CANONICAL_KEY}, headers=admin).json()
    assert ref["ref"] == "TELCO-CANONICAL@1.0" and ref["selectable"]
    full = client.get(f"{API}/canonical-schemas/{ref['artifact_id']}/versions/1.0", headers=admin).json()
    assert full["content"] == TELCO_CANONICAL and full["digest_verified"]
    lookups = client.get(f"{API}/lookups", headers=admin).json()
    assert {i["key"] for i in lookups["items"]} >= {"LIFECYCLE-STATUS", "PRODUCT-STATUS"}
    assert all(i["published_version"] == "1.0" for i in lookups["items"])
    assert client.get(f"{API}/transforms", params={"query": "date"}, headers=admin).json()["total"] == 2


def test_digest_is_stable_across_tenants(h, tenant, session_factory, platform, client):
    with session_factory() as s:
        seed_artifacts(s, platform.audit, tenant)
        seed_artifacts(s, platform.audit, f"{tenant}-b")
    from app.db.models import Artifact, ArtifactVersion
    with session_factory() as s:
        digests = s.execute(select(Artifact.tenant_id, ArtifactVersion.digest)
                            .join(ArtifactVersion, ArtifactVersion.artifact_id == Artifact.id)
                            .where(Artifact.key == CANONICAL_KEY,
                                   Artifact.tenant_id.in_([tenant, f"{tenant}-b"]))).all()
    assert len(digests) == 2 and digests[0].digest == digests[1].digest
