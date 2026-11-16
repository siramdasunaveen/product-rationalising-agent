"""API request/response models (part of the OpenAPI handover contract)."""

import uuid
from datetime import date, datetime
from typing import Literal

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


# ---------------------------------------------------------------------------
# Artifact registry
# ---------------------------------------------------------------------------

class ArtifactVersionSummary(BaseModel):
    id: uuid.UUID
    version: str = Field(description="major.minor, e.g. 1.0")
    ref: str = Field(description="KEY@major.minor; what runs pin")
    status: str
    digest: str = Field(description="sha256:<hex> of the canonical JSON of kind, key and content")
    parent_version: str | None
    valid_from: date | None
    valid_to: date | None
    selectable: bool = Field(description="PUBLISHED and valid today: new runs may select it")
    change_note: str | None
    status_reason: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    published_by: str | None
    published_at: datetime | None
    lock_version: int = Field(description="Send back on edits and status changes (optimistic locking)")


class ArtifactVersionOut(ArtifactVersionSummary):
    artifact_id: uuid.UUID
    kind: str
    key: str
    name: str
    content: dict
    digest_verified: bool = Field(description="Stored content still matches the digest")


class ArtifactOut(BaseModel):
    id: uuid.UUID
    kind: str
    key: str
    name: str
    description: str | None
    created_by: str
    created_at: datetime
    published_version: str | None
    versions: list[ArtifactVersionSummary]


class ArtifactListItem(BaseModel):
    id: uuid.UUID
    kind: str
    key: str
    name: str
    published_version: str | None
    published_digest: str | None
    latest_version: str
    latest_status: str
    updated_at: datetime


class ArtifactListOut(BaseModel):
    total: int
    items: list[ArtifactListItem]


class ArtifactCreateIn(BaseModel):
    key: str = Field(examples=["TELCO-CANONICAL"], description="Upper case, unique in the tenant")
    name: str
    description: str | None = None
    content: dict
    valid_from: date | None = None
    valid_to: date | None = None
    change_note: str | None = None


class ArtifactDraftPatch(BaseModel):
    lock_version: int
    content: dict | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    change_note: str | None = None


class ArtifactCloneIn(BaseModel):
    bump: Literal["minor", "major"] = "minor"
    change_note: str | None = None


class ArtifactLockIn(BaseModel):
    lock_version: int


class ArtifactStatusIn(BaseModel):
    lock_version: int
    reason: str = Field(description="Why (required, 3–2000 characters)")


class ArtifactValidationOut(BaseModel):
    valid: bool
    digest: str
    errors: list[FieldErrorOut]


class ArtifactRefOut(BaseModel):
    artifact_id: uuid.UUID
    version_id: uuid.UUID
    kind: str
    key: str
    version: str
    ref: str
    digest: str
    status: str
    selectable: bool
