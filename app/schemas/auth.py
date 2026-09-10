from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, HttpUrl

class AuthUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["admin", "viewer"]
    enabled: bool
    created_at: str
    updated_at: str


class SetupStatus(BaseModel):
    required: bool


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserPreferences(BaseModel):
    theme: Literal["system", "light", "dark"] = "system"
    updated_at: str | None = None


class UserPreferencesUpdate(BaseModel):
    theme: Literal["system", "light", "dark"]


class AuthSessionInfo(BaseModel):
    user: AuthUser
    csrf_token: str
    expires_at: str
    preferences: UserPreferences = Field(default_factory=UserPreferences)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["admin", "viewer"] = "viewer"
    enabled: bool = True


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: Literal["admin", "viewer"] | None = None
    enabled: bool | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=10, max_length=200)


class AdminResourceDescriptor(BaseModel):
    id: str
    name: str
    category: str
    mutable: bool
    status_field: str | None = None
    total: int
    active: int | None = None
    actions: list[str]
    management_mode: Literal["governance", "read_only", "dedicated"] = "governance"
    canonical_href: str | None = None


class AdminResourcePage(BaseModel):
    resource: AdminResourceDescriptor
    page: int
    page_size: int
    total: int
    items: list[dict[str, object]]


class ResourceStatusUpdate(BaseModel):
    enabled: bool


class BulkResourceStatusUpdate(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    enabled: bool


class AdminAuditItem(BaseModel):
    id: str
    user_id: str
    username: str
    action: str
    resource_type: str
    resource_id: str | None
    detail: str
    before_state: dict[str, object] | None = None
    after_state: dict[str, object] | None = None
    created_at: str
