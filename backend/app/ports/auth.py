from dataclasses import dataclass, field
from typing import Protocol


class AuthError(Exception):
    """Token missing, invalid or expired. Maps to HTTP 401."""


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. ``permissions`` is what the UI and the API checks use."""

    user_id: str
    tenant_id: str
    name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    approval_scopes: tuple[str, ...] = ()
    # Raw auth context kept for audit (issuer, token id, auth time...). Never the token itself.
    auth_context: dict = field(default_factory=dict)

    def has(self, permission: str) -> bool:
        return permission in self.permissions


class AuthProvider(Protocol):
    def authenticate(self, bearer_token: str) -> Principal:
        """Verify the token and return the caller. Raise ``AuthError`` on failure."""
        ...
