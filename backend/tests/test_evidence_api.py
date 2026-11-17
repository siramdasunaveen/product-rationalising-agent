"""Evidence Hub API: domain selection, template download, upload -> quarantine -> scan -> discovery ->
confirmed source schema versions."""

import uuid
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import AuditEvent, EvidenceUpload, OutboxEvent, Run
from app.domain.evidence import DOMAINS
from app.domain.excel_checks import FileLimits
from app.seed.telco import TEMPLATES
from app.services import evidence as service
from app.services.artifact_seed import seed_artifacts
from app.services.reference import seed_reference_data
from tests import excel_files as f

API = "/api/rationalisation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TPL_BY_DOMAIN = {c["domain"]: c for _, _, c in TEMPLATES}

# One valid data row per template (required + some optional columns), keyed by header.
ROWS = {
    "CRM_CATALOG": {"Product ID": "PRD-1", "Product Name": "Fibre 500", "Product Family": "Broadband",
                    "Status": "Active"},
    "CPQ": {"Product ID": "PRD-1", "Attribute/Option ID": "OPT-1", "Rule Type": "Dependency",
            "Effective From": "2026-01-01"},
    "PRICE_BOOK": {"Price Book ID": "PB-1", "Product ID": "PRD-1", "Currency": "GBP", "List Price": "29.99",
                   "Effective From": "2026-01-01"},
    "LIFECYCLE": {"Product ID": "PRD-1", "Lifecycle Status": "Active", "Effective From": "2026-01-01"},
    "TECHNICAL_CATALOG": {"Product ID": "PRD-1", "Service Spec ID": "CFS-1", "Service Type": "Internet",
                          "Relationship Type": "DECOMPOSES_TO"},
    "PROVISIONING": {"Service Spec ID": "CFS-1", "Workflow ID": "WF-1", "Rule/Action": "Activate",
                     "Target System": "Orchestrator"},
    "INVENTORY": {"Service Instance Aggregate Key": "AGG-1", "Service Spec ID": "CFS-1", "Resource Spec ID": "RS-1",
                  "Status": "ACTIVE", "Active Count": 1200},
    "ORDER_BILLING_ASSURANCE": {"Product ID": "PRD-1", "Period Start": "2026-10-01", "Period End": "2026-10-31",
                                "Orders": 5200, "Fallout Orders": 437, "Billing System ID": "BILL-A"},
    "CUSTOMER_REVENUE_AGGREGATES": {"Product ID": "PRD-1", "Period Start": "2026-10-01", "Period End": "2026-10-31",
                                    "Active Subscriptions": 86214, "MRR": 480000, "Currency": "GBP"},
}


@pytest.fixture
def env(client, login, session_factory, tenant, platform):
    """Seeded tenant, a Portfolio Manager, and a run whose objectives propose 5 domains."""
    with session_factory() as s:
        seed_reference_data(s, tenant)
        s.commit()
        seed_artifacts(s, platform.audit, tenant)
    pm = login("portfolio_manager", tenant)
    me = client.get(f"{API}/me", headers=pm).json()
    pid = {p["code"]: p["id"] for p in client.get(f"{API}/metadata/portfolios", headers=pm).json()}
    mid = {m["code"]: m["id"] for m in client.get(f"{API}/metadata/markets", headers=pm).json()}
    fid = {x["code"]: x["id"] for x in client.get(f"{API}/metadata/product-families", headers=pm).json()}
    run = client.post(f"{API}/runs", headers=pm, json={
        "name": "Evidence test run", "portfolio_id": pid["CONSUMER_MOBILE"], "market_ids": [mid["UK_CONSUMER"]],
        "product_family_ids": [fid["POSTPAID"]], "period_start": "2025-11-16", "period_end": "2026-11-16",
        "objectives": ["BSS_SUNSET"], "owner_id": me["user_id"]}).json()
    return {"pm": pm, "run": run, "tenant": tenant}


def ev(env, suffix=""):
    return f"{API}/runs/{env['run']['id']}/evidence{suffix}"


def upload(client, env, domain, data, name="evidence.xlsx", headers=None):
    return client.post(ev(env, f"/{domain}/uploads"), headers=headers or env["pm"],
                       files={"file": (name, data, XLSX)})


def process(session_factory, platform, upload_id):
    """What the background job does (the worker is not running in tests)."""
    with session_factory() as s:
        return service.process_upload(s, platform, FileLimits(), uuid.UUID(upload_id))


def template(client, env, domain):
    r = client.get(f"{API}/evidence/domains/{domain}/template", headers=env["pm"])
    assert r.status_code == 200, r.text
    return r.content


def filled(client, env, domain, *rows):
    tpl = template(client, env, domain)
    headers = [c["header"] for c in TPL_BY_DOMAIN[domain]["columns"]]
    return f.fill_template(tpl, [[row.get(h) for h in headers] for row in rows])


# ---- overview + domain selection ----

def test_overview_proposes_domains_from_objectives(client, env):
    ov = client.get(ev(env), headers=env["pm"]).json()
    assert ov["selection_confirmed"] is False and ov["editable"] is True
    selected = [d["key"] for d in ov["domains"] if d["selected"]]
    assert selected == ["CRM_CATALOG", "LIFECYCLE", "TECHNICAL_CATALOG", "PROVISIONING", "INVENTORY",
                        "ORDER_BILLING_ASSURANCE", "CUSTOMER_REVENUE_AGGREGATES"]
    assert len(ov["domains"]) == 9 and all(d["status"] == "NO_SOURCE" for d in ov["domains"])
    tech = next(d for d in ov["domains"] if d["key"] == "TECHNICAL_CATALOG")
    assert tech["proposal_reasons"] == ["Objective BSS_SUNSET"] and tech["template_ref"] == "TPL-TECHNICAL-CATALOG@1.0"
    available = {t["type"]: t["available"] for t in tech["source_types"]}
    assert available == {"API_CONNECTOR": True, "SECURE_QUERY": False, "SCHEDULED_EXTRACT": False,
                         "EXCEL_UPLOAD": True}  # live connectors since step 15


def test_confirm_domains(client, env, db):
    v = env["run"]["version"]
    r = client.put(ev(env, "/domains"), headers=env["pm"], json={"domains": ["CPQ", "CRM_CATALOG", "CPQ"], "version": v})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selection_confirmed"] and body["version"] == v + 1
    assert [d["key"] for d in body["domains"] if d["selected"]] == ["CRM_CATALOG", "CPQ"]
    stale = client.put(ev(env, "/domains"), headers=env["pm"], json={"domains": ["CPQ"], "version": v})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"
    bad = client.put(ev(env, "/domains"), headers=env["pm"], json={"domains": [], "version": v + 1})
    assert bad.status_code == 422
    audit = db.scalar(select(AuditEvent).where(AuditEvent.tenant_id == env["tenant"],
                                               AuditEvent.action == "run.evidence_domains_set"))
    assert audit.payload["added_to_proposal"] == ["CPQ"]
    assert "LIFECYCLE" in audit.payload["removed_from_proposal"]


def test_domains_only_change_while_configuring(client, env, session_factory):
    with session_factory() as s:
        s.get(Run, uuid.UUID(env["run"]["id"])).state = "ANALYSING"
        s.commit()
    r = client.put(ev(env, "/domains"), headers=env["pm"], json={"domains": ["CPQ"], "version": env["run"]["version"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_editable"
    assert upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD})).status_code == 409


# ---- templates ----

@pytest.mark.parametrize("domain", list(DOMAINS))
def test_template_download_and_round_trip(client, env, session_factory, platform, domain):
    """Download each governed template, fill one row, upload it: it is accepted and matches its template."""
    data = template(client, env, domain)
    wb = load_workbook(BytesIO(data))
    assert wb.sheetnames == ["Data", "Instructions"]
    headers = [c.value for c in wb["Data"][1]]
    assert headers == [c["header"] for c in TPL_BY_DOMAIN[domain]["columns"]]

    client.put(ev(env, "/domains"), headers=env["pm"], json={"domains": [domain], "version": env["run"]["version"]})
    r = upload(client, env, domain, filled(client, env, domain, ROWS[domain]), name=f"{domain}.xlsx")
    assert r.status_code == 202, r.text
    u = process(session_factory, platform, r.json()["id"])
    assert u.status == "READY", u.reasons
    sheet = next(s for s in u.discovery["sheets"] if s["name"] == "Data")
    assert u.discovery["default_sheet"] == "Data" and sheet["data_rows"] == 1
    assert sheet["template_match"]["complete"], sheet["template_match"]
    assert u.discovery["template_ref"] == f"{DOMAINS[domain].template_key}@1.0"


def test_template_has_allowed_values(client, env):
    wb = load_workbook(BytesIO(template(client, env, "LIFECYCLE")))
    lists = [dv.formula1 for dv in wb["Data"].data_validations.dataValidation]
    assert any("End of Sale" in x for x in lists)
    instructions = [[c.value for c in row] for row in wb["Instructions"].iter_rows()]
    assert any(r[0] == "Lifecycle Status" and r[1] == "Yes" for r in instructions)


# ---- upload flow ----

def test_upload_quarantine_scan_discover_confirm(client, env, session_factory, platform, db):
    data = filled(client, env, "CRM_CATALOG", ROWS["CRM_CATALOG"], ROWS["CRM_CATALOG"] | {"Product ID": "PRD-2"})
    r = upload(client, env, "CRM_CATALOG", data, name="crm.xlsx")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "PROCESSING" and body["discovery"] is None
    with session_factory() as s:
        row = s.get(EvidenceUpload, uuid.UUID(body["id"]))
        assert "/quarantine/" in row.storage_key and row.job_id
    assert next(d for d in client.get(ev(env), headers=env["pm"]).json()["domains"]
                if d["key"] == "CRM_CATALOG")["status"] == "CHECKING"

    u = process(session_factory, platform, body["id"])
    assert u.status == "READY" and "/checked/" in u.storage_key and u.scan["clean"]
    assert next(d for d in client.get(ev(env), headers=env["pm"]).json()["domains"]
                if d["key"] == "CRM_CATALOG")["status"] == "NEEDS_CONFIRMATION"

    preview = client.post(ev(env, f"/uploads/{body['id']}/preview"), headers=env["pm"],
                          json={"sheet": "Data", "header_row": 1}).json()
    assert preview["data_rows"] == 2 and preview["template_match"]["complete"]

    r = client.post(ev(env, f"/uploads/{body['id']}/confirm"), headers=env["pm"], json={"sheet": "Data", "header_row": 1})
    assert r.status_code == 200, r.text
    schema = r.json()["schema"]
    assert schema["version"] == 1 and schema["sheet_name"] == "Data" and schema["template_ref"] == "TPL-CRM-CATALOG@1.0"
    assert schema["fingerprint"].startswith("sha256:")
    domain = next(d for d in client.get(ev(env), headers=env["pm"]).json()["domains"] if d["key"] == "CRM_CATALOG")
    assert domain["status"] == "READY" and domain["uploads"][0]["schema_version"] == 1

    events = list(db.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == env["tenant"])
                             .order_by(OutboxEvent.id)))
    assert "RawEvidenceLanded" in events and "SourceSchemaDiscovered" in events
    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == env["tenant"])))
    assert {"evidence.uploaded", "evidence.upload_checked", "evidence.schema_confirmed"} <= actions


def test_same_headers_reuse_the_schema_version_and_changes_create_a_new_one(client, env, session_factory, platform, db):
    def upload_and_confirm(data):
        r = upload(client, env, "CRM_CATALOG", data)
        process(session_factory, platform, r.json()["id"])
        return client.post(ev(env, f"/uploads/{r.json()['id']}/confirm"), headers=env["pm"],
                           json={"sheet": "Data", "header_row": 1}).json()["schema"]

    v1 = upload_and_confirm(filled(client, env, "CRM_CATALOG", ROWS["CRM_CATALOG"]))
    again = upload_and_confirm(filled(client, env, "CRM_CATALOG", ROWS["CRM_CATALOG"] | {"Product ID": "PRD-9"}))
    assert again["id"] == v1["id"] and again["version"] == 1  # different data, same structure
    changed = upload_and_confirm(f.xlsx({"Data": [["Product ID", "Product Name", "Product Family", "Status", "Colour"],
                                                  ["P1", "A", "B", "Active", "Red"]]}))
    assert changed["version"] == 2 and changed["fingerprint"] != v1["fingerprint"]
    change = db.scalar(select(OutboxEvent).where(OutboxEvent.tenant_id == env["tenant"],
                                                 OutboxEvent.event_type == "SourceSchemaChanged"))
    assert change.payload["previous_version"] == 1 and change.payload["added_columns"] == ["Colour"]


def test_duplicate_file_returns_the_existing_upload(client, env):
    data = f.xlsx({"Data": f.GOOD})
    first = upload(client, env, "CRM_CATALOG", data)
    second = upload(client, env, "CRM_CATALOG", data)
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["duplicate"] is True and second.json()["id"] == first.json()["id"]


@pytest.mark.parametrize(("name", "data", "message"), [
    ("crm.xlsm", f.xlsx({"Data": f.GOOD}), "Macro-enabled"),
    ("crm.xlsx", f.with_macros(), "macros"),
    ("crm.xlsx", f.encrypted(), "password-protected"),
    ("crm.xlsx", f.broken_zip(), "damaged"),
    ("crm.csv", f.not_a_zip(), "Only Excel .xlsx"),
])
def test_bad_files_are_rejected_immediately(client, env, db, name, data, message):
    r = upload(client, env, "CRM_CATALOG", data, name=name)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "file_rejected"
    assert message in r.json()["detail"]["errors"][0]["message"]
    assert db.scalar(select(EvidenceUpload).where(EvidenceUpload.run_id == uuid.UUID(env["run"]["id"]))) is None
    audit = db.scalar(select(AuditEvent).where(AuditEvent.tenant_id == env["tenant"],
                                               AuditEvent.action == "evidence.upload_rejected"))
    assert audit is not None and audit.payload["filename"] == name


def test_file_size_limit(client, env, monkeypatch):
    client.app.state.settings = client.app.state.settings.model_copy(update={"upload_max_bytes": 2000})
    r = upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD * 50}))
    assert r.status_code == 422 and "limit" in r.json()["detail"]["errors"][0]["message"]


@pytest.mark.parametrize(("data", "code"), [
    (f.eicar_inside(), "malware"),
    (f.xlsx({"Data": [["Product ID", "MSISDN"], ["P1", "447700900123"]]}), "personal_data"),
    (f.xlsx({"Data": [[1, 2], [3, 4]]}), "no_data"),
])
def test_rejected_after_scan_or_reading_and_file_deleted(client, env, session_factory, platform, data, code):
    r = upload(client, env, "CRM_CATALOG", data)
    assert r.status_code == 202
    with session_factory() as s:
        key = s.get(EvidenceUpload, uuid.UUID(r.json()["id"])).storage_key
    u = process(session_factory, platform, r.json()["id"])
    assert u.status == "REJECTED" and u.reasons[0]["code"] == code and u.storage_key is None
    with pytest.raises(FileNotFoundError):
        platform.storage.get(key)
    out = client.get(ev(env, f"/uploads/{u.id}"), headers=env["pm"]).json()
    assert out["status"] == "REJECTED" and out["reasons"][0]["code"] == code
    assert next(d for d in client.get(ev(env), headers=env["pm"]).json()["domains"]
                if d["key"] == "CRM_CATALOG")["status"] == "REJECTED"
    # a rejected file can be fixed and uploaded again (same bytes are not a "duplicate" of a rejection)
    assert upload(client, env, "CRM_CATALOG", data).status_code == 202


def test_processing_twice_is_harmless(client, env, session_factory, platform):
    r = upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD}))
    first = process(session_factory, platform, r.json()["id"])
    second = process(session_factory, platform, r.json()["id"])
    assert first.status == second.status == "READY"


def test_confirm_rejects_unusable_sheets(client, env, session_factory, platform):
    data = f.xlsx({"Data": f.GOOD, "Dupes": [["A", "a"], [1, 2]]})
    r = upload(client, env, "CRM_CATALOG", data)
    uid = r.json()["id"]
    not_ready = client.post(ev(env, f"/uploads/{uid}/confirm"), headers=env["pm"], json={"sheet": "Data", "header_row": 1})
    assert not_ready.status_code == 409 and not_ready.json()["detail"]["code"] == "not_ready"
    process(session_factory, platform, uid)
    dupes = client.post(ev(env, f"/uploads/{uid}/confirm"), headers=env["pm"], json={"sheet": "Dupes", "header_row": 1})
    assert dupes.status_code == 422 and "Duplicate header" in dupes.json()["detail"]["errors"][0]["message"]
    missing = client.post(ev(env, f"/uploads/{uid}/preview"), headers=env["pm"], json={"sheet": "Nope"})
    assert missing.status_code == 422


def test_domain_must_be_selected(client, env):
    r = upload(client, env, "PRICE_BOOK", f.xlsx({"Data": f.GOOD}))  # not proposed for BSS_SUNSET
    assert r.status_code == 409 and r.json()["detail"]["code"] == "domain_not_selected"
    assert upload(client, env, "NOT_A_DOMAIN", f.xlsx({"Data": f.GOOD})).status_code == 404


# ---- permissions, tenancy, immutability ----

def test_permissions(client, env, login):
    steward = login("data_steward", env["tenant"])
    owner = login("product_owner", env["tenant"])
    viewer = login("viewer", env["tenant"])
    data = f.xlsx({"Data": f.GOOD})
    assert upload(client, env, "CRM_CATALOG", data, headers=steward).status_code == 202
    assert upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD * 2}), headers=owner).status_code == 403
    assert client.put(ev(env, "/domains"), headers=steward,
                      json={"domains": ["CPQ"], "version": env["run"]["version"]}).status_code == 403
    assert client.get(ev(env), headers=viewer).status_code == 200  # read-only view is allowed
    assert upload(client, env, "CRM_CATALOG", data, headers=viewer).status_code == 403


def test_tenant_isolation(client, env, login):
    r = upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD}))
    other = login("portfolio_manager", "t-other-evidence")
    assert client.get(ev(env), headers=other).status_code == 404
    assert client.get(ev(env, f"/uploads/{r.json()['id']}"), headers=other).status_code == 404
    assert upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD * 3}), headers=other).status_code == 404


def test_source_schema_versions_are_immutable(client, env, session_factory, platform):
    r = upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD}))
    process(session_factory, platform, r.json()["id"])
    schema_id = client.post(ev(env, f"/uploads/{r.json()['id']}/confirm"), headers=env["pm"],
                            json={"sheet": "Data", "header_row": 1}).json()["schema"]["id"]
    for sql in ("UPDATE rationalisation.source_schema_version SET sheet_name = 'X' WHERE id = :id",
                "DELETE FROM rationalisation.source_schema_version WHERE id = :id"):
        with session_factory() as s, pytest.raises(DBAPIError, match="immutable"):
            s.execute(text(sql), {"id": schema_id})
            s.commit()


def test_job_is_queued_with_the_upload_id(client, env, platform, session_factory):
    from rq.job import Job
    r = upload(client, env, "CRM_CATALOG", f.xlsx({"Data": f.GOOD}))
    with session_factory() as s:
        job_id = s.get(EvidenceUpload, uuid.UUID(r.json()["id"])).job_id
    job = Job.fetch(job_id, connection=platform.redis)
    assert job.func_name == "app.jobs.evidence.process_upload" and job.args == (r.json()["id"],)
