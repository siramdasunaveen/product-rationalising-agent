"""Deterministic mapping suggestions (no AI). Each suggestion records how it was found and a confidence.

Order (first that applies wins for a column):
1. PRIOR_MAPPING (95): the tenant's latest published mapping of the same domain and the same header
   fingerprint is reused as a whole; it was reviewed by a person for exactly this structure.
2. TEMPLATE (100): the column matches the governed template of the domain.
3. NAME_MATCH (90): the header equals a canonical field's label or name (unique among the domain's entities).
4. ALIAS (80): the header is a known alias of a canonical field (unique).

The rule type is chosen from the target field and the column's type and sample values: enum -> LOOKUP when a
published lookup feeds that field (or DIRECT when every sample is already an allowed value); date / decimal /
boolean from text -> the first published TRANSFORM that parses every sample; otherwise DIRECT when the
types are compatible. If nothing fits, no suggestion is made (never a guess).
"""

import re
from dataclasses import replace
from typing import Any

from app.domain.artifact_content import LookupMiss, apply_lookup, find_field
from app.domain.mapping import (
    DIRECT_COMPAT,
    TRANSFORM_COMPAT,
    MappingType,
    MappingValueError,
    ProposedBy,
    Rule,
    coerce,
)
from app.domain.transforms import TransformError, apply_transform

CONF = {ProposedBy.TEMPLATE: 100, ProposedBy.PRIOR_MAPPING: 95, ProposedBy.NAME_MATCH: 90, ProposedBy.ALIAS: 80}

# canonical field name -> extra header spellings seen in Telco exports
ALIASES: dict[str, tuple[str, ...]] = {
    "productId": ("product code", "prod id", "prod code", "sku", "product number", "offering id", "product key"),
    "productName": ("product title", "offering name", "product description short", "commercial name"),
    "productFamily": ("family", "product line", "category", "product category"),
    "status": ("product status", "state"),
    "offerId": ("offer code", "bundle id", "offer key"),
    "parentProductId": ("parent product id", "parent id", "parent sku"),
    "lifecycleStatus": ("lifecycle", "lifecycle stage", "plm status", "lifecycle state"),
    "endOfSaleDate": ("eos date", "end of sale"),
    "sunsetDate": ("end of life date", "eol date", "retirement date"),
    "replacementProductId": ("replacement product", "successor product id", "successor"),
    "priceBookId": ("pricebook id", "price list id", "price list"),
    "currency": ("ccy", "currency code", "currency iso code"),
    "listPrice": ("price", "rrp", "list price amount", "unit price", "recurring price"),
    "effectiveFrom": ("valid from", "start date", "from date", "effective date", "effective start"),
    "effectiveTo": ("valid to", "end date", "to date", "effective end", "expiry date"),
    "serviceSpecId": ("service id", "service specification id", "cfs id", "service spec"),
    "serviceType": ("service category",),
    "specCategory": ("cfs rfs", "spec category", "service layer"),
    "resourceSpecId": ("resource id", "resource specification id", "resource spec"),
    "workflowId": ("workflow", "process id", "flow id"),
    "ruleAction": ("action", "rule", "step"),
    "targetSystem": ("system", "target platform", "fulfilment system"),
    "attributeOptionId": ("attribute id", "option id", "characteristic id"),
    "ruleType": ("cpq rule type", "constraint type"),
    "activeCount": ("active instances", "instance count", "active services"),
    "aggregateKey": ("aggregate key", "aggregate id"),
}


def norm(text: str) -> str:
    """Comparable form: 'productId' / 'Product ID' / 'product_id' -> 'product id'."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _samples(col: dict) -> list[str]:
    return [s for s in col.get("samples", []) if s not in (None, "")]


def _lookup_for(entity: str, field: str, lookups: dict[str, tuple[str, dict]], preferred: str | None) -> str | None:
    if preferred and preferred in lookups:
        return lookups[preferred][0]
    for key in sorted(lookups):
        ref, content = lookups[key]
        t = content["target"]
        if t["entity"] == entity and t["field"] == field:
            return ref
    return None


def _lookup_covers(content: dict, samples: list[str]) -> bool:
    try:
        for s in samples:
            apply_lookup(content, s)
    except LookupMiss:
        return False
    return True


def _transform_for(fdef: dict, samples: list[str], transforms: dict[str, tuple[str, dict]]) -> str | None:
    for key in sorted(transforms):
        ref, content = transforms[key]
        if fdef["type"] not in TRANSFORM_COMPAT.get(content["output"], set()):
            continue
        if fdef["type"] in ("string", "text", "reference", "enum"):
            continue  # text targets do not need a transform suggestion
        if not samples:
            continue
        try:
            for s in samples:
                coerce(apply_transform(content, s), fdef)
        except (TransformError, MappingValueError):
            continue
        return ref
    return None


def _direct_fits(col: dict, fdef: dict) -> bool:
    if fdef["type"] not in DIRECT_COMPAT.get(col["type"], set()):
        return False
    try:
        for s in _samples(col):
            coerce(s, fdef)
    except MappingValueError:
        return False
    return True


def choose_rule(key: str, col: dict, entity: str, field: str, canonical: dict, how: ProposedBy, *,
                lookups: dict[str, tuple[str, dict]], transforms: dict[str, tuple[str, dict]],
                preferred_lookup: str | None = None, required: bool | None = None) -> Rule | None:
    fdef = find_field(canonical, entity, field)
    if fdef is None:
        return None
    samples = _samples(col)
    req = bool(fdef.get("required")) if required is None else required
    base = Rule(key=key, mapping_type=MappingType.DIRECT, source_column=col["header"], target_entity=entity,
                target_field=field, required=req, confidence=CONF.get(how), proposed_by=how)
    if fdef["type"] == "enum":
        ref = _lookup_for(entity, field, lookups, preferred_lookup)
        if ref:
            content = next(c for r, c in lookups.values() if r == ref)
            if not samples or _lookup_covers(content, samples) or how is ProposedBy.TEMPLATE:
                return replace(base, mapping_type=MappingType.LOOKUP, lookup_ref=ref)
        return base if samples and _direct_fits(col, fdef) else None
    if fdef["type"] in ("date", "datetime", "decimal", "integer", "boolean") and col["type"] in ("string",):
        ref = _transform_for(fdef, samples, transforms)
        if ref:
            return replace(base, mapping_type=MappingType.TRANSFORM, transform_ref=ref)
    return base if _direct_fits(col, fdef) else None


def suggest(columns: list[dict[str, Any]], *, canonical: dict, entities: list[str], template: dict | None,
            lookups: dict[str, tuple[str, dict]], transforms: dict[str, tuple[str, dict]],
            prior: list[Rule] | None = None) -> list[Rule]:
    """Rules for ``columns`` ({header, type, samples}); keys R1.. in column order, relationships last."""
    headers = {c["header"] for c in columns}
    if prior:
        usable = [r for r in prior if all(c in headers for c in r.columns_used())]
        return [replace(r, key=f"R{i}", confidence=CONF[ProposedBy.PRIOR_MAPPING],
                        proposed_by=ProposedBy.PRIOR_MAPPING) for i, r in enumerate(usable, start=1)]

    tpl_cols = {norm(c["header"]): c for c in (template or {}).get("columns", [])}
    fields: dict[str, list[tuple[str, str]]] = {}  # normalised label/name -> [(entity, field)]
    aliases: dict[str, list[tuple[str, str]]] = {}
    for e in canonical["entities"]:
        if e["name"] not in entities:
            continue
        for f in e["fields"]:
            for spelling in {norm(f["name"]), norm(f.get("label", ""))} - {""}:
                fields.setdefault(spelling, []).append((e["name"], f["name"]))
            for a in ALIASES.get(f["name"], ()):
                aliases.setdefault(norm(a), []).append((e["name"], f["name"]))

    rules: list[Rule] = []
    taken: set[tuple[str, str]] = set()
    n = 0
    for col in columns:
        h = norm(col["header"])
        candidate: tuple[str, str, ProposedBy, str | None] | None = None
        required: bool | None = None
        tc = tpl_cols.get(h)
        if tc is not None:
            if tc.get("target"):
                candidate = (tc["target"]["entity"], tc["target"]["field"], ProposedBy.TEMPLATE, tc.get("lookup"))
                required = bool(tc.get("required"))  # the template says which columns must be filled
        elif len(fields.get(h, [])) == 1:
            candidate = (*fields[h][0], ProposedBy.NAME_MATCH, None)
        elif len(aliases.get(h, [])) == 1:
            candidate = (*aliases[h][0], ProposedBy.ALIAS, None)
        if candidate is None or (candidate[0], candidate[1]) in taken:
            continue
        if required is None:  # key of the domain's main entity, or a required non-key field
            primary = entities[0] if entities else None
            e_def = next(e for e in canonical["entities"] if e["name"] == candidate[0])
            fdef = find_field(canonical, candidate[0], candidate[1]) or {}
            is_key = candidate[1] in e_def["keyFields"]
            required = (is_key and candidate[0] == primary) or (bool(fdef.get("required")) and not is_key)
        rule = choose_rule(f"R{n + 1}", col, candidate[0], candidate[1], canonical, candidate[2],
                           lookups=lookups, transforms=transforms, preferred_lookup=candidate[3],
                           required=required)
        if rule is not None:
            n += 1
            rules.append(rule)
            taken.add((candidate[0], candidate[1]))

    for rel in (template or {}).get("relationships", []):
        cols = [rel["fromColumn"], rel["toColumn"]] + ([rel["nameColumn"]] if rel.get("nameColumn") else [])
        actual = {norm(c["header"]): c["header"] for c in columns}
        if not all(norm(c) in actual for c in cols):
            continue
        n += 1
        spec = {"fromEntity": rel["fromEntity"], "fromColumn": actual[norm(rel["fromColumn"])],
                "toEntity": rel["toEntity"], "toColumn": actual[norm(rel["toColumn"])]}
        spec |= {"name": rel["name"]} if rel.get("name") else {"nameColumn": actual[norm(rel["nameColumn"])]}
        rules.append(Rule(key=f"R{n}", mapping_type=MappingType.RELATIONSHIP,
                          source_column=spec.get("nameColumn") or spec["fromColumn"], relationship=spec,
                          confidence=CONF[ProposedBy.TEMPLATE], proposed_by=ProposedBy.TEMPLATE))
    return rules
