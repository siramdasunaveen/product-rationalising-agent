"""Seed content for the registries: TELCO-CANONICAL@1.0, starter lookups and transforms.

Built from the Excel template columns in docs/SPEC_NOTES.md (Evidence Hub). Metadata and aggregates
only: no subscriber-level data. Change it by publishing new versions, not by editing this file
(editing here only affects tenants seeded afterwards).
"""

CANONICAL_KEY = "TELCO-CANONICAL"


def _f(name: str, label: str, type_: str = "string", *, required: bool = False, **extra) -> dict:
    field = {"name": name, "label": label, "type": type_}
    if required:
        field["required"] = True
    field.update(extra)
    return field


def _ref(name: str, label: str, entity: str, *, required: bool = False) -> dict:
    return _f(name, label, "reference", required=required, references=entity)


EFFECTIVE = [_f("effectiveFrom", "Effective From", "date"), _f("effectiveTo", "Effective To", "date")]

TELCO_CANONICAL = {
    "description": "Canonical Telco product and service model used by every evidence domain.",
    "dataClassification": "METADATA_AND_AGGREGATES",
    "entities": [
        {
            "name": "Product", "label": "Commercial product", "keyFields": ["productId"],
            "fields": [
                _f("productId", "Product ID", required=True),
                _f("productName", "Product Name", required=True, maxLength=255),
                _f("productFamily", "Product Family", required=True),
                _f("status", "Status", "enum", required=True,
                   values=["PLANNED", "ACTIVE", "INACTIVE", "WITHDRAWN"]),
                _ref("parentProductId", "Parent Product", "Product"),
                _f("market", "Market"),
                _f("segment", "Segment"),
                _f("channel", "Channel"),
                _f("description", "Description", "text"),
            ],
        },
        {
            "name": "Offer", "label": "Commercial offer / bundle", "keyFields": ["offerId"],
            "fields": [
                _f("offerId", "Offer ID", required=True),
                _f("offerName", "Offer Name"),
                _f("market", "Market"),
                _f("segment", "Segment"),
                _f("channel", "Channel"),
            ],
        },
        {
            "name": "CpqRule", "label": "CPQ configuration rule",
            "keyFields": ["productId", "attributeOptionId", "ruleType", "effectiveFrom"],
            "fields": [
                _ref("productId", "Product ID", "Product", required=True),
                _f("attributeOptionId", "Attribute/Option ID", required=True),
                _f("ruleType", "Rule Type", "enum", required=True,
                   values=["CONFIGURATION", "DEPENDENCY", "EXCLUSION", "ELIGIBILITY", "PRICING"]),
                _f("effectiveFrom", "Effective From", "date", required=True),
                _f("requirement", "Required/Optional", "enum", values=["REQUIRED", "OPTIONAL"]),
                _f("dependency", "Dependency"),
                _f("eligibility", "Eligibility"),
                _f("cardinality", "Cardinality"),
                _f("effectiveTo", "Effective To", "date"),
            ],
        },
        {
            "name": "PriceBookEntry", "label": "List price (no customer-level pricing)",
            "keyFields": ["priceBookId", "productId", "currency", "effectiveFrom"],
            "fields": [
                _f("priceBookId", "Price Book ID", required=True),
                _ref("productId", "Product ID", "Product", required=True),
                _f("currency", "Currency", required=True, pattern="^[A-Z]{3}$"),
                _f("listPrice", "List Price", "decimal", required=True, minimum=0),
                _f("effectiveFrom", "Effective From", "date", required=True),
                _f("segment", "Segment"),
                _f("channel", "Channel"),
                _f("market", "Market"),
                _f("effectiveTo", "Effective To", "date"),
                _f("taxClass", "Tax Class"),
            ],
        },
        {
            "name": "ProductLifecycle", "label": "Lifecycle state of a product",
            "keyFields": ["productId", "effectiveFrom"],
            "fields": [
                _ref("productId", "Product ID", "Product", required=True),
                _f("lifecycleStatus", "Lifecycle Status", "enum", required=True,
                   values=["PLANNED", "ACTIVE", "END_OF_SALE", "END_OF_SUPPORT", "SUNSET", "RETIRED"]),
                _f("effectiveFrom", "Effective From", "date", required=True),
                _f("endOfSaleDate", "End-of-Sale Date", "date"),
                _f("sunsetDate", "Sunset Date", "date"),
                _ref("replacementProductId", "Replacement Product ID", "Product"),
                _f("owner", "Owner"),
                _f("rationale", "Rationale", "text"),
            ],
        },
        {
            "name": "ServiceSpec", "label": "Service specification (CFS or RFS)", "keyFields": ["serviceSpecId"],
            "fields": [
                _f("serviceSpecId", "Service Spec ID", required=True),
                _f("serviceType", "Service Type", required=True),
                _f("specCategory", "CFS/RFS", "enum", values=["CFS", "RFS"]),
                _ref("parentServiceSpecId", "Parent Service", "ServiceSpec"),
                *EFFECTIVE,
            ],
        },
        {
            "name": "ResourceSpec", "label": "Resource specification", "keyFields": ["resourceSpecId"],
            "fields": [
                _f("resourceSpecId", "Resource Spec ID", required=True),
                _f("resourceName", "Resource Name"),
                _f("resourceType", "Resource Type"),
            ],
        },
        {
            "name": "ProvisioningWorkflow", "label": "Provisioning workflow / rule", "keyFields": ["workflowId"],
            "fields": [
                _f("workflowId", "Workflow ID", required=True),
                _f("ruleAction", "Rule/Action", required=True),
                _f("targetSystem", "Target System", required=True),
                _f("workflowVersion", "Version"),
                _f("legacyFlag", "Legacy Flag", "boolean"),
                _f("targetCompatibility", "Target Compatibility", "enum",
                   values=["COMPATIBLE", "PARTIAL", "INCOMPATIBLE", "UNKNOWN"]),
                _f("exceptionCode", "Exception Code"),
                *EFFECTIVE,
            ],
        },
        {
            "name": "InventoryAggregate", "label": "Service/resource inventory aggregate (counts only)",
            "keyFields": ["aggregateKey"],
            "fields": [
                _f("aggregateKey", "Service Instance Aggregate Key", required=True),
                _ref("serviceSpecId", "Service Spec ID", "ServiceSpec", required=True),
                _ref("resourceSpecId", "Resource Spec ID", "ResourceSpec", required=True),
                _f("status", "Status", "enum", required=True, values=["ACTIVE", "SUSPENDED", "PENDING", "CEASED"]),
                _f("activeCount", "Active Count", "integer", minimum=0),
                _f("variantCount", "Variant Count", "integer", minimum=0),
                _f("legacyDependency", "Legacy Dependency", "boolean"),
                _f("region", "Region"),
                _f("lastUpdated", "Last Updated", "datetime"),
            ],
        },
    ],
    "relationships": [
        {"name": "INCLUDES", "label": "Offer includes product", "from": "Offer", "to": "Product",
         "cardinality": "MANY_TO_MANY"},
        {"name": "PARENT_OF", "label": "Product hierarchy", "from": "Product", "to": "Product",
         "cardinality": "ONE_TO_MANY", "acyclic": True},
        {"name": "REPLACED_BY", "label": "Product replaced by product", "from": "Product", "to": "Product",
         "cardinality": "MANY_TO_ONE", "acyclic": True},
        {"name": "DECOMPOSES_TO", "label": "Product decomposes to customer-facing service", "from": "Product",
         "to": "ServiceSpec", "cardinality": "MANY_TO_MANY", "attributes": EFFECTIVE},
        {"name": "REALIZED_BY", "label": "CFS realised by RFS", "from": "ServiceSpec", "to": "ServiceSpec",
         "cardinality": "MANY_TO_MANY", "acyclic": True, "attributes": EFFECTIVE},
        {"name": "USES_RESOURCE", "label": "Service uses resource", "from": "ServiceSpec", "to": "ResourceSpec",
         "cardinality": "MANY_TO_MANY", "attributes": EFFECTIVE},
        {"name": "PROVISIONED_BY", "label": "Provisioned by workflow", "from": ["Product", "ServiceSpec"],
         "to": "ProvisioningWorkflow", "cardinality": "MANY_TO_MANY", "attributes": EFFECTIVE},
    ],
}


def _lookup(entity: str, field: str, pairs: list[tuple[str, str]], description: str) -> dict:
    return {
        "description": description,
        "target": {"schema": CANONICAL_KEY, "entity": entity, "field": field},
        "caseSensitive": False,
        "trimSource": True,
        "unmatched": "ERROR",
        "entries": [{"source": s, "target": t} for s, t in pairs],
    }


LOOKUPS: list[tuple[str, str, dict]] = [
    ("PRODUCT-STATUS", "Product status", _lookup("Product", "status", [
        ("Planned", "PLANNED"), ("Draft", "PLANNED"), ("Active", "ACTIVE"), ("Live", "ACTIVE"),
        ("Inactive", "INACTIVE"), ("Suspended", "INACTIVE"), ("Withdrawn", "WITHDRAWN"), ("Retired", "WITHDRAWN"),
    ], "Catalog product status labels to the canonical product status.")),
    ("LIFECYCLE-STATUS", "Lifecycle status", _lookup("ProductLifecycle", "lifecycleStatus", [
        ("Planned", "PLANNED"), ("In Development", "PLANNED"), ("Active", "ACTIVE"), ("GA", "ACTIVE"),
        ("Live", "ACTIVE"), ("End of Sale", "END_OF_SALE"), ("EOS", "END_OF_SALE"),
        ("End of Support", "END_OF_SUPPORT"), ("EOL", "END_OF_SUPPORT"), ("Sunset", "SUNSET"),
        ("Retired", "RETIRED"), ("Withdrawn", "RETIRED"),
    ], "PLM lifecycle labels to the canonical lifecycle status.")),
    ("SERVICE-SPEC-CATEGORY", "Service spec category", _lookup("ServiceSpec", "specCategory", [
        ("CFS", "CFS"), ("Customer Facing", "CFS"), ("Customer Facing Service", "CFS"),
        ("RFS", "RFS"), ("Resource Facing", "RFS"), ("Resource Facing Service", "RFS"),
    ], "Technical catalog labels to CFS / RFS.")),
    ("CPQ-RULE-TYPE", "CPQ rule type", _lookup("CpqRule", "ruleType", [
        ("Configuration", "CONFIGURATION"), ("Config", "CONFIGURATION"), ("Dependency", "DEPENDENCY"),
        ("Requires", "DEPENDENCY"), ("Exclusion", "EXCLUSION"), ("Excludes", "EXCLUSION"),
        ("Eligibility", "ELIGIBILITY"), ("Pricing", "PRICING"),
    ], "CPQ rule type labels to the canonical rule type.")),
]

TRANSFORMS: list[tuple[str, str, dict]] = [
    ("CODE-NORMALISE", "Normalise a code (trim, upper case)", {
        "description": "Trim and upper-case an identifier or code.",
        "input": "string", "output": "string", "steps": [{"op": "trim"}, {"op": "upper"}]}),
    ("DATE-ISO", "Date yyyy-mm-dd", {
        "input": "string", "output": "date", "steps": [{"op": "trim"}, {"op": "parse_date", "format": "%Y-%m-%d"}]}),
    ("DATE-DMY", "Date dd/mm/yyyy", {
        "input": "string", "output": "date", "steps": [{"op": "trim"}, {"op": "parse_date", "format": "%d/%m/%Y"}]}),
    ("PRICE-DECIMAL", "Price with thousands separator", {
        "description": "Text such as '1,299.50' to a decimal with 2 places.",
        "input": "string", "output": "decimal",
        "steps": [{"op": "to_decimal", "decimalSeparator": ".", "thousandsSeparator": ","},
                  {"op": "round", "places": 2}]}),
    ("YES-NO-BOOLEAN", "Yes/No to boolean", {
        "input": "string", "output": "boolean",
        "steps": [{"op": "to_bool", "true": ["Y", "Yes", "True", "1"], "false": ["N", "No", "False", "0"]}]}),
]
