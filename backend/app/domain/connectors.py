"""Live connectors (BUILD_PLAN step 15). Pure: connector configuration rules and the Salesforce domain queries.

A connector is an approved source system registered by an Agent Admin: the instance, the OAuth client (its
secret is Fernet-encrypted, never returned) and, per evidence domain, which object and fields to read. A pull
turns the records into the governed Excel template columns of the domain, so the same mapping, validation,
reconciliation and snapshot rules apply to live and Excel evidence alike (metadata and aggregates only).
"""

import re
from typing import Any

from app.domain.common import FieldError

SYSTEMS = ("SALESFORCE",)
AUTH_TYPES = ("OAUTH_CLIENT_CREDENTIALS",)
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
URL_RE = re.compile(r"^https://[a-z0-9][a-z0-9.-]*\.(my\.salesforce\.com|salesforce\.com|force\.com)/?$", re.I)
DEFAULT_API_VERSION = "v61.0"

# Salesforce objects and fields per evidence domain, keyed by governed template header. Dotted fields are
# relationship paths (PricebookEntry.Product2.ProductCode). Admins may override per connector (config.queries).
SALESFORCE_QUERIES: dict[str, dict[str, Any]] = {
    "CRM_CATALOG": {
        "object": "Product2", "where": "IsActive = true OR IsActive = false",
        "columns": {"Product ID": "ProductCode", "Product Name": "Name", "Product Family": "Family",
                    "Status": {"field": "IsActive", "map": {True: "Active", False: "Inactive"}},
                    "Offer ID": "StockKeepingUnit", "Description": "Description"},
    },
    "PRICE_BOOK": {
        "object": "PricebookEntry", "where": "IsActive = true",
        "columns": {"Price Book ID": "Pricebook2.Name", "Product ID": "Product2.ProductCode",
                    "Currency": "CurrencyIsoCode", "List Price": "UnitPrice"},
    },
    "CPQ": {  # Salesforce CPQ (Revenue Cloud) product options
        "object": "SBQQ__ProductOption__c", "where": None,
        "columns": {"Product ID": "SBQQ__ConfiguredSKU__r.ProductCode",
                    "Attribute/Option ID": "SBQQ__OptionalSKU__r.ProductCode",
                    "Rule Type": {"const": "Configuration"},
                    "Required/Optional": {"field": "SBQQ__Required__c", "map": {True: "Required", False: "Optional"}},
                    "Cardinality": "SBQQ__MaxQuantity__c"},
    },
}


def validate_config(body: dict[str, Any]) -> list[FieldError]:
    errors: list[FieldError] = []
    if not KEY_RE.match(body.get("key") or ""):
        errors.append(FieldError("key", "Use 2-40 lower-case letters, digits or hyphens"))
    if not 1 <= len((body.get("name") or "").strip()) <= 120:
        errors.append(FieldError("name", "Use 1-120 characters"))
    if (body.get("system") or "") not in SYSTEMS:
        errors.append(FieldError("system", f"Choose one of {', '.join(SYSTEMS)}"))
    if not URL_RE.match(body.get("instanceUrl") or ""):
        errors.append(FieldError("instanceUrl", "Use the https://<org>.my.salesforce.com address"))
    if (body.get("authType") or AUTH_TYPES[0]) not in AUTH_TYPES:
        errors.append(FieldError("authType", f"Choose one of {', '.join(AUTH_TYPES)}"))
    api = body.get("apiVersion") or DEFAULT_API_VERSION
    if not re.match(r"^v\d{2}\.\d$", api):
        errors.append(FieldError("apiVersion", "Use the form v61.0"))
    queries = body.get("queries") or {}
    if not isinstance(queries, dict):
        errors.append(FieldError("queries", "Must be an object keyed by evidence domain"))
    else:
        for domain, q in queries.items():
            if not isinstance(q, dict) or not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", str(q.get("object") or "")) \
                    or not isinstance(q.get("columns"), dict) or not q["columns"]:
                errors.append(FieldError(f"queries.{domain}", "Needs an object name and a columns map"))
    return errors


def query_for(domain: str, overrides: dict[str, Any] | None) -> dict[str, Any] | None:
    q = (overrides or {}).get(domain) or SALESFORCE_QUERIES.get(domain)
    return dict(q) if q else None


def soql(q: dict[str, Any]) -> str:
    fields = sorted({_field_name(spec) for spec in q["columns"].values() if _field_name(spec)})
    text = f"SELECT {', '.join(fields)} FROM {q['object']}"
    if q.get("where"):
        text += f" WHERE {q['where']}"
    return text


def _field_name(spec: Any) -> str | None:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        return spec.get("field")
    return None


def _path(record: dict[str, Any], path: str) -> Any:
    cur: Any = record
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def to_row(record: dict[str, Any], q: dict[str, Any]) -> dict[str, Any]:
    """One Salesforce record -> {template header: value}. Never raw customer data: only the listed fields."""
    out: dict[str, Any] = {}
    for header, spec in q["columns"].items():
        if isinstance(spec, str):
            out[header] = _path(record, spec)
        elif "const" in spec:
            out[header] = spec["const"]
        else:
            v = _path(record, spec["field"])
            out[header] = spec.get("map", {}).get(v, v) if "map" in spec else v
    return out


def redact(config: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k not in ("clientSecret", "clientId")}
