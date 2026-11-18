"""Token revocation (SEC-03): a Redis deny-list of token ids plus a per-user "sessions before X are invalid" mark.

Logout revokes the presented token; password change, deactivation and role changes invalidate every token the user
was issued before that moment. Entries expire with the token TTL, so the list never grows beyond live tokens.
"""

import time
from typing import Any


class RedisRevocations:
    def __init__(self, redis: Any, prefix: str = "pra:auth") -> None:
        self._r = redis
        self._prefix = prefix

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        self._r.set(f"{self._prefix}:revoked:{jti}", "1", ex=max(1, int(ttl_seconds)))

    def is_revoked(self, jti: str | None) -> bool:
        return bool(jti) and self._r.exists(f"{self._prefix}:revoked:{jti}") == 1

    def invalidate_user(self, user_id: str, ttl_seconds: int, now: float | None = None) -> None:
        self._r.set(f"{self._prefix}:nbf:{user_id}", str(int(now or time.time())), ex=max(1, int(ttl_seconds)))

    def invalidated_at(self, user_id: str) -> int | None:
        raw = self._r.get(f"{self._prefix}:nbf:{user_id}")
        return int(raw) if raw else None
