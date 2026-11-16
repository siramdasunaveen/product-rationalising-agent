"""Local JWT auth (standalone only): tokens issued after email + password sign-in, with the same claims AI Studio is expected to send:
``sub``, ``tenant_id``, ``roles``, ``name``, plus ``iss``, ``iat``, ``exp``, ``jti``.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.domain.permissions import (
    PERSONAS,
    approval_scopes_for_roles,
    permissions_for_roles,
)
from app.ports.auth import AuthError, Principal

ALGORITHM = "HS256"


class LocalJwtAuth:
    def __init__(self, secret: str, issuer: str, ttl_minutes: int) -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._secret = secret
        self._issuer = issuer
        self._ttl = timedelta(minutes=ttl_minutes)

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, *, user_id: str, tenant_id: str, name: str, roles: list[str],
              now: datetime | None = None) -> str:
        unknown = [r for r in roles if r not in PERSONAS]
        if unknown:
            raise ValueError(f"Unknown persona(s): {', '.join(unknown)}")
        now = now or datetime.now(UTC)
        claims = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "name": name,
            "roles": roles,
            "iss": self._issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM)

    def authenticate(self, bearer_token: str) -> Principal:
        try:
            claims = jwt.decode(
                bearer_token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                options={"require": ["sub", "tenant_id", "exp", "iat", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("Invalid token") from exc

        roles = claims.get("roles") or []
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise AuthError("Invalid roles claim")
        tenant_id = claims["tenant_id"]
        if not isinstance(tenant_id, str) or not tenant_id:
            raise AuthError("Invalid tenant_id claim")

        return Principal(
            user_id=str(claims["sub"]),
            tenant_id=tenant_id,
            name=str(claims.get("name") or claims["sub"]),
            roles=tuple(roles),
            permissions=frozenset(p.value for p in permissions_for_roles(roles)),
            approval_scopes=approval_scopes_for_roles(roles),
            auth_context={
                "method": "password",
                "iss": claims["iss"],
                "jti": claims.get("jti"),
                "iat": claims["iat"],
            },
        )
