"""Step 15 end to end: register a Salesforce connector (secret encrypted, never returned), test it, pull the CRM
catalogue into a run next to an Excel upload, map it with the governed template, reconcile both sources by the
precedence policy and approve the snapshot."""

import json
import uuid

import httpx
import pytest

from app.adapters.sources import salesforce as sf
from app.db.models import Connector
from tests.test_reconciliation_api import (  # noqa: F401 - env is a fixture
    CRM,
    R,
    apply,
    default_source,
    env,
    load,
    publish_mapping,
    reconcile,
)

API = "/api/rationalisation"
PRODUCTS = [
    {"ProductCode": "PRD-1", "Name": "Mobile Data 15GB", "Family": "Postpaid", "IsActive": True,
     "StockKeepingUnit": "OFR-1", "Description": "Salesforce master record"},
    {"ProductCode": "PRD-3", "Name": "Fibre 100", "Family": "Broadband", "IsActive": False,
     "StockKeepingUnit": None, "Description": None},
]


def salesforce_fake(calls: list[dict], *, token_ok: bool = True, pages: int = 1):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "auth": request.headers.get("Authorization")})
        if request.url.path == "/services/oauth2/token":
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            if not token_ok or body.get("client_secret") != "s3cret":
                return httpx.Response(400, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "tok-123", "token_type": "Bearer"})
        if request.headers.get("Authorization") != "Bearer tok-123":
            return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
        if request.url.path.endswith("/limits"):
            return httpx.Response(200, json={"DailyApiRequests": {"Max": 15000, "Remaining": 14999}})
        if request.url.path.endswith("/query"):
            soql = request.url.params["q"]
            assert "Product2" in soql and "ProductCode" in soql
            if pages > 1:
                return httpx.Response(200, json={"done": False, "records": PRODUCTS[:1],
                                                 "nextRecordsUrl": "/services/data/v61.0/query/01g-2000"})
            return httpx.Response(200, json={"done": True, "totalSize": 2, "records": PRODUCTS})
        if "/query/01g-2000" in request.url.path:
            return httpx.Response(200, json={"done": True, "records": PRODUCTS[1:]})
        return httpx.Response(404, json=[{"errorCode": "NOT_FOUND"}])
    return handler


@pytest.fixture
def fake_sf(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(sf, "TRANSPORT", httpx.MockTransport(salesforce_fake(calls, pages=2)))
    return calls


def register(c, headers, **over):
    body = {"key": "sf-prod", "name": "Salesforce production", "system": "SALESFORCE",
            "instanceUrl": "https://acme.my.salesforce.com", "clientId": "3MVG9-client", "clientSecret": "s3cret"}
    body.update(over)
    return c.post(f"{API}/connectors", headers=headers, json=body)


def test_register_test_and_secret_handling(env, fake_sf):
    c = env["client"]
    assert register(c, env["steward"]).status_code == 403  # connector.manage only
    bad = register(c, env["admin"], instanceUrl="http://acme.example")
    assert bad.status_code == 422 and bad.json()["detail"]["errors"][0]["field"] == "instanceUrl"
    r = register(c, env["admin"])
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["secretSet"] is True and "clientSecret" not in json.dumps(conn) and "s3cret" not in r.text
    assert conn["domains"] == ["CPQ", "CRM_CATALOG", "PRICE_BOOK"] and conn["apiVersion"] == "v61.0"
    assert register(c, env["admin"]).status_code == 422  # duplicate key
    with env["sf"]() as s:
        row = s.get(Connector, uuid.UUID(conn["id"]))
        assert row.secret_ciphertext and "s3cret" not in row.secret_ciphertext
        assert env["platform"].secrets.decrypt(row.secret_ciphertext) == "s3cret"
    t = c.post(f"{API}/connectors/{conn['id']}/test", headers=env["admin"]).json()
    assert t["ok"] is True and fake_sf[0]["url"].endswith("/services/oauth2/token")
    listed = c.get(f"{API}/connectors", headers=env["steward"]).json()
    assert listed[0]["lastCheck"]["ok"] is True and listed[0]["secretSet"] is True
    # wrong secret -> a safe failure, no stack trace
    upd = c.put(f"{API}/connectors/{conn['id']}", headers=env["admin"],
                json={"key": "sf-prod", "name": "Salesforce production", "system": "SALESFORCE",
                      "instanceUrl": "https://acme.my.salesforce.com", "clientSecret": "wrong"})
    assert upd.status_code == 200
    t2 = c.post(f"{API}/connectors/{conn['id']}/test", headers=env["admin"]).json()
    assert t2["ok"] is False and "refused the credentials" in t2["problems"][0]["message"]
    assert c.delete(f"{API}/connectors/{conn['id']}", headers=env["admin"]).status_code == 204
    assert c.get(f"{API}/connectors", headers=env["admin"]).json() == []


def test_pull_reconciles_with_excel_and_keeps_lineage(env, fake_sf):
    c = env["client"]
    conn = register(c, env["admin"]).json()
    ov = c.get(f"{R(env)}/evidence", headers=env["pm"]).json()
    assert any(t["type"] == "API_CONNECTOR" and t["available"] for t in ov["domains"][0]["source_types"])
    # Excel first: PRD-1 with a different name and PRD-2 only in Excel
    u = load(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB V1", "Postpaid", "Active", "OFR-1"],
                                       ["PRD-2", "Mobile Data 30GB", "Postpaid", "Active", None]], name="crm.xlsx")
    apply(env, u, publish_mapping(env, "CRM_CATALOG", default_source(env, "CRM_CATALOG")))

    pulled = c.post(f"{R(env)}/evidence/CRM_CATALOG/pull", headers=env["steward"], json={"connectorId": conn["id"]})
    assert pulled.status_code == 201, pulled.text
    p = pulled.json()
    assert p["rows"] == 2 and p["soql"].startswith("SELECT ") and p["upload"]["status"] == "READY"
    assert p["upload"]["schema"]["template_match"] and p["upload"]["filename"].startswith("sf-prod-crm_catalog-")
    assert sum(1 for x in fake_sf if "/query" in x["url"]) == 2  # paginated
    sources = next(d for d in c.get(f"{R(env)}/evidence", headers=env["pm"]).json()["domains"]
                   if d["key"] == "CRM_CATALOG")["sources"]
    live = next(s for s in sources if s["source_type"] == "API_CONNECTOR")
    assert live["system"] == "SALESFORCE"
    lineage = c.get(f"{R(env)}/evidence/pulls", headers=env["pm"]).json()
    assert lineage[0]["status"] == "SUCCEEDED" and lineage[0]["uploadId"] == p["upload"]["id"]
    # the same pull again is a duplicate upload, not a second file
    again = c.post(f"{R(env)}/evidence/CRM_CATALOG/pull", headers=env["steward"], json={"connectorId": conn["id"]}).json()
    assert again["upload"]["id"] == p["upload"]["id"]

    # map the live source with the same governed template, apply, reconcile both sources
    apply(env, p["upload"]["id"], publish_mapping(env, "CRM_CATALOG", live["id"]))
    rec = reconcile(env)["latest_succeeded"]
    inputs = {i["source_name"]: i for i in rec["inputs"]}
    assert any("Salesforce" in k for k in inputs) and len(inputs) == 2
    assert rec["counts"]["records"]["Product"] == 3  # PRD-1 (both), PRD-2 (Excel only), PRD-3 (Salesforce only)
    conflicts = c.get(f"{R(env)}/reconciliation/{rec['id']}/conflicts", headers=env["steward"]).json()
    assert len(conflicts) >= 1  # PRD-1 name differs between Salesforce and Excel: settled by precedence/steward


def test_pull_failures_are_safe(env, monkeypatch):
    c = env["client"]
    calls: list[dict] = []
    monkeypatch.setattr(sf, "TRANSPORT", httpx.MockTransport(salesforce_fake(calls, token_ok=False)))
    conn = register(c, env["admin"]).json()
    r = c.post(f"{R(env)}/evidence/CRM_CATALOG/pull", headers=env["steward"], json={"connectorId": conn["id"]})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "connector_unavailable"
    assert "s3cret" not in r.text and "Traceback" not in r.text
    lineage = c.get(f"{R(env)}/evidence/pulls", headers=env["pm"]).json()
    assert lineage[0]["status"] == "FAILED" and "refused" in lineage[0]["error"]
    no_query = c.post(f"{R(env)}/evidence/INVENTORY/pull", headers=env["steward"], json={"connectorId": conn["id"]})
    assert no_query.status_code in (409, 422)
    missing = register(c, env["admin"], key="sf-two", clientSecret=None).json()
    assert c.post(f"{API}/connectors/{missing['id']}/test", headers=env["admin"]).status_code == 409
    assert c.get(f"{API}/connectors", headers=env["viewer"]).status_code == 403
