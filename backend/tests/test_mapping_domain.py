"""Pure Mapping Rules engine: rule types, conversions, static + data checks, determinism, suggestions."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.domain.mapping import (
    TRANSITIONS,
    Context,
    MappingStatus,
    MappingType,
    MappingValueError,
    NullHandling,
    ProposedBy,
    RefInfo,
    Rule,
    can_transition,
    coerce,
    content_hash,
    execute,
    output_hash,
    rule_diff,
    validate,
)
from app.domain.mapping_suggest import norm, suggest
from app.seed.telco import LOOKUPS, TELCO_CANONICAL, TEMPLATES, TRANSFORMS

T = MappingType
LK = {k: (f"{k}@1.0", c) for k, _, c in LOOKUPS}
TF = {k: (f"{k}@1.0", c) for k, _, c in TRANSFORMS}
TPL = {c["domain"]: c for _, _, c in TEMPLATES}
XWALK = {"target": {"schema": "TELCO-CANONICAL", "entity": "Product"},
         "entries": [{"sourceSystem": "SALESFORCE", "sourceKey": "01t1", "canonicalId": "PRD-1"}]}
REFS = ({ref: RefInfo("LOOKUP", "PUBLISHED", True, c) for ref, c in LK.values()}
        | {ref: RefInfo("TRANSFORM", "PUBLISHED", True, c) for ref, c in TF.values()}
        | {"SF-XWALK@1.0": RefInfo("CROSSWALK", "PUBLISHED", True, XWALK),
           "OLD-LOOKUP@1.0": RefInfo("LOOKUP", "SUPERSEDED", False, LK["PRODUCT-STATUS"][1])})
COLS = {"Product ID": "string", "Name": "string", "Family": "string", "Status": "string", "Offer": "string",
        "Parent": "string", "SF Id": "string", "Desc A": "string", "Desc B": "string"}
SOURCE = {"upload_id": "00000000-0000-0000-0000-000000000001", "sheet": "Data"}


def ctx(columns=None):
    return Context(TELCO_CANONICAL, REFS, columns or COLS)


def R(key, typ, col=None, entity="Product", field=None, **kw):  # noqa: N802 - test helper
    return Rule(key=key, mapping_type=typ, source_column=col, target_entity=entity, target_field=field, **kw)


PRODUCT_RULES = [
    R("R1", T.DIRECT, "Product ID", field="productId", required=True),
    R("R2", T.DIRECT, "Name", field="productName", required=True),
    R("R3", T.DIRECT, "Family", field="productFamily", required=True),
    R("R4", T.LOOKUP, "Status", field="status", lookup_ref="PRODUCT-STATUS@1.0", required=True),
]


def row(n, **values):
    base = {"Product ID": "P1", "Name": "Fibre", "Family": "Broadband", "Status": "Active"}
    return {"__row__": n, **base, **values}


def codes(issues):
    return {i["code"] if isinstance(i, dict) else i.code for i in issues}


# ---- lifecycle ----

def test_only_editable_states_lead_to_review_and_nothing_returns_from_retired():
    assert can_transition("READY_FOR_REVIEW", "IN_REVIEW")
    assert not can_transition("DRAFT", "IN_REVIEW")  # must validate first
    assert can_transition("IN_REVIEW", "DRAFT")  # return
    assert not can_transition("PUBLISHED", "DRAFT")
    assert TRANSITIONS[MappingStatus.RETIRED] == frozenset()


# ---- conversions ----

@pytest.mark.parametrize(("raw", "fdef", "expected"), [
    (" PRD-1 ", {"type": "string"}, "PRD-1"),
    (12, {"type": "string"}, "12"),
    (12.0, {"type": "integer"}, 12),
    ("7", {"type": "integer"}, 7),
    (29.99, {"type": "decimal"}, Decimal("29.99")),
    ("Yes", {"type": "boolean"}, True),
    ("2026-01-05", {"type": "date"}, date(2026, 1, 5)),
    ("ACTIVE", {"type": "enum", "values": ["ACTIVE"]}, "ACTIVE"),
    ("", {"type": "string"}, None),
])
def test_coerce(raw, fdef, expected):
    assert coerce(raw, fdef) == expected


@pytest.mark.parametrize(("raw", "fdef", "code"), [
    ("1.5", {"type": "integer"}, "TYPE_MISMATCH"),
    ("05/01/2026", {"type": "date"}, "TYPE_MISMATCH"),
    ("Active", {"type": "enum", "values": ["ACTIVE"]}, "NOT_ALLOWED"),
    ("gbp", {"type": "string", "pattern": "^[A-Z]{3}$"}, "PATTERN"),
    (-1, {"type": "integer", "minimum": 0}, "BELOW_MINIMUM"),
    ("maybe", {"type": "boolean"}, "TYPE_MISMATCH"),
])
def test_coerce_rejects(raw, fdef, code):
    with pytest.raises(MappingValueError) as exc:
        coerce(raw, fdef)
    assert exc.value.code == code


# ---- static checks ----

def test_valid_rules_pass_static_checks():
    issues = validate(PRODUCT_RULES, iter([row(2)]), ctx(), source=SOURCE)["issues"]
    assert {i["severity"] for i in issues} == {"WARNING"}  # only the unmapped-columns warning
    assert codes(issues) == {"UNMAPPED_COLUMNS"}


@pytest.mark.parametrize(("rules", "code"), [
    ([], "NO_RULES"),
    ([*PRODUCT_RULES, R("R5", T.DIRECT, "Missing col", field="description")], "UNKNOWN_COLUMN"),
    ([*PRODUCT_RULES, R("R5", T.DIRECT, "Offer", field="productName")], "DUPLICATE_TARGET"),
    ([*PRODUCT_RULES, R("R5", T.LOOKUP, "Offer", field="market")], "MISSING_PARAM"),
    ([*PRODUCT_RULES, R("R5", T.DIRECT, "Offer", field="nope")], "UNKNOWN_TARGET"),
    ([*PRODUCT_RULES[:3], R("R4", T.LOOKUP, "Status", field="status", lookup_ref="NOPE@1.0")], "UNKNOWN_REF"),
    ([*PRODUCT_RULES[:3], R("R4", T.LOOKUP, "Status", field="status", lookup_ref="DATE-ISO@1.0")], "REF_KIND"),
    ([*PRODUCT_RULES[:3], R("R4", T.LOOKUP, "Status", field="status", lookup_ref="LIFECYCLE-STATUS@1.0")],
     "LOOKUP_TARGET"),
    ([*PRODUCT_RULES, R("R5", T.TRANSFORM, "Offer", field="description", transform_ref="DATE-ISO@1.0")],
     "TYPE_INCOMPATIBLE"),
    ([*PRODUCT_RULES[1:]], "KEY_NOT_MAPPED"),
    ([PRODUCT_RULES[0], PRODUCT_RULES[1], PRODUCT_RULES[3]], "REQUIRED_NOT_MAPPED"),
    ([*PRODUCT_RULES, R("R5", T.CROSSWALK, "SF Id", entity="Offer", field="offerId",
                        crosswalk_ref="SF-XWALK@1.0", source_system="SALESFORCE")], "CROSSWALK_TARGET"),
    ([*PRODUCT_RULES, R("R5", T.RELATIONSHIP, "Offer", entity=None, relationship={
        "name": "DECOMPOSES_TO", "fromEntity": "Offer", "fromColumn": "Offer", "toEntity": "Product",
        "toColumn": "Product ID"})], "RELATIONSHIP_ENDPOINTS"),
    ([*PRODUCT_RULES, R("R5", T.DIRECT, "MSISDN", field="description")], "PERSONAL_DATA"),
])
def test_static_blocking_checks(rules, code):
    columns = COLS | {"MSISDN": "string"}
    out = validate(rules, iter([row(2)]), ctx(columns), source=SOURCE)
    assert out["status"] == "BLOCKED"
    assert code in codes(out["issues"])
    assert "counts" not in out  # data is not run while the rules themselves are broken


def test_superseded_ref_is_a_warning():
    rules = [*PRODUCT_RULES[:3], R("R4", T.LOOKUP, "Status", field="status", lookup_ref="OLD-LOOKUP@1.0")]
    out = validate(rules, iter([row(2)]), ctx(), source=SOURCE)
    assert out["status"] == "WARNING" and "REF_NOT_CURRENT" in codes(out["issues"])


# ---- execution ----

def test_records_relationships_and_lineage():
    rules = [*PRODUCT_RULES,
             R("R5", T.DIRECT, "Offer", entity="Offer", field="offerId"),
             R("R6", T.DIRECT, "Parent", field="parentProductId"),
             R("R7", T.DERIVED, entity="Product", field="description",
               derived={"op": "CONCAT", "inputs": ["Desc A", "Desc B"], "separator": " - "}),
             R("R8", T.RELATIONSHIP, "Offer", entity=None, relationship={
                 "name": "INCLUDES", "fromEntity": "Offer", "fromColumn": "Offer", "toEntity": "Product",
                 "toColumn": "Product ID"})]
    rows = [row(2, Offer="O1", **{"Desc A": "Fast", "Desc B": "Home"}), row(3, Offer=None, Parent="P1",
                                                                          **{"Product ID": "P2", "Name": "Slow"})]
    res = execute(rules, iter(rows), ctx(), source=SOURCE)
    assert res.counts() == {"total_rows": 2, "mapped_rows": 2, "rejected_rows": 0,
                            "records": {"Offer": 1, "Product": 2}, "relationships": {"INCLUDES": 1}, "conflicts": 0}
    p1 = res.records[("Product", '["P1"]')]
    assert p1.values == {"productId": "P1", "productName": "Fibre", "productFamily": "Broadband", "status": "ACTIVE",
                         "description": "Fast - Home"}
    lin = p1.lineage["status"]
    assert (lin["source_field"], lin["source_value"], lin["rule_key"], lin["lookup_ref"], lin["row"]) == (
        "Status", "Active", "R4", "PRODUCT-STATUS@1.0", 2)
    assert lin["upload_id"] == SOURCE["upload_id"] and lin["sheet"] == "Data"
    assert res.records[("Product", '["P2"]')].values["parentProductId"] == "P1"
    assert [(e.name, e.from_key, e.to_key) for e in res.edges] == [("INCLUDES", '["O1"]', '["P1"]')]


def test_row_errors_reject_the_whole_row_with_reasons():
    rows = [row(2), row(3, Status="Gone"), row(4, Name=None), row(5, **{"Product ID": None})]
    out = validate(PRODUCT_RULES, iter(rows), ctx(), source=SOURCE)
    assert out["counts"]["mapped_rows"] == 1 and out["counts"]["rejected_rows"] == 3
    assert {"UNKNOWN_LOOKUP_VALUE", "NULL_REQUIRED"} <= codes(out["issues"])
    assert out["unknown_lookup_values"] == {"R4": [{"value": "Gone", "count": 1}]}
    assert out["required_field_coverage"] == 50  # rows 4 and 5 miss a required value
    assert [r["row"] for r in out["rejected_rows"]] == [3, 4, 5]


def test_duplicate_rows_merge_and_conflicts_are_rejected():
    rows = [row(2), row(3), row(4, **{"Product ID": "P2"}), row(5, **{"Product ID": "P2", "Name": "Other"})]
    res = execute(PRODUCT_RULES, iter(rows), ctx(), source=SOURCE)
    assert res.records[("Product", '["P1"]')].rows == [2, 3]  # identical rows merge
    assert ("Product", '["P2"]') not in res.records
    assert res.conflicts == [{"entity": "Product", "key": '["P2"]', "rows": [4, 5], "fields": ["productName"]}]


def test_transform_crosswalk_default_and_null_handling():
    cols = COLS | {"From": "string"}
    rules = [R("R1", T.CROSSWALK, "SF Id", field="productId", crosswalk_ref="SF-XWALK@1.0",
               source_system="SALESFORCE", required=True),
             *PRODUCT_RULES[1:3],
             R("R4", T.DERIVED, field="status", derived={"op": "CONSTANT", "value": "ACTIVE"}),
             R("R5", T.TRANSFORM, "From", entity="Product", field="market", transform_ref="CODE-NORMALISE@1.0",
               null_handling=NullHandling.DEFAULT, default_value="UK")]
    rows = [{"__row__": 2, "SF Id": "01t1", "Name": "A", "Family": "F", "From": " ie "},
            {"__row__": 3, "SF Id": "01t9", "Name": "B", "Family": "F", "From": None}]
    res = execute(rules, iter(rows), Context(TELCO_CANONICAL, REFS, cols), source=SOURCE)
    assert res.records[("Product", '["PRD-1"]')].values["market"] == "IE"
    assert res.unresolved_crosswalks["R1"]["01t9"] == 1 and res.rejected_rows[0]["row"] == 3


def test_relationship_name_from_column_and_cycles():
    cols = {"From": "string", "To": "string", "Kind": "string"}
    rules = [R("R1", T.RELATIONSHIP, "Kind", entity=None, relationship={
        "nameColumn": "Kind", "fromEntity": "Product", "fromColumn": "From", "toEntity": "Product", "toColumn": "To"})]
    rows = [{"__row__": 2, "From": "A", "To": "B", "Kind": "parent of"},
            {"__row__": 3, "From": "B", "To": "A", "Kind": "Parent-Of"},
            {"__row__": 4, "From": "C", "To": "D", "Kind": "REPLACED_BY"},
            {"__row__": 5, "From": "C", "To": "D", "Kind": "DECOMPOSES_TO"}]
    out = validate(rules, iter(rows), Context(TELCO_CANONICAL, REFS, cols), source=SOURCE)
    assert out["counts"]["relationships"] == {"REPLACED_BY": 1}  # A<->B cycle removed; row 5 invalid
    assert {"CYCLE", "INVALID_RELATIONSHIP"} <= codes(out["issues"])


def test_execution_is_deterministic_and_hash_sensitive():
    rows = [row(2), row(3, **{"Product ID": "P2", "Status": "Live"})]
    a = execute(PRODUCT_RULES, iter(rows), ctx(), source=SOURCE)
    b = execute(PRODUCT_RULES, iter([dict(r) for r in rows]), ctx(), source=SOURCE)
    assert output_hash(a) == output_hash(b)
    c = execute(PRODUCT_RULES, iter([row(2), row(3, **{"Product ID": "P2", "Status": "Planned"})]), ctx(),
                source=SOURCE)
    assert output_hash(c) != output_hash(a)


def test_content_hash_ignores_suggestion_metadata_but_not_rules():
    h = content_hash("sha256:f", "TELCO-CANONICAL@1.0", "sha256:d", PRODUCT_RULES)
    meta = [replace(r, confidence=50, proposed_by=ProposedBy.AI) for r in PRODUCT_RULES]
    assert content_hash("sha256:f", "TELCO-CANONICAL@1.0", "sha256:d", meta) == h
    assert content_hash("sha256:f", "TELCO-CANONICAL@1.0", "sha256:d", list(reversed(PRODUCT_RULES))) == h
    changed = [*PRODUCT_RULES[:3], replace(PRODUCT_RULES[3], required=False)]
    assert content_hash("sha256:f", "TELCO-CANONICAL@1.0", "sha256:d", changed) != h
    assert content_hash("sha256:g", "TELCO-CANONICAL@1.0", "sha256:d", PRODUCT_RULES) != h
    assert rule_diff(PRODUCT_RULES, changed) == {"added": [], "removed": [], "changed": ["R4"]}


# ---- suggestions ----

def cols(*specs):
    return [{"header": h, "type": t, "samples": s} for h, t, s in specs]


ENT = ["Product", "Offer"]


def test_template_suggestions_pick_lookups_and_transforms():
    columns = cols(("Product ID", "string", ["P1"]), ("Lifecycle Status", "string", ["EOS"]),
                   ("Effective From", "string", ["05/01/2026"]), ("Sunset Date", "date", ["2027-01-01"]))
    rules = suggest(columns, canonical=TELCO_CANONICAL, entities=["ProductLifecycle"], template=TPL["LIFECYCLE"],
                    lookups=LK, transforms=TF)
    by_col = {r.source_column: r for r in rules}
    assert by_col["Lifecycle Status"].lookup_ref == "LIFECYCLE-STATUS@1.0"
    assert by_col["Effective From"].transform_ref == "DATE-DMY@1.0"  # parses dd/mm/yyyy samples
    assert by_col["Sunset Date"].mapping_type is T.DIRECT
    assert all(r.proposed_by is ProposedBy.TEMPLATE and r.confidence == 100 for r in rules)
    assert by_col["Product ID"].required and not by_col["Sunset Date"].required


def test_name_and_alias_suggestions_and_ambiguity():
    columns = cols(("Product Name", "string", ["A"]), ("SKU", "string", ["P1"]), ("Market", "string", ["UK"]),
                   ("Colour", "string", ["Red"]))
    rules = suggest(columns, canonical=TELCO_CANONICAL, entities=ENT, template=None, lookups=LK, transforms=TF)
    by_col = {r.source_column: r for r in rules}
    assert (by_col["Product Name"].target_field, by_col["Product Name"].proposed_by) == ("productName",
                                                                                         ProposedBy.NAME_MATCH)
    assert (by_col["SKU"].target_field, by_col["SKU"].proposed_by, by_col["SKU"].confidence) == (
        "productId", ProposedBy.ALIAS, 80)
    assert "Market" not in by_col  # Product.market and Offer.market: ambiguous -> no guess
    assert "Colour" not in by_col


def test_no_suggestion_when_types_do_not_fit():
    columns = cols(("Effective From", "string", ["sometime"]), ("Status", "string", ["Unheard"]))
    rules = suggest(columns, canonical=TELCO_CANONICAL, entities=["CpqRule"], template=None, lookups={}, transforms=TF)
    assert rules == []


def test_prior_mapping_is_reused_whole():
    prior = [replace(r, proposed_by=ProposedBy.USER) for r in PRODUCT_RULES]
    columns = cols(*[(c, "string", []) for c in ("Product ID", "Name", "Family", "Status")])
    rules = suggest(columns, canonical=TELCO_CANONICAL, entities=ENT, template=TPL["CRM_CATALOG"], lookups=LK,
                    transforms=TF, prior=prior)
    assert [r.target_field for r in rules] == ["productId", "productName", "productFamily", "status"]
    assert {r.proposed_by for r in rules} == {ProposedBy.PRIOR_MAPPING}


def test_template_relationships_become_relationship_rules():
    columns = cols(("Product ID", "string", ["P1"]), ("Service Spec ID", "string", ["S1"]),
                   ("Service Type", "string", ["x"]), ("Relationship Type", "string", ["DECOMPOSES_TO"]))
    rules = suggest(columns, canonical=TELCO_CANONICAL, entities=["ServiceSpec", "ResourceSpec"],
                    template=TPL["TECHNICAL_CATALOG"], lookups=LK, transforms=TF)
    rel = [r for r in rules if r.mapping_type is T.RELATIONSHIP]
    assert len(rel) == 1 and rel[0].relationship["nameColumn"] == "Relationship Type"
    assert all(r.target_entity != "Product" for r in rules)  # Product ID is only an endpoint here


def test_norm():
    assert norm("productId") == norm("Product ID") == norm("product_id") == "product id"
