"""Mapping Rules end to end: suggestions -> validate -> submit -> review (SoD) -> publish -> apply -> staged
records + lineage; replay reproduces identical output; database immutability; permissions; optional AI."""

import json
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.adapters.sources.excel import ExcelSourceAdapter
from app.db.models import AuditEvent, FieldLineage, MappingExecution, OutboxEvent, StagedRecord
from app.domain.excel_checks import FileLimits
from app.domain.mapping import execute, output_hash
from app.ports.ai import AIRawResult, AIUnavailable
from app.services import evidence as ev_service
from app.services import mapping as service
from app.services.artifact_seed import seed_artifacts
from app.services.reference import seed_reference_data
from tests import excel_files as f

API = "/api/rationalisation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIMITS = FileLimits()

CRM_HEADERS = ["Product ID", "Product Name", "Product Family", "Status", "Offer ID", "Parent Product", "Market",
               "Segment", "Channel", "Description"]
CRM_ROWS = [
    ["PRD-1001", "Fibre 500 Home", "Broadband", "Active", "OFR-1", None, "UK Consumer", "Residential", "Online", None],
    ["PRD-1002", "Fibre 900 Home", "Broadband", "Live", None, None, "UK Consumer", "Residential", "Retail", None],
    ["PRD-1003", "Copper 80 Legacy", "Broadband", "Withdrawn", None, "PRD-1001", "UK Consumer", None, None, "Old"],
]


class FakeAI:
    name = "fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return AIRawResult(text=answer if isinstance(answer, str) else json.dumps(answer), model="fake-1",
                           provider="fake")


@pytest.fixture
def env(client, login, session_factory, tenant, platform):
    with session_factory() as s:
        seed_reference_data(s, tenant)
        s.commit()
        seed_artifacts(s, platform.audit, tenant)
    pm = login("portfolio_manager", tenant)
    users = {"pm": pm, "steward": login("data_steward", tenant), "steward2": login("data_steward", tenant),
             "admin": login("agent_admin", tenant), "viewer": login("viewer", tenant)}
    me = client.get(f"{API}/me", headers=pm).json()
    pid = {p["code"]: p["id"] for p in client.get(f"{API}/metadata/portfolios", headers=pm).json()}
    mid = {m["code"]: m["id"] for m in client.get(f"{API}/metadata/markets", headers=pm).json()}
    fid = {x["code"]: x["id"] for x in client.get(f"{API}/metadata/product-families", headers=pm).json()}
    run = client.post(f"{API}/runs", headers=pm, json={
        "name": f"Mapping run {uuid.uuid4().hex[:6]}", "portfolio_id": pid["CONSUMER_MOBILE"],
        "market_ids": [mid["UK_CONSUMER"]], "product_family_ids": [fid["POSTPAID"]], "period_start": "2025-11-16",
        "period_end": "2026-11-16", "objectives": ["BSS_SUNSET"], "owner_id": me["user_id"]}).json()
    return {**users, "run": run, "tenant": tenant}


def upload_confirmed(client, env, session_factory, platform, rows=CRM_ROWS, headers=CRM_HEADERS):
    data = f.xlsx({"Data": [headers, *rows]})
    r = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/uploads", headers=env["steward"],
                    files={"file": ("crm.xlsx", data, XLSX)})
    assert r.status_code in (200, 202), r.text
    uid = r.json()["id"]
    with session_factory() as s:
        ev_service.process_upload(s, platform, LIMITS, uuid.UUID(uid))
    c = client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{uid}/confirm", headers=env["steward"],
                    json={"sheet": "Data", "header_row": 1})
    assert c.status_code == 200, c.text
    return uid


def base(env, m):
    return f"{API}/runs/{env['run']['id']}/mappings/{m['mapping_id']}/versions/{m['version']}"


def create(client, env):
    r = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/mapping", headers=env["steward"], json={})
    assert r.status_code == 201, r.text
    return r.json()


def validate(client, env, session_factory, platform, m):
    r = client.post(f"{base(env, m)}/validate", headers=env["steward"])
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "VALIDATING"
    with session_factory() as s:
        service.run_validation(s, platform, LIMITS, uuid.UUID(r.json()["validation"]["id"]))
    return client.get(base(env, m), headers=env["steward"]).json()


def to_published(client, env, session_factory, platform, m=None):
    m = m or create(client, env)
    m = validate(client, env, session_factory, platform, m)
    assert m["status"] == "READY_FOR_REVIEW", m["validation"]
    m = client.post(f"{base(env, m)}/submit", headers=env["steward"],
                    json={"lock_version": m["lock_version"], "acknowledge_warnings": True}).json()
    m = client.post(f"{base(env, m)}/review", headers=env["steward2"],
                    json={"decision": "APPROVE", "content_hash": m["content_hash"]}).json()
    r = client.post(f"{base(env, m)}/publish", headers=env["admin"], json={"lock_version": m["lock_version"]})
    assert r.status_code == 200, r.text
    return r.json()


def apply(client, env, session_factory, platform, uid, version_id, key=None):
    headers = env["steward"] | ({"Idempotency-Key": key} if key else {})
    r = client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{uid}/apply", headers=headers,
                    json={"mapping_version_id": version_id})
    assert r.status_code in (200, 202), r.text
    if r.status_code == 202:
        with session_factory() as s:
            service.run_execution(s, platform, LIMITS, uuid.UUID(r.json()["id"]))
    return r


def version_id(session_factory, m):
    with session_factory() as s:
        return str(s.scalar(text("SELECT id FROM rationalisation.mapping_version WHERE definition_id = :d AND "
                                 "version = :v"), {"d": m["mapping_id"], "v": m["version"]}))


# ---- create + suggestions ----

def test_create_suggests_rules_from_the_template(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform)
    m = create(client, env)
    assert (m["version"], m["status"], m["canonical_ref"]) == (1, "DRAFT", "TELCO-CANONICAL@1.0")
    by_col = {r["source_column"]: r for r in m["rules"] if r["mapping_type"] != "RELATIONSHIP"}
    assert by_col["Product ID"]["target_field"] == "productId" and by_col["Product ID"]["proposed_by"] == "TEMPLATE"
    assert by_col["Status"]["mapping_type"] == "LOOKUP" and by_col["Status"]["lookup_ref"] == "PRODUCT-STATUS@1.0"
    assert any(r["mapping_type"] == "RELATIONSHIP" and r["relationship"]["name"] == "INCLUDES" for r in m["rules"])
    assert m["columns"][0]["samples"][:2] == ["PRD-1001", "PRD-1002"]
    assert {e["name"] for e in m["targets"]["entities"]} >= {"Product", "Offer"}
    assert any(o["key"] == "PRODUCT-STATUS" for o in m["options"]["lookups"])
    again = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/mapping", headers=env["steward"], json={})
    assert again.status_code == 409


def test_mapping_needs_a_confirmed_source(client, env):
    r = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/mapping", headers=env["steward"], json={})
    assert r.status_code == 409


# ---- validate, submit, review, publish ----

def test_full_lifecycle(client, env, session_factory, platform, db):
    upload_confirmed(client, env, session_factory, platform)
    m = create(client, env)
    m = validate(client, env, session_factory, platform, m)
    res = m["validation"]["result"]
    assert m["status"] == "READY_FOR_REVIEW" and m["validation"]["status"] == "PASS"  # every column mapped
    assert res["counts"]["records"] == {"Offer": 1, "Product": 3} and res["required_field_coverage"] == 100
    assert res["counts"]["relationships"] == {"INCLUDES": 1}

    m = client.post(f"{base(env, m)}/submit", headers=env["steward"], json={"lock_version": m["lock_version"]}).json()
    assert m["status"] == "IN_REVIEW" and m["content_hash"] == m["current_hash"] and not m["warnings_acknowledged"]

    own = client.post(f"{base(env, m)}/review", headers=env["steward"],
                      json={"decision": "APPROVE", "content_hash": m["content_hash"]})
    assert own.status_code == 403 and own.json()["detail"]["code"] == "separation_of_duties"
    stale = client.post(f"{base(env, m)}/review", headers=env["steward2"],
                        json={"decision": "APPROVE", "content_hash": "sha256:" + "0" * 64})
    assert stale.status_code == 409
    no_publish_yet = client.post(f"{base(env, m)}/publish", headers=env["admin"], json={"lock_version": m["lock_version"]})
    assert no_publish_yet.status_code == 409

    m = client.post(f"{base(env, m)}/review", headers=env["steward2"],
                    json={"decision": "APPROVE", "content_hash": m["content_hash"]}).json()
    assert m["status"] == "APPROVED" and m["approvals"][0]["decision"] == "APPROVE"
    assert client.post(f"{base(env, m)}/publish", headers=env["steward2"],
                       json={"lock_version": m["lock_version"]}).status_code == 403  # steward cannot publish
    m = client.post(f"{base(env, m)}/publish", headers=env["admin"], json={"lock_version": m["lock_version"]}).json()
    assert m["status"] == "PUBLISHED"

    events = set(db.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == env["tenant"])))
    assert {"MappingDraftCreated", "MappingValidationRequested", "MappingValidationCompleted",
            "MappingSubmittedForReview", "MappingApproved", "MappingPublished"} <= events
    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == env["tenant"])))
    assert {"mapping.created", "mapping.submitted", "mapping.approved", "mapping.published"} <= actions
    assert len(platform.notify.sent) >= 2  # reviewers notified, submitter told the decision


def test_warnings_need_acknowledgement(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform, headers=[*CRM_HEADERS, "Colour"],
                     rows=[[*r, "Red"] for r in CRM_ROWS])
    m = validate(client, env, session_factory, platform, create(client, env))
    assert m["validation"]["status"] == "WARNING"
    issue = m["validation"]["result"]["issues"][0]
    assert (issue["code"], issue["examples"]) == ("UNMAPPED_COLUMNS", ["Colour"])
    no_ack = client.post(f"{base(env, m)}/submit", headers=env["steward"], json={"lock_version": m["lock_version"]})
    assert no_ack.status_code == 422
    m = client.post(f"{base(env, m)}/submit", headers=env["steward"],
                    json={"lock_version": m["lock_version"], "acknowledge_warnings": True}).json()
    assert m["status"] == "IN_REVIEW" and m["warnings_acknowledged"]


def test_blocked_validation_and_edits_invalidate_it(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform,
                     rows=[*CRM_ROWS, ["PRD-1004", "Mystery", "Broadband", "Unheard-of", None, None, None, None, None,
                                       None]])
    m = validate(client, env, session_factory, platform, create(client, env))
    assert m["status"] == "VALIDATION_FAILED" and m["validation"]["status"] == "BLOCKED"
    assert m["validation"]["result"]["unknown_lookup_values"]
    r = client.post(f"{base(env, m)}/submit", headers=env["steward"], json={"lock_version": m["lock_version"]})
    assert r.status_code == 409

    # make status optional-by-default -> still blocked? no: drop the bad row's need by mapping status with DEFAULT
    rules = m["rules"]
    for rule in rules:
        if rule["source_column"] == "Status":
            rule["mapping_type"], rule["lookup_ref"] = "DIRECT", None
    r = client.patch(base(env, m), headers=env["steward"], json={"lock_version": m["lock_version"], "rules": rules})
    assert r.status_code == 200 and r.json()["status"] == "DRAFT"
    assert r.json()["validation"]["matches_current_rules"] is False  # the old result no longer applies
    stale = client.patch(base(env, m), headers=env["steward"], json={"lock_version": m["lock_version"], "rules": rules})
    assert stale.status_code == 409


def test_return_and_reject_need_comments(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform)
    m = validate(client, env, session_factory, platform, create(client, env))
    m = client.post(f"{base(env, m)}/submit", headers=env["steward"],
                    json={"lock_version": m["lock_version"], "acknowledge_warnings": True}).json()
    no_comment = client.post(f"{base(env, m)}/review", headers=env["steward2"],
                             json={"decision": "RETURN", "content_hash": m["content_hash"]})
    assert no_comment.status_code == 422
    m = client.post(f"{base(env, m)}/review", headers=env["steward2"],
                    json={"decision": "RETURN", "content_hash": m["content_hash"], "comment": "Map Segment too"}).json()
    assert m["status"] == "DRAFT" and m["content_hash"] is None and m["status_reason"] == "Map Segment too"
    m = validate(client, env, session_factory, platform, m)
    m = client.post(f"{base(env, m)}/submit", headers=env["steward"],
                    json={"lock_version": m["lock_version"], "acknowledge_warnings": True}).json()
    m = client.post(f"{base(env, m)}/review", headers=env["steward2"],
                    json={"decision": "REJECT", "content_hash": m["content_hash"], "comment": "Wrong source"}).json()
    assert m["status"] == "RETIRED"
    assert [a["decision"] for a in m["approvals"]] == ["RETURN", "REJECT"]


def test_new_version_supersedes(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform)
    v1 = to_published(client, env, session_factory, platform)
    r = client.post(f"{base(env, v1)}/clone", headers=env["steward"], json={})
    assert r.status_code == 201
    v2 = r.json()
    assert (v2["version"], v2["parent_version"], v2["status"]) == (2, 1, "DRAFT")
    assert client.post(f"{base(env, v1)}/clone", headers=env["steward"], json={}).status_code == 409  # one open
    v2 = to_published(client, env, session_factory, platform, v2)
    assert v2["validation"]["result"]["impact"]["rule_diff"] == {"added": [], "removed": [], "changed": []}
    old = client.get(base(env, v1), headers=env["steward"]).json()
    assert old["status"] == "SUPERSEDED"
    assert [v["status"] for v in v2["versions"]] == ["PUBLISHED", "SUPERSEDED"]


# ---- apply ----

def test_apply_stages_records_with_lineage_and_is_idempotent(client, env, session_factory, platform, db):
    uid = upload_confirmed(client, env, session_factory, platform)
    m = to_published(client, env, session_factory, platform)
    vid = version_id(session_factory, m)
    r = apply(client, env, session_factory, platform, uid, vid, key="apply-1")
    assert r.status_code == 202
    ex = client.get(f"{API}/runs/{env['run']['id']}/mapping-executions/{r.json()['id']}", headers=env["pm"]).json()
    assert ex["status"] == "SUCCEEDED" and ex["counts"]["records"] == {"Offer": 1, "Product": 3}
    assert ex["counts"]["rejected_rows"] == 0 and ex["output_hash"].startswith("sha256:")

    again = apply(client, env, session_factory, platform, uid, vid, key="apply-1")
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"] and again.json()["existing"]
    other_key = apply(client, env, session_factory, platform, uid, vid, key="apply-2")
    assert other_key.json()["id"] == r.json()["id"]  # same file + same version = same execution

    recs = client.get(f"{API}/runs/{env['run']['id']}/mapping-executions/{ex['id']}/records",
                      params={"entity": "Product"}, headers=env["pm"]).json()
    assert recs["total"] == 3
    p = next(i for i in recs["items"] if i["key"] == '["PRD-1002"]')
    assert p["values"]["status"] == "ACTIVE" and p["source_rows"] == [3]
    lin = {x["field"]: x for x in p["lineage"]}
    assert (lin["status"]["source_field"], lin["status"]["source_value"], lin["status"]["lookup_ref"],
            lin["status"]["row"], lin["status"]["sheet"]) == ("Status", "Live", "PRODUCT-STATUS@1.0", 3, "Data")
    assert lin["status"]["source_system"] == "EXCEL_UPLOAD:CRM_CATALOG" and lin["status"]["upload_id"] == uid
    assert lin["status"]["mapping_version_id"] == vid

    # replaying the same file with the same version reproduces identical staged values and lineage
    with session_factory() as s:
        ex_row = s.get(MappingExecution, uuid.UUID(ex["id"]))
        v = s.get(service.MappingVersion, uuid.UUID(vid))
        rules = service.rules_of(s, v)
        upload = s.get(service.EvidenceUpload, uuid.UUID(uid))
        replay = execute(rules, ExcelSourceAdapter("crm.xlsx", platform.storage.get(upload.storage_key), LIMITS)
                         .extract("Data", 1), service.context_for(s, v, rules),
                         source={"upload_id": uid, "sheet": "Data"})
        assert output_hash(replay) == ex_row.output_hash
        staged = {(r.entity, r.record_key): r.values for r in s.scalars(
            select(StagedRecord).where(StagedRecord.execution_id == ex_row.id))}
        assert staged == {k: rec.values for k, rec in replay.records.items()}
        n_lineage = s.scalar(select(text("count(*)")).select_from(FieldLineage).where(
            FieldLineage.execution_id == ex_row.id))
        assert n_lineage == sum(len(rec.lineage) for rec in replay.records.values())


def test_apply_rules(client, env, session_factory, platform):
    uid = upload_confirmed(client, env, session_factory, platform)
    m = validate(client, env, session_factory, platform, create(client, env))
    draft_vid = version_id(session_factory, m)
    r = client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{uid}/apply", headers=env["steward"],
                    json={"mapping_version_id": draft_vid})
    assert r.status_code == 409  # only published versions apply
    m = to_published(client, env, session_factory, platform, m)
    narrow = f.xlsx({"Data": [["Product ID", "Product Name"], ["PRD-9", "X"]]})
    up = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/uploads", headers=env["steward"],
                     files={"file": ("narrow.xlsx", narrow, XLSX)}).json()
    with session_factory() as s:
        ev_service.process_upload(s, platform, LIMITS, uuid.UUID(up["id"]))
    client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{up['id']}/confirm", headers=env["steward"],
                json={"sheet": "Data", "header_row": 1})
    r = client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{up['id']}/apply", headers=env["steward"],
                    json={"mapping_version_id": version_id(session_factory, m)})
    assert r.status_code == 409 and "Missing" in r.json()["detail"]["errors"][0]["message"]
    r1 = apply(client, env, session_factory, platform, uid, version_id(session_factory, m), key="k-1")
    up2 = f.xlsx({"Data": [CRM_HEADERS, CRM_ROWS[0]]})
    other = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/uploads", headers=env["steward"],
                        files={"file": ("one.xlsx", up2, XLSX)}).json()
    with session_factory() as s:
        ev_service.process_upload(s, platform, LIMITS, uuid.UUID(other["id"]))
    client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{other['id']}/confirm", headers=env["steward"],
                json={"sheet": "Data", "header_row": 1})
    reused_key = client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{other['id']}/apply",
                             headers=env["steward"] | {"Idempotency-Key": "k-1"},
                             json={"mapping_version_id": version_id(session_factory, m)})
    assert r1.status_code == 202 and reused_key.status_code == 409


def test_rows_with_errors_are_rejected_at_apply(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform)
    m = to_published(client, env, session_factory, platform)
    bad = f.xlsx({"Data": [CRM_HEADERS, CRM_ROWS[0], ["PRD-5", "Odd", "Broadband", "Unknown", None, None, None, None,
                                                      None, None]]})
    up = client.post(f"{API}/runs/{env['run']['id']}/evidence/CRM_CATALOG/uploads", headers=env["steward"],
                     files={"file": ("bad.xlsx", bad, XLSX)}).json()
    with session_factory() as s:
        ev_service.process_upload(s, platform, LIMITS, uuid.UUID(up["id"]))
    client.post(f"{API}/runs/{env['run']['id']}/evidence/uploads/{up['id']}/confirm", headers=env["steward"],
                json={"sheet": "Data", "header_row": 1})
    r = apply(client, env, session_factory, platform, up["id"], version_id(session_factory, m))
    ex = client.get(f"{API}/runs/{env['run']['id']}/mapping-executions/{r.json()['id']}", headers=env["pm"]).json()
    assert ex["counts"]["mapped_rows"] == 1 and ex["rejected_rows"][0]["row"] == 3
    assert "no lookup entry" in ex["rejected_rows"][0]["reasons"][0]


# ---- database immutability ----

def test_rules_freeze_after_submit_and_staged_data_is_append_only(client, env, session_factory, platform):
    uid = upload_confirmed(client, env, session_factory, platform)
    m = to_published(client, env, session_factory, platform)
    vid = version_id(session_factory, m)
    with session_factory() as s, pytest.raises(DBAPIError, match="cannot change"):
        s.execute(text("UPDATE rationalisation.mapping_rule SET required = NOT required WHERE version_id = :v"),
                  {"v": vid})
        s.commit()
    with session_factory() as s, pytest.raises(DBAPIError, match="frozen"):
        s.execute(text("UPDATE rationalisation.mapping_version SET content_hash = NULL WHERE id = :v"), {"v": vid})
        s.commit()
    with session_factory() as s, pytest.raises(DBAPIError, match="cannot change from"):
        s.execute(text("UPDATE rationalisation.mapping_version SET status = 'DRAFT' WHERE id = :v"), {"v": vid})
        s.commit()
    with session_factory() as s, pytest.raises(DBAPIError, match="never deleted"):
        s.execute(text("DELETE FROM rationalisation.mapping_version WHERE id = :v"), {"v": vid})
        s.commit()
    r = apply(client, env, session_factory, platform, uid, vid)
    for table in ("staged_record", "field_lineage", "staged_relationship", "mapping_approval"):
        col = "version_id" if table == "mapping_approval" else "execution_id"
        val = vid if table == "mapping_approval" else r.json()["id"]
        with session_factory() as s, pytest.raises(DBAPIError, match="append-only"):
            s.execute(text(f"DELETE FROM rationalisation.{table} WHERE {col} = :x"), {"x": val})
            s.commit()


@pytest.mark.parametrize(("current", "target"), [
    ("DRAFT", "IN_REVIEW"), ("DRAFT", "PUBLISHED"), ("APPROVED", "DRAFT"), ("PUBLISHED", "APPROVED")])
def test_trigger_refuses_illegal_status_jumps(client, env, session_factory, platform, current, target):
    upload_confirmed(client, env, session_factory, platform)
    m = create(client, env)
    vid = version_id(session_factory, m)
    path = {"DRAFT": [], "APPROVED": ["VALIDATING", "READY_FOR_REVIEW", "IN_REVIEW", "APPROVED"],
            "PUBLISHED": ["VALIDATING", "READY_FOR_REVIEW", "IN_REVIEW", "APPROVED", "PUBLISHED"]}[current]
    for step in path:
        with session_factory() as s:
            s.execute(text("UPDATE rationalisation.mapping_version SET status = :s WHERE id = :v"), {"s": step, "v": vid})
            s.commit()
    with session_factory() as s, pytest.raises(DBAPIError, match="cannot change from"):
        s.execute(text("UPDATE rationalisation.mapping_version SET status = :s WHERE id = :v"), {"s": target, "v": vid})
        s.commit()


# ---- permissions, tenancy ----

def test_permissions_and_tenancy(client, env, session_factory, platform, login):
    upload_confirmed(client, env, session_factory, platform)
    run_id = env["run"]["id"]
    assert client.post(f"{API}/runs/{run_id}/evidence/CRM_CATALOG/mapping", headers=env["pm"], json={}).status_code == 403
    m = create(client, env)
    assert client.get(base(env, m), headers=env["viewer"]).status_code == 200  # read-only view
    assert client.post(f"{base(env, m)}/validate", headers=env["viewer"]).status_code == 403
    other = login("data_steward", "t-other-mapping")
    assert client.get(base(env, m), headers=other).status_code == 404
    assert client.post(f"{base(env, m)}/validate", headers=other).status_code == 404


# ---- optional AI suggestions ----

def test_ai_suggestions_only_for_unmapped_columns(client, env, session_factory, platform):
    headers = [*CRM_HEADERS, "Prod Tagline"]
    rows = [[*r, "Fast and simple"] for r in CRM_ROWS]
    upload_confirmed(client, env, session_factory, platform, rows=rows, headers=headers)
    m = create(client, env)
    platform.ai = FakeAI([
        {"suggestions": [{"column": "Prod Tagline", "target": "Product.offerX"}]},  # invalid target -> retried
        {"suggestions": [{"column": "Prod Tagline", "target": "Product.description", "reason": "free text"}]},
    ])
    # Description column is mapped by the template; drop that rule so the target is free
    rules = [r for r in m["rules"] if r["source_column"] != "Description"]
    m = client.patch(base(env, m), headers=env["steward"], json={"lock_version": m["lock_version"],
                                                                 "rules": rules}).json()
    r = client.post(f"{base(env, m)}/ai-suggestions", headers=env["steward"], json={"lock_version": m["lock_version"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["added"], out["source"]) == (1, "llm")
    added = next(x for x in out["mapping"]["rules"] if x["source_column"] == "Prod Tagline")
    assert (added["target_field"], added["proposed_by"]) == ("description", "AI")
    prompt = json.loads(platform.ai.requests[0].prompt)
    assert [c["header"] for c in prompt["columns"]] == ["Description", "Prod Tagline"]  # only unmapped columns
    assert all(len(c["samples"]) <= 5 for c in prompt["columns"])


def test_ai_unavailable_falls_back_to_no_suggestions(client, env, session_factory, platform):
    upload_confirmed(client, env, session_factory, platform)
    m = create(client, env)
    rules = [r for r in m["rules"] if r["source_column"] != "Description"]  # leave one column unmapped
    m = client.patch(base(env, m), headers=env["steward"], json={"lock_version": m["lock_version"],
                                                                 "rules": rules}).json()
    platform.ai = FakeAI([AIUnavailable("down")])
    r = client.post(f"{base(env, m)}/ai-suggestions", headers=env["steward"], json={"lock_version": m["lock_version"]})
    assert r.status_code == 200 and r.json()["added"] == 0 and r.json()["source"] == "fallback"
