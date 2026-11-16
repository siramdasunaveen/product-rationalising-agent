"""Compare the columns of an uploaded sheet with the governed template of its evidence domain.

Header matching ignores case and repeated spaces. The result is shown on the Evidence Hub and later
seeds mapping suggestions (Step 5): a matched column already knows its canonical target.
"""

from typing import Any


def _norm(header: str) -> str:
    return " ".join(header.split()).casefold()


def match_template(template: dict, headers: list[str]) -> dict[str, Any]:
    present = {_norm(h): h for h in headers}
    known = {_norm(c["header"]) for c in template["columns"]}
    matched: list[dict[str, Any]] = []
    required_missing: list[str] = []
    groups: dict[str, list[str]] = {}
    group_hit: dict[str, bool] = {}
    for col in template["columns"]:
        found = present.get(_norm(col["header"]))
        if found is not None:
            matched.append({"template_header": col["header"], "column": found, "target": col.get("target"),
                            "lookup": col.get("lookup"), "required": bool(col.get("required"))})
        elif col.get("required"):
            required_missing.append(col["header"])
        if "oneOfGroup" in col:
            groups.setdefault(col["oneOfGroup"], []).append(col["header"])
            group_hit[col["oneOfGroup"]] = group_hit.get(col["oneOfGroup"], False) or found is not None
    one_of_missing = [" or ".join(members) for g, members in groups.items() if not group_hit[g]]
    required_total = sum(1 for c in template["columns"] if c.get("required")) + len(groups)
    required_found = required_total - len(required_missing) - len(one_of_missing)
    return {
        "complete": not required_missing and not one_of_missing,
        "required_found": required_found,
        "required_total": required_total,
        "required_missing": required_missing,
        "one_of_missing": one_of_missing,
        "matched": matched,
        "unknown_columns": [h for h in headers if _norm(h) not in known],
    }
