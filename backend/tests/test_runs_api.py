import pytest
from sqlalchemy import select

from app.db.models import AuditEvent
from app.services.reference import seed_reference_data

API = "/api/rationalisation"


@pytest.fixture
def env(client, login, session_factory, tenant):
    """Tenant with demo reference data and a signed-in Portfolio Manager (who is also the owner)."""
    with session_factory() as s:
        seed_reference_data(s, tenant)
        s.commit()
    headers = login("portfolio_manager", tenant)
    me = client.get(f"{API}/me", headers=headers).json()
    portfolios = {p["code"]: p["id"] for p in client.get(f"{API}/metadata/portfolios", headers=headers).json()}
    markets = {m["code"]: m["id"] for m in client.get(f"{API}/metadata/markets", headers=headers).json()}
    families = {f["code"]: f["id"] for f in client.get(f"{API}/metadata/product-families",
                                                         headers=headers).json()}
    return {"headers": headers, "me": me, "portfolios": portfolios, "markets": markets, "families": families}


def scope(env, **over):
    body = {
        "name": "Consumer Mobile Rationalisation Q4",
        "portfolio_id": env["portfolios"]["CONSUMER_MOBILE"],
        "market_ids": [env["markets"]["UK_CONSUMER"]],
        "product_family_ids": [env["families"]["POSTPAID"], env["families"]["SHARED_DATA"]],
        "period_start": "2025-11-16",
        "period_end": "2026-11-16",
        "objectives": ["REDUCE_DUPLICATES", "BSS_SUNSET", "REDUCE_FALLOUT"],
        "owner_id": env["me"]["user_id"],
    }
    return body | over


def create(client, env, **over):
    return client.post(f"{API}/runs", json=scope(env, **over), headers=env["headers"])


def error_fields(resp):
    return {e["field"] for e in resp.json()["detail"]["errors"]}


# ---- metadata ----

def test_metadata_lists(client, env):
    h = env["headers"]
    assert {"CONSUMER_MOBILE", "ENTERPRISE_CONNECTIVITY"} <= set(env["portfolios"])
    fams = client.get(f"{API}/metadata/product-families",
                      params={"portfolio_id": env["portfolios"]["ENTERPRISE_CONNECTIVITY"]}, headers=h).json()
    assert {f["code"] for f in fams} == {"MPLS", "DIA", "SD_WAN"}
    sim_only = next(f for f in client.get(f"{API}/metadata/product-families", headers=h).json()
                    if f["code"] == "POSTPAID_SIM_ONLY")
    assert sim_only["parent_id"] == env["families"]["POSTPAID"]
    objectives = client.get(f"{API}/metadata/objectives", headers=h).json()
    assert len(objectives) == 6 and objectives[0]["key"] == "REDUCE_DUPLICATES"


def test_people_picker_filters_owner_roles(client, env, make_user, tenant):
    make_user("data_steward", tenant, name="Dana Steward")
    make_user("product_owner", tenant, name="Paula Owner")
    people = client.get(f"{API}/people", params={"role": ["portfolio_manager", "product_owner"]},
                        headers=env["headers"]).json()
    names = {p["name"] for p in people}
    assert "Paula Owner" in names and "Dana Steward" not in names


# ---- create ----

def test_create_run(client, env, tenant, db):
    r = create(client, env)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["run_code"] == "RUN-0001"
    assert run["state"] == "CONFIGURING" and run["stage_label"] == "Configuring"
    assert run["portfolio"]["name"] == "Consumer Mobile"
    assert [m["name"] for m in run["markets"]] == ["UK Consumer"]
    assert run["owner"]["user_id"] == env["me"]["user_id"]
    assert run["scope_editable"] is True and run["version"] == 1 and run["risk"] is None

    second = create(client, env, name="Second run").json()
    assert second["run_code"] == "RUN-0002"

    events = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant,
                                                 AuditEvent.action == "run.created")
                        .order_by(AuditEvent.seq)).all()
    assert len(events) == 2
    assert events[0].payload["run_code"] == "RUN-0001"
    assert events[0].payload["scope"]["objectives"] == ["REDUCE_DUPLICATES", "BSS_SUNSET", "REDUCE_FALLOUT"]


def test_create_returns_all_field_errors(client, env):
    r = client.post(f"{API}/runs", json={"name": "ab"}, headers=env["headers"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "validation_failed"
    assert error_fields(r) == {"name", "portfolio_id", "market_ids", "product_family_ids",
                               "period_start", "period_end", "objectives", "owner_id"}


def test_families_must_belong_to_portfolio(client, env):
    r = create(client, env, product_family_ids=[env["families"]["MPLS"]])
    assert r.status_code == 422
    assert error_fields(r) == {"product_family_ids"}


def test_unknown_references_rejected(client, env):
    import uuid
    r = create(client, env, portfolio_id=str(uuid.uuid4()), market_ids=[str(uuid.uuid4())])
    assert r.status_code == 422
    assert {"portfolio_id", "market_ids"} <= error_fields(r)


def test_owner_must_be_pm_or_po(client, env, make_user, tenant, session_factory):
    make_user("data_steward", tenant, name="Dana Steward")
    steward = next(p for p in client.get(f"{API}/people", params={"query": "Dana"},
                                         headers=env["headers"]).json())
    r = create(client, env, owner_id=steward["user_id"])
    assert r.status_code == 422 and error_fields(r) == {"owner_id"}


def test_period_longer_than_36_months_rejected(client, env):
    r = create(client, env, period_start="2022-01-01", period_end="2026-01-02")
    assert r.status_code == 422 and error_fields(r) == {"period_end"}


def test_duplicate_active_name_rejected_case_insensitive(client, env):
    assert create(client, env).status_code == 201
    r = create(client, env, name="  consumer MOBILE rationalisation q4 ")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "name_taken" and error_fields(r) == {"name"}


def test_names_are_unique_per_tenant_only(client, env, login, session_factory, tenant):
    assert create(client, env).status_code == 201
    other = tenant + "-x"
    with session_factory() as s:
        seed_reference_data(s, other)
        s.commit()
    h2 = login("portfolio_manager", other)
    me2 = client.get(f"{API}/me", headers=h2).json()
    p = {x["code"]: x["id"] for x in client.get(f"{API}/metadata/portfolios", headers=h2).json()}
    m = {x["code"]: x["id"] for x in client.get(f"{API}/metadata/markets", headers=h2).json()}
    f = {x["code"]: x["id"] for x in client.get(f"{API}/metadata/product-families", headers=h2).json()}
    body = scope({"portfolios": p, "markets": m, "families": f, "me": me2})
    r = client.post(f"{API}/runs", json=body, headers=h2)
    assert r.status_code == 201 and r.json()["run_code"] == "RUN-0001"


# ---- permissions & tenancy ----

def test_viewer_cannot_create_or_edit(client, env, login, tenant):
    viewer = login("viewer", tenant)
    assert client.post(f"{API}/runs", json=scope(env), headers=viewer).status_code == 403
    run = create(client, env).json()
    assert client.get(f"{API}/runs/{run['id']}", headers=viewer).status_code == 200
    r = client.patch(f"{API}/runs/{run['id']}", json={"version": 1, "name": "New"}, headers=viewer)
    assert r.status_code == 403


def test_other_tenant_cannot_see_run(client, env, login, tenant):
    run = create(client, env).json()
    outsider = login("portfolio_manager", tenant + "-other")
    assert client.get(f"{API}/runs/{run['id']}", headers=outsider).status_code == 404
    assert client.get(f"{API}/runs", headers=outsider).json()["total"] == 0
    r = client.patch(f"{API}/runs/{run['id']}", json={"version": 1, "name": "Hijack"}, headers=outsider)
    assert r.status_code == 404


# ---- list / search / summary ----

def test_list_search_and_summary(client, env):
    create(client, env)
    create(client, env, name="Enterprise Connectivity",
           portfolio_id=env["portfolios"]["ENTERPRISE_CONNECTIVITY"],
           product_family_ids=[env["families"]["MPLS"]])
    h = env["headers"]
    all_runs = client.get(f"{API}/runs", headers=h).json()
    assert all_runs["total"] == 2
    assert all_runs["items"][0]["name"] == "Enterprise Connectivity"  # newest first

    by_portfolio = client.get(f"{API}/runs", params={"query": "consumer mob"}, headers=h).json()
    assert [i["name"] for i in by_portfolio["items"]] == ["Consumer Mobile Rationalisation Q4"]
    by_owner = client.get(f"{API}/runs", params={"query": env["me"]["name"].split()[0]}, headers=h).json()
    assert by_owner["total"] == 2
    by_code = client.get(f"{API}/runs", params={"query": "run-0002"}, headers=h).json()
    assert by_code["items"][0]["portfolio_name"] == "Enterprise Connectivity"

    assert client.get(f"{API}/runs/summary", headers=h).json() == {
        "active_runs": 2, "opportunities": 0, "awaiting_approval": 0, "blocked": 0}


# ---- edit ----

def test_edit_scope_bumps_version_and_audits(client, env, tenant, db):
    run = create(client, env).json()
    r = client.patch(f"{API}/runs/{run['id']}", json={
        "version": 1, "name": "Renamed run", "objectives": ["IMPROVE_MARGIN"]}, headers=env["headers"])
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["version"] == 2 and updated["name"] == "Renamed run"
    assert updated["objectives"] == ["IMPROVE_MARGIN"]
    assert updated["markets"] == run["markets"]  # untouched fields kept

    event = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant,
                                                AuditEvent.action == "run.scope_updated")).one()
    assert event.payload["changed_fields"] == ["name", "objectives"]
    assert event.payload["before"]["name"] == "Consumer Mobile Rationalisation Q4"


def test_edit_with_stale_version_conflicts(client, env):
    run = create(client, env).json()
    h = env["headers"]
    assert client.patch(f"{API}/runs/{run['id']}", json={"version": 1, "name": "First edit"},
                        headers=h).status_code == 200
    r = client.patch(f"{API}/runs/{run['id']}", json={"version": 1, "name": "Second edit"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_conflict"


def test_edit_without_changes_keeps_version(client, env):
    run = create(client, env).json()
    r = client.patch(f"{API}/runs/{run['id']}", json={"version": 1, "name": run["name"]},
                     headers=env["headers"])
    assert r.status_code == 200 and r.json()["version"] == 1


def test_edit_validates_merged_scope(client, env):
    run = create(client, env).json()
    r = client.patch(f"{API}/runs/{run['id']}", json={
        "version": 1, "portfolio_id": env["portfolios"]["ENTERPRISE_CONNECTIVITY"]}, headers=env["headers"])
    assert r.status_code == 422 and error_fields(r) == {"product_family_ids"}


def test_scope_locked_after_configuring(client, env, session_factory):
    from app.db.models import Run
    run = create(client, env).json()
    with session_factory() as s:
        s.get(Run, __import__("uuid").UUID(run["id"])).state = "ANALYSING"
        s.commit()
    r = client.patch(f"{API}/runs/{run['id']}", json={"version": 1, "name": "Too late"}, headers=env["headers"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_editable"
    assert client.get(f"{API}/runs/{run['id']}", headers=env["headers"]).json()["scope_editable"] is False
