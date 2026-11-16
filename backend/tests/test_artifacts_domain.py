import hashlib
from datetime import date

import pytest

from app.domain.artifacts import (
    TRANSITIONS,
    ArtifactKind,
    ArtifactStatus,
    Bump,
    CanonicalJsonError,
    Version,
    can_transition,
    canonical_json,
    compute_digest,
    format_ref,
    is_selectable,
    key_error,
    next_version,
    parse_ref,
)

S = ArtifactStatus
CONTENT = {"b": [1, "x", None, True], "a": {"z": "é", "y": 2}}


# ---- canonical JSON + digest ----

def test_canonical_json_is_sorted_compact_utf8():
    assert canonical_json(CONTENT) == '{"a":{"y":2,"z":"é"},"b":[1,"x",null,true]}'.encode()


def test_digest_is_pinned_to_the_documented_algorithm():
    body = '{"content":{"a":{"y":2,"z":"é"},"b":[1,"x",null,true]},"key":"DEMO-KEY","kind":"LOOKUP"}'
    expected = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert compute_digest(ArtifactKind.LOOKUP, "DEMO-KEY", CONTENT) == expected
    # Golden value: changing it breaks every pinned run, so it must never change silently.
    assert expected == "sha256:dda27e64b20d79885ed6f1f35087ee7febe9e630a772303767ca4d2eb865b006"


def test_digest_ignores_key_order_but_not_content():
    reordered = {"a": {"y": 2, "z": "é"}, "b": [1, "x", None, True]}
    d = compute_digest("LOOKUP", "K-1", CONTENT)
    assert compute_digest("LOOKUP", "K-1", reordered) == d
    assert compute_digest("LOOKUP", "K-1", {**CONTENT, "b": [1, "x", None, False]}) != d
    assert compute_digest("LOOKUP", "K-1", {**CONTENT, "b": ["x", 1, None, True]}) != d  # list order matters
    assert compute_digest("LOOKUP", "K-2", CONTENT) != d
    assert compute_digest("TRANSFORM", "K-1", CONTENT) != d


@pytest.mark.parametrize("bad", [{"price": 1.5}, {"n": 2**60}, {"x": {1, 2}}, {"nan": float("nan")}])
def test_canonical_json_rejects_unstable_values(bad):
    with pytest.raises(CanonicalJsonError):
        canonical_json(bad)


# ---- keys, versions, refs ----

@pytest.mark.parametrize("key", ["TELCO-CANONICAL", "LIFECYCLE_STATUS", "ABC", "X1-Y2"])
def test_valid_keys(key):
    assert key_error(key) is None


@pytest.mark.parametrize("key", ["ab", "telco", "TELCO--X", "-ABC", "ABC-", "1ABC", "A B C", "X" * 65])
def test_invalid_keys(key):
    assert key_error(key)


def test_version_parse_and_order():
    assert Version.parse("1.0") == Version(1, 0)
    assert str(Version.parse("2.15")) == "2.15"
    assert Version(1, 10) > Version(1, 9) > Version(0, 99)
    for bad in ["1", "1.0.0", "01.0", "a.b", "-1.0"]:
        with pytest.raises(ValueError):
            Version.parse(bad)


def test_next_version_follows_highest():
    assert next_version(Version(1, 3), Bump.MINOR) == Version(1, 4)
    assert next_version(Version(1, 3), Bump.MAJOR) == Version(2, 0)


def test_refs():
    assert format_ref("TELCO-CANONICAL", Version(2, 1)) == "TELCO-CANONICAL@2.1"
    assert parse_ref("TELCO-CANONICAL@2.1") == ("TELCO-CANONICAL", Version(2, 1))
    assert parse_ref("TELCO-CANONICAL") == ("TELCO-CANONICAL", None)
    with pytest.raises(ValueError):
        parse_ref("TELCO@latest")


# ---- lifecycle ----

def test_transitions():
    assert can_transition(S.DRAFT, S.PUBLISHED)
    assert can_transition(S.PUBLISHED, S.SUPERSEDED)
    assert can_transition(S.SUPERSEDED, S.RETIRED)
    assert not can_transition(S.PUBLISHED, S.DRAFT)
    assert not can_transition(S.DRAFT, S.RETIRED)  # drafts are discarded, not retired
    assert not can_transition(S.RETIRED, S.PUBLISHED)
    assert not any(S.DRAFT in targets for targets in TRANSITIONS.values())  # nothing goes back to draft
    assert TRANSITIONS[S.RETIRED] == frozenset()


def test_only_published_and_in_window_is_selectable():
    today = date(2026, 11, 16)
    assert is_selectable(S.PUBLISHED, None, None, today)
    assert is_selectable(S.PUBLISHED, date(2026, 11, 16), date(2026, 11, 16), today)
    assert not is_selectable(S.PUBLISHED, date(2026, 11, 17), None, today)
    assert not is_selectable(S.PUBLISHED, None, date(2026, 11, 15), today)
    for status in (S.DRAFT, S.DEPRECATED, S.SUPERSEDED, S.RETIRED):
        assert not is_selectable(status, None, None, today)
