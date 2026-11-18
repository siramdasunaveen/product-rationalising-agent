"""SEC-01: production settings refuse dev defaults and placeholder secrets at start-up."""

import pytest
from cryptography.fernet import Fernet

from app.config import Settings as _Settings


def Settings(**kw):  # noqa: N802 - ignore the developer's .env so the tests see only what they pass
    return _Settings(_env_file=None, **kw)

GOOD = {"jwt_secret": "x7Qp9vL2mN4rT8wZ1aB3cD5eF6gH0jK2mN4pQ6rS8tU0", "fernet_key": Fernet.generate_key().decode(),
        "database_url": "postgresql+psycopg://svc:strong-pass@db.internal:5432/pra", "cors_origins": "https://pra.example"}


def test_dev_defaults_are_allowed_in_dev_and_test():
    assert Settings(environment="dev").jwt_secret.startswith("dev-only")
    assert Settings(environment="test", demo_password="demo-pass-1").demo_password == "demo-pass-1"


def test_production_refuses_placeholders_and_defaults():
    with pytest.raises(ValueError) as exc:
        Settings(environment="prod")
    msg = str(exc.value)
    assert "PRA_JWT_SECRET" in msg and "PRA_FERNET_KEY" in msg and "PRA_DATABASE_URL" in msg
    with pytest.raises(ValueError, match="dev placeholder"):
        Settings(environment="prod", **{**GOOD, "jwt_secret": "change-me-to-a-long-random-string-at-least-32-bytes"})
    with pytest.raises(ValueError, match="too weak"):
        Settings(environment="prod", **{**GOOD, "jwt_secret": "a" * 40})
    with pytest.raises(ValueError, match="PRA_DEMO_PASSWORD"):
        Settings(environment="prod", **GOOD, demo_password="demo-pass-1")
    with pytest.raises(ValueError, match="PRA_CORS_ORIGINS"):
        Settings(environment="prod", **{**GOOD, "cors_origins": "*"})


def test_production_accepts_strong_settings_and_studio_mode_needs_no_jwt_secret():
    assert Settings(environment="prod", **GOOD).environment == "prod"
    studio = Settings(environment="prod", platform_mode="studio", **{**GOOD, "jwt_secret": "x" * 32})
    assert not studio.is_standalone
