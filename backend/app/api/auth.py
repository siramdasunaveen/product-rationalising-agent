"""Email + password sign-in (standalone mode only; in AI Studio the studio's sign-in is used)."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, PlatformDep
from app.api.schemas import LoginRequest, TokenResponse
from app.services.users import LoginFailed, authenticate

router = APIRouter(prefix="/auth", tags=["auth (standalone only)"])

INVALID = "Invalid email or password"


@router.post("/login", response_model=TokenResponse, summary="Sign in with email and password")
def login(body: LoginRequest, platform: PlatformDep, db: DbDep) -> TokenResponse:
    if platform.token_issuer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not available")
    try:
        user = authenticate(db, platform.audit, email=body.email, password=body.password)
    except LoginFailed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID) from None
    token = platform.token_issuer.issue(user_id=str(user.id), tenant_id=user.tenant_id,
                                        name=user.name, roles=list(user.roles))
    return TokenResponse(access_token=token, expires_in=platform.token_issuer.ttl_seconds)
