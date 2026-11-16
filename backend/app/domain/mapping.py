"""Mapping Rules engine (BUILD_PLAN step 5). Pure and deterministic: no I/O, no AI.

A mapping version is an ordered list of rules from source columns to canonical fields (or relationships).
``check_rules`` finds problems that need no data (structure, schema, types, refs, coverage, security);
``execute`` runs the rules over rows and returns staged records, relationship edges, field lineage and
per-row rejections; ``validate`` combines both into the validation result shown to the user.

Rule types: DIRECT, LOOKUP (code -> canonical enum), TRANSFORM (fixed step chain), CROSSWALK (source key ->
canonical id), RELATIONSHIP (graph edge), DERIVED (CONCAT / COALESCE / CONSTANT over source columns).
Mapping never chooses between sources; that is reconciliation (step 6).
"""

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.domain.artifact_content import LookupMiss, apply_lookup, find_field
from app.domain.artifacts import canonical_json
from app.domain.excel_discovery import as_text, looks_personal
from app.domain.transforms import TransformError, apply_transform

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class MappingStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"  # the one version usable by new applies
    DEPRECATED = "DEPRECATED"
    SUPERSEDED = "SUPERSEDED"  # pinned runs only
    RETIRED = "RETIRED"


M = MappingStatus
# Rules can change only in these statuses; any change returns the version to DRAFT.
EDITABLE = frozenset({M.DRAFT, M.VALIDATION_FAILED, M.READY_FOR_REVIEW})

# Keep in sync with the trigger in migration 0006 (a test checks both).
TRANSITIONS: dict[MappingStatus, frozenset[MappingStatus]] = {
    M.DRAFT: frozenset({M.VALIDATING}),
    M.VALIDATING: frozenset({M.VALIDATION_FAILED, M.READY_FOR_REVIEW, M.DRAFT}),
    M.VALIDATION_FAILED: frozenset({M.DRAFT, M.VALIDATING}),
    M.READY_FOR_REVIEW: frozenset({M.DRAFT, M.VALIDATING, M.IN_REVIEW}),
    M.IN_REVIEW: frozenset({M.APPROVED, M.DRAFT, M.RETIRED}),  # approve / return / reject
    M.APPROVED: frozenset({M.PUBLISHED, M.RETIRED}),
    M.PUBLISHED: frozenset({M.DEPRECATED, M.SUPERSEDED, M.RETIRED}),
    M.DEPRECATED: frozenset({M.SUPERSEDED, M.RETIRED}),
    M.SUPERSEDED: frozenset({M.RETIRED}),
    M.RETIRED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return MappingStatus(target) in TRANSITIONS[MappingStatus(current)]


class MappingType(StrEnum):
    DIRECT = "DIRECT"
    LOOKUP = "LOOKUP"
    TRANSFORM = "TRANSFORM"
    CROSSWALK = "CROSSWALK"
    RELATIONSHIP = "RELATIONSHIP"
    DERIVED = "DERIVED"


class NullHandling(StrEnum):
    KEEP_NULL = "KEEP_NULL"  # leave the field empty
    DEFAULT = "DEFAULT"  # use default_value
    REJECT_ROW = "REJECT_ROW"  # the row is rejected


class ProposedBy(StrEnum):
    TEMPLATE = "TEMPLATE"
    PRIOR_MAPPING = "PRIOR_MAPPING"
    NAME_MATCH = "NAME_MATCH"
    ALIAS = "ALIAS"
    AI = "AI"
    USER = "USER"


DERIVED_OPS = ("CONCAT", "COALESCE", "CONSTANT")
REF_RE = re.compile(r"^[A-Z][A-Z0-9_-]*@\d+\.\d+$")


@dataclass(frozen=True)
class Rule:
    key: str  # stable id within the version, e.g. "R3"
    mapping_type: MappingType
    source_column: str | None = None
    target_entity: str | None = None
    target_field: str | None = None
    lookup_ref: str | None = None
    transform_ref: str | None = None
    crosswalk_ref: str | None = None
    source_system: str | None = None  # crosswalk: which system the source key belongs to
    derived: dict | None = None  # {"op": CONCAT|COALESCE|CONSTANT, "inputs": [...], "separator": "", "value": ""}
    # RELATIONSHIP: {"name" | "nameColumn", "fromEntity", "fromColumn", "toEntity", "toColumn"}
    relationship: dict | None = None
    required: bool = False
    null_handling: NullHandling = NullHandling.KEEP_NULL
    default_value: str | None = None
    confidence: int | None = None  # suggestion confidence 0..100 (not a business score)
    proposed_by: ProposedBy = ProposedBy.USER

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mapping_type"] = self.mapping_type.value
        d["null_handling"] = self.null_handling.value
        d["proposed_by"] = self.proposed_by.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Rule":
        return cls(**{**d, "mapping_type": MappingType(d["mapping_type"]),
                      "null_handling": NullHandling(d.get("null_handling") or "KEEP_NULL"),
                      "proposed_by": ProposedBy(d.get("proposed_by") or "USER")})

    @property
    def refs(self) -> list[str]:
        return [r for r in (self.lookup_ref, self.transform_ref, self.crosswalk_ref) if r]

    def columns_used(self) -> list[str]:
        cols: list[str] = []
        if self.source_column and self.mapping_type is not MappingType.DERIVED:
            cols.append(self.source_column)
        if self.derived and self.derived.get("op") in ("CONCAT", "COALESCE"):
            cols += list(self.derived.get("inputs") or [])
        if self.relationship:
            cols += [c for c in (self.relationship.get("fromColumn"), self.relationship.get("toColumn"),
                                 self.relationship.get("nameColumn")) if c]
        return list(dict.fromkeys(cols))


def content_hash(source_fingerprint: str, canonical_ref: str, canonical_digest: str, rules: list[Rule]) -> str:
    """Identity of what reviewers approve: source structure, pinned schema and every rule (except who/
    how-confident suggested it, which is metadata)."""
    body = {
        "sourceSchema": source_fingerprint,
        "canonical": {"ref": canonical_ref, "digest": canonical_digest},
        "rules": [{k: v for k, v in r.to_dict().items() if k not in ("confidence", "proposed_by")}
                  for r in sorted(rules, key=lambda r: r.key)],
    }
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


class Layer(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    TYPE = "TYPE"
    SCHEMA = "SCHEMA"
    COVERAGE = "COVERAGE"
    LOOKUP = "LOOKUP"
    CROSSWALK = "CROSSWALK"
    RELATIONSHIP = "RELATIONSHIP"
    TRANSFORM = "TRANSFORM"
    SAMPLE = "SAMPLE"
    SECURITY = "SECURITY"


class Severity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


@dataclass
class Issue:
    layer: Layer
    severity: Severity
    code: str
    message: str
    rule_key: str | None = None
    count: int = 0
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"layer": self.layer.value, "severity": self.severity.value, "code": self.code,
                "message": self.message, "rule_key": self.rule_key, "count": self.count,
                "examples": self.examples}


B, W = Severity.BLOCKING, Severity.WARNING


# ---------------------------------------------------------------------------
# Type compatibility
# ---------------------------------------------------------------------------

# discovered source column type -> canonical field types a DIRECT rule may target
DIRECT_COMPAT: dict[str, set[str]] = {
    "string": {"string", "text", "reference", "enum", "integer", "decimal", "boolean", "date", "datetime"},
    "integer": {"integer", "decimal", "string", "text", "reference", "boolean"},
    "decimal": {"decimal", "string", "text"},
    "boolean": {"boolean", "string", "text"},
    "date": {"date", "datetime", "string", "text"},
    "datetime": {"datetime", "string", "text"},
    "empty": {"string", "text", "reference", "enum", "integer", "decimal", "boolean", "date", "datetime"},
}
# transform output type -> canonical field types it may feed
TRANSFORM_COMPAT: dict[str, set[str]] = {
    "string": {"string", "text", "reference", "enum"},
    "integer": {"integer", "decimal"},
    "decimal": {"decimal"},
    "boolean": {"boolean"},
    "date": {"date", "datetime"},
    "datetime": {"datetime"},
}
BOOL_TRUE = {"true", "y", "yes", "1"}
BOOL_FALSE = {"false", "n", "no", "0"}


class MappingValueError(ValueError):  # noqa: N818 - a value error with a reason code
    """A value that cannot be mapped; ``code`` names the reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def coerce(raw: Any, fdef: dict) -> Any:
    """Convert a cell (or transform output) to the canonical field type. None stays None."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    t = fdef["type"]
    if t in ("string", "text", "reference"):
        v = raw.strip() if isinstance(raw, str) else as_text(raw)
        if "maxLength" in fdef and len(v) > fdef["maxLength"]:
            raise MappingValueError("TOO_LONG", f"longer than {fdef['maxLength']} characters")
        if "pattern" in fdef and not re.fullmatch(fdef["pattern"], v):
            raise MappingValueError("PATTERN", f"{v!r} does not match {fdef['pattern']}")
        return v
    if t == "enum":
        v = raw.strip() if isinstance(raw, str) else as_text(raw)
        if v not in fdef["values"]:
            raise MappingValueError("NOT_ALLOWED", f"{v!r} is not an allowed value (use a lookup)")
        return v
    if t == "integer":
        if isinstance(raw, bool):
            raise MappingValueError("TYPE_MISMATCH", "true/false is not a whole number")
        if isinstance(raw, int):
            v = raw
        elif isinstance(raw, (float, Decimal)) and float(raw).is_integer():
            v = int(raw)
        elif isinstance(raw, str) and re.fullmatch(r"\s*[+-]?\d+\s*", raw):
            v = int(raw)
        else:
            raise MappingValueError("TYPE_MISMATCH", f"{as_text(raw)!r} is not a whole number")
        if "minimum" in fdef and v < fdef["minimum"]:
            raise MappingValueError("BELOW_MINIMUM", f"{v} is below {fdef['minimum']}")
        return v
    if t == "decimal":
        if isinstance(raw, bool):
            raise MappingValueError("TYPE_MISMATCH", "true/false is not a number")
        try:
            v = raw if isinstance(raw, Decimal) else Decimal(str(raw).strip())
        except InvalidOperation as exc:
            raise MappingValueError("TYPE_MISMATCH", f"{as_text(raw)!r} is not a number") from exc
        if not v.is_finite():
            raise MappingValueError("TYPE_MISMATCH", f"{as_text(raw)!r} is not a finite number")
        if "minimum" in fdef and v < fdef["minimum"]:
            raise MappingValueError("BELOW_MINIMUM", f"{v} is below {fdef['minimum']}")
        return v
    if t == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().casefold()
        if text in BOOL_TRUE:
            return True
        if text in BOOL_FALSE:
            return False
        raise MappingValueError("TYPE_MISMATCH", f"{as_text(raw)!r} is not true/false (use a transform)")
    if t == "date":
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw).strip())
        except ValueError as exc:
            raise MappingValueError("TYPE_MISMATCH", f"{as_text(raw)!r} is not a date yyyy-mm-dd (use a transform)") from exc
    if t == "datetime":
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, date):
            return datetime(raw.year, raw.month, raw.day)
        try:
            return datetime.fromisoformat(str(raw).strip())
        except ValueError as exc:
            raise MappingValueError("TYPE_MISMATCH", f"{as_text(raw)!r} is not a date-time (use a transform)") from exc
    raise MappingValueError("TYPE_MISMATCH", f"unsupported field type {t}")


def to_json(v: Any) -> Any:
    """Canonical JSON form of a mapped value (decimals as strings so they stay exact)."""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else format(v, "f")
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


# ---------------------------------------------------------------------------
# Context: the pinned canonical schema and referenced artifacts
# ---------------------------------------------------------------------------


@dataclass
class RefInfo:
    kind: str  # LOOKUP | TRANSFORM | CROSSWALK
    status: str
    selectable: bool
    content: dict


@dataclass
class Context:
    canonical: dict
    refs: dict[str, RefInfo | None]  # ref -> info, or None when it does not resolve
    columns: dict[str, str]  # source column header -> discovered type

    def entity(self, name: str) -> dict | None:
        return next((e for e in self.canonical.get("entities", []) if e["name"] == name), None)

    def field(self, entity: str, name: str) -> dict | None:
        return find_field(self.canonical, entity, name)

    def relationship(self, name: str) -> dict | None:
        return next((r for r in self.canonical.get("relationships", []) if r["name"] == name), None)


def _rel_endpoints(rel: dict) -> tuple[list[str], str]:
    froms = [rel["from"]] if isinstance(rel["from"], str) else list(rel["from"])
    return froms, rel["to"]


def _single_key(ctx: Context, entity: str) -> str | None:
    e = ctx.entity(entity)
    return e["keyFields"][0] if e and len(e["keyFields"]) == 1 else None


# ---------------------------------------------------------------------------
# Static checks (no data)
# ---------------------------------------------------------------------------


def check_rules(rules: list[Rule], ctx: Context) -> list[Issue]:
    issues: list[Issue] = []
    if not rules:
        return [Issue(Layer.STRUCTURAL, B, "NO_RULES", "Map at least one column")]
    keys = Counter(r.key for r in rules)
    for k, n in keys.items():
        if n > 1:
            issues.append(Issue(Layer.STRUCTURAL, B, "DUPLICATE_RULE_KEY", f"Rule id {k} is used {n} times", k))

    targets: dict[tuple[str, str], str] = {}
    mapped_entities: set[str] = set()
    for r in rules:
        def add(layer, sev, code, msg, rule=r):
            issues.append(Issue(layer, sev, code, msg, rule.key))

        for col in r.columns_used():
            if col not in ctx.columns:
                add(Layer.STRUCTURAL, B, "UNKNOWN_COLUMN", f"Column '{col}' is not in the source schema")
            elif looks_personal(col):
                add(Layer.SECURITY, B, "PERSONAL_DATA", f"Column '{col}' looks like personal data and cannot be mapped")

        # parameters per type
        need = {MappingType.LOOKUP: "lookup_ref", MappingType.TRANSFORM: "transform_ref",
                MappingType.CROSSWALK: "crosswalk_ref"}
        if r.mapping_type in need and not getattr(r, need[r.mapping_type]):
            add(Layer.STRUCTURAL, B, "MISSING_PARAM", f"A {r.mapping_type} rule needs {need[r.mapping_type]}")
        if r.mapping_type is MappingType.CROSSWALK and not r.source_system:
            add(Layer.STRUCTURAL, B, "MISSING_PARAM", "A CROSSWALK rule needs source_system")
        if r.mapping_type is MappingType.DERIVED:
            d = r.derived or {}
            if d.get("op") not in DERIVED_OPS:
                add(Layer.STRUCTURAL, B, "MISSING_PARAM", "A DERIVED rule needs op CONCAT, COALESCE or CONSTANT")
            elif d["op"] == "CONSTANT" and d.get("value") in (None, ""):
                add(Layer.STRUCTURAL, B, "MISSING_PARAM", "A CONSTANT rule needs a value")
            elif d["op"] != "CONSTANT" and len(d.get("inputs") or []) < 1:
                add(Layer.STRUCTURAL, B, "MISSING_PARAM", f"{d['op']} needs at least one input column")
        elif r.mapping_type not in (MappingType.RELATIONSHIP,) and not r.source_column:
            add(Layer.STRUCTURAL, B, "MISSING_PARAM", "Choose a source column")
        if r.null_handling is NullHandling.DEFAULT and r.default_value in (None, ""):
            add(Layer.STRUCTURAL, B, "MISSING_PARAM", "null handling DEFAULT needs a default value")

        for ref in r.refs:
            if not REF_RE.match(ref):
                add(Layer.STRUCTURAL, B, "BAD_REF", f"{ref} is not an exact KEY@major.minor reference")
                continue
            info = ctx.refs.get(ref)
            expected = {r.lookup_ref: "LOOKUP", r.transform_ref: "TRANSFORM", r.crosswalk_ref: "CROSSWALK"}[ref]
            if info is None:
                add(Layer.SCHEMA, B, "UNKNOWN_REF", f"{ref} does not exist or is a draft")
            elif info.kind != expected:
                add(Layer.SCHEMA, B, "REF_KIND", f"{ref} is a {info.kind}, not a {expected}")
            elif info.status == "RETIRED":
                add(Layer.SCHEMA, B, "REF_RETIRED", f"{ref} is retired")
            elif not info.selectable:
                add(Layer.SCHEMA, W, "REF_NOT_CURRENT", f"{ref} is {info.status}; a newer version may exist")

        if r.mapping_type is MappingType.RELATIONSHIP:
            issues += _check_relationship_rule(r, ctx)
            continue

        # field targets
        if not r.target_entity or not r.target_field:
            add(Layer.STRUCTURAL, B, "MISSING_TARGET", "Choose a target field")
            continue
        fdef = ctx.field(r.target_entity, r.target_field)
        if fdef is None:
            add(Layer.SCHEMA, B, "UNKNOWN_TARGET", f"{r.target_entity}.{r.target_field} is not in the canonical schema")
            continue
        if fdef.get("pii"):
            add(Layer.SECURITY, B, "PERSONAL_DATA", f"{r.target_entity}.{r.target_field} is personal data")
        tkey = (r.target_entity, r.target_field)
        if tkey in targets:
            add(Layer.STRUCTURAL, B, "DUPLICATE_TARGET",
                f"{r.target_entity}.{r.target_field} is already mapped by rule {targets[tkey]}")
        else:
            targets[tkey] = r.key
        mapped_entities.add(r.target_entity)
        issues += _check_types(r, fdef, ctx)

    # coverage: every mapped entity has its key fields and required fields mapped
    for entity in sorted(mapped_entities):
        e = ctx.entity(entity)
        for kf in e["keyFields"]:
            if (entity, kf) not in targets:
                issues.append(Issue(Layer.COVERAGE, B, "KEY_NOT_MAPPED",
                                    f"{entity} records need their key field {kf}; map it or remove {entity} rules"))
        for f in e["fields"]:
            if f.get("required") and f["name"] not in e["keyFields"] and (entity, f["name"]) not in targets:
                issues.append(Issue(Layer.COVERAGE, B, "REQUIRED_NOT_MAPPED",
                                    f"{entity}.{f['name']} is required but not mapped"))

    used = {c for r in rules for c in r.columns_used()}
    unmapped = [c for c in ctx.columns if c not in used]
    if unmapped:
        issues.append(Issue(Layer.COVERAGE, W, "UNMAPPED_COLUMNS",
                            f"{len(unmapped)} column(s) are not mapped and will be ignored",
                            count=len(unmapped), examples=unmapped[:20]))
    return issues


def _check_types(r: Rule, fdef: dict, ctx: Context) -> list[Issue]:
    issues: list[Issue] = []
    ftype = fdef["type"]
    target = f"{r.target_entity}.{r.target_field}"
    if r.mapping_type is MappingType.DIRECT and r.source_column in ctx.columns:
        stype = ctx.columns[r.source_column]
        if ftype not in DIRECT_COMPAT.get(stype, set()):
            issues.append(Issue(Layer.TYPE, B, "TYPE_INCOMPATIBLE",
                                f"A {stype} column cannot be copied into {target} ({ftype}); use a transform", r.key))
    elif r.mapping_type is MappingType.TRANSFORM and (info := ctx.refs.get(r.transform_ref or "")):
        if info.kind == "TRANSFORM":
            out = info.content["output"]
            if ftype not in TRANSFORM_COMPAT.get(out, set()):
                issues.append(Issue(Layer.TYPE, B, "TYPE_INCOMPATIBLE",
                                    f"{r.transform_ref} produces {out}; {target} needs {ftype}", r.key))
    elif r.mapping_type is MappingType.LOOKUP and (info := ctx.refs.get(r.lookup_ref or "")):
        if info.kind == "LOOKUP":
            if ftype != "enum":
                issues.append(Issue(Layer.LOOKUP, B, "LOOKUP_TARGET", f"{target} is not an enum field", r.key))
            else:
                outputs = {e["target"] for e in info.content["entries"]} | (
                    {info.content["default"]} if "default" in info.content else set())
                bad = sorted(outputs - set(fdef["values"]))
                if bad:
                    issues.append(Issue(Layer.LOOKUP, B, "LOOKUP_TARGET",
                                        f"{r.lookup_ref} produces values not allowed in {target}: {', '.join(bad)}",
                                        r.key))
    elif r.mapping_type is MappingType.CROSSWALK and (info := ctx.refs.get(r.crosswalk_ref or "")):
        if info.kind == "CROSSWALK":
            xt = info.content["target"]["entity"]
            entity = ctx.entity(r.target_entity)
            if xt != r.target_entity or r.target_field not in entity["keyFields"]:
                issues.append(Issue(Layer.CROSSWALK, B, "CROSSWALK_TARGET",
                                    f"{r.crosswalk_ref} resolves {xt} ids; map it to a key field of {xt}", r.key))
    return issues


def _check_relationship_rule(r: Rule, ctx: Context) -> list[Issue]:
    rel = r.relationship or {}
    issues: list[Issue] = []

    def bad(code: str, msg: str) -> None:
        issues.append(Issue(Layer.RELATIONSHIP, B, code, msg, r.key))

    for k in ("fromEntity", "fromColumn", "toEntity", "toColumn"):
        if not rel.get(k):
            bad("MISSING_PARAM", f"A relationship rule needs {k}")
    if not rel.get("name") and not rel.get("nameColumn"):
        bad("MISSING_PARAM", "A relationship rule needs a fixed name or a name column")
    if issues:
        return issues
    for side in ("fromEntity", "toEntity"):
        if ctx.entity(rel[side]) is None:
            bad("UNKNOWN_TARGET", f"Entity {rel[side]} is not in the canonical schema")
        elif _single_key(ctx, rel[side]) is None:
            bad("UNSUPPORTED", f"{rel[side]} has a composite key; relationship endpoints need a single key")
    if rel.get("name"):
        cdef = ctx.relationship(rel["name"])
        if cdef is None:
            bad("UNKNOWN_RELATIONSHIP", f"Relationship {rel['name']} is not in the canonical schema")
        else:
            froms, to = _rel_endpoints(cdef)
            if rel["fromEntity"] not in froms or rel["toEntity"] != to:
                bad("RELATIONSHIP_ENDPOINTS",
                    f"{rel['name']} links {'/'.join(froms)} to {to}, not {rel['fromEntity']} to {rel['toEntity']}")
    return issues


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class StagedRecord:
    entity: str
    key: str  # canonical JSON of the key values
    values: dict[str, Any]  # field -> JSON value
    rows: list[int]
    lineage: dict[str, dict[str, Any]]  # field -> lineage of the value


@dataclass
class Edge:
    name: str
    from_entity: str
    from_key: str
    to_entity: str
    to_key: str
    row: int
    rule_key: str


@dataclass
class ExecutionResult:
    total_rows: int = 0
    records: dict[tuple[str, str], StagedRecord] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)  # {row, reasons[]}
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    # rule key -> code -> (count, examples)
    rule_errors: dict[str, dict[str, list]] = field(default_factory=lambda: defaultdict(dict))
    unknown_lookup_values: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    unresolved_crosswalks: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    cycles: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    required_failed_rows: set[int] = field(default_factory=set)  # rows with an empty key/required value

    @property
    def mapped_rows(self) -> int:
        return self.total_rows - len(self.rejected_rows)

    def counts(self) -> dict[str, Any]:
        per_entity = Counter(e for e, _ in self.records)
        per_rel = Counter(e.name for e in self.edges)
        return {"total_rows": self.total_rows, "mapped_rows": self.mapped_rows,
                "rejected_rows": len(self.rejected_rows), "records": dict(sorted(per_entity.items())),
                "relationships": dict(sorted(per_rel.items())), "conflicts": len(self.conflicts)}


SAMPLE_ROWS = 5
MAX_REJECTED_LISTED = 500
MAX_EXAMPLES = 5


def _note(result: ExecutionResult, rule_key: str, code: str, example: str) -> None:
    slot = result.rule_errors[rule_key].setdefault(code, [0, []])
    slot[0] += 1
    if len(slot[1]) < MAX_EXAMPLES:
        slot[1].append(example)


def _rule_value(r: Rule, row: dict[str, Any], ctx: Context, fdef: dict, result: ExecutionResult) -> Any:
    """Value of a field rule for one row (typed), or raises MappingValueError."""
    t = r.mapping_type
    if t is MappingType.DERIVED:
        d = r.derived or {}
        if d["op"] == "CONSTANT":
            raw = d["value"]
        elif d["op"] == "COALESCE":
            raw = next((row.get(c) for c in d["inputs"] if as_text(row.get(c)) is not None), None)
        else:
            parts = [as_text(row.get(c)) for c in d["inputs"]]
            parts = [p for p in parts if p is not None]
            raw = (d.get("separator") or "").join(parts) if parts else None
        return coerce(raw, fdef)
    raw = row.get(r.source_column) if r.source_column else None
    if t is MappingType.DIRECT:
        return coerce(raw, fdef)
    text = as_text(raw)
    if text is None:
        return None
    if t is MappingType.LOOKUP:
        try:
            return coerce(apply_lookup(ctx.refs[r.lookup_ref].content, text), fdef)
        except LookupMiss as exc:
            result.unknown_lookup_values[r.key][text] += 1
            raise MappingValueError("UNKNOWN_LOOKUP_VALUE", f"no lookup entry for {text!r}") from exc
    if t is MappingType.TRANSFORM:
        try:
            out = apply_transform(ctx.refs[r.transform_ref].content, text)
        except TransformError as exc:
            raise MappingValueError("TRANSFORM_FAILED", str(exc)) from exc
        return coerce(out, fdef)
    if t is MappingType.CROSSWALK:
        xw = ctx.refs[r.crosswalk_ref].content
        hit = next((e["canonicalId"] for e in xw["entries"]
                    if e["sourceSystem"] == r.source_system and e["sourceKey"] == text), None)
        if hit is None:
            result.unresolved_crosswalks[r.key][text] += 1
            raise MappingValueError("UNRESOLVED_CROSSWALK", f"{r.source_system} key {text!r} has no crosswalk entry")
        return coerce(hit, fdef)
    raise MappingValueError("TYPE_MISMATCH", f"unsupported rule type {t}")


def _relationship_name(r: Rule, row: dict[str, Any]) -> str | None:
    rel = r.relationship or {}
    if rel.get("name"):
        return rel["name"]
    raw = as_text(row.get(rel.get("nameColumn")))
    return re.sub(r"[\s-]+", "_", raw.strip()).upper() if raw else None


def execute(rules: list[Rule], rows: Iterable[dict[str, Any]], ctx: Context, *, source: dict[str, Any]) -> ExecutionResult:
    """Run the rules over rows (dicts keyed by column header, each with ``__row__``).

    A row with any error is rejected as a whole (nothing from it is staged). Two rows that give the same
    entity key different values make that record a conflict (both kept out). ``source`` is copied into
    lineage (e.g. upload id, file, sheet, mapping version id).
    """
    result = ExecutionResult()
    field_rules = [r for r in rules if r.mapping_type is not MappingType.RELATIONSHIP]
    rel_rules = [r for r in rules if r.mapping_type is MappingType.RELATIONSHIP]
    conflicted: set[tuple[str, str]] = set()

    for row in rows:
        n = row["__row__"]
        result.total_rows += 1
        errors: list[str] = []
        failed_fields: set[tuple[str, str]] = set()
        per_entity: dict[str, dict[str, tuple[Any, dict]]] = defaultdict(dict)
        for r in field_rules:
            fdef = ctx.field(r.target_entity, r.target_field)
            try:
                value = _rule_value(r, row, ctx, fdef, result)
                if value is None:
                    if r.null_handling is NullHandling.DEFAULT:
                        value = coerce(r.default_value, fdef)
                    elif r.null_handling is NullHandling.REJECT_ROW or r.required:
                        result.required_failed_rows.add(n)
                        raise MappingValueError("NULL_REQUIRED", f"{r.source_column or r.target_field} is empty")
            except MappingValueError as exc:
                _note(result, r.key, exc.code, f"row {n}: {exc}")
                errors.append(f"{r.key} {r.target_entity}.{r.target_field}: {exc}")
                failed_fields.add((r.target_entity, r.target_field))
                continue
            if value is None:
                continue
            src_val = row.get(r.source_column) if r.source_column else None
            per_entity[r.target_entity][r.target_field] = (to_json(value), {
                "source_field": r.source_column, "source_value": as_text(src_val), "rule_key": r.key,
                "mapping_type": r.mapping_type.value, "lookup_ref": r.lookup_ref, "transform_ref": r.transform_ref,
                "crosswalk_ref": r.crosswalk_ref, "row": n, **source})

        row_records: list[tuple[str, str, dict[str, Any]]] = []
        for entity in sorted(per_entity):
            fields_ = per_entity[entity]
            e = ctx.entity(entity)
            keys = [fields_.get(k, (None, None))[0] for k in e["keyFields"]]
            if any(k is None for k in keys):
                missing = [k for k, v in zip(e["keyFields"], keys, strict=True) if v is None]
                errors.append(f"{entity}: key field {', '.join(missing)} is empty")
                result.required_failed_rows.add(n)
                for kf in missing:
                    _note(result, f"{entity}.{kf}", "MISSING_KEY", f"row {n}")
                continue
            for f in e["fields"]:
                if f.get("required") and f["name"] not in fields_ and f["name"] not in e["keyFields"] \
                        and (entity, f["name"]) not in failed_fields:
                    errors.append(f"{entity}.{f['name']} is required but empty")
                    result.required_failed_rows.add(n)
                    _note(result, f"{entity}.{f['name']}", "NULL_REQUIRED", f"row {n}")
            row_records.append((entity, canonical_json(keys).decode("utf-8"), fields_))

        row_edges: list[Edge] = []
        for r in rel_rules:
            rel = r.relationship
            f_key, t_key = as_text(row.get(rel["fromColumn"])), as_text(row.get(rel["toColumn"]))
            if f_key is None or t_key is None:
                if r.required:
                    errors.append(f"{r.key}: relationship endpoint is empty")
                    _note(result, r.key, "NULL_REQUIRED", f"row {n}")
                continue
            name = _relationship_name(r, row)
            cdef = ctx.relationship(name) if name else None
            ok = cdef is not None and rel["fromEntity"] in _rel_endpoints(cdef)[0] and \
                rel["toEntity"] == _rel_endpoints(cdef)[1]
            if not ok:
                msg = f"{name or '(empty)'} is not a relationship from {rel['fromEntity']} to {rel['toEntity']}"
                errors.append(f"{r.key}: {msg}")
                _note(result, r.key, "INVALID_RELATIONSHIP", f"row {n}: {msg}")
                continue
            row_edges.append(Edge(name, rel["fromEntity"], canonical_json([f_key]).decode("utf-8"), rel["toEntity"],
                                  canonical_json([t_key]).decode("utf-8"), n, r.key))

        if errors:
            if len(result.rejected_rows) < MAX_REJECTED_LISTED:
                result.rejected_rows.append({"row": n, "reasons": errors})
            else:
                result.rejected_rows.append({"row": n, "reasons": errors[:1]})
            continue

        for entity, key, fields_ in row_records:
            values = {f: v for f, (v, _) in fields_.items()}
            existing = result.records.get((entity, key))
            if existing is None:
                result.records[(entity, key)] = StagedRecord(
                    entity, key, values, [n], {f: lin for f, (_, lin) in fields_.items()})
                continue
            clash = sorted(f for f in set(values) | set(existing.values)
                           if f in values and f in existing.values and values[f] != existing.values[f])
            if clash:
                conflicted.add((entity, key))
                result.conflicts.append({"entity": entity, "key": key, "rows": [existing.rows[0], n],
                                         "fields": clash})
            else:
                for f, v in values.items():
                    if f not in existing.values:
                        existing.values[f] = v
                        existing.lineage[f] = fields_[f][1]
                existing.rows.append(n)
        result.edges += row_edges
        if len(result.samples) < SAMPLE_ROWS:
            result.samples.append({"row": n, "records": [{"entity": e, "key": k, "values": {f: v for f, (v, _) in fs.items()}}
                                                         for e, k, fs in row_records],
                                   "relationships": [{"name": x.name, "from": x.from_key, "to": x.to_key}
                                                     for x in row_edges]})

    for k in conflicted:
        result.records.pop(k, None)
    result.edges = _dedupe_edges(result.edges)
    _reject_cycles(result, ctx)
    return result


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: set[tuple] = set()
    out: list[Edge] = []
    for e in edges:
        k = (e.name, e.from_entity, e.from_key, e.to_entity, e.to_key)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def _reject_cycles(result: ExecutionResult, ctx: Context) -> None:
    """Remove edges that form a cycle in a relationship declared acyclic (e.g. product hierarchy)."""
    by_name: dict[str, list[Edge]] = defaultdict(list)
    for e in result.edges:
        by_name[e.name].append(e)
    removed: set[int] = set()
    for name, edges in by_name.items():
        cdef = ctx.relationship(name)
        if not cdef or not cdef.get("acyclic"):
            continue
        graph: dict[str, list[str]] = defaultdict(list)
        for e in edges:
            graph[f"{e.from_entity}|{e.from_key}"].append(f"{e.to_entity}|{e.to_key}")
        in_cycle = _nodes_in_cycles(graph)
        for e in edges:
            a, b = f"{e.from_entity}|{e.from_key}", f"{e.to_entity}|{e.to_key}"
            if a in in_cycle and b in in_cycle:
                removed.add(id(e))
                result.cycles.append({"name": name, "from": e.from_key, "to": e.to_key, "row": e.row,
                                      "rule_key": e.rule_key})
    result.edges = [e for e in result.edges if id(e) not in removed]


def _nodes_in_cycles(graph: dict[str, list[str]]) -> set[str]:
    """Tarjan's strongly connected components; nodes in components of size > 1 or with a self loop."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: set[str] = set()
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, []):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in graph.get(v, []):
                out.update(comp)

    for v in sorted(graph):
        if v not in index:
            visit(v)
    return out


def output_hash(result: ExecutionResult) -> str:
    """Fingerprint of everything an apply stages (records, lineage, edges, rejections, conflicts)."""
    body = {
        "records": [{"entity": r.entity, "key": r.key, "values": r.values, "rows": r.rows,
                     "lineage": r.lineage} for _, r in sorted(result.records.items())],
        "edges": [asdict(e) for e in sorted(result.edges, key=lambda e: (e.name, e.from_key, e.to_key, e.row))],
        "rejected": result.rejected_rows,
        "conflicts": result.conflicts,
        "cycles": result.cycles,
    }
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()


# ---------------------------------------------------------------------------
# Validation = static checks + a full deterministic run over the confirmed file
# ---------------------------------------------------------------------------

DATA_ISSUES: dict[str, tuple[Layer, str]] = {
    "MISSING_KEY": (Layer.COVERAGE, "Key field is empty"),
    "NULL_REQUIRED": (Layer.COVERAGE, "Required value is empty"),
    "UNKNOWN_LOOKUP_VALUE": (Layer.LOOKUP, "Values have no lookup entry"),
    "UNRESOLVED_CROSSWALK": (Layer.CROSSWALK, "Source keys have no crosswalk entry"),
    "TRANSFORM_FAILED": (Layer.TRANSFORM, "Transform failed"),
    "INVALID_RELATIONSHIP": (Layer.RELATIONSHIP, "Invalid relationship"),
    "TYPE_MISMATCH": (Layer.TYPE, "Values do not fit the target type"),
    "NOT_ALLOWED": (Layer.TYPE, "Values are not allowed for the target enum"),
    "TOO_LONG": (Layer.TYPE, "Values are too long"),
    "PATTERN": (Layer.TYPE, "Values do not match the field pattern"),
    "BELOW_MINIMUM": (Layer.TYPE, "Values are below the minimum"),
}


def validate(rules: list[Rule], rows: Iterable[dict[str, Any]], ctx: Context, *, source: dict[str, Any]) -> dict[str, Any]:
    issues = check_rules(rules, ctx)
    result = None
    if not any(i.severity is B for i in issues):
        result = execute(rules, rows, ctx, source=source)
        for rule_key, codes in sorted(result.rule_errors.items()):
            for code, (count, examples) in sorted(codes.items()):
                layer, text = DATA_ISSUES.get(code, (Layer.SAMPLE, code))
                issues.append(Issue(layer, B, code, f"{text}: {count} row(s)", rule_key, count, examples))
        for c in result.conflicts[:MAX_EXAMPLES]:
            issues.append(Issue(Layer.COVERAGE, B, "CONFLICTING_VALUES",
                                f"{c['entity']} {c['key']} has different {', '.join(c['fields'])} in rows "
                                f"{c['rows'][0]} and {c['rows'][1]}", count=1))
        if result.cycles:
            issues.append(Issue(Layer.RELATIONSHIP, B, "CYCLE",
                                f"{len(result.cycles)} edge(s) form a cycle in an acyclic relationship",
                                count=len(result.cycles),
                                examples=[f"{c['name']} {c['from']} -> {c['to']} (row {c['row']})" for c in result.cycles[:5]]))
        if result.total_rows == 0:
            issues.append(Issue(Layer.SAMPLE, B, "NO_ROWS", "The source sheet has no data rows"))

    blocking = [i for i in issues if i.severity is B]
    warnings = [i for i in issues if i.severity is W]
    status = "BLOCKED" if blocking else ("WARNING" if warnings else "PASS")
    out: dict[str, Any] = {
        "status": status,
        "blocking_errors": len(blocking),
        "warnings": len(warnings),
        "issues": [i.to_dict() for i in issues],
    }
    if result is not None:
        total = result.total_rows
        out |= {
            "counts": result.counts(),
            "required_field_coverage": (100 * (total - len(result.required_failed_rows))) // total if total else 0,
            "unknown_lookup_values": {k: [{"value": v, "count": c} for v, c in cnt.most_common(20)]
                                      for k, cnt in result.unknown_lookup_values.items()},
            "unresolved_crosswalks": {k: [{"value": v, "count": c} for v, c in cnt.most_common(20)]
                                      for k, cnt in result.unresolved_crosswalks.items()},
            "sample_rows": result.samples,
            "rejected_rows": result.rejected_rows[:50],
            "output_hash": output_hash(result),
        }
    return out


def rule_diff(old: list[Rule], new: list[Rule]) -> dict[str, list[str]]:
    """What changed between two versions (by rule id), for reviewers."""
    def body(r: Rule) -> dict:
        return {k: v for k, v in r.to_dict().items() if k not in ("confidence", "proposed_by")}
    a = {r.key: body(r) for r in old}
    b = {r.key: body(r) for r in new}
    return {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])}
