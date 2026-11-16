"""AI Studio adapter stubs. See the package docstring."""

from typing import Any

from app.ports.ai import AIRawResult, AIRequest
from app.ports.audit import AuditEntry, AuditRecord, ChainCheck
from app.ports.auth import Principal
from app.ports.jobs import JobStatus
from app.ports.notify import Notification
from app.ports.storage import StorageArea, StoredObject

_TODO = "AI Studio adapter not implemented yet (see docstring)"


class StudioAuth:
    """Verify the JWT that AI Studio issues for the signed-in user.

    Needs from the studio:
    - JWKS URL (or public keys) and the expected ``iss`` and ``aud``; algorithm (RS256/ES256).
    - Claims: ``sub`` (user id), ``tenant_id``, ``name``, and either ``roles`` (studio role names,
      mapped to permissions via a mapping table) or ``permissions`` (already resolved, using the
      names in manifest.json).
    - Optional approval scopes (commercial/product/architecture) for approvers.
    Return a ``Principal``; raise ``AuthError`` for invalid/expired tokens. Cache JWKS.
    """

    def authenticate(self, bearer_token: str) -> Principal:
        raise NotImplementedError(_TODO)


class StudioAIProvider:
    """Call models through the studio's model gateway, if it offers one.

    Needs: gateway URL + auth, model ids available per tenant, structured (JSON schema) output
    support, and usage/cost reporting. Must raise ``AIUnavailable`` on refusal/outage so the
    no-LLM fallback runs. If the studio offers no gateway, keep ``AnthropicProvider`` (BYOK).
    """

    name = "studio"

    def complete_json(self, request: AIRequest) -> AIRawResult:
        raise NotImplementedError(_TODO)


class StudioStorage:
    """Store uploaded files in the studio's object store.

    Needs: bucket/container per tenant, put/get/move/delete, server-side encryption, and a way
    to keep quarantined files unreadable by other services until checks pass.
    """

    def put(self, tenant_id: str, name: str, data: bytes,
            area: StorageArea = StorageArea.QUARANTINE) -> StoredObject:
        raise NotImplementedError(_TODO)

    def get(self, key: str) -> bytes:
        raise NotImplementedError(_TODO)

    def promote(self, key: str) -> StoredObject:
        raise NotImplementedError(_TODO)

    def delete(self, key: str) -> None:
        raise NotImplementedError(_TODO)


class StudioJobQueue:
    """Run background jobs on the studio's job runner, if it has one.

    Needs: enqueue a Python callable by dotted path with JSON args, job id, status
    (queued/running/succeeded/failed), progress 0..100 + message, and error text.
    If the studio has no runner, keep ``RqJobQueue`` with our own Redis.
    """

    def enqueue(self, func_path: str, *args: Any, job_id: str | None = None, **kwargs: Any) -> str:
        raise NotImplementedError(_TODO)

    def status(self, job_id: str) -> JobStatus:
        raise NotImplementedError(_TODO)


class StudioAuditLog:
    """Write audit events to the studio's central audit service, if required.

    Needs: append-only API, fields actor/role/timestamp/auth context/decision version/policy
    result/content hash, 7-year retention, and a way to verify integrity. Must be written in the
    same unit of work as the business change (or via an outbox). Otherwise keep ``DbAuditLog``.
    """

    def record(self, record: AuditRecord, *, session: Any = None) -> AuditEntry:
        raise NotImplementedError(_TODO)

    def verify_chain(self, tenant_id: str, *, session: Any = None) -> ChainCheck:
        raise NotImplementedError(_TODO)


class StudioSecretCipher:
    """Encrypt/decrypt secrets with the studio's KMS / secret store.

    Needs: encrypt and decrypt endpoints (or envelope keys) scoped to this agent and tenant.
    """

    def encrypt(self, plaintext: str) -> str:
        raise NotImplementedError(_TODO)

    def decrypt(self, token: str) -> str:
        raise NotImplementedError(_TODO)


class StudioNotifier:
    """Deliver notifications (approvals due, evidence requests) via the studio's inbox/email.

    Needs: send API taking tenant, recipient user ids, subject, body and a deep link.
    """

    def send(self, notification: Notification) -> None:
        raise NotImplementedError(_TODO)
