"""Versioned, immutable, digest-identified artifacts (BUILD_PLAN step 3).

One pattern for every governed configuration item: canonical schema, lookup, transform, crosswalk now;
mappings, scoring profiles, policies, prompts and model routes later.

- An artifact has a key (``TELCO-CANONICAL``) and versions ``major.minor`` (``TELCO-CANONICAL@1.0``).
- Lifecycle: DRAFT -> PUBLISHED -> DEPRECATED / SUPERSEDED / RETIRED. Only DRAFT content can change.
- A change is a new version (clone -> draft -> publish). Publishing supersedes the previous PUBLISHED one.
- Only a PUBLISHED version inside its validity window is selectable for new runs; runs pin exact
  versions + digests, so SUPERSEDED versions stay readable for them.
- The digest is SHA-256 over a canonical JSON form, so equal content always gives the same digest.

Pure functions only; the database enforces the same rules with triggers (migration 0004).
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


class ArtifactKind(StrEnum):
    CANONICAL_SCHEMA = "CANONICAL_SCHEMA"
    LOOKUP = "LOOKUP"
    TRANSFORM = "TRANSFORM"
    CROSSWALK = "CROSSWALK"
    EVIDENCE_TEMPLATE = "EVIDENCE_TEMPLATE"  # governed Excel template of an evidence domain


KIND_LABELS: dict[ArtifactKind, str] = {
    ArtifactKind.CANONICAL_SCHEMA: "Canonical schema",
    ArtifactKind.LOOKUP: "Lookup",
    ArtifactKind.TRANSFORM: "Transform",
    ArtifactKind.CROSSWALK: "Crosswalk",
    ArtifactKind.EVIDENCE_TEMPLATE: "Evidence template",
}


class ArtifactStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"  # the one version new runs may select
    DEPRECATED = "DEPRECATED"  # no longer selectable; pinned runs keep using it
    SUPERSEDED = "SUPERSEDED"  # replaced by a newer published version; pinned runs only
    RETIRED = "RETIRED"  # final; not to be used at all


S = ArtifactStatus

# Allowed status changes. Keep in sync with the trigger in migration 0004 (a test checks both).
TRANSITIONS: dict[ArtifactStatus, frozenset[ArtifactStatus]] = {
    S.DRAFT: frozenset({S.PUBLISHED}),
    S.PUBLISHED: frozenset({S.DEPRECATED, S.SUPERSEDED, S.RETIRED}),
    S.DEPRECATED: frozenset({S.SUPERSEDED, S.RETIRED}),
    S.SUPERSEDED: frozenset({S.RETIRED}),
    S.RETIRED: frozenset(),
}

# Statuses that need a reason when a person sets them.
REASON_REQUIRED = frozenset({S.DEPRECATED, S.RETIRED})


def can_transition(current: ArtifactStatus | str, target: ArtifactStatus | str) -> bool:
    return ArtifactStatus(target) in TRANSITIONS[ArtifactStatus(current)]


def is_selectable(status: ArtifactStatus | str, valid_from: date | None, valid_to: date | None,
                  on: date) -> bool:
    """May a new run select this version on day ``on``? PUBLISHED and inside its validity window."""
    if ArtifactStatus(status) is not S.PUBLISHED:
        return False
    if valid_from and on < valid_from:
        return False
    return not (valid_to and on > valid_to)


# ---------------------------------------------------------------------------
# Keys, versions, references
# ---------------------------------------------------------------------------

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*$")
KEY_MIN, KEY_MAX = 3, 64
VERSION_RE = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")
REF_RE = re.compile(r"^(?P<key>[A-Z0-9_-]+)@(?P<version>\d+\.\d+)$")


def key_error(key: str) -> str | None:
    if not KEY_MIN <= len(key) <= KEY_MAX:
        return f"Key must be {KEY_MIN}–{KEY_MAX} characters"
    if not KEY_RE.match(key):
        return "Key must be upper-case letters and digits, separated by single '-' or '_' (e.g. TELCO-CANONICAL)"
    return None


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    @classmethod
    def parse(cls, text: str) -> "Version":
        m = VERSION_RE.match(text.strip())
        if not m:
            raise ValueError(f"Not a version (expected major.minor, e.g. 1.0): {text!r}")
        return cls(int(m.group(1)), int(m.group(2)))


FIRST_VERSION = Version(1, 0)


class Bump(StrEnum):
    MINOR = "minor"  # compatible change (new optional field, new lookup entry)
    MAJOR = "major"  # breaking change (removed/renamed field, changed meaning)


def next_version(highest: Version, bump: Bump | str) -> Version:
    """New versions always follow the highest existing version, so numbers never collide."""
    if Bump(bump) is Bump.MAJOR:
        return Version(highest.major + 1, 0)
    return Version(highest.major, highest.minor + 1)


def format_ref(key: str, version: Version) -> str:
    return f"{key}@{version}"


def parse_ref(ref: str) -> tuple[str, Version | None]:
    """``KEY@1.0`` -> (key, Version); ``KEY`` -> (key, None)."""
    ref = ref.strip()
    if "@" not in ref:
        return ref, None
    m = REF_RE.match(ref)
    if not m:
        raise ValueError(f"Not an artifact reference (expected KEY@major.minor): {ref!r}")
    return m.group("key"), Version.parse(m.group("version"))


# ---------------------------------------------------------------------------
# Canonical JSON and digest
# ---------------------------------------------------------------------------

MAX_SAFE_INT = 2**53 - 1
DIGEST_ALGORITHM = "sha256"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CanonicalJsonError(ValueError):
    pass


def _check_values(value: Any, path: str) -> None:
    """Only strings, integers, booleans, null, arrays and objects with string keys.

    Fractional numbers are rejected: their text form is not stable across JSON/JSONB round trips.
    Write decimals as strings (e.g. ``"12.50"``).
    """
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            raise CanonicalJsonError(f"{path or 'content'}: integer out of range")
        return
    if isinstance(value, float):
        raise CanonicalJsonError(f"{path or 'content'}: fractional numbers are not allowed; use a string")
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check_values(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, item in value.items():
            if not isinstance(k, str):
                raise CanonicalJsonError(f"{path or 'content'}: object keys must be strings")
            _check_values(item, f"{path}.{k}" if path else k)
        return
    raise CanonicalJsonError(f"{path or 'content'}: unsupported value type {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no insignificant whitespace, UTF-8. Same data -> same bytes."""
    _check_values(value, "")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def compute_digest(kind: ArtifactKind | str, key: str, content: Any) -> str:
    """``sha256:<hex>`` over the canonical JSON of kind, key and content.

    The version number is not part of the digest: a version whose content equals another one's has
    the same digest, which makes "nothing actually changed" visible.
    """
    body = canonical_json({"kind": str(ArtifactKind(kind)), "key": key, "content": content})
    return f"{DIGEST_ALGORITHM}:{hashlib.sha256(body).hexdigest()}"
