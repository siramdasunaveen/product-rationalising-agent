"""Evidence readiness demo: load the sample evidence of docs/samples stage by stage through the real API + worker.

Needs the API (port 8000) and the worker running, seeded demo users and PRA_DEMO_PASSWORD in backend/.env.
Run from the backend folder:

    .\\.venv\\Scripts\\python.exe scripts\\demo_readiness.py new                 # new run, all 9 domains selected
    .\\.venv\\Scripts\\python.exe scripts\\demo_readiness.py stage 1 RUN-0005    # CRM catalog
    .\\.venv\\Scripts\\python.exe scripts\\demo_readiness.py stage 2 RUN-0005    # CPQ, price book, lifecycle
    .\\.venv\\Scripts\\python.exe scripts\\demo_readiness.py stage 3 RUN-0005    # technical catalog, provisioning
    .\\.venv\\Scripts\\python.exe scripts\\demo_readiness.py stage 4 RUN-0005    # inventory, order/billing, customer/revenue

Each stage uploads and confirms the files, maps them with the template suggestions, validates, submits (Data
Steward), approves and publishes (Agent Admin: separation of duties), applies, reconciles and approves a new
evidence snapshot. Add --leave-approval to stop before the snapshot approval so you can click it live.
Domains already applied are skipped, so a stage can be re-run safely.
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from app.config import get_settings

API = "http://localhost:8000/api/rationalisation"
SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STAGES = {
    1: [("CRM_CATALOG", "CRM_Catalog_sample.xlsx")],
    2: [("CPQ", "CPQ_Rules_sample.xlsx"), ("PRICE_BOOK", "Price_Book_sample.xlsx"),
        ("LIFECYCLE", "Lifecycle_sample.xlsx")],
    3: [("TECHNICAL_CATALOG", "Technical_Catalog_sample.xlsx"), ("PROVISIONING", "Provisioning_sample.xlsx")],
    4: [("INVENTORY", "Inventory_sample.xlsx"), ("ORDER_BILLING_ASSURANCE", "Order_Billing_Assurance_sample.xlsx"),
        ("CUSTOMER_REVENUE_AGGREGATES", "Customer_Revenue_sample.xlsx")],
}

c = httpx.Client(timeout=60)


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def ok(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        fail(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def login(email: str) -> dict:
    password = get_settings().demo_password
    if not password:
        fail("Set PRA_DEMO_PASSWORD in backend/.env")
    token = ok(c.post(f"{API}/auth/login", json={"email": email, "password": password}))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def wait(fn, done, what: str, tries: int = 240) -> dict:
    for _ in range(tries):
        v = fn()
        if done(v):
            return v
        time.sleep(0.5)
    fail(f"timed out waiting for {what} (is the worker running?)")


def find_run(headers: dict, code: str) -> dict:
    items = ok(c.get(f"{API}/runs", headers=headers, params={"query": code}))["items"]
    run = next((r for r in items if r["run_code"] == code), None)
    if run is None:
        fail(f"run {code} not found")
    return run


def new_run() -> None:
    pm = login("pm@demo.local")
    me = ok(c.get(f"{API}/me", headers=pm))
    pid = {p["code"]: p["id"] for p in ok(c.get(f"{API}/metadata/portfolios", headers=pm))}
    mid = {m["code"]: m["id"] for m in ok(c.get(f"{API}/metadata/markets", headers=pm))}
    fid = {x["code"]: x["id"] for x in ok(c.get(f"{API}/metadata/product-families", headers=pm))}
    today = date.today()
    run = ok(c.post(f"{API}/runs", headers=pm, json={
        "name": f"Readiness demo {time.strftime('%d %b %H:%M')}", "portfolio_id": pid["CONSUMER_MOBILE"],
        "market_ids": [mid["UK_CONSUMER"]], "product_family_ids": [fid["POSTPAID"]],
        "period_start": today.replace(year=today.year - 1).isoformat(), "period_end": today.isoformat(),
        "objectives": ["REDUCE_DUPLICATES", "BSS_SUNSET"], "owner_id": me["user_id"]}))
    base = f"{API}/runs/{run['id']}"
    ov = ok(c.get(f"{base}/evidence", headers=pm))
    ok(c.put(f"{base}/evidence/domains", headers=pm,
             json={"domains": [d["key"] for d in ov["domains"]], "version": ov["version"]}))
    print(f"Created {run['run_code']} ({run['name']}) with all 9 evidence domains selected.")
    print(f"Next: scripts\\demo_readiness.py stage 1 {run['run_code']}")


def applied_domains(st: dict, base: str) -> set[str]:
    rec = ok(c.get(f"{base}/reconciliation", headers=st))
    return {i["domain"] for i in rec["current_inputs"]}


def load_domain(st: dict, admin: dict, base: str, domain: str, filename: str) -> None:
    path = SAMPLES / filename
    u = ok(c.post(f"{base}/evidence/{domain}/uploads", headers=st, files={"file": (filename, path.read_bytes(), XLSX)}))
    u = wait(lambda: ok(c.get(f"{base}/evidence/uploads/{u['id']}", headers=st)),
             lambda x: x["status"] != "PROCESSING", f"{filename} checks")
    if u["status"] != "READY":
        fail(f"{filename} was not accepted: {u.get('reasons')}")
    ok(c.post(f"{base}/evidence/uploads/{u['id']}/confirm", headers=st, json={"sheet": "Data", "header_row": 1}))
    m = ok(c.post(f"{base}/evidence/{domain}/mapping", headers=st, params={"source_id": u["source_id"]}, json={}))
    v = f"{base}/mappings/{m['mapping_id']}/versions/{m['version']}"
    ok(c.post(f"{v}/validate", headers=st))
    m = wait(lambda: ok(c.get(v, headers=st)), lambda x: x["status"] != "VALIDATING", f"{domain} validation")
    if m["status"] != "READY_FOR_REVIEW":
        issues = [i["message"] for i in m["validation"]["result"]["issues"] if i["severity"] == "BLOCKING"]
        fail(f"{domain} mapping did not validate: {issues[:5]}")
    m = ok(c.post(f"{v}/submit", headers=st, json={"lock_version": m["lock_version"], "acknowledge_warnings": True}))
    m = ok(c.post(f"{v}/review", headers=admin, json={"decision": "APPROVE", "content_hash": m["content_hash"]}))
    m = ok(c.post(f"{v}/publish", headers=admin, json={"lock_version": m["lock_version"]}))
    e = ok(c.post(f"{base}/evidence/uploads/{u['id']}/apply", headers=st, json={"mapping_version_id": m["version_id"]}))
    e = wait(lambda: ok(c.get(f"{base}/mapping-executions/{e['id']}", headers=st)),
             lambda x: x["status"] in ("SUCCEEDED", "FAILED"), f"{domain} apply")
    if e["status"] != "SUCCEEDED":
        fail(f"{domain} apply failed: {e.get('error')}")
    print(f"  {domain}: uploaded, mapped ({m['version_id'][:8]}), approved, published and applied")


def stage(number: int, code: str, leave_approval: bool) -> None:
    st, admin = login("steward@demo.local"), login("admin@demo.local")
    run = find_run(st, code)
    base = f"{API}/runs/{run['id']}"
    done = applied_domains(st, base)
    print(f"Stage {number} on {code}:")
    for domain, filename in STAGES[number]:
        if domain in done:
            print(f"  {domain}: already applied, skipped")
            continue
        load_domain(st, admin, base, domain, filename)
    rid = ok(c.post(f"{base}/reconciliation", headers=st))["id"]
    ov = wait(lambda: ok(c.get(f"{base}/reconciliation", headers=st)),
              lambda x: x["latest"] and x["latest"]["status"] in ("SUCCEEDED", "FAILED"), "reconciliation")
    if ov["latest"]["status"] != "SUCCEEDED":
        fail(f"reconciliation failed: {ov['latest']['error']}")
    print(f"  reconciled: {ov['latest']['counts']['records']}")
    if leave_approval:
        print("  Snapshot NOT approved: click 'Approve snapshot' on the Reconcile screen.")
    elif ov["approval_blockers"]:
        print(f"  Snapshot not approved, blockers: {ov['approval_blockers']}")
    else:
        snap = ok(c.post(f"{base}/snapshots", headers=st, json={"reconciliation_id": rid}))
        print(f"  approved {snap['code']} ({snap['counts']['facts']} facts)")
    r = ok(c.get(f"{base}/evidence-readiness", headers=st, params={"source": "preview" if leave_approval else
                                                                     "snapshot"}))
    k = r["kpis"]
    print(f"  readiness: {k['overall_completeness']}% complete, {k['domains_available']}/{k['domains_selected']} "
          f"domains available, {k['approval_blockers']} approval blockers")
    print(f"  dispositions ready: {', '.join(d['disposition'] for d in r['dispositions'] if d['ready'])}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new", help="Create a demo run with all evidence domains selected")
    s = sub.add_parser("stage", help="Load one stage of sample evidence into a run")
    s.add_argument("number", type=int, choices=sorted(STAGES))
    s.add_argument("run_code", help="e.g. RUN-0005")
    s.add_argument("--leave-approval", action="store_true", help="Do not approve the snapshot (click it live)")
    a = p.parse_args()
    if a.cmd == "new":
        new_run()
    else:
        stage(a.number, a.run_code, a.leave_approval)


if __name__ == "__main__":
    main()
