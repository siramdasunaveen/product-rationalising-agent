"""Shared fixtures.

DB tests run against the ``pra_test`` database on the docker-compose Postgres
(``docker compose up -d``). Redis is faked with fakeredis; the AI is faked per test.
"""

import uuid
from pathlib import Path

import fakeredis
import pytest
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.adapters.registry import build_platform
from app.config import Settings
from app.main import create_app
from app.services.users import create_user

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    base = Settings()
    return Settings(
        platform_mode="standalone",
        database_url=base.test_database_url,
        jwt_secret="test-secret-test-secret-test-secret-0123",
        fernet_key=Fernet.generate_key().decode(),
        ai_provider="none",
        storage_dir=tmp_path_factory.mktemp("storage"),
    )


@pytest.fixture(scope="session")
def migrated_db(settings: Settings) -> str:
    """Fresh schema in the test database, migrated with Alembic (also tests the migrations)."""
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS rationalisation CASCADE"))
    except OperationalError as exc:
        pytest.fail(
            "Test database not reachable. Start it with `docker compose up -d` "
            f"(url: {settings.database_url}). {exc.orig}",
            pytrace=False,
        )
    finally:
        engine.dispose()

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return settings.database_url


@pytest.fixture(scope="session")
def session_factory(migrated_db: str):
    engine = create_engine(migrated_db)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def tenant() -> str:
    """Unique tenant per test, so tests never share audit chains or data."""
    return f"t-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def platform(settings: Settings):
    return build_platform(settings, redis=fakeredis.FakeRedis())


@pytest.fixture
def client(settings: Settings, platform, session_factory):
    app = create_app(settings, platform=platform, session_factory=session_factory)
    with TestClient(app) as c:
        yield c


PASSWORD = "correct-horse-battery"


@pytest.fixture
def make_user(session_factory):
    """Create a user with the given role; returns its email."""

    def _make(role: str, tenant_id: str = "demo", *, name: str = "Test User", active: bool = True) -> str:
        email = f"{role}-{uuid.uuid4().hex[:8]}@test.local"
        with session_factory() as s:
            user = create_user(s, email=email, name=name, password=PASSWORD, roles=[role], tenant_id=tenant_id)
            user.is_active = active
            s.commit()
        return email

    return _make


@pytest.fixture
def login(client, make_user):
    """Create a user with ``role`` and sign in; returns auth headers."""

    def _login(role: str, tenant_id: str = "demo") -> dict[str, str]:
        email = make_user(role, tenant_id)
        r = client.post("/api/rationalisation/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login
