"""Per-request / per-job context carried in contextvars, read by logging, audit and security events.

Set by the request-context middleware (request id, client IP, user agent), extended after authentication (tenant,
user) and by background jobs (job id, tenant). Never holds secrets or tokens.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RequestContext:
    request_id: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    job_id: str | None = None


_CTX: ContextVar[RequestContext] = ContextVar("pra_request_context", default=RequestContext())


def current() -> RequestContext:
    return _CTX.get()


def start(**values: str | None) -> Token:
    """Begin a fresh context (a request or a job). Return the token for ``end``."""
    return _CTX.set(RequestContext(**values))


def end(token: Token) -> None:
    _CTX.reset(token)


def bind(**values: str | None) -> None:
    """Add facts to the current context (e.g. tenant and user once the caller is authenticated)."""
    _CTX.set(replace(_CTX.get(), **values))
