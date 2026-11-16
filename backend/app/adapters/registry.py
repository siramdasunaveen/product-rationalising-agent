"""Builds the set of adapters for the configured platform mode."""

from dataclasses import dataclass
from typing import Any

from redis import Redis

from app.adapters.local.ai import AnthropicProvider, NoLLMProvider
from app.adapters.local.audit import DbAuditLog
from app.adapters.local.auth import LocalJwtAuth
from app.adapters.local.jobs import RqJobQueue
from app.adapters.local.notify import LogNotifier
from app.adapters.local.people import DbPeopleDirectory
from app.adapters.local.secrets import FernetCipher
from app.adapters.local.storage import DiskStorage
from app.adapters.studio import adapters as studio
from app.config import AIProviderName, PlatformMode, Settings
from app.ports.ai import AIProvider
from app.ports.audit import AuditLog
from app.ports.auth import AuthProvider
from app.ports.jobs import JobQueue
from app.ports.notify import Notifier
from app.ports.people import PeopleDirectory
from app.ports.secrets import SecretCipher
from app.ports.storage import ObjectStorage


@dataclass
class Platform:
    mode: PlatformMode
    auth: AuthProvider
    ai: AIProvider
    storage: ObjectStorage
    jobs: JobQueue
    audit: AuditLog
    secrets: SecretCipher
    notify: Notifier
    people: PeopleDirectory
    # Standalone only: issues tokens after email + password sign-in.
    token_issuer: LocalJwtAuth | None = None
    redis: Any = None


def build_ai(settings: Settings) -> AIProvider:
    if settings.ai_provider == AIProviderName.ANTHROPIC:
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
    return NoLLMProvider()


def build_platform(settings: Settings, *, redis: Any = None) -> Platform:
    redis = redis if redis is not None else Redis.from_url(settings.redis_url)

    if settings.platform_mode == PlatformMode.STUDIO:
        return Platform(
            mode=PlatformMode.STUDIO,
            auth=studio.StudioAuth(),
            ai=studio.StudioAIProvider(),
            storage=studio.StudioStorage(),
            jobs=studio.StudioJobQueue(),
            audit=studio.StudioAuditLog(),
            secrets=studio.StudioSecretCipher(),
            notify=studio.StudioNotifier(),
            people=studio.StudioPeopleDirectory(),
            redis=redis,
        )

    token_issuer = LocalJwtAuth(settings.jwt_secret, settings.jwt_issuer, settings.jwt_ttl_minutes)
    return Platform(
        mode=PlatformMode.STANDALONE,
        auth=token_issuer,
        ai=build_ai(settings),
        storage=DiskStorage(settings.storage_dir),
        jobs=RqJobQueue(redis),
        audit=DbAuditLog(),
        secrets=FernetCipher(settings.fernet_key),
        notify=LogNotifier(),
        people=DbPeopleDirectory(),
        token_issuer=token_issuer,
        redis=redis,
    )
