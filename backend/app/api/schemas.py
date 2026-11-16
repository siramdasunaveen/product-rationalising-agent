"""API request/response models (part of the OpenAPI handover contract)."""

import uuid
from datetime import date, datetime

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


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FieldErrorOut(BaseModel):
    field: str
    message: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    errors: list[FieldErrorOut] = []


class ErrorResponse(BaseModel):
    detail: ErrorDetail


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

class PortfolioOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class MarketOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    timezone: str


class ProductFamilyOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    portfolio_id: uuid.UUID
    parent_id: uuid.UUID | None


class ObjectiveOut(BaseModel):
    key: str
    label: str


class PersonOut(BaseModel):
    user_id: str
    name: str
    email: str | None
    roles: list[str]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class RunScopeIn(BaseModel):
    """Define Scope (field spec tab 02). Rules are enforced by the Run Service, which returns all
    field errors at once as 422 ``validation_failed``."""

    name: str = Field(max_length=500, examples=["Consumer Mobile Rationalisation Q4"])
    portfolio_id: uuid.UUID | None = None
    market_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    product_family_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    period_start: date | None = None
    period_end: date | None = None
    objectives: list[str] = Field(default_factory=list, max_length=20)
    owner_id: str | None = Field(default=None, max_length=128)


class RunScopePatch(BaseModel):
    """Partial scope change. ``version`` must equal the version you loaded (optimistic locking)."""

    version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=500)
    portfolio_id: uuid.UUID | None = None
    market_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    product_family_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    period_start: date | None = None
    period_end: date | None = None
    objectives: list[str] | None = Field(default=None, max_length=20)
    owner_id: str | None = Field(default=None, max_length=128)


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class OwnerRef(BaseModel):
    user_id: str
    name: str


class RunOut(BaseModel):
    id: uuid.UUID
    run_code: str
    name: str
    state: str
    stage_label: str
    portfolio: Ref
    markets: list[Ref]
    product_families: list[Ref]
    period_start: date
    period_end: date
    objectives: list[str]
    owner: OwnerRef
    risk: str | None = Field(description="Set once analysis has assessed the run; null before")
    scope_editable: bool
    version: int
    created_at: datetime
    updated_at: datetime


class RunListItem(BaseModel):
    id: uuid.UUID
    run_code: str
    name: str
    state: str
    stage_label: str
    portfolio_name: str
    owner_name: str
    risk: str | None
    updated_at: datetime


class RunListOut(BaseModel):
    items: list[RunListItem]
    total: int


class RunSummaryOut(BaseModel):
    active_runs: int
    opportunities: int
    awaiting_approval: int
    blocked: int
