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
}

# Approval scopes: which approval steps an approver may act on.
APPROVAL_SCOPES = ("commercial", "product", "architecture")

P = Permission

# persona key -> (display name, permissions, approval scopes)
PERSONAS: dict[str, tuple[str, frozenset[Permission], tuple[str, ...]]] = {
    "portfolio_manager": (
        "Portfolio Manager",
        frozenset({P.RUN_CREATE, P.RUN_START, P.CANDIDATE_VIEW, P.DECISION_SUBMIT}),
        (),
    ),
    "product_owner": ("Product Owner", frozenset({P.CANDIDATE_VIEW, P.DECISION_SUBMIT}), ()),
    "data_steward": (
        "Data Steward",
        frozenset({P.EVIDENCE_UPLOAD, P.EVIDENCE_MAP, P.SNAPSHOT_APPROVE, P.CONFLICT_RESOLVE}),
        (),
    ),
    "evidence_owner": ("Evidence Owner", frozenset({P.REQUEST_FULFIL}), ()),
    "commercial_approver": ("Commercial Approver", frozenset({P.APPROVAL_ACT}), ("commercial",)),
    "product_approver": ("Product Approver", frozenset({P.APPROVAL_ACT}), ("product",)),
    "architecture_approver": (
        "Architecture Approver",
        frozenset({P.APPROVAL_ACT}),
        ("architecture",),
    ),
    "enterprise_architect": (
        "Enterprise Architect",
        frozenset({P.CANDIDATE_VIEW, P.APPROVAL_ACT}),
        ("architecture",),
    ),
    "agent_admin": (
        "Agent Admin",
        frozenset({P.CONFIG_SCORING, P.CONFIG_POLICY, P.CONFIG_AI, P.CONNECTOR_MANAGE}),
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
