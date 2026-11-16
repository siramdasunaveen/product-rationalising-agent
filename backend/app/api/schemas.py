"""API request/response models (part of the OpenAPI handover contract)."""

from pydantic import BaseModel, Field


class AgentInfo(BaseModel):
    id: str
    name: str
    version: str
    industry: str


class MeResponse(BaseModel):
    user_id: str
    name: str
    tenant_id: str
    roles: list[str]
    permissions: list[str] = Field(description="What the UI uses to decide what to show")
    approval_scopes: list[str]
    platform_mode: str
    agent: AgentInfo


class NavItem(BaseModel):
    key: str
    label: str
    path: str
    permission: str
    status: str | None = None
    sidebarLabel: str | None = None  # shown in the WORKSPACE sidebar when set


class ManifestResponse(BaseModel):
    id: str
    name: str
    vendor: str
    industry: str
    version: str
    description: str
    nav: list[NavItem]
    permissions: list[dict]
    requiredConnectors: list[str]
    optionalConnectors: list[str]


class LoginRequest(BaseModel):
    email: str = Field(max_length=254, examples=["pm@demo.local"])
    password: str = Field(max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
