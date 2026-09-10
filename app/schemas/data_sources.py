from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DataSourceHealthState = Literal["healthy", "degraded", "offline", "disabled", "untested"]
DataSourceRouteMode = Literal["fallback", "aggregate"]


class DataSourceProfile(BaseModel):
    id: str
    legacy_source_id: int | None = None
    name: str
    domain: Literal["market", "index", "content", "indicator"]
    category: str
    adapter: str
    provider: str
    endpoint: str
    enabled: bool
    markets: list[str] = Field(default_factory=list)
    asset_classes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    auth_type: str = "none"
    api_key_env: str = ""
    credential_id: str | None = None
    credential_configured: bool = False
    priority: int = 50
    reliability_weight: float = 0.8
    timeout_seconds: int = 8
    cache_ttl_seconds: int = 60
    stale_ttl_seconds: int = 900
    sync_interval_seconds: int = 900
    timezone: str = "UTC"
    config: dict[str, object] = Field(default_factory=dict)
    health_status: DataSourceHealthState = "untested"
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    last_latency_ms: float | None = None
    created_at: str
    updated_at: str


class DataSourceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    domain: Literal["market", "index", "content", "indicator"]
    category: str = Field(min_length=1, max_length=40)
    adapter: str = Field(min_length=1, max_length=60)
    provider: str = Field(min_length=1, max_length=80)
    endpoint: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    markets: list[str] = Field(default_factory=list)
    asset_classes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    auth_type: str = "none"
    api_key_env: str = Field(default="", max_length=120)
    priority: int = Field(default=50, ge=0, le=100)
    reliability_weight: float = Field(default=0.8, ge=0, le=1)
    timeout_seconds: int = Field(default=8, ge=1, le=60)
    cache_ttl_seconds: int = Field(default=60, ge=0, le=86400)
    stale_ttl_seconds: int = Field(default=900, ge=0, le=604800)
    sync_interval_seconds: int = Field(default=900, ge=300, le=86400)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    config: dict[str, object] = Field(default_factory=dict)


class DataSourceProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    markets: list[str] | None = None
    asset_classes: list[str] | None = None
    capabilities: list[str] | None = None
    auth_type: str | None = None
    api_key_env: str | None = Field(default=None, max_length=120)
    priority: int | None = Field(default=None, ge=0, le=100)
    reliability_weight: float | None = Field(default=None, ge=0, le=1)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    cache_ttl_seconds: int | None = Field(default=None, ge=0, le=86400)
    stale_ttl_seconds: int | None = Field(default=None, ge=0, le=604800)
    sync_interval_seconds: int | None = Field(default=None, ge=300, le=86400)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, object] | None = None


class DataSourceRouteMember(BaseModel):
    source_id: str
    source_name: str
    adapter: str
    position: int
    role: Literal["primary", "fallback", "member"]
    enabled: bool
    health_status: DataSourceHealthState


class DataSourceRoute(BaseModel):
    id: str
    market: str
    capability: str
    mode: DataSourceRouteMode
    enabled: bool
    version: int
    members: list[DataSourceRouteMember]
    updated_at: str


class DataSourceRouteUpdate(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=20)
    mode: DataSourceRouteMode = "fallback"
    enabled: bool = True
    expected_version: int | None = None


class DataSourceConfigPublishRequest(DataSourceRouteUpdate):
    market: str = Field(min_length=1, max_length=20)
    capability: str = Field(min_length=1, max_length=60)


class DataSourceHealthCheck(BaseModel):
    id: int
    source_id: str
    status: Literal["pass", "fail"]
    latency_ms: float
    message: str
    checked_at: str


class DataSourceRevision(BaseModel):
    id: str
    route_id: str
    version: int
    status: Literal["active", "rejected", "rolled_back"]
    config: dict[str, object]
    validation: list[dict[str, object]]
    created_by: str
    created_at: str


class DataSourceControlSummary(BaseModel):
    total_sources: int
    enabled_sources: int
    healthy_sources: int
    degraded_sources: int
    offline_sources: int
    routes: int
    active_revision: int
    generated_at: str


class DataProvenance(BaseModel):
    source_id: str
    source_name: str
    adapter: str
    fetched_at: str
    health_status: DataSourceHealthState
    stale: bool = False
    fallback_reason: str | None = None
