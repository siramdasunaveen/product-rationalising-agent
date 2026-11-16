import copy
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain.artifact_content import LookupMiss, apply_lookup, validate_content
from app.domain.artifacts import ArtifactKind as K
from app.domain.transforms import TransformError, apply_transform
from app.seed.telco import LOOKUPS, TELCO_CANONICAL, TRANSFORMS

SCHEMA = {
    "entities": [
        {"name": "Product", "keyFields": ["productId"], "fields": [
            {"name": "productId", "type": "string", "required": True},
            {"name": "status", "type": "enum", "values": ["ACTIVE", "RETIRED"]},
            {"name": "replacementId", "type": "reference", "references": "Product"},
        ]},
        {"name": "ServiceSpec", "keyFields": ["serviceSpecId"], "fields": [
            {"name": "serviceSpecId", "type": "string", "required": True},
        ]},
    ],
    "relationships": [{"name": "DECOMPOSES_TO", "from": "Product", "to": "ServiceSpec",
                       "cardinality": "MANY_TO_MANY"}],
}


def fields(errors):
    return {e.field for e in errors}


# ---- seed content ----

def test_seed_content_is_valid():
    assert validate_content(K.CANONICAL_SCHEMA, TELCO_CANONICAL) == []
    for _, _, content in LOOKUPS:
        assert validate_content(K.LOOKUP, content, canonical=TELCO_CANONICAL) == []
    for _, _, content in TRANSFORMS:
        assert validate_content(K.TRANSFORM, content) == []


def test_seed_schema_has_the_telco_graph():
    names = {e["name"] for e in TELCO_CANONICAL["entities"]}
    assert {"Product", "ServiceSpec", "ResourceSpec", "ProvisioningWorkflow", "InventoryAggregate"} <= names
    rels = {r["name"] for r in TELCO_CANONICAL["relationships"]}
    assert {"DECOMPOSES_TO", "REALIZED_BY", "USES_RESOURCE", "PROVISIONED_BY"} <= rels


# ---- canonical schema ----

def test_canonical_schema_ok():
    assert validate_content(K.CANONICAL_SCHEMA, SCHEMA) == []


def test_canonical_schema_structure_errors_have_paths():
    bad = copy.deepcopy(SCHEMA)
    bad["entities"][0]["fields"][0]["type"] = "money"
    del bad["entities"][1]["keyFields"]
    errors = validate_content(K.CANONICAL_SCHEMA, bad)
    assert {"entities[0].fields[0].type", "entities[1]"} <= fields(errors)


def test_canonical_schema_meaning_errors():
    bad = copy.deepcopy(SCHEMA)
    product = bad["entities"][0]
    product["fields"].append({"name": "status", "type": "string"})  # duplicate field
    product["fields"].append({"name": "kind", "type": "enum"})  # enum without values
    product["fields"].append({"name": "owner", "type": "reference", "references": "Person"})  # unknown entity
    product["fields"].append({"name": "msisdn", "type": "string", "pii": True})  # personal data
    product["keyFields"].append("missing")
    bad["entities"][1]["fields"][0]["required"] = False  # key field not required
    bad["relationships"].append({"name": "DECOMPOSES_TO", "from": "Product", "to": "Nowhere",
                                 "cardinality": "MANY_TO_MANY"})
    errors = validate_content(K.CANONICAL_SCHEMA, bad)
    assert {
        "entities[0].fields[3].name", "entities[0].fields[4].values", "entities[0].fields[5].references",
        "entities[0].fields[6].pii", "entities[0].keyFields[1]", "entities[1].keyFields[0]",
        "relationships[1].name", "relationships[1].to",
    } <= fields(errors)


def test_duplicate_entities_and_bad_pattern():
    bad = copy.deepcopy(SCHEMA)
    bad["entities"].append(copy.deepcopy(bad["entities"][1]))
    bad["entities"][0]["fields"][0]["pattern"] = "([unclosed"
    errors = validate_content(K.CANONICAL_SCHEMA, bad)
    assert {"entities[2].name", "entities[0].fields[0].pattern"} <= fields(errors)


# ---- lookup ----

LOOKUP = {
    "target": {"schema": "TEST-SCHEMA", "entity": "Product", "field": "status"},
    "entries": [{"source": "Active", "target": "ACTIVE"}, {"source": "Gone", "target": "RETIRED"}],
}


def test_lookup_ok_and_apply():
    assert validate_content(K.LOOKUP, LOOKUP, canonical=SCHEMA) == []
    assert apply_lookup(LOOKUP, "  active ") == "ACTIVE"
    assert apply_lookup(LOOKUP, None) is None
    with pytest.raises(LookupMiss):
        apply_lookup(LOOKUP, "Unknown")
    assert apply_lookup({**LOOKUP, "unmatched": "DEFAULT", "default": "ACTIVE"}, "?") == "ACTIVE"
    assert apply_lookup({**LOOKUP, "unmatched": "NULL"}, "?") is None


def test_lookup_case_sensitive():
    content = {**LOOKUP, "caseSensitive": True}
    assert apply_lookup(content, "Active") == "ACTIVE"
    with pytest.raises(LookupMiss):
        apply_lookup(content, "active")


def test_lookup_duplicate_source_is_blocked():
    content = {**LOOKUP, "entries": [*LOOKUP["entries"], {"source": " ACTIVE", "target": "RETIRED"}]}
    errors = validate_content(K.LOOKUP, content, canonical=SCHEMA)
    assert fields(errors) == {"entries[2].source"}


def test_lookup_cross_checks_the_canonical_schema():
    assert fields(validate_content(K.LOOKUP, LOOKUP, canonical=None)) == {"target.schema"}
    not_enum = {**LOOKUP, "target": {**LOOKUP["target"], "field": "productId"}}
    assert fields(validate_content(K.LOOKUP, not_enum, canonical=SCHEMA)) == {"target.field"}
    wrong_value = {**LOOKUP, "entries": [{"source": "x", "target": "PENDING"}],
                   "unmatched": "DEFAULT", "default": "DELETED"}
    assert fields(validate_content(K.LOOKUP, wrong_value, canonical=SCHEMA)) == {"entries[0].target", "default"}


def test_lookup_default_rules():
    assert fields(validate_content(K.LOOKUP, {**LOOKUP, "unmatched": "DEFAULT"}, canonical=SCHEMA)) == {"default"}
    assert fields(validate_content(K.LOOKUP, {**LOOKUP, "default": "ACTIVE"}, canonical=SCHEMA)) == {"default"}


# ---- crosswalk ----

CROSSWALK = {
    "target": {"schema": "TEST-SCHEMA", "entity": "Product"},
    "entries": [
        {"sourceSystem": "SALESFORCE", "sourceKey": "01t001", "canonicalId": "PRD-1"},
        {"sourceSystem": "SAP", "sourceKey": "M-1", "canonicalId": "PRD-1"},  # many keys -> one entity: fine
    ],
}


def test_crosswalk_ok():
    assert validate_content(K.CROSSWALK, CROSSWALK, canonical=SCHEMA) == []


def test_crosswalk_one_key_one_entity():
    conflicting = {**CROSSWALK, "entries": [
        *CROSSWALK["entries"], {"sourceSystem": "SALESFORCE", "sourceKey": "01t001", "canonicalId": "PRD-2"}]}
    errors = validate_content(K.CROSSWALK, conflicting, canonical=SCHEMA)
    assert fields(errors) == {"entries[2].sourceKey"}
    assert "more than one entity" in errors[0].message


def test_crosswalk_rejects_padded_keys_and_unknown_entity():
    padded = {**CROSSWALK, "entries": [{"sourceSystem": "SAP", "sourceKey": " M-1", "canonicalId": "PRD-1"}]}
    assert fields(validate_content(K.CROSSWALK, padded, canonical=SCHEMA)) == {"entries[0].sourceKey"}
    unknown = {**CROSSWALK, "target": {"schema": "TEST-SCHEMA", "entity": "Offer"}}
    assert fields(validate_content(K.CROSSWALK, unknown, canonical=SCHEMA)) == {"target.entity"}


# ---- transform ----

def t(output, *steps):
    return {"input": "string", "output": output, "steps": list(steps)}


def test_transform_type_flow():
    assert validate_content(K.TRANSFORM, t("date", {"op": "trim"}, {"op": "parse_date", "format": "%d/%m/%Y"})) == []
    # declared output does not match what the steps produce
    assert fields(validate_content(K.TRANSFORM, t("integer", {"op": "trim"}))) == {"output"}
    # a string step after a date
    errors = validate_content(K.TRANSFORM, t("string", {"op": "parse_date", "format": "%Y"}, {"op": "upper"}))
    assert "steps[1].op" in fields(errors)


def test_transform_step_parameters():
    assert fields(validate_content(K.TRANSFORM, t("string", {"op": "replace", "find": "a"}))) == {"steps[0]"}
    assert fields(validate_content(K.TRANSFORM, t("string", {"op": "trim", "extra": 1}))) == {"steps[0]"}
    assert fields(validate_content(K.TRANSFORM, t("string", {"op": "exec", "code": "x"}))) == {"steps[0].op"}
    assert fields(validate_content(K.TRANSFORM, t("date", {"op": "parse_date", "format": "%Q-%d"}))) == {
        "steps[0].format"}
    bools = {"op": "to_bool", "true": ["Y", "yes"], "false": ["YES", "N"]}
    assert fields(validate_content(K.TRANSFORM, t("boolean", bools))) == {"steps[0]"}


def test_apply_transforms_is_deterministic():
    by_key = {key: content for key, _, content in TRANSFORMS}
    assert apply_transform(by_key["CODE-NORMALISE"], "  ab-12 ") == "AB-12"
    assert apply_transform(by_key["DATE-DMY"], "05/11/2026") == date(2026, 11, 5)
    assert apply_transform(by_key["DATE-ISO"], "") is None
    assert apply_transform(by_key["PRICE-DECIMAL"], "1,299.505") == Decimal("1299.51")
    assert apply_transform(by_key["YES-NO-BOOLEAN"], " yes ") is True
    assert apply_transform(by_key["YES-NO-BOOLEAN"], None) is None
    chain = t("datetime", {"op": "default", "value": "2026-01-01 00:00"},
              {"op": "parse_datetime", "format": "%Y-%m-%d %H:%M"})
    assert apply_transform(chain, None) == datetime(2026, 1, 1)
    euro = t("decimal", {"op": "to_decimal", "decimalSeparator": ",", "thousandsSeparator": "."})
    assert apply_transform(euro, "1.234,5") == Decimal("1234.5")
    pad = t("string", {"op": "split", "separator": "-", "index": 1}, {"op": "pad_left", "width": 5, "char": "0"})
    assert apply_transform(pad, "SKU-42") == "00042"


def test_apply_transform_names_the_failing_step():
    with pytest.raises(TransformError) as exc:
        apply_transform(t("date", {"op": "trim"}, {"op": "parse_date", "format": "%d/%m/%Y"}), "2026-13-01")
    assert exc.value.step == 1
    with pytest.raises(TransformError):
        apply_transform(t("integer", {"op": "to_integer"}), "1.5")
    with pytest.raises(TransformError):
        apply_transform(t("decimal", {"op": "to_decimal"}), "Infinity")
