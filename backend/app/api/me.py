from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, PlatformDep
from app.api.schemas import AgentInfo, ManifestResponse, MeResponse

router = APIRouter(tags=["identity"])


@router.get("/me", response_model=MeResponse, summary="The caller and their permissions")
def me(principal: CurrentUser, platform: PlatformDep, request: Request) -> MeResponse:
    manifest = request.app.state.manifest
    return MeResponse(
        user_id=principal.user_id,
        name=principal.name,
        tenant_id=principal.tenant_id,
        roles=list(principal.roles),
        permissions=sorted(principal.permissions),
        approval_scopes=list(principal.approval_scopes),
        platform_mode=platform.mode.value,
        agent=AgentInfo(id=manifest["id"], name=manifest["name"], version=manifest["version"],
                        industry=manifest["industry"]),
    )


@router.get("/manifest", response_model=ManifestResponse, summary="Agent manifest (nav, permissions)")
def manifest(_: CurrentUser, request: Request) -> ManifestResponse:
    return ManifestResponse.model_validate(request.app.state.manifest)
