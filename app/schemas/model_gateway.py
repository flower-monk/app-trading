from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr


ProviderType = Literal[
    "openai", "azure_openai", "anthropic", "gemini", "deepseek", "dashscope",
    "zhipu", "volcengine", "moonshot", "siliconflow", "openrouter", "xai",
    "mistral", "ollama", "vllm", "openai_compatible",
]
ModelProtocol = Literal["openai_chat", "openai_responses", "azure_openai", "anthropic", "gemini"]
ChannelHealth = Literal["unknown", "healthy", "degraded", "unavailable", "disabled"]
MODEL_CATALOG_MAX_ITEMS = 5000
ModelCatalogName = Annotated[str, Field(min_length=1, max_length=300)]


class ModelProviderPreset(BaseModel):
    id: ProviderType
    name: str
    protocol: ModelProtocol
    base_url: str
    api_key_env: str
    models: list[str]
    supports_model_sync: bool = True
    requires_api_key: bool = True


class ModelChannelProfile(BaseModel):
    id: str
    name: str
    provider_type: ProviderType
    protocol: ModelProtocol
    base_url: str
    api_key_env: str
    timeout_seconds: int
    config: dict[str, object]
    enabled: bool
    health_status: ChannelHealth
    consecutive_failures: int
    response_time_ms: int | None = None
    last_tested_at: str | None = None
    last_error: str | None = None
    secret_configured: bool = False
    credential_id: str | None = None
    credential_origin: Literal["database", "environment", "none", "error"] = "none"
    secret_hint: str = ""
    models: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ModelChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider_type: ProviderType
    protocol: ModelProtocol | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    api_key_env: str | None = Field(default=None, max_length=120)
    api_key: SecretStr | None = Field(default=None, max_length=10000)
    timeout_seconds: int = Field(default=30, ge=3, le=180)
    config: dict[str, object] = Field(default_factory=dict)
    models: list[ModelCatalogName] = Field(default_factory=list, max_length=MODEL_CATALOG_MAX_ITEMS)
    enabled: bool = True


class ModelChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider_type: ProviderType | None = None
    protocol: ModelProtocol | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    api_key_env: str | None = Field(default=None, max_length=120)
    api_key: SecretStr | None = Field(default=None, max_length=10000)
    timeout_seconds: int | None = Field(default=None, ge=3, le=180)
    config: dict[str, object] | None = None
    models: list[ModelCatalogName] | None = Field(default=None, max_length=MODEL_CATALOG_MAX_ITEMS)
    enabled: bool | None = None


class ModelChannelBatchUpdate(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    enabled: bool


class ModelRouteTarget(BaseModel):
    id: str
    route_id: str
    channel_id: str
    channel_name: str = ""
    model: str
    priority: int
    weight: int
    enabled: bool
    health_status: ChannelHealth = "unknown"


class ModelRouteTargetInput(BaseModel):
    channel_id: str
    model: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=0, ge=-1000, le=1000)
    weight: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True


class ModelRouteProfile(BaseModel):
    id: str
    name: str
    alias: str
    description: str
    enabled: bool
    temperature: float
    max_tokens: int
    targets: list[ModelRouteTarget]
    created_at: str
    updated_at: str


class ModelRouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    alias: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1200, ge=16, le=32000)
    targets: list[ModelRouteTargetInput] = Field(min_length=1, max_length=50)
    enabled: bool = True


class ModelRouteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    alias: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=16, le=32000)
    targets: list[ModelRouteTargetInput] | None = Field(default=None, min_length=1, max_length=50)
    enabled: bool | None = None


class ModelInvocation(BaseModel):
    id: str
    request_id: str
    source: Literal["agent", "playground", "health_test"]
    route_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    model: str | None = None
    status: Literal["succeeded", "failed"]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    created_at: str


class ModelGatewaySummary(BaseModel):
    channels: int
    healthy_channels: int
    routes: int
    success_rate: float | None = None
    average_latency_ms: int | None = None
    total_tokens: int = 0
    recent_failures: int = 0


class PlaygroundMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=30000)


class PlaygroundChatRequest(BaseModel):
    route_id: str
    channel_id: str | None = None
    messages: list[PlaygroundMessage] = Field(min_length=1, max_length=100)
    system_prompt: str = Field(default="", max_length=12000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=16, le=32000)
