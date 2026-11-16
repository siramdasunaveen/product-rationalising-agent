"""Content rules for each artifact kind: canonical schema, lookup, transform, crosswalk.

Two layers, both deterministic (no LLM):
1. Structure: a JSON schema per kind (shapes, enums, limits).
2. Meaning: duplicates, references between entities, key fields, one key -> one entity, type flow of
   transform steps, and cross-checks of lookups/crosswalks against the published canonical schema.

There is no user-authored code anywhere: transforms are chains of steps from a fixed list.
"""

import re
from collections.abc import Callable
from typing import Any

from jsonschema import Draft202012Validator

from app.domain.artifacts import KEY_RE, ArtifactKind
from app.domain.common import FieldError
from app.domain.transforms import TRANSFORM_SCHEMA_JSON, check_transform

# ---------------------------------------------------------------------------
# Shared definitions
# ---------------------------------------------------------------------------

FIELD_TYPES = ("string", "text", "integer", "decimal", "boolean", "date", "datetime", "enum", "reference")
CARDINALITIES = ("ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY")
DATA_CLASSIFICATION = "METADATA_AND_AGGREGATES"

ENTITY_NAME = {"type": "string", "pattern": r"^[A-Z][A-Za-z0-9]*$", "maxLength": 64}
FIELD_NAME = {"type": "string", "pattern": r"^[a-z][A-Za-z0-9]*$", "maxLength": 64}
ENUM_CODE = {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64}
ARTIFACT_KEY = {"type": "string", "pattern": KEY_RE.pattern, "minLength": 3, "maxLength": 64}
TRIMMED = r"^\S(?:.*\S)?$"  # non-empty, no leading/trailing whitespace
LABEL = {"type": "string", "maxLength": 120}
DESCRIPTION = {"type": "string", "maxLength": 2000}

FIELD_SCHEMA = {
    "type": "object",
    "required": ["name", "type"],
    "additionalProperties": False,
    "properties": {
        "name": FIELD_NAME,
        "label": LABEL,
        "description": DESCRIPTION,
        "type": {"enum": list(FIELD_TYPES)},
        "required": {"type": "boolean"},
        "values": {"type": "array", "items": ENUM_CODE, "minItems": 1, "maxItems": 500, "uniqueItems": True},
        "references": ENTITY_NAME,
        "pattern": {"type": "string", "minLength": 1, "maxLength": 200},
        "maxLength": {"type": "integer", "minimum": 1, "maximum": 100000},
        "minimum": {"type": "integer"},
        "pii": {"type": "boolean"},
    },
}

# ---------------------------------------------------------------------------
# Canonical schema
# ---------------------------------------------------------------------------

CANONICAL_SCHEMA_JSON = {
    "type": "object",
    "required": ["entities"],
    "additionalProperties": False,
    "properties": {
        "description": DESCRIPTION,
        "dataClassification": {"const": DATA_CLASSIFICATION},
        "entities": {
            "type": "array", "minItems": 1, "maxItems": 200,
            "items": {
                "type": "object",
                "required": ["name", "keyFields", "fields"],
                "additionalProperties": False,
                "properties": {
                    "name": ENTITY_NAME,
                    "label": LABEL,
                    "description": DESCRIPTION,
                    "keyFields": {"type": "array", "items": FIELD_NAME, "minItems": 1, "maxItems": 10},
                    "fields": {"type": "array", "items": FIELD_SCHEMA, "minItems": 1, "maxItems": 300},
                },
            },
        },
        "relationships": {
            "type": "array", "maxItems": 200,
            "items": {
                "type": "object",
                "required": ["name", "from", "to", "cardinality"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64},
                    "label": LABEL,
                    "description": DESCRIPTION,
                    "from": {"oneOf": [ENTITY_NAME, {"type": "array", "items": ENTITY_NAME, "minItems": 1,
                                                     "uniqueItems": True}]},
                    "to": ENTITY_NAME,
                    "cardinality": {"enum": list(CARDINALITIES)},
                    "acyclic": {"type": "boolean"},
                    "attributes": {"type": "array", "items": FIELD_SCHEMA, "maxItems": 50},
                },
            },
        },
    },
}

PII_MESSAGE = "Subscriber or personal data is not allowed (metadata and aggregates only)"


def _check_field(f: dict, path: str, entity_names: set[str]) -> list[FieldError]:
    errors: list[FieldError] = []
    ftype = f["type"]
    if ftype == "enum" and "values" not in f:
        errors.append(FieldError(f"{path}.values", "An enum field needs its allowed values"))
    if ftype != "enum" and "values" in f:
        errors.append(FieldError(f"{path}.values", "Only enum fields have values"))
    if ftype == "reference":
        if "references" not in f:
            errors.append(FieldError(f"{path}.references", "A reference field must name the entity it points to"))
        elif f["references"] not in entity_names:
            errors.append(FieldError(f"{path}.references", f"Unknown entity {f['references']}"))
    elif "references" in f:
        errors.append(FieldError(f"{path}.references", "Only reference fields point to an entity"))
    if ("pattern" in f or "maxLength" in f) and ftype not in ("string", "text"):
        errors.append(FieldError(path, "pattern and maxLength apply to string and text fields only"))
    if "pattern" in f:
        try:
            re.compile(f["pattern"])
        except re.error as exc:
            errors.append(FieldError(f"{path}.pattern", f"Not a valid pattern: {exc}"))
    if "minimum" in f and ftype not in ("integer", "decimal"):
        errors.append(FieldError(f"{path}.minimum", "minimum applies to integer and decimal fields only"))
    if f.get("pii"):
        errors.append(FieldError(f"{path}.pii", PII_MESSAGE))
    return errors


def _duplicates(names: list[str]) -> set[str]:
    seen: set[str] = set()
    return {n for n in names if n in seen or seen.add(n)}  # type: ignore[func-returns-value]


def check_canonical_schema(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    entities = content["entities"]
    names = [e["name"] for e in entities]
    entity_names = set(names)
    dupes = _duplicates(names)
    for i, e in enumerate(entities):
        ep = f"entities[{i}]"
        if e["name"] in dupes:
            errors.append(FieldError(f"{ep}.name", f"Entity {e['name']} is defined more than once"))
        fields = {f["name"]: f for f in e["fields"]}
        fdupes = _duplicates([f["name"] for f in e["fields"]])
        for j, f in enumerate(e["fields"]):
            fp = f"{ep}.fields[{j}]"
            if f["name"] in fdupes:
                errors.append(FieldError(f"{fp}.name", f"Field {f['name']} is defined more than once"))
            errors += _check_field(f, fp, entity_names)
        if len(set(e["keyFields"])) != len(e["keyFields"]):
            errors.append(FieldError(f"{ep}.keyFields", "Key fields must be unique"))
        for k, key in enumerate(e["keyFields"]):
            f = fields.get(key)
            if f is None:
                errors.append(FieldError(f"{ep}.keyFields[{k}]", f"Key field {key} is not a field of {e['name']}"))
            elif not f.get("required"):
                errors.append(FieldError(f"{ep}.keyFields[{k}]", f"Key field {key} must be required"))

    rels = content.get("relationships", [])
    rdupes = _duplicates([r["name"] for r in rels])
    for i, r in enumerate(rels):
        rp = f"relationships[{i}]"
        if r["name"] in rdupes:
            errors.append(FieldError(f"{rp}.name", f"Relationship {r['name']} is defined more than once"))
        sources = [r["from"]] if isinstance(r["from"], str) else r["from"]
        for s in sources:
            if s not in entity_names:
                errors.append(FieldError(f"{rp}.from", f"Unknown entity {s}"))
        if r["to"] not in entity_names:
            errors.append(FieldError(f"{rp}.to", f"Unknown entity {r['to']}"))
        attrs = r.get("attributes", [])
        adupes = _duplicates([a["name"] for a in attrs])
        for j, a in enumerate(attrs):
            ap = f"{rp}.attributes[{j}]"
            if a["name"] in adupes:
                errors.append(FieldError(f"{ap}.name", f"Attribute {a['name']} is defined more than once"))
            errors += _check_field(a, ap, entity_names)
    return errors


def find_field(canonical: dict, entity: str, field: str) -> dict | None:
    for e in canonical.get("entities", []):
        if e["name"] == entity:
            return next((f for f in e["fields"] if f["name"] == field), None)
    return None


def has_entity(canonical: dict, entity: str) -> bool:
    return any(e["name"] == entity for e in canonical.get("entities", []))


# ---------------------------------------------------------------------------
# Lookup: source code -> canonical enum value
# ---------------------------------------------------------------------------

LOOKUP_SCHEMA_JSON = {
    "type": "object",
    "required": ["target", "entries"],
    "additionalProperties": False,
    "properties": {
        "description": DESCRIPTION,
        "target": {
            "type": "object",
            "required": ["schema", "entity", "field"],
            "additionalProperties": False,
            "properties": {"schema": ARTIFACT_KEY, "entity": ENTITY_NAME, "field": FIELD_NAME},
        },
        "caseSensitive": {"type": "boolean"},
        "trimSource": {"type": "boolean"},
        "unmatched": {"enum": ["ERROR", "NULL", "DEFAULT"]},
        "default": ENUM_CODE,
        "entries": {
            "type": "array", "minItems": 1, "maxItems": 5000,
            "items": {
                "type": "object",
                "required": ["source", "target"],
                "additionalProperties": False,
                "properties": {
                    "source": {"type": "string", "minLength": 1, "maxLength": 200},
                    "target": ENUM_CODE,
                    "note": {"type": "string", "maxLength": 500},
                },
            },
        },
    },
}


def _lookup_key(content: dict, source: str) -> str:
    if content.get("trimSource", True):
        source = source.strip()
    if not content.get("caseSensitive", False):
        source = source.casefold()
    return source


def check_lookup(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    unmatched = content.get("unmatched", "ERROR")
    if unmatched == "DEFAULT" and "default" not in content:
        errors.append(FieldError("default", "unmatched = DEFAULT needs a default value"))
    if unmatched != "DEFAULT" and "default" in content:
        errors.append(FieldError("default", "A default value is only used when unmatched = DEFAULT"))
    seen: dict[str, int] = {}
    for i, entry in enumerate(content["entries"]):
        key = _lookup_key(content, entry["source"])
        if not key:
            errors.append(FieldError(f"entries[{i}].source", "Source value is empty"))
        elif key in seen:
            errors.append(FieldError(f"entries[{i}].source",
                                     f"Source value {entry['source']!r} is already mapped in entries[{seen[key]}]"))
        else:
            seen[key] = i
    return errors


def check_lookup_against(content: dict, canonical: dict) -> list[FieldError]:
    t = content["target"]
    if not has_entity(canonical, t["entity"]):
        return [FieldError("target.entity", f"Entity {t['entity']} is not in {t['schema']}")]
    field = find_field(canonical, t["entity"], t["field"])
    if field is None:
        return [FieldError("target.field", f"Field {t['field']} is not in {t['entity']}")]
    if field["type"] != "enum":
        return [FieldError("target.field", f"{t['entity']}.{t['field']} is not an enum field")]
    allowed = set(field["values"])
    errors = [FieldError(f"entries[{i}].target", f"{e['target']} is not an allowed value of {t['field']}")
              for i, e in enumerate(content["entries"]) if e["target"] not in allowed]
    if "default" in content and content["default"] not in allowed:
        errors.append(FieldError("default", f"{content['default']} is not an allowed value of {t['field']}"))
    return errors


class LookupMiss(ValueError):
    pass


def apply_lookup(content: dict, raw: str | None) -> str | None:
    """Map one source value. Deterministic; used by mapping execution (Step 5)."""
    if raw is None:
        return None
    key = _lookup_key(content, raw)
    for e in content["entries"]:
        if _lookup_key(content, e["source"]) == key:
            return e["target"]
    unmatched = content.get("unmatched", "ERROR")
    if unmatched == "DEFAULT":
        return content["default"]
    if unmatched == "NULL":
        return None
    raise LookupMiss(f"No lookup entry for {raw!r}")


# ---------------------------------------------------------------------------
# Crosswalk: (source system, source key) -> one canonical entity id
# ---------------------------------------------------------------------------

CROSSWALK_SCHEMA_JSON = {
    "type": "object",
    "required": ["target", "entries"],
    "additionalProperties": False,
    "properties": {
        "description": DESCRIPTION,
        "target": {
            "type": "object",
            "required": ["schema", "entity"],
            "additionalProperties": False,
            "properties": {"schema": ARTIFACT_KEY, "entity": ENTITY_NAME},
        },
        "entries": {
            "type": "array", "minItems": 1, "maxItems": 50000,
            "items": {
                "type": "object",
                "required": ["sourceSystem", "sourceKey", "canonicalId"],
                "additionalProperties": False,
                "properties": {
                    "sourceSystem": {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64},
                    "sourceKey": {"type": "string", "pattern": TRIMMED, "maxLength": 200},
                    "canonicalId": {"type": "string", "pattern": TRIMMED, "maxLength": 200},
                },
            },
        },
    },
}


def check_crosswalk(content: dict) -> list[FieldError]:
    """Crosswalk integrity: one source key maps to exactly one canonical entity.

    Several source keys may map to the same entity (that is the point of a crosswalk).
    """
    errors: list[FieldError] = []
    seen: dict[tuple[str, str], int] = {}
    for i, e in enumerate(content["entries"]):
        k = (e["sourceSystem"], e["sourceKey"])
        if k in seen:
            first = content["entries"][seen[k]]
            if first["canonicalId"] != e["canonicalId"]:
                msg = (f"{e['sourceSystem']} key {e['sourceKey']!r} maps to more than one entity "
                       f"({first['canonicalId']} in entries[{seen[k]}] and {e['canonicalId']})")
            else:
                msg = f"Duplicate of entries[{seen[k]}]"
            errors.append(FieldError(f"entries[{i}].sourceKey", msg))
        else:
            seen[k] = i
    return errors


def check_crosswalk_against(content: dict, canonical: dict) -> list[FieldError]:
    t = content["target"]
    if not has_entity(canonical, t["entity"]):
        return [FieldError("target.entity", f"Entity {t['entity']} is not in {t['schema']}")]
    return []


# ---------------------------------------------------------------------------
# Evidence template: governed Excel columns of an evidence domain -> canonical fields
# ---------------------------------------------------------------------------

EVIDENCE_TEMPLATE_JSON = {
    "type": "object",
    "required": ["domain", "canonicalSchema", "columns"],
    "additionalProperties": False,
    "properties": {
        "description": DESCRIPTION,
        "domain": {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64},
        "canonicalSchema": ARTIFACT_KEY,
        "sheetName": {"type": "string", "minLength": 1, "maxLength": 31},
        "columns": {
            "type": "array", "minItems": 1, "maxItems": 200,
            "items": {
                "type": "object",
                "required": ["header"],
                "additionalProperties": False,
                "properties": {
                    "header": {"type": "string", "pattern": TRIMMED, "maxLength": 100},
                    "required": {"type": "boolean"},
                    # At least one column of the same group must be present (e.g. Product ID or Service Spec ID).
                    "oneOfGroup": {"type": "string", "pattern": r"^[a-z][a-zA-Z0-9]*$", "maxLength": 40},
                    "target": {
                        "type": "object",
                        "required": ["entity", "field"],
                        "additionalProperties": False,
                        "properties": {"entity": ENTITY_NAME, "field": FIELD_NAME},
                    },
                    "lookup": ARTIFACT_KEY,
                    "description": {"type": "string", "maxLength": 500},
                    "example": {"type": "string", "maxLength": 100},
                },
            },
        },
        # Graph edges the template's rows carry, e.g. Product ID -> Service Spec ID (DECOMPOSES_TO).
        "relationships": {
            "type": "array", "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["fromEntity", "fromColumn", "toEntity", "toColumn"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64},
                    "nameColumn": {"type": "string", "maxLength": 100},
                    "fromEntity": ENTITY_NAME, "fromColumn": {"type": "string", "maxLength": 100},
                    "toEntity": ENTITY_NAME, "toColumn": {"type": "string", "maxLength": 100},
                },
            },
        },
    },
}


def check_evidence_template(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    seen: dict[str, int] = {}
    groups: dict[str, list[int]] = {}
    for i, col in enumerate(content["columns"]):
        key = " ".join(col["header"].split()).casefold()
        if key in seen:
            errors.append(FieldError(f"columns[{i}].header", f"Header {col['header']!r} is already columns[{seen[key]}]"))
        else:
            seen[key] = i
        if "oneOfGroup" in col:
            if col.get("required"):
                errors.append(FieldError(f"columns[{i}]", "A column in a one-of group cannot also be required"))
            groups.setdefault(col["oneOfGroup"], []).append(i)
    for name, members in groups.items():
        if len(members) < 2:
            errors.append(FieldError(f"columns[{members[0]}].oneOfGroup", f"Group {name} needs at least two columns"))
    for i, rel in enumerate(content.get("relationships", [])):
        if bool(rel.get("name")) == bool(rel.get("nameColumn")):
            errors.append(FieldError(f"relationships[{i}]", "Give either a fixed name or a name column"))
        for col in (rel["fromColumn"], rel["toColumn"], rel.get("nameColumn")):
            if col and " ".join(col.split()).casefold() not in seen:
                errors.append(FieldError(f"relationships[{i}]", f"Column {col!r} is not a template column"))
    return errors


def check_evidence_template_against(content: dict, canonical: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    rel_names = {r["name"] for r in canonical.get("relationships", [])}
    for i, col in enumerate(content["columns"]):
        t = col.get("target")
        if t is not None:
            if not has_entity(canonical, t["entity"]):
                errors.append(FieldError(f"columns[{i}].target.entity", f"Entity {t['entity']} is not in the schema"))
            elif find_field(canonical, t["entity"], t["field"]) is None:
                errors.append(FieldError(f"columns[{i}].target.field", f"Field {t['field']} is not in {t['entity']}"))
    for i, rel in enumerate(content.get("relationships", [])):
        for side in ("fromEntity", "toEntity"):
            if not has_entity(canonical, rel[side]):
                errors.append(FieldError(f"relationships[{i}].{side}", f"Entity {rel[side]} is not in the schema"))
        if rel.get("name") and rel["name"] not in rel_names:
            errors.append(FieldError(f"relationships[{i}].name", f"Relationship {rel['name']} is not in the schema"))
    return errors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_SCHEMAS: dict[ArtifactKind, dict] = {
    ArtifactKind.CANONICAL_SCHEMA: CANONICAL_SCHEMA_JSON,
    ArtifactKind.LOOKUP: LOOKUP_SCHEMA_JSON,
    ArtifactKind.TRANSFORM: TRANSFORM_SCHEMA_JSON,
    ArtifactKind.CROSSWALK: CROSSWALK_SCHEMA_JSON,
    ArtifactKind.EVIDENCE_TEMPLATE: EVIDENCE_TEMPLATE_JSON,
}
_VALIDATORS = {k: Draft202012Validator(v) for k, v in _SCHEMAS.items()}
_CHECKS: dict[ArtifactKind, Callable[[dict], list[FieldError]]] = {
    ArtifactKind.CANONICAL_SCHEMA: check_canonical_schema,
    ArtifactKind.LOOKUP: check_lookup,
    ArtifactKind.TRANSFORM: check_transform,
    ArtifactKind.CROSSWALK: check_crosswalk,
    ArtifactKind.EVIDENCE_TEMPLATE: check_evidence_template,
}
_CROSS_CHECKS: dict[ArtifactKind, Callable[[dict, dict], list[FieldError]]] = {
    ArtifactKind.LOOKUP: check_lookup_against,
    ArtifactKind.CROSSWALK: check_crosswalk_against,
    ArtifactKind.EVIDENCE_TEMPLATE: check_evidence_template_against,
}

MAX_STRUCTURE_ERRORS = 50


def json_schema_for(kind: ArtifactKind | str) -> dict:
    return _SCHEMAS[ArtifactKind(kind)]


def _path(parts) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "content"


def check_structure(kind: ArtifactKind | str, content: Any) -> list[FieldError]:
    errors = sorted(_VALIDATORS[ArtifactKind(kind)].iter_errors(content), key=lambda e: list(e.absolute_path))
    return [FieldError(_path(e.absolute_path), e.message) for e in errors[:MAX_STRUCTURE_ERRORS]]


def canonical_schema_key(kind: ArtifactKind | str, content: Any) -> str | None:
    """The canonical schema a lookup or crosswalk points to (None for other kinds or bad content)."""
    if ArtifactKind(kind) not in _CROSS_CHECKS or not isinstance(content, dict):
        return None
    if ArtifactKind(kind) is ArtifactKind.EVIDENCE_TEMPLATE:
        key = content.get("canonicalSchema")
        return key if isinstance(key, str) else None
    target = content.get("target")
    return target.get("schema") if isinstance(target, dict) else None


def validate_content(kind: ArtifactKind | str, content: Any, *,
                     canonical: dict | None = None) -> list[FieldError]:
    """All blocking problems with ``content``. Empty list = publishable.

    For lookups and crosswalks pass the content of the published canonical schema they target
    (``canonical``); if it is None the target schema is reported as missing.
    """
    kind = ArtifactKind(kind)
    errors = check_structure(kind, content)
    if errors:
        return errors
    errors = _CHECKS[kind](content)
    if kind in _CROSS_CHECKS:
        if canonical is None:
            key = canonical_schema_key(kind, content)
            path = "canonicalSchema" if kind is ArtifactKind.EVIDENCE_TEMPLATE else "target.schema"
            errors.append(FieldError(path, f"No published canonical schema {key}"))
        else:
            errors += _CROSS_CHECKS[kind](content, canonical)
    return errors
