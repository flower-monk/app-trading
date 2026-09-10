from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


CredentialOrigin = Literal["database", "environment", "none", "error"]
CredentialTestStatus = Literal["unknown", "pending", "pass", "partial", "fail"]


class CredentialBinding(BaseModel):
    resource_type: Literal["market_provider", "data_source", "information_source", "agent_provider", "model_channel"]
    resource_id: str
    resource_name: str


class CredentialProfile(BaseModel):
    id: str
    name: str
    env_alias: str
    configured: bool
    origin: CredentialOrigin
    secret_hint: str
    version: int
    binding_count: int
    bindings: list[CredentialBinding] = Field(default_factory=list)
    test_status: CredentialTestStatus
    test_message: str
    last_tested_at: str | None
    last_rotated_at: str | None
    created_at: str
    updated_at: str


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    env_alias: str = Field(min_length=2, max_length=120)
    secret: SecretStr | None = None


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    env_alias: str | None = Field(default=None, min_length=2, max_length=120)


class CredentialSecretUpdate(BaseModel):
    secret: SecretStr = Field(min_length=1, max_length=10000)


class ResourceSecretUpdate(BaseModel):
    api_key: SecretStr = Field(min_length=1, max_length=10000)


class CredentialVaultStatus(BaseModel):
    writable: bool
    master_key_configured: bool
    total: int
    configured: int
    database_overrides: int
    environment_fallbacks: int
    errors: int
