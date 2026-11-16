"""Transform artifacts: a chain of fixed, deterministic steps. No user-authored code.

A transform declares its input and output type, e.g. ``string -> date``. Each step has an input and
an output type; the chain must connect and end at the declared output type. ``apply_transform``
runs a chain on one value, so the same input always gives the same output.

Missing values: ``None`` (and an empty string for parse steps) passes through as ``None``; use the
``default`` step to fill it.
"""

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from jsonschema import Draft202012Validator

from app.domain.common import FieldError

VALUE_TYPES = ("string", "integer", "decimal", "boolean", "date", "datetime")
INPUT_TYPES = ("string",)  # source cells arrive as text; typed cells are handled by mapping (Step 5)

_STR = {"type": "string", "maxLength": 200}
_NO_PARAMS: dict = {}

# op -> (input type, output type, parameter schema properties, required parameters)
OPS: dict[str, tuple[str, str, dict, tuple[str, ...]]] = {
    "trim": ("string", "string", _NO_PARAMS, ()),
    "upper": ("string", "string", _NO_PARAMS, ()),
    "lower": ("string", "string", _NO_PARAMS, ()),
    "collapse_spaces": ("string", "string", _NO_PARAMS, ()),
    "replace": ("string", "string", {"find": {"type": "string", "minLength": 1, "maxLength": 200},
                                     "with": _STR}, ("find", "with")),
    "prefix": ("string", "string", {"value": _STR}, ("value",)),
    "suffix": ("string", "string", {"value": _STR}, ("value",)),
    "pad_left": ("string", "string", {"width": {"type": "integer", "minimum": 1, "maximum": 100},
                                      "char": {"type": "string", "minLength": 1, "maxLength": 1}},
                 ("width", "char")),
    "split": ("string", "string", {"separator": {"type": "string", "minLength": 1, "maxLength": 10},
                                   "index": {"type": "integer", "minimum": 0, "maximum": 100}},
              ("separator", "index")),
    "default": ("string", "string", {"value": _STR}, ("value",)),
    "parse_date": ("string", "date", {"format": {"type": "string", "minLength": 2, "maxLength": 40}},
                   ("format",)),
    "parse_datetime": ("string", "datetime", {"format": {"type": "string", "minLength": 2, "maxLength": 40}},
                       ("format",)),
    "to_decimal": ("string", "decimal", {"decimalSeparator": {"enum": [".", ","]},
                                         "thousandsSeparator": {"enum": ["", ",", ".", " "]}}, ()),
    "to_integer": ("string", "integer", _NO_PARAMS, ()),
    "to_bool": ("string", "boolean", {
        "true": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 20},
                 "minItems": 1, "maxItems": 20},
        "false": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 20},
                  "minItems": 1, "maxItems": 20}}, ("true", "false")),
    "round": ("decimal", "decimal", {"places": {"type": "integer", "minimum": 0, "maximum": 10}}, ("places",)),
}

TRANSFORM_SCHEMA_JSON = {
    "type": "object",
    "required": ["input", "output", "steps"],
    "additionalProperties": False,
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "input": {"enum": list(INPUT_TYPES)},
        "output": {"enum": list(VALUE_TYPES)},
        "steps": {
            "type": "array", "minItems": 1, "maxItems": 20,
            "items": {"type": "object", "required": ["op"], "properties": {"op": {"enum": list(OPS)}}},
        },
    },
}

_STEP_VALIDATORS = {
    op: Draft202012Validator({
        "type": "object",
        "required": ["op", *required],
        "additionalProperties": False,
        "properties": {"op": {"const": op}, **params},
    })
    for op, (_, _, params, required) in OPS.items()
}

DATE_DIRECTIVES = set("YymdbBj")
DATETIME_DIRECTIVES = DATE_DIRECTIVES | set("HMSIpfz")


def _format_error(fmt: str, allowed: set[str]) -> str | None:
    directives = re.findall(r"%(.)", fmt)
    used = [d for d in directives if d != "%"]
    if not used:
        return "Format needs at least one directive such as %Y, %m or %d"
    bad = sorted({d for d in used if d not in allowed})
    if bad:
        return "Unsupported directive(s): " + ", ".join(f"%{d}" for d in bad)
    if fmt.endswith("%") and not fmt.endswith("%%"):
        return "Format ends with a lone %"
    return None


def check_transform(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    current = content["input"]
    for i, step in enumerate(content["steps"]):
        sp = f"steps[{i}]"
        op = step["op"]
        step_errors = list(_STEP_VALIDATORS[op].iter_errors(step))
        if step_errors:
            errors += [FieldError(sp, f"{op}: {e.message}") for e in step_errors]
            continue
        in_type, out_type, _, _ = OPS[op]
        if current != in_type:
            errors.append(FieldError(f"{sp}.op", f"{op} expects a {in_type} value but receives {current}"))
        if op in ("parse_date", "parse_datetime"):
            msg = _format_error(step["format"], DATE_DIRECTIVES if op == "parse_date" else DATETIME_DIRECTIVES)
            if msg:
                errors.append(FieldError(f"{sp}.format", msg))
        if op == "to_decimal" and step.get("decimalSeparator", ".") == step.get("thousandsSeparator", ""):
            errors.append(FieldError(sp, "Decimal and thousands separators must differ"))
        if op == "to_bool":
            overlap = {v.casefold() for v in step["true"]} & {v.casefold() for v in step["false"]}
            if overlap:
                errors.append(FieldError(sp, "Values listed as both true and false: " + ", ".join(sorted(overlap))))
        current = out_type
    if not errors and current != content["output"]:
        errors.append(FieldError("output", f"The steps produce {current}, not {content['output']}"))
    return errors


class TransformError(ValueError):
    def __init__(self, step: int, message: str) -> None:
        super().__init__(f"steps[{step}]: {message}")
        self.step = step


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _apply_step(step: dict, value: Any) -> Any:
    op = step["op"]
    if op == "default":
        return step["value"] if _blank(value) else value
    if value is None:
        return None
    match op:
        case "trim":
            return value.strip()
        case "upper":
            return value.upper()
        case "lower":
            return value.lower()
        case "collapse_spaces":
            return " ".join(value.split())
        case "replace":
            return value.replace(step["find"], step["with"])
        case "prefix":
            return step["value"] + value
        case "suffix":
            return value + step["value"]
        case "pad_left":
            return value.rjust(step["width"], step["char"])
        case "split":
            parts = value.split(step["separator"])
            if step["index"] >= len(parts):
                raise ValueError(f"no part {step['index']} after splitting {value!r}")
            return parts[step["index"]]
    if _blank(value):  # parse steps: empty text means "no value"
        return None
    match op:
        case "parse_date":
            return datetime.strptime(value.strip(), step["format"]).date()
        case "parse_datetime":
            return datetime.strptime(value.strip(), step["format"])
        case "to_decimal":
            text = value.strip()
            if step.get("thousandsSeparator"):
                text = text.replace(step["thousandsSeparator"], "")
            if step.get("decimalSeparator", ".") == ",":
                text = text.replace(",", ".")
            try:
                number = Decimal(text)
            except InvalidOperation as exc:
                raise ValueError(f"{value!r} is not a number") from exc
            if not number.is_finite():
                raise ValueError(f"{value!r} is not a finite number")
            return number
        case "to_integer":
            text = value.strip()
            if not re.fullmatch(r"[+-]?\d+", text):
                raise ValueError(f"{value!r} is not a whole number")
            return int(text)
        case "to_bool":
            text = value.strip().casefold()
            if text in {v.casefold() for v in step["true"]}:
                return True
            if text in {v.casefold() for v in step["false"]}:
                return False
            raise ValueError(f"{value!r} is neither true nor false")
        case "round":
            return value.quantize(Decimal(1).scaleb(-step["places"]), rounding=ROUND_HALF_UP)
    raise ValueError(f"unknown step {op}")  # pragma: no cover (structure check rejects it)


def apply_transform(content: dict, value: Any) -> str | int | Decimal | bool | date | datetime | None:
    """Run the chain on one value. Raises ``TransformError`` naming the failing step."""
    for i, step in enumerate(content["steps"]):
        try:
            value = _apply_step(step, value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TransformError(i, str(exc)) from exc
    return value
