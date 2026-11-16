"""Permissions and the proposed persona -> permission mapping (BUILD_PLAN step 1).

The UI must decide what to show from permissions, never from role names. In studio mode the
platform maps its own roles to these permissions (declared in manifest.json); in standalone mode
the dev personas below are used.
"""

from enum import StrEnum


class Permission(StrEnum):
    READ = "read"
    RUN_CREATE = "run.create"
    RUN_START = "run.start"
    CANDIDATE_VIEW = "candidate.view"
    DECISION_SUBMIT = "decision.submit"
    EVIDENCE_UPLOAD = "evidence.upload"
    EVIDENCE_MAP = "evidence.map"
    SNAPSHOT_APPROVE = "snapshot.approve"
    CONFLICT_RESOLVE = "conflict.resolve"
    REQUEST_FULFIL = "request.fulfil"
    APPROVAL_ACT = "approval.act"
    CONFIG_SCORING = "config.scoring"
    CONFIG_POLICY = "config.policy"
    CONFIG_AI = "config.ai"
    CONNECTOR_MANAGE = "connector.manage"
    REGISTRY_READ = "registry.read"
    REGISTRY_MANAGE = "registry.manage"
    REGISTRY_PUBLISH = "registry.publish"
    MAPPING_APPROVE = "mapping.approve"
    MAPPING_PUBLISH = "mapping.publish"


PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.READ: "View runs, evidence, candidates, decisions and approvals",
    Permission.RUN_CREATE: "Create and edit rationalisation runs",
    Permission.RUN_START: "Lock configuration and start an analysis run",
    Permission.CANDIDATE_VIEW: "View candidates and their evidence",
    Permission.DECISION_SUBMIT: "Submit a decision for approval",
    Permission.EVIDENCE_UPLOAD: "Upload evidence files",
    Permission.EVIDENCE_MAP: "Map and validate evidence",
    Permission.SNAPSHOT_APPROVE: "Approve evidence snapshots",
    Permission.CONFLICT_RESOLVE: "Resolve reconciliation conflicts",
    Permission.REQUEST_FULFIL: "Fulfil evidence requests",
    Permission.APPROVAL_ACT: "Approve, reject, return or hold decisions",
    Permission.CONFIG_SCORING: "Manage scoring profiles",
    Permission.CONFIG_POLICY: "Manage policy profiles",
    Permission.CONFIG_AI: "Manage the AI runtime and model routing",
    Permission.CONNECTOR_MANAGE: "Manage source connectors",
    Permission.REGISTRY_READ: "View registry artifacts (canonical schema, lookups, transforms, crosswalks)",
    Permission.REGISTRY_MANAGE: "Create, edit and clone draft registry artifacts",
    Permission.REGISTRY_PUBLISH: "Publish, deprecate and retire registry artifacts",
    Permission.MAPPING_APPROVE: "Approve, return or reject submitted mapping versions (not your own)",
    Permission.MAPPING_PUBLISH: "Publish approved mapping versions",
}

# Approval scopes: which approval steps an approver may act on.
APPROVAL_SCOPES = ("commercial", "product", "architecture")

P = Permission

RR = P.REGISTRY_READ  # every persona except Viewer may read the registries

# persona key -> (display name, permissions, approval scopes)
PERSONAS: dict[str, tuple[str, frozenset[Permission], tuple[str, ...]]] = {
    "portfolio_manager": (
        "Portfolio Manager",
        frozenset({P.RUN_CREATE, P.RUN_START, P.CANDIDATE_VIEW, P.DECISION_SUBMIT, RR, P.EVIDENCE_UPLOAD}),
        (),
    ),
    "product_owner": ("Product Owner", frozenset({P.CANDIDATE_VIEW, P.DECISION_SUBMIT, RR}), ()),
    "data_steward": (
        "Data Steward",
        frozenset({P.EVIDENCE_UPLOAD, P.EVIDENCE_MAP, P.SNAPSHOT_APPROVE, P.CONFLICT_RESOLVE, RR,
                   P.REGISTRY_MANAGE, P.MAPPING_APPROVE}),
        (),
    ),
    "evidence_owner": ("Evidence Owner", frozenset({P.REQUEST_FULFIL, RR}), ()),
    "commercial_approver": ("Commercial Approver", frozenset({P.APPROVAL_ACT, RR}), ("commercial",)),
    "product_approver": ("Product Approver", frozenset({P.APPROVAL_ACT, RR}), ("product",)),
    "architecture_approver": (
        "Architecture Approver",
        frozenset({P.APPROVAL_ACT, RR}),
        ("architecture",),
    ),
    "enterprise_architect": (
        "Enterprise Architect",
        frozenset({P.CANDIDATE_VIEW, P.APPROVAL_ACT, RR}),
        ("architecture",),
    ),
    "agent_admin": (
        "Agent Admin",
        frozenset({P.CONFIG_SCORING, P.CONFIG_POLICY, P.CONFIG_AI, P.CONNECTOR_MANAGE, RR,
                   P.REGISTRY_MANAGE, P.REGISTRY_PUBLISH, P.MAPPING_APPROVE, P.MAPPING_PUBLISH}),
        (),
    ),
    "viewer": ("Viewer", frozenset(), ()),
}


def permissions_for_roles(roles: list[str] | tuple[str, ...]) -> frozenset[Permission]:
    """Union of permissions for the given persona keys. Every known persona gets ``read``.

    Unknown roles grant nothing (fail closed).
    """
    perms: set[Permission] = set()
    for role in roles:
        persona = PERSONAS.get(role)
        if persona is None:
            continue
        perms |= persona[1]
        perms.add(Permission.READ)
    return frozenset(perms)


def approval_scopes_for_roles(roles: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    scopes: list[str] = []
    for role in roles:
        persona = PERSONAS.get(role)
        if persona:
            scopes.extend(s for s in persona[2] if s not in scopes)
    return tuple(scopes)
