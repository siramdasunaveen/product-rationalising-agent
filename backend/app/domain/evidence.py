"""Evidence domains, source types and upload/source states (screen 03 Evidence Hub).

Pure rules only. The proposed domains for a run follow a fixed table (no AI): the product catalog and
lifecycle are always needed; each business objective adds the domains it depends on.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.runs import Objective


class EvidenceDomain(StrEnum):
    CRM_CATALOG = "CRM_CATALOG"
    CPQ = "CPQ"
    PRICE_BOOK = "PRICE_BOOK"
    LIFECYCLE = "LIFECYCLE"
    TECHNICAL_CATALOG = "TECHNICAL_CATALOG"
    PROVISIONING = "PROVISIONING"
    INVENTORY = "INVENTORY"
    # The field spec also names ORDER_BILLING_ASSURANCE; it is added once its template is specified
    # (DECISIONS.md, Step 4).


@dataclass(frozen=True)
class DomainInfo:
    key: EvidenceDomain
    label: str
    live_system: str  # short label of the typical live system (screen chip)
    typical_systems: str
    canonical_entities: tuple[str, ...]
    template_key: str


D = EvidenceDomain

DOMAINS: dict[EvidenceDomain, DomainInfo] = {
    D.CRM_CATALOG: DomainInfo(D.CRM_CATALOG, "CRM Product / Offer Catalog", "CRM",
                              "Salesforce, Microsoft Dynamics 365, Oracle CX or another CRM",
                              ("Product", "Offer"), "TPL-CRM-CATALOG"),
    D.CPQ: DomainInfo(D.CPQ, "CPQ Product Configuration", "CPQ",
                      "Salesforce Revenue Cloud, Oracle CPQ, SAP CPQ or another CPQ",
                      ("CpqRule",), "TPL-CPQ"),
    D.PRICE_BOOK: DomainInfo(D.PRICE_BOOK, "Pricing / Price Book Metadata", "ERP",
                             "CRM price books, CPQ, ERP or pricing engine",
                             ("PriceBookEntry",), "TPL-PRICE-BOOK"),
    D.LIFECYCLE: DomainInfo(D.LIFECYCLE, "Product Lifecycle / Status", "PLM",
                            "PLM, CRM, ERP or portfolio repository",
                            ("ProductLifecycle",), "TPL-LIFECYCLE"),
    D.TECHNICAL_CATALOG: DomainInfo(D.TECHNICAL_CATALOG, "Technical Product / Service Catalog", "OSS/BSS",
                                    "OSS/BSS service catalog or catalog management platform",
                                    ("ServiceSpec", "ResourceSpec"), "TPL-TECHNICAL-CATALOG"),
    D.PROVISIONING: DomainInfo(D.PROVISIONING, "Provisioning & Activation", "Orchestrator",
                               "Activation engine, service orchestrator or provisioning platform",
                               ("ProvisioningWorkflow",), "TPL-PROVISIONING"),
    D.INVENTORY: DomainInfo(D.INVENTORY, "Service / Resource Inventory", "Inventory",
                            "Service inventory, resource inventory, network inventory or data platform",
                            ("InventoryAggregate",), "TPL-INVENTORY"),
}

ALWAYS_PROPOSED = (D.CRM_CATALOG, D.LIFECYCLE)

O = Objective
OBJECTIVE_DOMAINS: dict[Objective, tuple[EvidenceDomain, ...]] = {
    O.REDUCE_DUPLICATES: (D.CPQ, D.PRICE_BOOK, D.TECHNICAL_CATALOG),
    O.BSS_SUNSET: (D.TECHNICAL_CATALOG, D.PROVISIONING, D.INVENTORY),
    O.REDUCE_FALLOUT: (D.PROVISIONING, D.INVENTORY),
    O.IMPROVE_MARGIN: (D.PRICE_BOOK, D.CPQ),
    O.MIGRATION_READINESS: (D.TECHNICAL_CATALOG, D.PROVISIONING, D.INVENTORY),
    O.PORTFOLIO_STANDARDISATION: (D.CPQ, D.TECHNICAL_CATALOG),
}


def propose_domains(objectives: list[str]) -> list[EvidenceDomain]:
    """Domains proposed for a run, in screen order. Unknown objectives are ignored."""
    wanted = set(ALWAYS_PROPOSED)
    for o in objectives:
        try:
            wanted.update(OBJECTIVE_DOMAINS[Objective(o)])
        except ValueError:
            continue
    return [d for d in DOMAINS if d in wanted]


def proposal_reasons(objectives: list[str]) -> dict[EvidenceDomain, list[str]]:
    """Why each domain is proposed (deterministic trace shown on the screen)."""
    reasons: dict[EvidenceDomain, list[str]] = {d: ["Always needed"] for d in ALWAYS_PROPOSED}
    for o in objectives:
        try:
            obj = Objective(o)
        except ValueError:
            continue
        for d in OBJECTIVE_DOMAINS[obj]:
            reasons.setdefault(d, []).append(f"Objective {obj.value}")
    return reasons


class SourceType(StrEnum):
    API_CONNECTOR = "API_CONNECTOR"
    SECURE_QUERY = "SECURE_QUERY"
    SCHEDULED_EXTRACT = "SCHEDULED_EXTRACT"
    EXCEL_UPLOAD = "EXCEL_UPLOAD"


# Source types that can be used now. Live connectors arrive in BUILD_PLAN step 14.
AVAILABLE_SOURCE_TYPES = frozenset({SourceType.EXCEL_UPLOAD})


class UploadStatus(StrEnum):
    PROCESSING = "PROCESSING"  # quick checks passed; in quarantine; scan + discovery running
    READY = "READY"  # clean and readable; sheets discovered; waiting for / has a confirmed schema
    REJECTED = "REJECTED"  # failed a check; the file has been deleted, the record and reasons remain


class DomainStatus(StrEnum):
    """Ingest status of a domain card (not the evidence completeness of Step 7)."""

    NO_SOURCE = "NO_SOURCE"
    CHECKING = "CHECKING"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"  # a file is ready; sheet/header not confirmed yet
    READY = "READY"  # at least one confirmed source schema
    REJECTED = "REJECTED"  # only rejected files so far


def domain_status(upload_statuses: list[tuple[str, bool]]) -> DomainStatus:
    """``upload_statuses``: (status, has_confirmed_schema) per upload of the domain."""
    if not upload_statuses:
        return DomainStatus.NO_SOURCE
    if any(s == UploadStatus.READY and confirmed for s, confirmed in upload_statuses):
        return DomainStatus.READY
    if any(s == UploadStatus.PROCESSING for s, _ in upload_statuses):
        return DomainStatus.CHECKING
    if any(s == UploadStatus.READY for s, _ in upload_statuses):
        return DomainStatus.NEEDS_CONFIRMATION
    return DomainStatus.REJECTED
