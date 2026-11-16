"""Shared FastAPI dependencies: platform, DB session, caller, permission checks."""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.domain.permissions import Permission
from app.ports.auth import AuthError, Principal

_bearer = HTTPBearer(auto_error=False)


def get_platform(request: Request) -> Platform:
    return request.app.state.platform


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        return get_platform(request).auth.authenticate(credentials.credentials)
    except AuthError as exc:
        unauthorized.detail = str(exc)
        raise unauthorized from exc


def require_permission(permission: Permission) -> Callable[..., Principal]:
    """Route dependency: 403 unless the caller has ``permission``. Use on every route."""

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has(permission.value):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission.value}")
        return principal

    return dependency


PlatformDep = Annotated[Platform, Depends(get_platform)]
DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[Principal, Depends(require_permission(Permission.READ))]
