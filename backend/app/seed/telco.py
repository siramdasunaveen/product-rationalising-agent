"""Seed content for the registries: TELCO-CANONICAL@1.0, starter lookups and transforms.

Built from the Excel template columns in docs/SPEC_NOTES.md (Evidence Hub). Metadata and aggregates
only: no subscriber-level data. Change it by publishing new versions, not by editing this file
(editing here only affects tenants seeded afterwards).
"""

CANONICAL_KEY = "TELCO-CANONICAL"
# Smallest group a customer aggregate may describe (DECISIONS.md, Step 6): 1-9 subscriptions are rejected.
MIN_COHORT = 10


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
        # --- Aggregates and billing (v1.1; needed by the candidate guides). Aggregates only, never
        # subscriber-level rows: counts below the minimum cohort size are rejected at mapping time.
        {
            "name": "CustomerRevenueAggregate", "label": "Customer and revenue aggregate per product and period",
            "keyFields": ["productId", "periodEnd"],
            "fields": [
                _ref("productId", "Product ID", "Product", required=True),
                _f("periodStart", "Period Start", "date", required=True),
                _f("periodEnd", "Period End", "date", required=True),
                _f("activeSubscriptions", "Active Subscriptions", "integer", required=True, minimum=0,
                   minCohort=MIN_COHORT),
                _f("protectedPriceContracts", "Protected-Price Contracts", "integer", minimum=0),
                _f("mrr", "Monthly Recurring Revenue", "decimal", minimum=0),
                _f("currency", "Currency", pattern="^[A-Z]{3}$"),
                _f("segment", "Segment"),
                _f("market", "Market"),
            ],
        },
        {
            "name": "OrderAssuranceAggregate", "label": "Order and assurance aggregate per product and period",
            "keyFields": ["productId", "periodEnd"],
            "fields": [
                _ref("productId", "Product ID", "Product", required=True),
                _f("periodStart", "Period Start", "date", required=True),
                _f("periodEnd", "Period End", "date", required=True),
                _f("orderCount", "Orders", "integer", minimum=0),
                _f("falloutOrders", "Fallout Orders", "integer", minimum=0),
                _f("manualTouchOrders", "Manual-Touch Orders", "integer", minimum=0),
                _f("incidentCount", "Incidents", "integer", minimum=0),
                _f("provisioningExceptions", "Provisioning Exceptions", "integer", minimum=0),
                _f("avgHandlingMinutes", "Average Handling Minutes", "decimal", minimum=0),
            ],
        },
        {
            "name": "BillingSystem", "label": "Billing system", "keyFields": ["billingSystemId"],
            "fields": [
                _f("billingSystemId", "Billing System ID", required=True),
                _f("billingSystemName", "Billing System Name"),
                _f("legacy", "Legacy System", "boolean"),
            ],
        },
        {
            "name": "BillingDependency", "label": "Product billed by a billing system",
            "keyFields": ["productId", "billingSystemId"],
            "fields": [
                _ref("productId", "Product ID", "Product", required=True),
                _ref("billingSystemId", "Billing System ID", "BillingSystem", required=True),
                _f("billingCode", "Billing Code"),
                _f("legacyBillingCode", "Legacy Billing Code", "boolean"),
                _f("dependencyStatus", "Dependency Status", "enum", values=["ACTIVE", "DEPRECATED"]),
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
        {"name": "BILLED_BY", "label": "Product billed by billing system", "from": "Product", "to": "BillingSystem",
         "cardinality": "MANY_TO_MANY"},
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


# ---------------------------------------------------------------------------
# Governed Excel templates per evidence domain (user guide: "Recommended Excel template columns").
# ---------------------------------------------------------------------------

def _col(header: str, entity: str | None = None, field: str | None = None, *, required: bool = False,
         description: str = "", example: str = "", **extra) -> dict:
    col: dict = {"header": header}
    if required:
        col["required"] = True
    if entity and field:
        col["target"] = {"entity": entity, "field": field}
    if description:
        col["description"] = description
    if example:
        col["example"] = example
    col.update(extra)
    return col


def _template(domain: str, description: str, columns: list[dict], relationships: list[dict] | None = None) -> dict:
    content = {"description": description, "domain": domain, "canonicalSchema": CANONICAL_KEY,
               "sheetName": "Data", "columns": columns}
    if relationships:
        content["relationships"] = relationships
    return content


def _rel(from_entity: str, from_column: str, to_entity: str, to_column: str, *, name: str | None = None,
         name_column: str | None = None) -> dict:
    rel = {"fromEntity": from_entity, "fromColumn": from_column, "toEntity": to_entity, "toColumn": to_column}
    return rel | ({"name": name} if name else {"nameColumn": name_column})


TEMPLATES: list[tuple[str, str, dict]] = [
    ("TPL-CRM-CATALOG", "CRM Product / Offer Catalog template", _template(
        "CRM_CATALOG", "One row per commercial product (and offer, if any).", [
            _col("Product ID", "Product", "productId", required=True,
                 description="Enterprise Product ID; keep it exactly as in the CRM", example="PRD-1001"),
            _col("Product Name", "Product", "productName", required=True, example="Fibre 500 Home"),
            _col("Product Family", "Product", "productFamily", required=True, example="Broadband"),
            _col("Status", "Product", "status", required=True, lookup="PRODUCT-STATUS", example="Active"),
            _col("Offer ID", "Offer", "offerId", example="OFR-2001"),
            _col("Parent Product", "Product", "parentProductId", description="Product ID of the parent product"),
            _col("Market", "Product", "market", example="UK Consumer"),
            _col("Segment", "Product", "segment", example="Residential"),
            _col("Channel", "Product", "channel", example="Online"),
            _col("Description", "Product", "description"),
        ], [_rel("Offer", "Offer ID", "Product", "Product ID", name="INCLUDES")])),
    ("TPL-CPQ", "CPQ Product Configuration template", _template(
        "CPQ", "One row per configuration rule of a product.", [
            _col("Product ID", "CpqRule", "productId", required=True, example="PRD-1001"),
            _col("Attribute/Option ID", "CpqRule", "attributeOptionId", required=True, example="OPT-SPEED-500"),
            _col("Rule Type", "CpqRule", "ruleType", required=True, lookup="CPQ-RULE-TYPE", example="Dependency"),
            _col("Effective From", "CpqRule", "effectiveFrom", required=True, example="2026-01-01"),
            _col("Required/Optional", "CpqRule", "requirement", example="Required"),
            _col("Dependency", "CpqRule", "dependency"),
            _col("Eligibility", "CpqRule", "eligibility"),
            _col("Cardinality", "CpqRule", "cardinality", example="0..1"),
            _col("Effective To", "CpqRule", "effectiveTo"),
        ])),
    ("TPL-PRICE-BOOK", "Pricing / Price Book template", _template(
        "PRICE_BOOK", "List prices only; negotiated customer prices are not accepted.", [
            _col("Price Book ID", "PriceBookEntry", "priceBookId", required=True, example="PB-UK-2026"),
            _col("Product ID", "PriceBookEntry", "productId", required=True, example="PRD-1001"),
            _col("Currency", "PriceBookEntry", "currency", required=True, example="GBP"),
            _col("List Price", "PriceBookEntry", "listPrice", required=True, example="29.99"),
            _col("Effective From", "PriceBookEntry", "effectiveFrom", required=True, example="2026-01-01"),
            _col("Segment", "PriceBookEntry", "segment"),
            _col("Channel", "PriceBookEntry", "channel"),
            _col("Market", "PriceBookEntry", "market"),
            _col("Effective To", "PriceBookEntry", "effectiveTo"),
            _col("Tax Class", "PriceBookEntry", "taxClass"),
        ])),
    ("TPL-LIFECYCLE", "Product Lifecycle template", _template(
        "LIFECYCLE", "Lifecycle status per product, maintained by Product Management.", [
            _col("Product ID", "ProductLifecycle", "productId", required=True, example="PRD-1001"),
            _col("Lifecycle Status", "ProductLifecycle", "lifecycleStatus", required=True,
                 lookup="LIFECYCLE-STATUS", example="End of Sale"),
            _col("Effective From", "ProductLifecycle", "effectiveFrom", required=True, example="2026-01-01"),
            _col("End-of-Sale Date", "ProductLifecycle", "endOfSaleDate"),
            _col("Sunset Date", "ProductLifecycle", "sunsetDate"),
            _col("Replacement Product ID", "ProductLifecycle", "replacementProductId"),
            _col("Owner", "ProductLifecycle", "owner", description="Accountable product manager (internal)"),
            _col("Rationale", "ProductLifecycle", "rationale"),
        ])),
    ("TPL-TECHNICAL-CATALOG", "Technical Product / Service Catalog template", _template(
        "TECHNICAL_CATALOG", "Commercial product to CFS/RFS decomposition.", [
            _col("Product ID", required=True, example="PRD-1001",
                 description="Commercial product decomposed into this service (relationship endpoint)"),
            _col("Service Spec ID", "ServiceSpec", "serviceSpecId", required=True, example="CFS-FTTP-INTERNET"),
            _col("Service Type", "ServiceSpec", "serviceType", required=True, example="Internet access"),
            _col("Relationship Type", required=True,
                 description="Relationship from the product to the service, e.g. DECOMPOSES_TO",
                 example="DECOMPOSES_TO"),
            _col("CFS/RFS", "ServiceSpec", "specCategory", lookup="SERVICE-SPEC-CATEGORY", example="CFS"),
            _col("Parent Service", "ServiceSpec", "parentServiceSpecId"),
            _col("Resource Spec ID", "ResourceSpec", "resourceSpecId"),
            _col("Effective From", "ServiceSpec", "effectiveFrom"),
            _col("Effective To", "ServiceSpec", "effectiveTo"),
        ], [_rel("Product", "Product ID", "ServiceSpec", "Service Spec ID", name_column="Relationship Type"),
            _rel("ServiceSpec", "Service Spec ID", "ResourceSpec", "Resource Spec ID", name="USES_RESOURCE")])),
    ("TPL-PROVISIONING", "Provisioning & Activation template", _template(
        "PROVISIONING", "Provisioning workflows and rules; give a Product ID or a Service Spec ID on each row.", [
            _col("Product ID", oneOfGroup="subject", example="PRD-1001",
                 description="Product provisioned by this workflow (relationship endpoint)"),
            _col("Service Spec ID", oneOfGroup="subject",
                 description="Service provisioned by this workflow (relationship endpoint)"),
            _col("Workflow ID", "ProvisioningWorkflow", "workflowId", required=True, example="WF-FTTP-ACT"),
            _col("Rule/Action", "ProvisioningWorkflow", "ruleAction", required=True, example="Activate ONT"),
            _col("Target System", "ProvisioningWorkflow", "targetSystem", required=True, example="Orchestrator"),
            _col("Version", "ProvisioningWorkflow", "workflowVersion"),
            _col("Legacy Flag", "ProvisioningWorkflow", "legacyFlag", example="No"),
            _col("Target Compatibility", "ProvisioningWorkflow", "targetCompatibility", example="COMPATIBLE"),
            _col("Exception Code", "ProvisioningWorkflow", "exceptionCode"),
            _col("Effective From", "ProvisioningWorkflow", "effectiveFrom"),
            _col("Effective To", "ProvisioningWorkflow", "effectiveTo"),
        ], [_rel("Product", "Product ID", "ProvisioningWorkflow", "Workflow ID", name="PROVISIONED_BY"),
            _rel("ServiceSpec", "Service Spec ID", "ProvisioningWorkflow", "Workflow ID", name="PROVISIONED_BY")])),
    ("TPL-INVENTORY", "Service / Resource Inventory template", _template(
        "INVENTORY", "Aggregated counts only; no service instances or subscriber identifiers.", [
            _col("Service Instance Aggregate Key", "InventoryAggregate", "aggregateKey", required=True,
                 example="AGG-FTTP-500-UK"),
            _col("Service Spec ID", "InventoryAggregate", "serviceSpecId", required=True),
            _col("Resource Spec ID", "InventoryAggregate", "resourceSpecId", required=True),
            _col("Status", "InventoryAggregate", "status", required=True, example="ACTIVE"),
            _col("Active Count", "InventoryAggregate", "activeCount", example="12450"),
            _col("Variant Count", "InventoryAggregate", "variantCount"),
            _col("Legacy Dependency", "InventoryAggregate", "legacyDependency", example="No"),
            _col("Region", "InventoryAggregate", "region"),
            _col("Last Updated", "InventoryAggregate", "lastUpdated"),
        ])),
    ("TPL-ORDER-BILLING-ASSURANCE", "Order / Billing / Assurance template", _template(
        "ORDER_BILLING_ASSURANCE",
        "One row per product, period and billing system. Aggregates only; the order and assurance figures are "
        "repeated on each billing-system row of the same product and period.", [
            _col("Product ID", "OrderAssuranceAggregate", "productId", required=True, example="PRD-V1",
                 alsoTargets=[{"entity": "BillingDependency", "field": "productId"}]),
            _col("Period Start", "OrderAssuranceAggregate", "periodStart", required=True, example="2026-10-01"),
            _col("Period End", "OrderAssuranceAggregate", "periodEnd", required=True, example="2026-10-31"),
            _col("Orders", "OrderAssuranceAggregate", "orderCount", example="5200"),
            _col("Fallout Orders", "OrderAssuranceAggregate", "falloutOrders", example="437",
                 description="Orders that failed and needed rework"),
            _col("Manual-Touch Orders", "OrderAssuranceAggregate", "manualTouchOrders", example="310"),
            _col("Incidents", "OrderAssuranceAggregate", "incidentCount", example="42"),
            _col("Provisioning Exceptions", "OrderAssuranceAggregate", "provisioningExceptions", example="18"),
            _col("Average Handling Minutes", "OrderAssuranceAggregate", "avgHandlingMinutes", example="12.5"),
            _col("Billing System ID", "BillingSystem", "billingSystemId", required=True, example="BILL-LEGACY",
                 alsoTargets=[{"entity": "BillingDependency", "field": "billingSystemId"}]),
            _col("Billing System Name", "BillingSystem", "billingSystemName", example="Legacy billing"),
            _col("Billing Code", "BillingDependency", "billingCode", example="LB-15GB"),
            _col("Legacy Billing Code", "BillingDependency", "legacyBillingCode", example="Yes"),
        ], [_rel("Product", "Product ID", "BillingSystem", "Billing System ID", name="BILLED_BY")])),
    ("TPL-CUSTOMER-REVENUE", "Customer & Revenue Aggregates template", _template(
        "CUSTOMER_REVENUE_AGGREGATES",
        "One row per product and period. Aggregates only: counts of 1 to 9 subscriptions are rejected "
        "(minimum group size 10); no customer or subscriber identifiers.", [
            _col("Product ID", "CustomerRevenueAggregate", "productId", required=True, example="PRD-V1"),
            _col("Period Start", "CustomerRevenueAggregate", "periodStart", required=True, example="2026-10-01"),
            _col("Period End", "CustomerRevenueAggregate", "periodEnd", required=True, example="2026-10-31"),
            _col("Active Subscriptions", "CustomerRevenueAggregate", "activeSubscriptions", required=True,
                 example="86214"),
            _col("Protected-Price Contracts", "CustomerRevenueAggregate", "protectedPriceContracts",
                 example="4209"),
            _col("MRR", "CustomerRevenueAggregate", "mrr", example="480000.00",
                 description="Monthly recurring revenue of the active base"),
            _col("Currency", "CustomerRevenueAggregate", "currency", example="GBP"),
            _col("Segment", "CustomerRevenueAggregate", "segment"),
            _col("Market", "CustomerRevenueAggregate", "market"),
        ])),
]

# ---------------------------------------------------------------------------
# Reconciliation precedence (Step 6): which source wins each field, which fields are material
# (conflicts need a Data Steward), and how fresh each domain's data must be.
# ---------------------------------------------------------------------------

PRECEDENCE_KEY = "TELCO-PRECEDENCE"
TELCO_PRECEDENCE = {
    "description": "Field-level source precedence: CRM owns product names, CPQ owns configuration, the price "
                   "book owns list prices, PLM owns lifecycle.",
    "canonicalSchema": CANONICAL_KEY,
    "defaultFreshnessDays": 90,
    "freshnessDays": {
        "CRM_CATALOG": 90, "CPQ": 90, "PRICE_BOOK": 30, "LIFECYCLE": 30, "TECHNICAL_CATALOG": 180,
        "PROVISIONING": 180, "INVENTORY": 30, "ORDER_BILLING_ASSURANCE": 30, "CUSTOMER_REVENUE_AGGREGATES": 30,
    },
    "precedence": [
        {"field": "Product.*", "order": ["CRM_CATALOG"]},
        {"field": "Offer.*", "order": ["CRM_CATALOG", "CPQ"]},
        {"field": "CpqRule.*", "order": ["CPQ"]},
        {"field": "PriceBookEntry.*", "order": ["PRICE_BOOK", "CPQ"]},
        {"field": "ProductLifecycle.*", "order": ["LIFECYCLE", "CRM_CATALOG"]},
        {"field": "ServiceSpec.*", "order": ["TECHNICAL_CATALOG"]},
        {"field": "ResourceSpec.*", "order": ["TECHNICAL_CATALOG", "INVENTORY"]},
        {"field": "ProvisioningWorkflow.*", "order": ["PROVISIONING"]},
        {"field": "InventoryAggregate.*", "order": ["INVENTORY"]},
        {"field": "CustomerRevenueAggregate.*", "order": ["CUSTOMER_REVENUE_AGGREGATES"]},
        {"field": "OrderAssuranceAggregate.*", "order": ["ORDER_BILLING_ASSURANCE"]},
        {"field": "BillingSystem.*", "order": ["ORDER_BILLING_ASSURANCE"]},
        {"field": "BillingDependency.*", "order": ["ORDER_BILLING_ASSURANCE"]},
    ],
    "materialFields": [
        "Product.productName", "Product.productFamily", "Product.status",
        "ProductLifecycle.lifecycleStatus", "ProductLifecycle.endOfSaleDate", "ProductLifecycle.sunsetDate",
        "PriceBookEntry.listPrice", "PriceBookEntry.currency",
        "InventoryAggregate.status", "InventoryAggregate.activeCount",
        "CustomerRevenueAggregate.activeSubscriptions", "CustomerRevenueAggregate.protectedPriceContracts",
        "CustomerRevenueAggregate.mrr", "CustomerRevenueAggregate.currency",
        "BillingDependency.legacyBillingCode", "ProvisioningWorkflow.targetCompatibility",
    ],
}
