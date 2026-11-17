"""Pure rules of the Evidence Hub: proposed domains, file checks, discovery, template matching."""

from datetime import datetime

import pytest

from app.adapters.local.scanner import EicarTestScanner
from app.adapters.sources.excel import ExcelSourceAdapter
from app.domain.evidence import DomainStatus, EvidenceDomain, domain_status, propose_domains, proposal_reasons
from app.domain.evidence_templates import match_template
from app.domain.excel_checks import FileLimits, check_workbook_file
from app.domain.excel_discovery import discover_workbook, looks_personal
from app.seed.telco import TEMPLATES
from app.services.evidence import schema_fingerprint
from tests import excel_files as f

D = EvidenceDomain
LIMITS = FileLimits()


# ---- proposed domains ----

def test_catalog_and_lifecycle_are_always_proposed():
    assert propose_domains([]) == [D.CRM_CATALOG, D.LIFECYCLE]


def test_objectives_add_domains_in_screen_order():
    assert propose_domains(["BSS_SUNSET"]) == [D.CRM_CATALOG, D.LIFECYCLE, D.TECHNICAL_CATALOG, D.PROVISIONING,
                                               D.INVENTORY, D.ORDER_BILLING_ASSURANCE, D.CUSTOMER_REVENUE_AGGREGATES]
    assert propose_domains(["IMPROVE_MARGIN", "UNKNOWN"]) == [D.CRM_CATALOG, D.CPQ, D.PRICE_BOOK, D.LIFECYCLE,
                                                              D.CUSTOMER_REVENUE_AGGREGATES]
    assert propose_domains(["PORTFOLIO_STANDARDISATION"]) == [D.CRM_CATALOG, D.CPQ, D.LIFECYCLE, D.TECHNICAL_CATALOG]
    assert len(propose_domains(["REDUCE_DUPLICATES", "BSS_SUNSET", "IMPROVE_MARGIN"])) == 9


def test_proposal_reasons_are_traceable():
    reasons = proposal_reasons(["REDUCE_FALLOUT", "BSS_SUNSET"])
    assert reasons[D.CRM_CATALOG] == ["Always needed"]
    assert reasons[D.PROVISIONING] == ["Objective REDUCE_FALLOUT", "Objective BSS_SUNSET"]


def test_domain_status():
    assert domain_status([]) is DomainStatus.NO_SOURCE
    assert domain_status([("REJECTED", False)]) is DomainStatus.REJECTED
    assert domain_status([("REJECTED", False), ("PROCESSING", False)]) is DomainStatus.CHECKING
    assert domain_status([("READY", False)]) is DomainStatus.NEEDS_CONFIRMATION
    assert domain_status([("READY", True), ("PROCESSING", False)]) is DomainStatus.READY


# ---- file checks ----

def codes(problems):
    return [p.code for p in problems]


def test_good_file_passes():
    assert check_workbook_file("catalog.xlsx", f.xlsx({"Data": f.GOOD}), LIMITS) == []
    assert check_workbook_file("Catalog.XLSX", f.with_printer_settings(), LIMITS) == []


@pytest.mark.parametrize(("name", "data", "code"), [
    ("catalog.xlsm", f.xlsx({"Data": f.GOOD}), "macro_enabled"),
    ("catalog.xls", f.legacy_xls(), "legacy_format"),
    ("catalog.csv", f.not_a_zip(), "not_xlsx"),
    ("catalog.xlsx", b"", "empty"),
    ("catalog.xlsx", f.with_macros(), "macros"),
    ("catalog.xlsx", f.encrypted(), "password_protected"),
    ("catalog.xlsx", f.legacy_xls(), "legacy_format"),
    ("catalog.xlsx", f.not_a_zip(), "malformed"),
    ("catalog.xlsx", f.broken_zip(), "malformed"),
    ("catalog.xlsx", f.many_sheets(21), "too_many_sheets"),
])
def test_bad_files_are_rejected_with_a_reason(name, data, code):
    problems = check_workbook_file(name, data, LIMITS)
    assert codes(problems) == [code]
    assert problems[0].message


def test_size_and_zip_bomb_limits():
    small = FileLimits(max_bytes=5000)
    assert codes(check_workbook_file("a.xlsx", f.xlsx({"Data": f.GOOD * 200}), small)) == ["too_large"]
    bomb = f.zip_bomb(3 * 1024 * 1024)
    assert len(bomb) < 100_000  # tiny on disk ...
    tight = FileLimits(max_unpacked_bytes=1024 * 1024)
    assert codes(check_workbook_file("a.xlsx", bomb, tight)) == ["too_large_unpacked"]  # ... huge unpacked
    assert codes(check_workbook_file("a.xlsx", f.xlsx({"Data": f.GOOD}), FileLimits(max_parts=3))) == ["too_complex"]


def test_eicar_scanner_stub():
    scanner = EicarTestScanner()
    assert scanner.scan(f.xlsx({"Data": f.GOOD}), filename="a.xlsx").clean
    result = scanner.scan(f.eicar_inside(), filename="a.xlsx")
    assert not result.clean and result.signature == "EICAR-Test-File"


# ---- discovery ----

def test_header_detection_skips_title_rows_and_infers_types():
    data = f.xlsx({"Export": [
        ["Product catalog export"], ["Generated 2026-11-16"], [],
        ["Product ID", "Name", "Price", "Live", "From", "Seen"],
        ["P1", "Fibre", 12.5, True, datetime(2026, 1, 1), datetime(2026, 1, 1, 10, 30)],
        [None, None, None, None, None, None],
        ["P2", "Copper", 20, False, datetime(2026, 2, 1), None],
    ]})
    info = discover_workbook(data, LIMITS)
    sheet = info.sheets[0]
    assert info.problems == [] and info.default_sheet == "Export"
    assert sheet.header_row == 4 and sheet.data_rows == 2  # the blank row is skipped
    assert [(c.header, c.type) for c in sheet.columns] == [
        ("Product ID", "string"), ("Name", "string"), ("Price", "decimal"), ("Live", "boolean"),
        ("From", "date"), ("Seen", "datetime")]
    assert sheet.columns[5].empty_pct == 50
    assert sheet.samples[0] == ["P1", "Fibre", "12.5", "TRUE", "2026-01-01", "2026-01-01 10:30:00"]
    assert any("row 4" in w for w in sheet.warnings)


def test_forced_header_row_and_sheet_problems():
    data = f.xlsx({"Data": [["Title"], ["A", "B"], ["1", "2"]],
                   "Dupes": [["Product ID", "product id"], ["x", "y"]],
                   "Empty": [["Product ID", "Name"]]})
    forced = discover_workbook(data, LIMITS, sheet="Data", header_row=2).sheets[0]
    assert forced.header_row == 2 and [c.header for c in forced.columns] == ["A", "B"]
    info = discover_workbook(data, LIMITS)
    by_name = {s.name: s for s in info.sheets}
    assert by_name["Empty"].problems == ["No data rows under the header"]
    # the real header is kept and the duplicate is reported
    dupes = by_name["Dupes"]
    assert dupes.header_row == 1 and dupes.problems == [
        "Duplicate header 'product id' (columns A and B)"]
    numbers = discover_workbook(f.xlsx({"N": [[1, 2], [3, 4]]}), LIMITS).sheets[0]
    assert numbers.problems == ["No header row found"]


def test_default_sheet_prefers_visible_valid_sheets():
    data = f.xlsx({"Hidden": f.GOOD, "Notes": [["just a note"]], "Data": f.GOOD}, hidden=("Hidden",))
    info = discover_workbook(data, LIMITS)
    assert info.default_sheet == "Data"
    assert "Hidden sheet" in info.sheets[0].warnings


def test_row_and_column_limits():
    rows = [["Product ID"]] + [[f"P{i}"] for i in range(30)]
    assert [p.code for p in discover_workbook(f.xlsx({"D": rows}), FileLimits(max_rows=20)).problems] == [
        "too_many_rows"]
    wide = [[f"C{i}" for i in range(12)], list(range(12))]
    assert [p.code for p in discover_workbook(f.xlsx({"D": wide}), FileLimits(max_columns=10)).problems] == [
        "too_many_columns"]


@pytest.mark.parametrize("header", ["MSISDN", "Customer Name", "E-mail", "Billing Account Number", "IMSI",
                                    "Date of Birth", "Subscriber ID", "Postcode"])
def test_personal_data_headers(header):
    assert looks_personal(header)


@pytest.mark.parametrize("header", ["Subscriber Count", "Active Count", "Product ID", "Owner", "Customer Segment",
                                    "Account Type", "Region"])
def test_aggregates_and_metadata_are_allowed(header):
    assert not looks_personal(header)


def test_personal_data_blocks_the_file():
    by_header = f.xlsx({"Data": [["Product ID", "MSISDN"], ["P1", "447700900123"]]})
    assert [p.code for p in discover_workbook(by_header, LIMITS).problems] == ["personal_data"]
    by_value = f.xlsx({"Data": [["Product ID", "Contact"], ["P1", "jane.doe@example.com"]]})
    problems = discover_workbook(by_value, LIMITS).problems
    assert [p.code for p in problems] == ["personal_data"] and "e-mail" in problems[0].message


# ---- template matching, fingerprint, adapter ----

TPL = {key: content for key, _, content in TEMPLATES}


def test_template_match_complete_and_missing():
    crm = TPL["TPL-CRM-CATALOG"]
    m = match_template(crm, ["product id", "Product  Name", "Product Family", "Status", "Colour"])
    assert m["complete"] and m["required_found"] == m["required_total"] == 4
    assert m["unknown_columns"] == ["Colour"]
    assert m["matched"][0]["target"] == {"entity": "Product", "field": "productId"}
    m = match_template(crm, ["Product ID", "Status"])
    assert not m["complete"] and m["required_missing"] == ["Product Name", "Product Family"]


def test_template_one_of_group():
    prov = TPL["TPL-PROVISIONING"]
    base = ["Workflow ID", "Rule/Action", "Target System"]
    assert not match_template(prov, base)["complete"]
    assert match_template(prov, base)["one_of_missing"] == ["Product ID or Service Spec ID"]
    assert match_template(prov, [*base, "Service Spec ID"])["complete"]


def test_fingerprint_depends_on_ordered_headers_only():
    a = schema_fingerprint(["Product ID", "Name"])
    assert schema_fingerprint(["Product  ID", "Name "]) == a
    assert schema_fingerprint(["Name", "Product ID"]) != a
    assert schema_fingerprint(["Product ID", "Name", "Status"]) != a


def test_excel_adapter_extracts_rows_by_header():
    data = f.xlsx({"Data": [["Title"], ["Product ID", "Name"], ["P1", "Fibre"], [None, None], ["P2", "Copper"]]})
    adapter = ExcelSourceAdapter("a.xlsx", data, LIMITS)
    assert adapter.test_connection().ok
    assert adapter.discover_schema()["default_sheet"] == "Data"
    assert list(adapter.extract("Data", 2)) == [
        {"__row__": 3, "Product ID": "P1", "Name": "Fibre"},
        {"__row__": 5, "Product ID": "P2", "Name": "Copper"}]
    assert not ExcelSourceAdapter("a.xlsm", data, LIMITS).test_connection().ok
