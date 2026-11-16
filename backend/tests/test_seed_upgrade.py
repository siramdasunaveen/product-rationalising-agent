"""Seed upgrades: changed seed content is published as a new minor version, but only for artifacts the seed
itself published and nobody has touched since."""

import copy

from app.domain.artifacts import ArtifactKind, Version
from app.services import artifact_seed
from app.services.artifacts import resolve


def test_seed_upgrades_only_its_own_versions(session_factory, platform, tenant, monkeypatch):
    with session_factory() as s:
        artifact_seed.seed_artifacts(s, platform.audit, tenant)
    items = artifact_seed.seed_items()
    changed = []
    for kind, key, name, content in items:
        if key == "TPL-CPQ":
            content = copy.deepcopy(content)
            content["description"] = "Updated by a newer seed"
        changed.append((kind, key, name, content))
    monkeypatch.setattr(artifact_seed, "seed_items", lambda: changed)
    with session_factory() as s:
        result = artifact_seed.seed_artifacts(s, platform.audit, tenant)
    assert result["upgraded"] == ["TPL-CPQ@1.1"] and result["created"] == []
    with session_factory() as s:
        _, v = resolve(s, tenant, "TPL-CPQ", kind=ArtifactKind.EVIDENCE_TEMPLATE)
        assert v.content["description"] == "Updated by a newer seed"
        _, old = resolve(s, tenant, "TPL-CPQ", Version(1, 0))
        assert old.status == "SUPERSEDED"
    with session_factory() as s:  # running again changes nothing
        assert artifact_seed.seed_artifacts(s, platform.audit, tenant)["upgraded"] == []


def test_seed_never_overrides_a_person(session_factory, platform, tenant, monkeypatch):
    from app.domain.artifacts import Bump
    from app.ports.auth import Principal
    from app.services.artifacts import clone_version, publish, update_draft

    with session_factory() as s:
        artifact_seed.seed_artifacts(s, platform.audit, tenant)
    admin = Principal(user_id="admin-1", tenant_id=tenant, name="Admin", roles=("agent_admin",),
                      permissions=frozenset())
    with session_factory() as s:
        a, _ = resolve(s, tenant, "TPL-LIFECYCLE")
        _, d = clone_version(s, platform.audit, admin, ArtifactKind.EVIDENCE_TEMPLATE, a.id, Version(1, 0), Bump.MINOR)
        content = copy.deepcopy(d.content)
        content["description"] = "Customer specific"
        _, d = update_draft(s, platform.audit, admin, ArtifactKind.EVIDENCE_TEMPLATE, a.id, Version(1, 1),
                            {"content": content}, d.lock_version)
        publish(s, platform.audit, admin, ArtifactKind.EVIDENCE_TEMPLATE, a.id, Version(1, 1), d.lock_version)
    changed = [(k, key, n, {**c, "description": "seed v2"} if key == "TPL-LIFECYCLE" else c)
               for k, key, n, c in artifact_seed.seed_items()]
    monkeypatch.setattr(artifact_seed, "seed_items", lambda: changed)
    with session_factory() as s:
        assert artifact_seed.seed_artifacts(s, platform.audit, tenant)["upgraded"] == []
    with session_factory() as s:
        assert resolve(s, tenant, "TPL-LIFECYCLE")[1].content["description"] == "Customer specific"
