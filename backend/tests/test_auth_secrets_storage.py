from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.fernet import Fernet

from app.adapters.local.auth import LocalJwtAuth
from app.adapters.local.secrets import FernetCipher
from app.adapters.local.storage import DiskStorage
from app.ports.auth import AuthError
from app.ports.secrets import SecretError
from app.ports.storage import StorageArea

SECRET = "unit-test-secret-unit-test-secret-012345"


@pytest.fixture
def auth() -> LocalJwtAuth:
    return LocalJwtAuth(SECRET, "pra-standalone", 60)


# ---- auth ----

def test_jwt_round_trip(auth):
    token = auth.issue(user_id="u1", tenant_id="acme", name="A. Patel", roles=["portfolio_manager"])
    p = auth.authenticate(token)
    assert (p.user_id, p.tenant_id, p.name) == ("u1", "acme", "A. Patel")
    assert p.roles == ("portfolio_manager",)
    assert "run.create" in p.permissions and "read" in p.permissions
    assert p.auth_context["method"] == "password"


def test_expired_token_rejected(auth):
    token = auth.issue(user_id="u1", tenant_id="acme", name="x", roles=["viewer"],
                       now=datetime.now(UTC) - timedelta(hours=2))
    with pytest.raises(AuthError, match="expired"):
        auth.authenticate(token)


def test_wrong_signature_rejected(auth):
    other = LocalJwtAuth("another-secret-another-secret-another-0", "pra-standalone", 60)
    token = other.issue(user_id="u1", tenant_id="acme", name="x", roles=["viewer"])
    with pytest.raises(AuthError):
        auth.authenticate(token)


def test_wrong_issuer_rejected(auth):
    other = LocalJwtAuth(SECRET, "someone-else", 60)
    token = other.issue(user_id="u1", tenant_id="acme", name="x", roles=["viewer"])
    with pytest.raises(AuthError):
        auth.authenticate(token)


def test_alg_none_rejected(auth):
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode({"sub": "u1", "tenant_id": "acme", "roles": ["agent_admin"], "iss": "pra-standalone",
                        "iat": now, "exp": now + 600}, key=None, algorithm="none")
    with pytest.raises(AuthError):
        auth.authenticate(token)


def test_missing_tenant_rejected(auth):
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode({"sub": "u1", "roles": [], "iss": "pra-standalone", "iat": now, "exp": now + 600},
                       SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        auth.authenticate(token)


def test_unknown_persona_cannot_be_issued(auth):
    with pytest.raises(ValueError):
        auth.issue(user_id="u1", tenant_id="acme", name="x", roles=["root"])


def test_short_secret_refused():
    with pytest.raises(ValueError):
        LocalJwtAuth("short", "iss", 60)


# ---- secrets ----

def test_fernet_round_trip():
    cipher = FernetCipher(Fernet.generate_key().decode())
    token = cipher.encrypt("salesforce-client-secret")
    assert token != "salesforce-client-secret"
    assert cipher.decrypt(token) == "salesforce-client-secret"


def test_fernet_wrong_key_fails():
    token = FernetCipher(Fernet.generate_key().decode()).encrypt("x")
    with pytest.raises(SecretError):
        FernetCipher(Fernet.generate_key().decode()).decrypt(token)


def test_fernet_missing_key_fails_on_use():
    with pytest.raises(SecretError, match="not set"):
        FernetCipher("").encrypt("x")


def test_fernet_invalid_key_rejected():
    with pytest.raises(SecretError):
        FernetCipher("not-a-key")


# ---- storage ----

def test_storage_quarantine_then_promote(tmp_path):
    store = DiskStorage(tmp_path)
    obj = store.put("acme", "Product Catalog.xlsx", b"data")
    assert "/quarantine/" in obj.key and obj.size == 4 and len(obj.sha256) == 64
    assert store.get(obj.key) == b"data"

    checked = store.promote(obj.key)
    assert "/checked/" in checked.key
    assert store.get(checked.key) == b"data"
    with pytest.raises(FileNotFoundError):
        store.get(obj.key)

    with pytest.raises(ValueError):
        store.promote(checked.key)  # only quarantined objects


def test_storage_sanitises_names_and_blocks_traversal(tmp_path):
    store = DiskStorage(tmp_path / "root")
    obj = store.put("../evil", "../../etc/passwd", b"x", area=StorageArea.CHECKED)
    assert ".." not in obj.key
    with pytest.raises(ValueError):
        store.get("../outside.txt")


def test_storage_delete(tmp_path):
    store = DiskStorage(tmp_path)
    obj = store.put("acme", "a.xlsx", b"x")
    store.delete(obj.key)
    with pytest.raises(FileNotFoundError):
        store.get(obj.key)
