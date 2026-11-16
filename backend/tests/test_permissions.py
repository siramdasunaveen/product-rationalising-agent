import json
from pathlib import Path

from app.domain.permissions import (
    PERSONAS,
    Permission,
    approval_scopes_for_roles,
    permissions_for_roles,
)

P = Permission
MANIFEST = Path(__file__).resolve().parents[2] / "manifest.json"


def test_portfolio_manager_permissions():
    assert permissions_for_roles(["portfolio_manager"]) == {
        P.READ, P.RUN_CREATE, P.RUN_START, P.CANDIDATE_VIEW, P.DECISION_SUBMIT, P.REGISTRY_READ,
        P.EVIDENCE_UPLOAD,
    }


def test_registry_permissions():
    everyone_but_viewer = [k for k in PERSONAS if k != "viewer"]
    for persona in everyone_but_viewer:
        assert P.REGISTRY_READ in permissions_for_roles([persona]), persona
    managers = {k for k in PERSONAS if P.REGISTRY_MANAGE in permissions_for_roles([k])}
    publishers = {k for k in PERSONAS if P.REGISTRY_PUBLISH in permissions_for_roles([k])}
    assert managers == {"data_steward", "agent_admin"}
    assert publishers == {"agent_admin"}
    approvers = {k for k in PERSONAS if P.MAPPING_APPROVE in permissions_for_roles([k])}
    assert approvers == {"data_steward", "agent_admin"}
    assert {k for k in PERSONAS if P.MAPPING_PUBLISH in permissions_for_roles([k])} == {"agent_admin"}


def test_data_steward_cannot_submit_decisions_or_approve():
    perms = permissions_for_roles(["data_steward"])
    assert P.SNAPSHOT_APPROVE in perms
    assert P.DECISION_SUBMIT not in perms
    assert P.APPROVAL_ACT not in perms


def test_viewer_is_read_only():
    assert permissions_for_roles(["viewer"]) == {P.READ}


def test_unknown_roles_grant_nothing():
    assert permissions_for_roles(["superuser", "admin"]) == frozenset()


def test_multiple_roles_are_combined():
    perms = permissions_for_roles(["product_owner", "evidence_owner"])
    assert {P.DECISION_SUBMIT, P.REQUEST_FULFIL, P.READ} <= perms


def test_approval_scopes():
    assert approval_scopes_for_roles(["commercial_approver"]) == ("commercial",)
    assert approval_scopes_for_roles(["enterprise_architect"]) == ("architecture",)
    assert approval_scopes_for_roles(["portfolio_manager"]) == ()


def test_manifest_matches_domain_permissions():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert {p["key"] for p in manifest["permissions"]} == {p.value for p in Permission}
    for persona, perms in manifest["suggestedRoles"].items():
        assert persona in PERSONAS
        assert set(perms) == {p.value for p in permissions_for_roles([persona])}
    assert set(manifest["suggestedRoles"]) == set(PERSONAS)
