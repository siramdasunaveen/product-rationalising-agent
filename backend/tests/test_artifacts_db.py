"""Immutability is enforced by the database itself (migration 0004), not only by the service.

These tests bypass the service and use raw SQL, as a buggy job or a manual fix would.
"""

import itertools
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import Artifact, ArtifactVersion
from app.domain.artifacts import ArtifactStatus, can_transition, compute_digest

S = ArtifactStatus
T = "rationalisation.artifact_version"
PATH_TO = {  # valid way to reach each status
    S.DRAFT: [],
    S.PUBLISHED: [S.PUBLISHED],
    S.DEPRECATED: [S.PUBLISHED, S.DEPRECATED],
    S.SUPERSEDED: [S.PUBLISHED, S.SUPERSEDED],
    S.RETIRED: [S.PUBLISHED, S.RETIRED],
}


def new_version(session_factory, tenant: str) -> uuid.UUID:
    with session_factory() as s:
        key = f"K-{uuid.uuid4().hex[:8].upper()}"
        a = Artifact(tenant_id=tenant, kind="LOOKUP", key=key, name="Test", created_by="u1")
        s.add(a)
        s.flush()
        content = {"x": 1}
        v = ArtifactVersion(tenant_id=tenant, artifact_id=a.id, kind="LOOKUP", version_major=1, version_minor=0,
                            status="DRAFT", content=content, digest=compute_digest("LOOKUP", key, content),
                            created_by="u1", lock_version=1)
        s.add(v)
        s.commit()
        return v.id


def set_status(session_factory, vid: uuid.UUID, status: str) -> None:
    with session_factory() as s:
        s.execute(text(f"UPDATE {T} SET status = :s, published_at = coalesce(published_at, now()), "
                       "published_by = coalesce(published_by, 'u1') WHERE id = :id"), {"s": status, "id": vid})
        s.commit()


def reach(session_factory, tenant: str, status: S) -> uuid.UUID:
    vid = new_version(session_factory, tenant)
    for step in PATH_TO[status]:
        set_status(session_factory, vid, step)
    return vid


def run_sql(session_factory, sql: str, **params) -> None:
    with session_factory() as s:
        s.execute(text(sql), params)
        s.commit()


@pytest.mark.parametrize(("current", "target"),
                         [(a, b) for a, b in itertools.product(S, S) if a != b])
def test_trigger_transitions_match_domain(session_factory, tenant, current, target):
    vid = reach(session_factory, tenant, current)
    if can_transition(current, target):
        set_status(session_factory, vid, target)
    else:
        with pytest.raises(DBAPIError, match="cannot change from"):
            set_status(session_factory, vid, target)


def test_new_versions_must_start_as_draft(session_factory, tenant):
    vid = new_version(session_factory, tenant)
    with pytest.raises(DBAPIError, match="must start as DRAFT"):
        run_sql(session_factory,
                f"INSERT INTO {T} (id, tenant_id, created_at, artifact_id, kind, version_major, version_minor, "
                "status, content, digest, created_by, updated_at, lock_version, published_at, published_by) "
                f"SELECT gen_random_uuid(), tenant_id, now(), artifact_id, kind, 2, 0, 'PUBLISHED', content, "
                f"digest, created_by, now(), 1, now(), 'u1' FROM {T} WHERE id = :id", id=vid)


def test_draft_content_can_change(session_factory, tenant):
    vid = new_version(session_factory, tenant)
    run_sql(session_factory, f"UPDATE {T} SET content = '{{\"x\": 2}}' WHERE id = :id", id=vid)


@pytest.mark.parametrize("status", [S.PUBLISHED, S.DEPRECATED, S.SUPERSEDED, S.RETIRED])
@pytest.mark.parametrize("assignment", [
    "content = '{\"x\": 2}'",
    "digest = 'sha256:' || repeat('0', 64)",
    "valid_to = DATE '2030-01-01'",
    "change_note = 'edited'",
    "published_by = 'someone-else'",
])
def test_non_draft_versions_are_immutable(session_factory, tenant, status, assignment):
    vid = reach(session_factory, tenant, status)
    with pytest.raises(DBAPIError, match="immutable"):
        run_sql(session_factory, f"UPDATE {T} SET {assignment} WHERE id = :id", id=vid)


@pytest.mark.parametrize("assignment", ["version_minor = 9", "artifact_id = gen_random_uuid()",
                                        "kind = 'TRANSFORM'", "tenant_id = 'other'"])
def test_identity_never_changes_even_for_drafts(session_factory, tenant, assignment):
    vid = new_version(session_factory, tenant)
    with pytest.raises(DBAPIError, match="identity"):
        run_sql(session_factory, f"UPDATE {T} SET {assignment} WHERE id = :id", id=vid)


def test_only_drafts_can_be_deleted(session_factory, tenant):
    draft = new_version(session_factory, tenant)
    run_sql(session_factory, f"DELETE FROM {T} WHERE id = :id", id=draft)
    published = reach(session_factory, tenant, S.PUBLISHED)
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        run_sql(session_factory, f"DELETE FROM {T} WHERE id = :id", id=published)


def test_truncate_is_blocked(session_factory):
    with pytest.raises(DBAPIError, match="cannot be truncated"):
        run_sql(session_factory, f"TRUNCATE {T} CASCADE")


def test_publishing_needs_publisher(session_factory, tenant):
    vid = new_version(session_factory, tenant)
    with pytest.raises(DBAPIError, match="published_at and published_by"):
        run_sql(session_factory, f"UPDATE {T} SET status = 'PUBLISHED' WHERE id = :id", id=vid)


def test_one_published_and_one_draft_per_artifact(session_factory, tenant):
    vid = reach(session_factory, tenant, S.PUBLISHED)
    copy = (f"INSERT INTO {T} (id, tenant_id, created_at, artifact_id, kind, version_major, version_minor, "
            "status, content, digest, created_by, updated_at, lock_version) "
            f"SELECT gen_random_uuid(), tenant_id, now(), artifact_id, kind, 1, :minor, 'DRAFT', content, "
            f"digest, created_by, now(), 1 FROM {T} WHERE id = :id RETURNING id")
    with session_factory() as s:
        second = s.execute(text(copy), {"id": vid, "minor": 1}).scalar_one()
        s.commit()
    with pytest.raises(IntegrityError, match="uq_artifact_version_draft"):
        run_sql(session_factory, copy, id=vid, minor=2)
    with pytest.raises(IntegrityError, match="uq_artifact_version_published"):
        set_status(session_factory, second, "PUBLISHED")


def test_artifact_key_and_kind_never_change(session_factory, tenant):
    vid = new_version(session_factory, tenant)
    with session_factory() as s:
        aid = s.get(ArtifactVersion, vid).artifact_id
    run_sql(session_factory, "UPDATE rationalisation.artifact SET name = 'Renamed' WHERE id = :id", id=aid)
    for assignment in ("key = 'OTHER-KEY'", "kind = 'TRANSFORM'"):
        with pytest.raises(DBAPIError, match="cannot change"):
            run_sql(session_factory, f"UPDATE rationalisation.artifact SET {assignment} WHERE id = :id", id=aid)


def test_digest_format_is_checked(session_factory, tenant):
    vid = new_version(session_factory, tenant)
    with pytest.raises(IntegrityError, match="ck_artifact_version_digest"):
        run_sql(session_factory, f"UPDATE {T} SET digest = 'md5:abc' WHERE id = :id", id=vid)
