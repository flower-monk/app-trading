"""Extracted domain implementation. Public entry points are exposed by this module.

Do not import repositories or adapters from another domain; use its service API.
"""

from __future__ import annotations

import asyncio
import csv
import hmac
import os
import secrets
import socket
import ssl
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import pbkdf2_hmac, sha256
from html import unescape
from math import sqrt
import re
import sqlite3
import json
import random
from threading import RLock, Thread
from io import StringIO
from time import perf_counter, sleep
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request as UrlRequest, urlopen
import xml.etree.ElementTree as ET

import feedparser

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, HttpUrl
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.db.session import add_column_if_missing, connect
from app.schemas import (
    AgentConfig,
    AgentCreate,
    AgentProposal,
    AgentProviderCreate,
    AgentProviderProfile,
    AgentProviderUpdate,
    AgentRun,
    AgentRunDetail,
    AgentUpdate,
    AlertRuleCreate,
    AuthUser,
    CredentialProfile,
    CredentialTestStatus,
    FactorDefinitionCreate,
    ModelChannelCreate,
    ModelChannelProfile,
    ModelChannelUpdate,
    ModelGatewaySummary,
    ModelInvocation,
    MODEL_CATALOG_MAX_ITEMS,
    ModelProviderPreset,
    ModelRouteCreate,
    ModelRouteProfile,
    ModelRouteTarget,
    ModelRouteTargetInput,
    ModelRouteUpdate,
    NewsEventAnnotation,
    NewsImpactGraphEdgeCreate,
    NewsImpactGraphEdgeReview,
    NewsTaggingReviewOverride,
    PlaygroundChatRequest,
    WatchlistItemCreate,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
create_alert = service_call('app.modules.trading.service', 'create_alert_rule')
list_news_sources = service_call('app.modules.news.service', 'list_news_sources')
news_events = service_call('app.modules.news.service', 'list_news_events')
from app.modules.market.service import MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
add_watchlist_symbol = service_call('app.modules.market.service', 'add_watchlist_symbol')
build_ai_briefing = service_call('app.modules.trading.service', 'build_ai_briefing')
create_factor_definition = service_call('app.modules.research.service', 'create_factor_definition')
create_news_impact_graph_edge = service_call('app.modules.news.service', 'create_news_impact_graph_edge')
credential_cipher = service_call('app.modules.platform.service', 'credential_cipher')
credential_metadata_for_resource = service_call('app.modules.platform.service', 'credential_metadata_for_resource')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
deliver_event = service_call('app.modules.trading.service', 'deliver_event')
ensure_credential_binding = service_call('app.modules.platform.service', 'ensure_credential_binding')
extract_symbols = service_call('app.modules.news.service', 'extract_symbols')
fetch_information_source_items = service_call('app.modules.news.service', 'fetch_information_source_items')
get_credential = service_call('app.modules.platform.service', 'get_credential')
get_market_provider = service_call('app.modules.market.service', 'get_market_provider')
get_news_event_annotation = service_call('app.modules.news.service', 'get_news_event_annotation')
get_portfolio_summary = service_call('app.modules.trading.service', 'get_portfolio_summary')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
insert_news_symbol_tag = service_call('app.modules.news.service', 'insert_news_symbol_tag')
list_watchlist_snapshots = service_call('app.modules.market.service', 'list_watchlist_snapshots')
materialize_news_primary_symbols = service_call('app.modules.news.service', 'materialize_news_primary_symbols')
news_instrument_catalog = service_call('app.modules.news.service', 'news_instrument_catalog')
news_symbol_override = service_call('app.modules.news.service', 'news_symbol_override')
normalize_symbols = service_call('app.modules.news.service', 'normalize_symbols')
normalize_tag_symbol = service_call('app.modules.news.service', 'normalize_tag_symbol')
normalized_ratio = service_call('app.modules.news.service', 'normalized_ratio')
persist_news_annotation_pipeline = service_call('app.modules.news.service', 'persist_news_annotation_pipeline')
resolve_api_key = service_call('app.modules.platform.service', 'resolve_api_key')
review_news_impact_graph_edge = service_call('app.modules.news.service', 'review_news_impact_graph_edge')
set_credential_secret = service_call('app.modules.platform.service', 'set_credential_secret')
stored_article_symbols = service_call('app.modules.news.service', 'stored_article_symbols')


AGENT_TRIGGER_TYPES = {"manual", "schedule", "news_ingested", "pipeline", "high_impact_news", "alert_triggered", "backtest_completed", "calendar_event_upcoming", "calendar_event_released"}


def model_provider_presets() -> list[ModelProviderPreset]:
    return list(model_gateway_service.PROVIDER_PRESETS.values())


def normalize_model_names(models: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in models if item.strip()))[:MODEL_CATALOG_MAX_ITEMS]


def validate_model_channel_values(provider_type: str, protocol: str | None, base_url: str | None,
                                  api_key_env: str | None) -> tuple[ModelProviderPreset, str, str, str]:
    preset = model_gateway_service.PROVIDER_PRESETS.get(provider_type)
    if preset is None:
        raise HTTPException(status_code=422, detail="Unsupported model provider type")
    resolved_protocol = protocol or preset.protocol
    resolved_url = (base_url or preset.base_url).strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s]+", resolved_url):
        raise HTTPException(status_code=422, detail="Model channel base URL must use HTTP or HTTPS")
    resolved_env = (api_key_env if api_key_env is not None else preset.api_key_env).strip()
    if resolved_env and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", resolved_env):
        raise HTTPException(status_code=422, detail="API key environment variable must use uppercase letters, numbers, and underscores")
    if preset.requires_api_key and not resolved_env:
        raise HTTPException(status_code=422, detail="This provider requires an API key environment alias")
    return preset, str(resolved_protocol), resolved_url, resolved_env


def row_to_model_channel(row: sqlite3.Row, models: list[str] | None = None) -> ModelChannelProfile:
    credential_id, origin, configured, hint, _status = credential_metadata_for_resource(
        "model_channel", row["id"], row["api_key_env"]
    )
    return ModelChannelProfile(
        id=row["id"], name=row["name"], provider_type=row["provider_type"], protocol=row["protocol"],
        base_url=row["base_url"], api_key_env=row["api_key_env"], timeout_seconds=row["timeout_seconds"],
        config=json_value(row["config"], {}), enabled=bool(row["enabled"]),
        health_status="disabled" if not row["enabled"] else row["health_status"],
        consecutive_failures=row["consecutive_failures"], response_time_ms=row["response_time_ms"],
        last_tested_at=row["last_tested_at"], last_error=row["last_error"], secret_configured=configured,
        credential_id=credential_id, credential_origin=origin, secret_hint=hint, models=models or [],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def list_model_channels() -> list[ModelChannelProfile]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM model_channels ORDER BY enabled DESC, name").fetchall()
        model_rows = connection.execute(
            "SELECT channel_id, model FROM model_channel_models WHERE enabled=1 ORDER BY model"
        ).fetchall()
    models: dict[str, list[str]] = {}
    for item in model_rows:
        models.setdefault(item["channel_id"], []).append(item["model"])
    return [row_to_model_channel(row, models.get(row["id"], [])) for row in rows]


def get_model_channel(channel_id: str, require_enabled: bool = False) -> ModelChannelProfile:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone()
        models = [item["model"] for item in connection.execute(
            "SELECT model FROM model_channel_models WHERE channel_id=? AND enabled=1 ORDER BY model", (channel_id,)
        ).fetchall()]
    if row is None:
        raise HTTPException(status_code=404, detail="Model channel not found")
    channel = row_to_model_channel(row, models)
    if require_enabled and not channel.enabled:
        raise HTTPException(status_code=409, detail="Model channel is disabled")
    return channel


def save_channel_models(connection: sqlite3.Connection, channel_id: str, models: list[str], source: str = "manual") -> None:
    now = datetime.now(timezone.utc).isoformat()
    normalized = normalize_model_names(models)
    if not normalized:
        return
    for model in normalized:
        connection.execute(
            """INSERT INTO model_channel_models
               (channel_id, model, source, capabilities, enabled, created_at, updated_at)
               VALUES (?, ?, ?, '["chat","json"]', 1, ?, ?)
               ON CONFLICT(channel_id, model) DO UPDATE SET source=excluded.source, enabled=1, updated_at=excluded.updated_at""",
            (channel_id, model, source, now, now),
        )


def create_model_channel(payload: ModelChannelCreate) -> ModelChannelProfile:
    init_db()
    if payload.api_key is not None:
        credential_cipher()
    preset, protocol, base_url, env_name = validate_model_channel_values(
        payload.provider_type, payload.protocol, payload.base_url, payload.api_key_env
    )
    now = datetime.now(timezone.utc).isoformat()
    channel_id = f"channel-{sha256(f'{payload.name}:{now}'.encode()).hexdigest()[:14]}"
    models = normalize_model_names(payload.models) or preset.models
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO model_channels
               (id,name,provider_type,protocol,base_url,api_key_env,timeout_seconds,config,enabled,health_status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,'unknown',?,?)""",
            (channel_id, payload.name.strip(), payload.provider_type, protocol, base_url, env_name,
             payload.timeout_seconds, json.dumps(payload.config, ensure_ascii=False), int(payload.enabled), now, now),
        )
        save_channel_models(connection, channel_id, models, "preset" if not payload.models else "manual")
    credential_id = ensure_credential_binding("model_channel", channel_id, payload.name.strip(), env_name)
    if payload.api_key is not None and credential_id:
        set_credential_secret(credential_id, payload.api_key.get_secret_value())
    return get_model_channel(channel_id)


def update_model_channel(channel_id: str, payload: ModelChannelUpdate) -> ModelChannelProfile:
    current = get_model_channel(channel_id)
    values = current.model_dump(exclude={"id", "secret_configured", "credential_id", "credential_origin", "secret_hint",
                                         "health_status", "consecutive_failures", "response_time_ms", "last_tested_at",
                                         "last_error", "created_at", "updated_at", "models"})
    values.update(payload.model_dump(exclude_none=True, exclude={"api_key", "models"}))
    _preset, protocol, base_url, env_name = validate_model_channel_values(
        str(values["provider_type"]), str(values["protocol"]), str(values["base_url"]), str(values["api_key_env"])
    )
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """UPDATE model_channels SET name=?,provider_type=?,protocol=?,base_url=?,api_key_env=?,timeout_seconds=?,
               config=?,enabled=?,health_status=CASE WHEN ?=1 AND health_status='disabled' THEN 'unknown' ELSE health_status END,
               updated_at=? WHERE id=?""",
            (values["name"], values["provider_type"], protocol, base_url, env_name, values["timeout_seconds"],
             json.dumps(values["config"], ensure_ascii=False), int(values["enabled"]), int(values["enabled"]), now, channel_id),
        )
        if payload.models is not None:
            connection.execute("UPDATE model_channel_models SET enabled=0, updated_at=? WHERE channel_id=?", (now, channel_id))
            save_channel_models(connection, channel_id, payload.models)
    credential_id = ensure_credential_binding("model_channel", channel_id, str(values["name"]), env_name)
    if payload.api_key is not None:
        if not credential_id:
            raise HTTPException(status_code=422, detail="API key environment alias is required")
        set_credential_secret(credential_id, payload.api_key.get_secret_value())
    return get_model_channel(channel_id)


def delete_model_channel(channel_id: str) -> None:
    get_model_channel(channel_id)
    with db_connect() as connection:
        refs = connection.execute("SELECT COUNT(*) AS total FROM model_route_targets WHERE channel_id=?", (channel_id,)).fetchone()["total"]
        if refs:
            raise HTTPException(status_code=409, detail=f"Model channel is referenced by {refs} route target(s)")
        connection.execute("DELETE FROM model_channel_models WHERE channel_id=?", (channel_id,))
        connection.execute("DELETE FROM credential_bindings WHERE resource_type='model_channel' AND resource_id=?", (channel_id,))
        connection.execute("DELETE FROM model_channels WHERE id=?", (channel_id,))


def row_to_model_route(row: sqlite3.Row, targets: list[ModelRouteTarget]) -> ModelRouteProfile:
    return ModelRouteProfile(id=row["id"], name=row["name"], alias=row["alias"], description=row["description"],
                             temperature=row["temperature"], max_tokens=row["max_tokens"], enabled=bool(row["enabled"]),
                             targets=targets, created_at=row["created_at"], updated_at=row["updated_at"])


def list_model_routes() -> list[ModelRouteProfile]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM model_routes ORDER BY enabled DESC, name").fetchall()
        target_rows = connection.execute(
            """SELECT t.*, c.name AS channel_name, c.health_status, c.enabled AS channel_enabled
               FROM model_route_targets t JOIN model_channels c ON c.id=t.channel_id
               ORDER BY t.priority DESC, t.weight DESC"""
        ).fetchall()
    grouped: dict[str, list[ModelRouteTarget]] = {}
    for item in target_rows:
        grouped.setdefault(item["route_id"], []).append(ModelRouteTarget(
            id=item["id"], route_id=item["route_id"], channel_id=item["channel_id"], channel_name=item["channel_name"],
            model=item["model"], priority=item["priority"], weight=item["weight"], enabled=bool(item["enabled"]),
            health_status="disabled" if not item["channel_enabled"] else item["health_status"],
        ))
    return [row_to_model_route(row, grouped.get(row["id"], [])) for row in rows]


def get_model_route(route_id: str, require_enabled: bool = False) -> ModelRouteProfile:
    route = next((item for item in list_model_routes() if item.id == route_id), None)
    if route is None:
        raise HTTPException(status_code=404, detail="Model route not found")
    if require_enabled and not route.enabled:
        raise HTTPException(status_code=409, detail="Model route is disabled")
    return route


def validate_route_alias(alias: str) -> str:
    value = alias.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,99}", value):
        raise HTTPException(status_code=422, detail="Route alias may contain lowercase letters, numbers, dots, dashes and underscores")
    return value


def replace_route_targets(connection: sqlite3.Connection, route_id: str, targets: list[ModelRouteTargetInput]) -> None:
    connection.execute("DELETE FROM model_route_targets WHERE route_id=?", (route_id,))
    now = datetime.now(timezone.utc).isoformat()
    for index, target in enumerate(targets):
        channel = connection.execute("SELECT id FROM model_channels WHERE id=?", (target.channel_id,)).fetchone()
        if channel is None:
            raise HTTPException(status_code=422, detail=f"Unknown model channel: {target.channel_id}")
        target_id = f"target-{sha256(f'{route_id}:{target.channel_id}:{target.model}:{index}'.encode()).hexdigest()[:14]}"
        connection.execute(
            "INSERT INTO model_route_targets VALUES (?,?,?,?,?,?,?,?,?)",
            (target_id, route_id, target.channel_id, target.model.strip(), target.priority, target.weight,
             int(target.enabled), now, now),
        )
        save_channel_models(connection, target.channel_id, [target.model])


def create_model_route(payload: ModelRouteCreate) -> ModelRouteProfile:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    route_id = f"route-{sha256(f'{payload.alias}:{now}'.encode()).hexdigest()[:14]}"
    try:
        with db_connect() as connection:
            connection.execute("INSERT INTO model_routes VALUES (?,?,?,?,?,?,?,?,?)", (
                route_id, payload.name.strip(), validate_route_alias(payload.alias), payload.description.strip(),
                payload.temperature, payload.max_tokens, int(payload.enabled), now, now,
            ))
            replace_route_targets(connection, route_id, payload.targets)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Model route alias already exists") from exc
    return get_model_route(route_id)


def update_model_route(route_id: str, payload: ModelRouteUpdate) -> ModelRouteProfile:
    current = get_model_route(route_id)
    values = current.model_dump(exclude={"id", "targets", "created_at", "updated_at"})
    values.update(payload.model_dump(exclude_none=True, exclude={"targets"}))
    now = datetime.now(timezone.utc).isoformat()
    try:
        with db_connect() as connection:
            connection.execute(
                "UPDATE model_routes SET name=?,alias=?,description=?,temperature=?,max_tokens=?,enabled=?,updated_at=? WHERE id=?",
                (values["name"], validate_route_alias(str(values["alias"])), values["description"], values["temperature"],
                 values["max_tokens"], int(values["enabled"]), now, route_id),
            )
            if payload.targets is not None:
                replace_route_targets(connection, route_id, payload.targets)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Model route alias already exists") from exc
    return get_model_route(route_id)


def delete_model_route(route_id: str) -> None:
    get_model_route(route_id)
    with db_connect() as connection:
        refs = connection.execute("SELECT COUNT(*) AS total FROM agent_configs WHERE model_route_id=?", (route_id,)).fetchone()["total"]
        if refs:
            raise HTTPException(status_code=409, detail=f"Model route is used by {refs} Agent(s)")
        connection.execute("DELETE FROM model_route_targets WHERE route_id=?", (route_id,))
        connection.execute("DELETE FROM model_routes WHERE id=?", (route_id,))


def resolve_model_channel_key(channel: ModelChannelProfile) -> str:
    if not channel.api_key_env:
        return ""
    return resolve_api_key("model_channel", channel.id, channel.api_key_env) or resolve_api_key(
        "agent_provider", channel.id, channel.api_key_env
    )


def redact_model_error(error: Exception | str, secret: str = "") -> str:
    detail = str(error)
    if secret:
        detail = detail.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(bearer|api[-_ ]?key)[=: ]+[^\s,;]+", r"\1=[REDACTED]", detail)[:1000]


def record_model_invocation(request_id: str, source: str, route_id: str | None, channel: ModelChannelProfile | None,
                            model: str | None, status: str, started: datetime, prompt_tokens: int = 0,
                            completion_tokens: int = 0, error: str | None = None) -> ModelInvocation:
    created_at = datetime.now(timezone.utc)
    channel_key = channel.id if channel else "none"
    invocation_seed = f"{request_id}:{channel_key}:{created_at.isoformat()}"
    invocation_id = f"invoke-{sha256(invocation_seed.encode()).hexdigest()[:16]}"
    latency = int((created_at - started).total_seconds() * 1000)
    with db_connect() as connection:
        connection.execute(
            "INSERT INTO model_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (invocation_id, request_id, source, route_id, channel.id if channel else None,
             channel.name if channel else None, model, status, prompt_tokens, completion_tokens,
             latency, error, created_at.isoformat()),
        )
    return ModelInvocation(id=invocation_id, request_id=request_id, source=source, route_id=route_id,
                           channel_id=channel.id if channel else None, channel_name=channel.name if channel else None,
                           model=model, status=status, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                           latency_ms=latency, error=error, created_at=created_at.isoformat())


def list_model_invocations(limit: int = 100, status: str | None = None) -> list[ModelInvocation]:
    init_db()
    query = "SELECT * FROM model_invocations"
    params: list[object] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(500, limit)))
    with db_connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [ModelInvocation(**dict(row)) for row in rows]


def model_gateway_summary() -> ModelGatewaySummary:
    init_db()
    with db_connect() as connection:
        channels = connection.execute("SELECT COUNT(*) AS total FROM model_channels").fetchone()["total"]
        healthy = connection.execute("SELECT COUNT(*) AS total FROM model_channels WHERE enabled=1 AND health_status='healthy'").fetchone()["total"]
        routes = connection.execute("SELECT COUNT(*) AS total FROM model_routes WHERE enabled=1").fetchone()["total"]
        recent = connection.execute(
            """SELECT COUNT(*) AS total, SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                      AVG(latency_ms) AS latency, SUM(prompt_tokens + completion_tokens) AS tokens,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failures
               FROM model_invocations WHERE created_at >= ?""",
            ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
        ).fetchone()
    total = int(recent["total"] or 0)
    return ModelGatewaySummary(channels=channels, healthy_channels=healthy, routes=routes,
                               success_rate=round(int(recent["succeeded"] or 0) * 100 / total, 1) if total else None,
                               average_latency_ms=round(recent["latency"]) if recent["latency"] is not None else None,
                               total_tokens=int(recent["tokens"] or 0), recent_failures=int(recent["failures"] or 0))


def update_model_channel_health(channel_id: str, success: bool, latency_ms: int, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        row = connection.execute("SELECT consecutive_failures FROM model_channels WHERE id=?", (channel_id,)).fetchone()
        if row is None:
            return
        failures = 0 if success else int(row["consecutive_failures"]) + 1
        status = "healthy" if success else "unavailable" if failures >= 3 else "degraded"
        connection.execute(
            """UPDATE model_channels SET health_status=?,consecutive_failures=?,response_time_ms=?,
               last_tested_at=?,last_error=?,updated_at=? WHERE id=?""",
            (status, failures, latency_ms, now, None if success else error, now, channel_id),
        )


def ordered_route_targets(route: ModelRouteProfile, channel_id: str | None = None) -> list[ModelRouteTarget]:
    targets = [item for item in route.targets if item.enabled and (not channel_id or item.channel_id == channel_id)]
    if channel_id and not targets:
        raise HTTPException(status_code=422, detail="Pinned channel is not part of this model route")
    available: list[ModelRouteTarget] = []
    now = datetime.now(timezone.utc)
    for target in targets:
        channel = get_model_channel(target.channel_id)
        if not channel.enabled:
            continue
        if channel.health_status == "unavailable" and channel.last_tested_at:
            tested = datetime.fromisoformat(channel.last_tested_at.replace("Z", "+00:00"))
            if now - tested < timedelta(minutes=5) and not channel_id:
                continue
        available.append(target)
    ordered: list[ModelRouteTarget] = []
    for priority in sorted({item.priority for item in available}, reverse=True):
        pool = [item for item in available if item.priority == priority]
        while pool:
            weights = [max(1, item.weight) for item in pool]
            chosen = random.choices(pool, weights=weights, k=1)[0]
            ordered.append(chosen)
            pool.remove(chosen)
    return ordered[:3]


def run_model_route(route_id: str, messages: list[dict[str, str]], system_prompt: str = "",
                    temperature: float | None = None, max_tokens: int | None = None,
                    source: str = "agent", channel_id: str | None = None) -> tuple[model_gateway_service.GatewayResponse, ModelChannelProfile, str]:
    route = get_model_route(route_id, require_enabled=True)
    targets = ordered_route_targets(route, channel_id)
    if not targets:
        raise RuntimeError("No healthy model route target is available")
    request_id = f"model-{secrets.token_hex(10)}"
    errors: list[str] = []
    for target in targets:
        channel = get_model_channel(target.channel_id, require_enabled=True)
        key = resolve_model_channel_key(channel)
        preset = model_gateway_service.PROVIDER_PRESETS[channel.provider_type]
        if preset.requires_api_key and not key:
            detail = f"Credential {channel.api_key_env} is not configured"
            record_model_invocation(request_id, source, route.id, channel, target.model, "failed", datetime.now(timezone.utc), error=detail)
            errors.append(f"{channel.name}: {detail}")
            continue
        started = datetime.now(timezone.utc)
        try:
            response = model_gateway_service.call_model(
                channel, target.model, messages, system_prompt,
                route.temperature if temperature is None else temperature,
                route.max_tokens if max_tokens is None else max_tokens, key,
            )
            latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            update_model_channel_health(channel.id, True, latency)
            record_model_invocation(request_id, source, route.id, channel, target.model, "succeeded", started,
                                    response.prompt_tokens, response.completion_tokens)
            return response, channel, target.model
        except Exception as exc:
            latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            detail = redact_model_error(exc, key)
            update_model_channel_health(channel.id, False, latency, detail)
            record_model_invocation(request_id, source, route.id, channel, target.model, "failed", started, error=detail)
            errors.append(f"{channel.name}: {detail}")
            if isinstance(exc, model_gateway_service.GatewayUpstreamError) and exc.status_code in {400, 404, 422}:
                break
    raise RuntimeError("; ".join(errors)[:1000])


def test_model_channel(channel_id: str, model: str | None = None) -> dict[str, object]:
    channel = get_model_channel(channel_id)
    selected = model or (channel.models[0] if channel.models else None)
    if not selected:
        raise HTTPException(status_code=422, detail="Configure at least one model before testing this channel")
    key = resolve_model_channel_key(channel)
    preset = model_gateway_service.PROVIDER_PRESETS[channel.provider_type]
    if preset.requires_api_key and not key:
        raise HTTPException(status_code=409, detail=f"Credential {channel.api_key_env} is not configured")
    started = datetime.now(timezone.utc)
    request_id = f"health-{secrets.token_hex(10)}"
    try:
        response = model_gateway_service.call_model(channel, selected, [{"role": "user", "content": "Reply with OK."}], "", 0, 16, key)
    except Exception as exc:
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        detail = redact_model_error(exc, key)
        update_model_channel_health(channel.id, False, latency, detail)
        record_model_invocation(request_id, "health_test", None, channel, selected, "failed", started, error=detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    update_model_channel_health(channel.id, True, latency)
    record_model_invocation(request_id, "health_test", None, channel, selected, "succeeded", started,
                            response.prompt_tokens, response.completion_tokens)
    return {"ok": True, "latency_ms": latency, "model": selected, "response": response.text[:120]}


def sync_model_channel_models(channel_id: str) -> ModelChannelProfile:
    channel = get_model_channel(channel_id)
    key = resolve_model_channel_key(channel)
    preset = model_gateway_service.PROVIDER_PRESETS[channel.provider_type]
    if not preset.supports_model_sync:
        raise HTTPException(status_code=409, detail="This provider does not expose model discovery")
    if preset.requires_api_key and not key:
        raise HTTPException(status_code=409, detail=f"Credential {channel.api_key_env} is not configured")
    try:
        models = model_gateway_service.fetch_models(channel, key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=redact_model_error(exc, key)) from exc
    with db_connect() as connection:
        save_channel_models(connection, channel.id, models, "upstream")
    return get_model_channel(channel.id)


def stream_playground_chat(payload: PlaygroundChatRequest):
    route = get_model_route(payload.route_id, require_enabled=True)
    targets = ordered_route_targets(route, payload.channel_id)
    request_id = f"play-{secrets.token_hex(10)}"
    if not targets:
        yield {"type": "error", "request_id": request_id, "message": "No healthy model route target is available"}
        return
    messages = [item.model_dump() for item in payload.messages]
    errors: list[str] = []
    for target in targets:
        channel = get_model_channel(target.channel_id, require_enabled=True)
        key = resolve_model_channel_key(channel)
        preset = model_gateway_service.PROVIDER_PRESETS[channel.provider_type]
        if preset.requires_api_key and not key:
            errors.append(f"{channel.name}: credential missing")
            continue
        started = datetime.now(timezone.utc)
        prompt_tokens = completion_tokens = 0
        emitted = False
        try:
            yield {"type": "meta", "request_id": request_id, "route_id": route.id,
                   "channel_id": channel.id, "channel_name": channel.name, "model": target.model}
            for event in model_gateway_service.stream_model(
                channel, target.model, messages, payload.system_prompt,
                route.temperature if payload.temperature is None else payload.temperature,
                route.max_tokens if payload.max_tokens is None else payload.max_tokens, key,
            ):
                if event.get("type") == "delta" and event.get("text"):
                    emitted = True
                if event.get("type") == "usage":
                    prompt_tokens = int(event.get("prompt_tokens", prompt_tokens))
                    completion_tokens = int(event.get("completion_tokens", completion_tokens))
                yield event
            latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            update_model_channel_health(channel.id, True, latency)
            record_model_invocation(request_id, "playground", route.id, channel, target.model, "succeeded", started,
                                    prompt_tokens, completion_tokens)
            yield {"type": "done", "request_id": request_id, "latency_ms": latency,
                   "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
            return
        except Exception as exc:
            latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            detail = redact_model_error(exc, key)
            update_model_channel_health(channel.id, False, latency, detail)
            record_model_invocation(request_id, "playground", route.id, channel, target.model, "failed", started, error=detail)
            errors.append(f"{channel.name}: {detail}")
            if emitted:
                yield {"type": "error", "request_id": request_id, "message": detail}
                return
            if isinstance(exc, model_gateway_service.GatewayUpstreamError) and exc.status_code in {400, 404, 422}:
                break
    yield {"type": "error", "request_id": request_id, "message": "; ".join(errors)[:1000]}


def json_value(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def row_to_agent_provider(row: sqlite3.Row) -> AgentProviderProfile:
    credential_id, origin, configured, secret_hint, test_status = credential_metadata_for_resource(
        "agent_provider", row["id"], row["api_key_env"]
    )
    return AgentProviderProfile(
        id=row["id"], name=row["name"], base_url=row["base_url"], model=row["model"],
        api_key_env=row["api_key_env"], timeout_seconds=row["timeout_seconds"],
        temperature=row["temperature"], max_tokens=row["max_tokens"], enabled=bool(row["enabled"]),
        secret_configured=configured,
        credential_id=credential_id,
        credential_origin=origin,
        secret_hint=secret_hint,
        credential_test_status=test_status,
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def row_to_agent_config(row: sqlite3.Row) -> AgentConfig:
    return AgentConfig(
        id=row["id"], template_type=row["template_type"], builtin=bool(row["builtin"]), name=row["name"],
        description=row["description"], enabled=bool(row["enabled"]), runtime_mode=row["runtime_mode"],
        provider_id=row["provider_id"], model_route_id=row["model_route_id"], symbol_scope=row["symbol_scope"], symbols=json_value(row["symbols"], []),
        schedule_enabled=bool(row["schedule_enabled"]), interval_seconds=row["interval_seconds"],
        triggers=json_value(row["triggers"], []), system_prompt=row["system_prompt"], task_prompt=row["task_prompt"],
        settings=json_value(row["settings"], {}), notification_channel_ids=json_value(row["notification_channel_ids"], []),
        fallback_to_rules=bool(row["fallback_to_rules"]), last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def row_to_agent_run(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"], agent_id=row["agent_id"], agent_name=row["agent_name"], trigger=row["trigger"],
        symbol=row["symbol"], status=row["status"], runtime_mode=row["runtime_mode"], output=json_value(row["output"], None),
        error=row["error"], model=row["model"], prompt_tokens=row["prompt_tokens"], completion_tokens=row["completion_tokens"],
        latency_ms=row["latency_ms"], test=bool(row["test"]), created_at=row["created_at"],
        started_at=row["started_at"], finished_at=row["finished_at"],
    )


_AGENT_INPUT_SENSITIVE_KEY = re.compile(r"(?i)(api[-_]?key|token|secret|password|authorization|credential|cookie)")


_AGENT_INPUT_MAX_BYTES = 256 * 1024


def redact_agent_input_text(value: str) -> str:
    redacted = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return re.sub(
        r'(?i)(["\']?(?:api[-_]?key|token|secret|password|authorization|credential|cookie)["\']?\s*[:=]\s*)(["\'][^"\']*["\']|[^,}\s]+)',
        r"\1[REDACTED]",
        redacted,
    )


def sanitize_agent_input(
    value: object,
    path: str = "",
    truncated: list[str] | None = None,
    max_string: int = 120_000,
    max_list: int = 1_000,
) -> object:
    truncated = truncated if truncated is not None else []
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _AGENT_INPUT_SENSITIVE_KEY.search(key):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = sanitize_agent_input(item, child_path, truncated, max_string, max_list)
        return cleaned
    if isinstance(value, (list, tuple)):
        items = list(value)
        if len(items) > max_list:
            truncated.append(path or "list")
            items = items[:max_list]
        return [sanitize_agent_input(item, f"{path}[{index}]", truncated, max_string, max_list) for index, item in enumerate(items)]
    if isinstance(value, str):
        cleaned = redact_agent_input_text(value)
        if len(cleaned) > max_string:
            truncated.append(path or "text")
            return f"{cleaned[:max_string]}\n...[TRUNCATED {len(cleaned) - max_string} characters]"
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_agent_input_text(str(value))


def bounded_agent_input_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    for max_string, max_list in [(120_000, 1_000), (60_000, 200), (8_000, 50)]:
        truncated: list[str] = []
        cleaned = sanitize_agent_input(snapshot, truncated=truncated, max_string=max_string, max_list=max_list)
        assert isinstance(cleaned, dict)
        existing = list(cleaned.get("truncated_fields", [])) if isinstance(cleaned.get("truncated_fields"), list) else []
        cleaned["truncated_fields"] = list(dict.fromkeys([*existing, *truncated]))
        encoded = json.dumps(cleaned, ensure_ascii=False)
        if len(encoded.encode("utf-8")) <= _AGENT_INPUT_MAX_BYTES:
            return cleaned
    return {
        "version": 1,
        "captured_at": snapshot.get("captured_at"),
        "capture_error": "Input snapshot exceeded the 256KB storage limit.",
        "preview": encoded[:220_000],
        "truncated_fields": ["snapshot"],
    }


def save_agent_input_snapshot(run_id: str, snapshot: dict[str, object]) -> dict[str, object]:
    bounded = bounded_agent_input_snapshot(snapshot)
    with db_connect() as connection:
        connection.execute(
            "UPDATE agent_runs SET input_snapshot=? WHERE id=?",
            (json.dumps(bounded, ensure_ascii=False), run_id),
        )
    return bounded


def get_agent_run_detail(run_id: str) -> AgentRunDetail:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    run = row_to_agent_run(row)
    trigger_input = sanitize_agent_input(json_value(row["input_payload"], {}))
    snapshot = json_value(row["input_snapshot"], None)
    sanitized_snapshot = sanitize_agent_input(snapshot) if snapshot is not None else None
    return AgentRunDetail(
        **run.model_dump(),
        trigger_input=trigger_input if isinstance(trigger_input, dict) else {},
        input_snapshot=sanitized_snapshot if isinstance(sanitized_snapshot, dict) else None,
    )


def row_to_agent_proposal(row: sqlite3.Row) -> AgentProposal:
    return AgentProposal(
        id=row["id"], run_id=row["run_id"], agent_id=row["agent_id"], resource_type=row["resource_type"],
        action=row["action"], title=row["title"], payload=json_value(row["payload"], {}), status=row["status"],
        reviewed_by=row["reviewed_by"], review_note=row["review_note"], created_at=row["created_at"], reviewed_at=row["reviewed_at"],
    )


def normalize_agent_symbols(symbols: list[str]) -> list[str]:
    return list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))[:100]


def validate_agent_triggers(triggers: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(triggers or ["manual"]))
    invalid = set(normalized) - AGENT_TRIGGER_TYPES
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unsupported Agent triggers: {', '.join(sorted(invalid))}")
    return normalized


def validate_agent_provider_input(base_url: str, api_key_env: str) -> tuple[str, str]:
    url = base_url.strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s]+", url):
        raise HTTPException(status_code=422, detail="Provider base URL must use HTTP or HTTPS")
    env_name = api_key_env.strip()
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_name):
        raise HTTPException(status_code=422, detail="API key environment variable must use uppercase letters, numbers, and underscores")
    return url, env_name


def agent_http_post(url: str, payload: dict[str, object], api_key: str, timeout: int) -> dict[str, object]:
    request = UrlRequest(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Agent provider request failed: {exc}") from exc
    if not isinstance(body, dict):
        raise RuntimeError("Agent provider returned a non-object response")
    return body


def list_agent_providers() -> list[AgentProviderProfile]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM agent_provider_profiles ORDER BY enabled DESC, name").fetchall()
    return [row_to_agent_provider(row) for row in rows]


def get_agent_provider(provider_id: str, require_enabled: bool = False) -> AgentProviderProfile:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM agent_provider_profiles WHERE id = ?", (provider_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent provider not found")
    provider = row_to_agent_provider(row)
    if require_enabled and not provider.enabled:
        raise HTTPException(status_code=409, detail="Agent provider is disabled")
    return provider


def create_agent_provider(payload: AgentProviderCreate) -> AgentProviderProfile:
    init_db()
    if payload.api_key is not None:
        credential_cipher()
    base_url, env_name = validate_agent_provider_input(payload.base_url, payload.api_key_env)
    created_at = datetime.now(timezone.utc).isoformat()
    provider_id = f"provider-{sha256(f'{payload.name}:{created_at}'.encode()).hexdigest()[:12]}"
    with db_connect() as connection:
        connection.execute(
            "INSERT INTO agent_provider_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (provider_id, payload.name.strip(), base_url, payload.model.strip(), env_name, payload.timeout_seconds,
             payload.temperature, payload.max_tokens, int(payload.enabled), created_at, created_at),
        )
        row = connection.execute("SELECT * FROM agent_provider_profiles WHERE id = ?", (provider_id,)).fetchone()
    credential_id = ensure_credential_binding("agent_provider", provider_id, payload.name.strip(), env_name)
    if payload.api_key is not None and credential_id is not None:
        set_credential_secret(credential_id, payload.api_key.get_secret_value())
    provider = row_to_agent_provider(row)
    sync_legacy_provider_route(provider)
    return provider


def update_agent_provider(provider_id: str, payload: AgentProviderUpdate) -> AgentProviderProfile:
    if payload.api_key is not None:
        credential_cipher()
    current = get_agent_provider(provider_id)
    secret = payload.api_key.get_secret_value() if payload.api_key is not None else None
    values = current.model_dump(exclude={
        "secret_configured", "credential_id", "credential_origin", "secret_hint",
        "credential_test_status", "created_at", "updated_at",
    })
    values.update(payload.model_dump(exclude_none=True, exclude={"api_key"}))
    base_url, env_name = validate_agent_provider_input(str(values["base_url"]), str(values["api_key_env"]))
    updated_at = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """UPDATE agent_provider_profiles SET name=?, base_url=?, model=?, api_key_env=?, timeout_seconds=?,
               temperature=?, max_tokens=?, enabled=?, updated_at=? WHERE id=?""",
            (values["name"], base_url, values["model"], env_name, values["timeout_seconds"], values["temperature"],
             values["max_tokens"], int(values["enabled"]), updated_at, provider_id),
        )
    credential_id = ensure_credential_binding("agent_provider", provider_id, str(values["name"]), env_name)
    if secret is not None and credential_id is not None:
        set_credential_secret(credential_id, secret)
    provider = get_agent_provider(provider_id)
    sync_legacy_provider_route(provider)
    return provider


def sync_legacy_provider_route(provider: AgentProviderProfile) -> None:
    now = datetime.now(timezone.utc).isoformat()
    route_id = f"route-{provider.id}"
    target_id = f"target-{provider.id}"
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO model_channels
               (id,name,provider_type,protocol,base_url,api_key_env,timeout_seconds,config,enabled,health_status,created_at,updated_at)
               VALUES (?,?,'openai_compatible','openai_chat',?,?,?,'{}',?,'unknown',?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,base_url=excluded.base_url,
                 api_key_env=excluded.api_key_env,timeout_seconds=excluded.timeout_seconds,
                 enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (provider.id, provider.name, provider.base_url, provider.api_key_env, provider.timeout_seconds,
             int(provider.enabled), provider.created_at, now),
        )
        save_channel_models(connection, provider.id, [provider.model], "legacy")
        connection.execute(
            """INSERT INTO model_routes VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,temperature=excluded.temperature,
                 max_tokens=excluded.max_tokens,enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (route_id, provider.name, f"legacy-{provider.id}", "由旧模型服务兼容接口维护",
             provider.temperature, provider.max_tokens, int(provider.enabled), provider.created_at, now),
        )
        connection.execute(
            """INSERT INTO model_route_targets VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET model=excluded.model,enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (target_id, route_id, provider.id, provider.model, 0, 100, int(provider.enabled), provider.created_at, now),
        )
    ensure_credential_binding("model_channel", provider.id, provider.name, provider.api_key_env)


def run_credential_tests(credential_id: str) -> CredentialProfile:
    profile = get_credential(credential_id)
    if not profile.configured:
        status: CredentialTestStatus = "fail"
        message = "凭据未配置或无法解密"
    else:
        results: list[tuple[bool, str]] = []
        for binding in profile.bindings:
            started = datetime.now(timezone.utc)
            try:
                if binding.resource_type == "market_provider":
                    provider = MARKET_PROVIDERS.get(binding.resource_id)
                    if provider is None:
                        raise RuntimeError("行情 Provider 不存在")
                    if binding.resource_id == "finnhub":
                        provider._quote("AAPL")
                elif binding.resource_type == "information_source":
                    source = next(
                        (item for item in list_news_sources() if str(item.id) == binding.resource_id),
                        None,
                    )
                    if source is None:
                        raise RuntimeError("信息源不存在")
                    fetch_information_source_items(source)
                elif binding.resource_type == "agent_provider":
                    provider = get_agent_provider(binding.resource_id)
                    api_key = resolve_api_key("agent_provider", provider.id, provider.api_key_env)
                    agent_http_post(
                        f"{provider.base_url.rstrip('/')}/chat/completions",
                        {
                            "model": provider.model,
                            "messages": [{"role": "user", "content": "Reply with OK."}],
                            "max_tokens": 10,
                            "temperature": 0,
                        },
                        api_key,
                        provider.timeout_seconds,
                    )
                elif binding.resource_type == "model_channel":
                    channel = get_model_channel(binding.resource_id)
                    result = test_model_channel(channel.id)
                    if not result["ok"]:
                        raise RuntimeError("Model channel test failed")
                latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                results.append((True, f"{binding.resource_name} {latency}ms"))
            except Exception as exc:
                secret = resolve_api_key(binding.resource_type, binding.resource_id, profile.env_alias)
                detail = str(exc).replace(secret, "[REDACTED]") if secret else str(exc)
                results.append((False, f"{binding.resource_name}: {detail[:160]}"))
        if not results:
            status = "pass"
            message = "密钥已加密保存，暂无绑定资源"
        else:
            successes = sum(result[0] for result in results)
            status = "pass" if successes == len(results) else "partial" if successes else "fail"
            message = "；".join(result[1] for result in results)[:500]
    tested_at = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE api_credentials
            SET test_status = ?, test_message = ?, last_tested_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, message, tested_at, tested_at, credential_id),
        )
    return get_credential(credential_id)


def schedule_credential_test(credential_id: str) -> CredentialProfile:
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE api_credentials
            SET test_status = 'pending', test_message = '等待连接检测', updated_at = ?
            WHERE id = ?
            """,
            (now, credential_id),
        )
    if os.getenv("APP_CREDENTIAL_ASYNC_TESTS", "true").lower() != "false":
        Thread(target=run_credential_tests, args=(credential_id,), daemon=True).start()
    return get_credential(credential_id)


def list_agents(enabled_only: bool = False) -> list[AgentConfig]:
    init_db()
    query = "SELECT * FROM agent_configs"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY builtin DESC, template_type, name"
    with db_connect() as connection:
        rows = connection.execute(query).fetchall()
    return [row_to_agent_config(row) for row in rows]


def get_agent(agent_id: str, require_enabled: bool = False) -> AgentConfig:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM agent_configs WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = row_to_agent_config(row)
    if require_enabled and not agent.enabled:
        raise HTTPException(status_code=409, detail="Agent is disabled")
    return agent


def create_agent(payload: AgentCreate, builtin: bool = False, agent_id: str | None = None) -> AgentConfig:
    init_db()
    if payload.provider_id:
        get_agent_provider(payload.provider_id)
    model_route_id = payload.model_route_id
    if model_route_id:
        get_model_route(model_route_id)
    if payload.runtime_mode in {"llm", "hybrid"} and not (model_route_id or payload.provider_id):
        raise HTTPException(status_code=422, detail="LLM and hybrid Agents require a model route")
    symbols = normalize_agent_symbols(payload.symbols)
    if payload.symbol_scope == "custom" and not symbols:
        raise HTTPException(status_code=422, detail="Custom symbol scope requires at least one symbol")
    triggers = validate_agent_triggers(payload.triggers)
    now = datetime.now(timezone.utc).isoformat()
    next_run = (datetime.now(timezone.utc) + timedelta(seconds=payload.interval_seconds)).isoformat() if payload.schedule_enabled else None
    resolved_id = agent_id or f"agent-{sha256(f'{payload.name}:{now}'.encode()).hexdigest()[:14]}"
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO agent_configs (
                id, template_type, builtin, name, description, enabled, runtime_mode, provider_id, symbol_scope,
                symbols, schedule_enabled, interval_seconds, triggers, system_prompt, task_prompt, settings,
                notification_channel_ids, fallback_to_rules, last_run_at, next_run_at, created_at, updated_at, model_route_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (resolved_id, payload.template_type, int(builtin), payload.name.strip(), payload.description.strip(), int(payload.enabled),
             payload.runtime_mode, payload.provider_id, payload.symbol_scope, json.dumps(symbols), int(payload.schedule_enabled),
             payload.interval_seconds, json.dumps(triggers), payload.system_prompt.strip(), payload.task_prompt.strip(),
             json.dumps(payload.settings, ensure_ascii=False), json.dumps(payload.notification_channel_ids), int(payload.fallback_to_rules),
             next_run, now, now, model_route_id),
        )
    return get_agent(resolved_id)


def update_agent(agent_id: str, payload: AgentUpdate) -> AgentConfig:
    current = get_agent(agent_id)
    changes = payload.model_dump(exclude_unset=True)
    values = current.model_dump(exclude={"id", "template_type", "builtin", "last_run_at", "next_run_at", "created_at", "updated_at"})
    values.update(changes)
    if "model_route_id" in changes:
        values["provider_id"] = None
    provider_id = values.get("provider_id")
    if provider_id:
        get_agent_provider(str(provider_id))
    model_route_id = values.get("model_route_id")
    if model_route_id:
        get_model_route(str(model_route_id))
    if values["runtime_mode"] in {"llm", "hybrid"} and not (model_route_id or provider_id):
        raise HTTPException(status_code=422, detail="LLM and hybrid Agents require a model route")
    symbols = normalize_agent_symbols(values["symbols"])
    if values["symbol_scope"] == "custom" and not symbols:
        raise HTTPException(status_code=422, detail="Custom symbol scope requires at least one symbol")
    triggers = validate_agent_triggers(values["triggers"])
    now = datetime.now(timezone.utc)
    next_run = (now + timedelta(seconds=int(values["interval_seconds"]))).isoformat() if values["schedule_enabled"] else None
    with db_connect() as connection:
        connection.execute(
            """UPDATE agent_configs SET name=?, description=?, enabled=?, runtime_mode=?, provider_id=?, symbol_scope=?,
               symbols=?, schedule_enabled=?, interval_seconds=?, triggers=?, system_prompt=?, task_prompt=?, settings=?,
               notification_channel_ids=?, fallback_to_rules=?, next_run_at=?, updated_at=?, model_route_id=? WHERE id=?""",
            (values["name"], values["description"], int(values["enabled"]), values["runtime_mode"], provider_id,
             values["symbol_scope"], json.dumps(symbols), int(values["schedule_enabled"]), values["interval_seconds"],
             json.dumps(triggers), values["system_prompt"], values["task_prompt"], json.dumps(values["settings"], ensure_ascii=False),
             json.dumps(values["notification_channel_ids"]), int(values["fallback_to_rules"]), next_run, now.isoformat(), model_route_id, agent_id),
        )
    return get_agent(agent_id)


def clone_agent(agent_id: str) -> AgentConfig:
    source = get_agent(agent_id)
    data = source.model_dump(exclude={"id", "builtin", "last_run_at", "next_run_at", "created_at", "updated_at"})
    data.update({"name": f"{source.name} Copy", "enabled": False, "schedule_enabled": False})
    payload = AgentCreate(**data)
    return create_agent(payload)


def resolve_agent_symbols(agent: AgentConfig, requested_symbol: str | None = None) -> list[str]:
    if requested_symbol:
        return [requested_symbol.upper()]
    if agent.symbol_scope == "custom":
        return agent.symbols
    if agent.symbol_scope == "all":
        return [item.symbol for item in get_market_provider().list_symbols()]
    return [item.symbol for item in list_watchlist_snapshots()]


def news_tagger_context(article_id: str) -> dict[str, object]:
    with db_connect() as connection:
        row = connection.execute(
            """SELECT a.*, s.symbols AS source_symbols FROM news_articles a
               JOIN news_sources s ON s.id = a.source_id WHERE a.id = ?""",
            (article_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("News article was not found")
    catalog = list({item["symbol"]: item for item in news_instrument_catalog().values()}.values())
    return {
        "article_id": article_id,
        "title": row["title"],
        "summary": row["summary"],
        "source": row["source_name"],
        "published_at": row["published_at"],
        "source_hints": normalize_symbols(row["source_symbols"]),
        "current_symbols": stored_article_symbols(row["symbols"]),
        "instrument_catalog": catalog,
    }


def agent_rule_output(agent: AgentConfig, symbol: str | None, context: dict[str, object]) -> dict[str, object]:
    if agent.template_type == "news_tagger":
        article_id = str(context.get("article_id") or "")
        if not article_id:
            return {
                "article_id": "", "summary": "当前没有可供测试的新闻文章。", "risk_level": "info",
                "confidence": 0, "symbols": [], "tags": [], "evidence": [], "actions": [],
                "proposals": [], "created_at": datetime.now(timezone.utc).isoformat(),
            }
        article = news_tagger_context(article_id)
        matched = extract_symbols(str(article["title"]), str(article["summary"]), list(article["current_symbols"]))
        tags = [
            {"symbol": item, "relation": "primary", "confidence": 0.92, "direction": "neutral",
             "evidence": "正文精确匹配标的代码或名称"}
            for item in matched[:int(agent.settings.get("max_primary", 5))]
        ]
        return {
            "article_id": article_id, "summary": f"识别出 {len(tags)} 个主要关联标的。",
            "risk_level": "info", "confidence": int(max((item["confidence"] for item in tags), default=0) * 100),
            "symbols": [item["symbol"] for item in tags], "tags": tags,
            "evidence": [item["evidence"] for item in tags], "actions": [], "proposals": [],
            "article": article, "created_at": datetime.now(timezone.utc).isoformat(),
        }
    if agent.template_type == "economic_event_analyst":
        from app.modules.calendar.service import get_event
        event_id = str(context.get("event_id") or "")
        if not event_id:
            return {"summary": "缺少财经事件上下文。", "risk_level": "watch", "confidence": 0,
                    "symbols": [], "affected_markets": [], "scenarios": [], "risks": ["无法识别事件"],
                    "evidence": [], "actions": [], "proposals": [], "created_at": datetime.now(timezone.utc).isoformat()}
        event = get_event(event_id).event
        country_markets = {
            "US": ["美股", "美元", "美债"], "UNITED STATES": ["美股", "美元", "美债"],
            "CN": ["A股", "人民币", "港股"], "CHINA": ["A股", "人民币", "港股"],
            "EU": ["欧洲股市", "欧元"], "EURO AREA": ["欧洲股市", "欧元"],
            "GB": ["英国股市", "英镑"], "UNITED KINGDOM": ["英国股市", "英镑"],
            "JP": ["日本股市", "日元"], "JAPAN": ["日本股市", "日元"],
            "HK": ["港股", "港元"], "HONG KONG": ["港股", "港元"],
        }
        affected = country_markets.get(str(event.country or "").upper(), ["全球风险资产"])
        symbols = [event.symbol] if event.symbol else []
        evidence = [f"事件：{event.title}", f"重要度：{event.importance}", f"公布时间：{event.scheduled_at}"]
        if event.forecast is not None:
            evidence.append(f"市场预期：{event.forecast}{event.unit or ''}")
        if event.previous is not None:
            evidence.append(f"前值：{event.previous}{event.unit or ''}")
        if event.actual is not None:
            evidence.append(f"公布值：{event.actual}{event.unit or ''}")
        released = event.status in {"released", "revised"}
        if released and event.surprise_direction:
            summary = f"{event.title} 已公布，结果相对预期为 {event.surprise_direction}，需结合指标含义判断资产方向。"
        else:
            summary = f"{event.title} 尚未公布，重点观察实际值相对预期和前值的偏离。"
        return {
            "summary": summary, "risk_level": "risk" if event.importance == "high" else "watch",
            "confidence": 72 if event.forecast is not None else 58, "symbols": symbols,
            "affected_markets": affected,
            "scenarios": ["高于预期：评估通胀、增长或盈利含义后观察风险资产与利率联动。",
                          "低于预期：关注增长担忧、政策预期变化及避险资产反应。"],
            "risks": ["事件公布初期波动和点差可能快速扩大。", "单一数据不应脱离趋势与后续修订独立判断。"],
            "evidence": evidence, "actions": ["等待实际值并复核预期差。"], "proposals": [],
            "event": event.model_dump(mode="json"), "created_at": datetime.now(timezone.utc).isoformat(),
        }
    symbols = resolve_agent_symbols(agent, symbol)
    snapshots = list_watchlist_snapshots()
    selected = [item for item in snapshots if item.symbol in symbols] or snapshots[:1]
    created_at = datetime.now(timezone.utc).isoformat()
    settings = agent.settings
    if agent.template_type == "briefing":
        briefing = build_ai_briefing()
        return {"summary": briefing.summary, "risk_level": briefing.risk_level, "confidence": 78,
                "symbols": symbols, "evidence": briefing.evidence, "actions": briefing.actions,
                "opportunities": briefing.opportunities, "risks": briefing.risks, "created_at": created_at, "proposals": []}
    if agent.template_type == "news_analyst":
        news = []
        for item_symbol in symbols[:6]:
            news.extend(news_events(item_symbol, limit=3))
        news = sorted(news, key=lambda item: item.impact_score, reverse=True)[:int(settings.get("news_limit", 8))]
        top_impact = max((item.impact_score for item in news), default=0)
        bearish = sum(item.sentiment == "bearish" for item in news)
        risk = "risk" if top_impact >= 75 and bearish else "watch" if top_impact >= 60 else "info"
        return {"summary": f"分析 {len(news)} 条相关新闻，最高影响分 {top_impact}，负面事件 {bearish} 条。",
                "risk_level": risk, "confidence": min(90, 55 + len(news) * 4), "symbols": symbols,
                "evidence": [f"{item.source}: {item.title}（{item.impact_score}）" for item in news[:6]],
                "actions": ["将高影响新闻与 K 线标记时间对齐复核。"], "created_at": created_at, "proposals": []}
    if agent.template_type == "risk_sentinel":
        portfolio = get_portfolio_summary()
        weak = sorted(selected, key=lambda item: item.trend_score)[:3]
        risk = "risk" if portfolio.total_unrealized_pnl_percent <= -float(settings.get("max_drawdown_percent", 5)) else "watch"
        return {"summary": f"组合未实现盈亏 {portfolio.total_unrealized_pnl_percent:.2f}%，重点关注 {', '.join(item.symbol for item in weak)}。",
                "risk_level": risk, "confidence": 76, "symbols": [item.symbol for item in weak],
                "evidence": [f"{item.symbol}: RSI {item.rsi:.1f}, 趋势 {item.trend_score}" for item in weak],
                "actions": ["复核弱势标的仓位、止损和新闻风险。"], "created_at": created_at, "proposals": []}
    if agent.template_type == "factor_research":
        strongest = max(selected, key=lambda item: item.trend_score) if selected else None
        factor_name = f"{strongest.symbol if strongest else '市场'} 趋势新闻确认"
        proposal = {"resource_type": "factors", "action": "create", "title": f"创建因子：{factor_name}",
                    "payload": {"name": factor_name, "category": "Agent 推荐", "description": "趋势评分较高且使用新闻影响作为确认条件。",
                                "signal": "long", "horizon": "3-10 days", "enabled": False}}
        return {"summary": f"当前优先研究 {factor_name}，建议先创建停用因子并完成回测验证。", "risk_level": "opportunity",
                "confidence": max(60, strongest.trend_score if strongest else 60), "symbols": symbols,
                "evidence": [f"{item.symbol}: trend={item.trend_score}, change={item.change_percent:+.2f}%" for item in selected[:5]],
                "actions": ["审批因子草案后运行回测和前向测试。"], "created_at": created_at, "proposals": [proposal]}
    top = max(selected, key=lambda item: abs(item.change_percent)) if selected else None
    if top is None:
        return {"summary": "当前没有可分析的行情标的。", "risk_level": "watch", "confidence": 30, "symbols": [],
                "evidence": [], "actions": ["添加自选标的。"], "created_at": created_at, "proposals": []}
    severity = "risk" if top.change_percent <= -float(settings.get("change_threshold", 1.5)) else "opportunity" if top.trend_score >= int(settings.get("min_trend_score", 65)) else "watch"
    return {"summary": f"{top.symbol} 涨跌 {top.change_percent:+.2f}%，趋势评分 {top.trend_score}，是当前范围内最显著异动。",
            "risk_level": severity, "confidence": max(55, min(92, top.trend_score)), "symbols": [top.symbol],
            "evidence": [f"价格 {top.price:.2f}", f"RSI {top.rsi:.1f}", f"趋势评分 {top.trend_score}"],
            "actions": ["结合成交量和新闻标记确认异动来源。"], "created_at": created_at, "proposals": []}


def agent_model_prompt(agent: AgentConfig, rule_output: dict[str, object], context: dict[str, object]) -> tuple[str, str]:
    if agent.template_type == "news_tagger":
        contract = (
            "Return JSON with article_id, summary, event_annotation, tags and impacts. event_annotation must contain "
            "taxonomy, scope, topics, countries, regions, actors, macro_factors, horizon, significance and summary. "
            "Each tag must contain symbol, relation "
            "(primary or secondary), confidence (0..1), direction (positive, negative, neutral or mixed), "
            "and short evidence. Each impact must contain target_type, target_id, link_type (direct or derived), "
            "channel, direction, relevance, impact_score, confidence, horizon, path and evidence. "
            "When a high-confidence derived impact has no approved graph path, also return relationship_proposals. "
            "Each relationship proposal must contain source_type, source_id, source_label, target_type, target_id, "
            "target_label, relation, direction, weight, confidence, evidence and path. "
            "source_type must be topic, macro_factor, country, region or event and source_id must exactly equal one "
            "value returned in event_annotation (event uses taxonomy). target_type must be instrument, sector, industry, "
            "index, etf, asset_class, commodity, currency, country or market. path must be a JSON string array with at "
            "least three transmission steps; confidence, weight and significance must be numeric. "
            "Use only instruments from instrument_catalog. Source hints are candidates, not facts. "
            "Never claim an indirect path without explaining each transmission step."
        )
    elif agent.template_type == "economic_event_analyst":
        contract = (
            "Required keys: summary,risk_level,confidence,symbols,affected_markets,scenarios,risks,evidence,actions,proposals. "
            "Separate facts from scenarios. Do not state a market direction as certain. Use only the supplied event values."
        )
    else:
        contract = "Required keys: summary,risk_level,confidence,symbols,evidence,actions,proposals."
    system_prompt = agent.system_prompt or "Return a JSON object grounded only in supplied evidence."
    user_prompt = f"{agent.task_prompt}\n{contract}\nRule baseline:\n{json.dumps(rule_output, ensure_ascii=False)}\nAdditional context:\n{json.dumps(context, ensure_ascii=False)}"
    return system_prompt, user_prompt


def parse_agent_model_content(content: str, agent: AgentConfig, rule_output: dict[str, object],
                              usage: dict[str, int]) -> tuple[dict[str, object], dict[str, int]]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    output = json.loads(content)
    if agent.template_type == "news_tagger":
        if not isinstance(output, dict) or not isinstance(output.get("tags"), list):
            raise RuntimeError("Model response does not match the news tagging contract")
        clean_tags: list[dict[str, object]] = []
        for item in output["tags"]:
            if not isinstance(item, dict) or item.get("relation") not in {"primary", "secondary"}:
                continue
            instrument = normalize_tag_symbol(str(item.get("symbol", "")))
            if not instrument:
                continue
            direction = str(item.get("direction", "neutral"))
            if direction not in {"positive", "negative", "neutral", "mixed"}:
                direction = "neutral"
            clean_tags.append({
                "symbol": instrument["symbol"], "relation": item["relation"],
                "confidence": max(0.0, min(1.0, float(item.get("confidence", 0)))),
                "direction": direction, "evidence": str(item.get("evidence", ""))[:500],
            })
        output["tags"] = clean_tags
        output.setdefault("article_id", rule_output.get("article_id"))
        output.setdefault("summary", f"识别出 {len(clean_tags)} 个关联标的。")
        output.update({
            "risk_level": "info", "confidence": int(max((item["confidence"] for item in clean_tags), default=0) * 100),
            "symbols": [item["symbol"] for item in clean_tags if item["relation"] == "primary"],
            "evidence": [item["evidence"] for item in clean_tags], "actions": [], "proposals": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return output, usage
    required = {"summary", "risk_level", "confidence", "evidence", "actions"}
    if not isinstance(output, dict) or not required.issubset(output):
        raise RuntimeError("Model response does not match the Agent output contract")
    if output["risk_level"] not in {"info", "watch", "risk", "opportunity"}:
        raise RuntimeError("Model returned an unsupported risk level")
    output["confidence"] = max(0, min(100, int(output["confidence"])))
    output.setdefault("symbols", rule_output.get("symbols", []))
    output.setdefault("proposals", [])
    output["created_at"] = datetime.now(timezone.utc).isoformat()
    return output, usage


def call_agent_model_route(route_id: str, agent: AgentConfig, rule_output: dict[str, object],
                           context: dict[str, object], prompts: tuple[str, str] | None = None) -> tuple[dict[str, object], dict[str, int], str]:
    system_prompt, user_prompt = prompts or agent_model_prompt(agent, rule_output, context)
    response, _channel, model = run_model_route(
        route_id, [{"role": "user", "content": user_prompt}], system_prompt=system_prompt, source="agent"
    )
    usage = {"prompt_tokens": response.prompt_tokens, "completion_tokens": response.completion_tokens}
    output, usage = parse_agent_model_content(response.text, agent, rule_output, usage)
    return output, usage, model


def call_agent_llm(provider: AgentProviderProfile, agent: AgentConfig, rule_output: dict[str, object], context: dict[str, object],
                   prompts: tuple[str, str] | None = None) -> tuple[dict[str, object], dict[str, int]]:
    api_key = resolve_api_key("agent_provider", provider.id, provider.api_key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {provider.api_key_env} is not configured")
    system_prompt, user_prompt = prompts or agent_model_prompt(agent, rule_output, context)
    payload = {
        "model": provider.model, "temperature": provider.temperature, "max_tokens": provider.max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    body = agent_http_post(f"{provider.base_url.rstrip('/')}/chat/completions", payload, api_key, provider.timeout_seconds)
    usage = body.get("usage") or {}
    return parse_agent_model_content(body["choices"][0]["message"]["content"], agent, rule_output, {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)), "completion_tokens": int(usage.get("completion_tokens", 0)),
    })


def enqueue_agent_run(
    agent_id: str,
    trigger: str = "manual",
    symbol: str | None = None,
    context: dict[str, object] | None = None,
    test: bool = False,
    trace_id: str | None = None,
    parent_run_id: str | None = None,
    workflow_run_id: str | None = None,
    parent_span_id: str | None = None,
) -> AgentRun:
    agent = get_agent(agent_id, require_enabled=not test)
    if trigger not in AGENT_TRIGGER_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported Agent trigger")
    now = datetime.now(timezone.utc).isoformat()
    run_context = dict(context or {})
    if agent.template_type == "news_tagger" and not test and not run_context.get("article_id"):
        with db_connect() as connection:
            article = connection.execute(
                """SELECT a.id FROM news_articles a LEFT JOIN news_tagging_jobs j ON j.article_id=a.id
                   WHERE a.enabled=1 ORDER BY CASE WHEN j.status='queued' THEN 0 ELSE 1 END, a.published_at DESC LIMIT 1"""
            ).fetchone()
        if not article:
            raise HTTPException(status_code=409, detail="No news article is available for tagging")
        run_context["article_id"] = article["id"]
    run_id = f"agent-run-{sha256(f'{agent_id}:{now}:{secrets.token_hex(4)}'.encode()).hexdigest()[:16]}"
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO agent_runs (id, agent_id, agent_name, trigger, symbol, status, runtime_mode, input_payload,
               output, error, model, prompt_tokens, completion_tokens, latency_ms, test, created_at, started_at, finished_at,
               trace_id, parent_run_id, workflow_run_id, parent_span_id)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, NULL, NULL, NULL, 0, 0, 0, ?, ?, NULL, NULL, ?, ?, ?, ?)""",
            (run_id, agent.id, agent.name, trigger, symbol.upper() if symbol else None, agent.runtime_mode,
             json.dumps(run_context, ensure_ascii=False), int(test), now,
             trace_id, parent_run_id, workflow_run_id, parent_span_id),
        )
        row = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return row_to_agent_run(row)


def get_agent_run(run_id: str) -> AgentRun:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return row_to_agent_run(row)


def list_agent_runs(agent_id: str | None = None, limit: int = 50) -> list[AgentRun]:
    init_db()
    with db_connect() as connection:
        if agent_id:
            rows = connection.execute("SELECT * FROM agent_runs WHERE agent_id=? ORDER BY created_at DESC LIMIT ?", (agent_id, max(1, min(200, limit)))).fetchall()
        else:
            rows = connection.execute("SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?", (max(1, min(200, limit)),)).fetchall()
    return [row_to_agent_run(row) for row in rows]


def normalize_relationship_path(value: object) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"\s*(?:→|->|--[^>]*-->|=>|＞)\s*", value)
    if not isinstance(value, list):
        return []
    return [str(step).strip()[:160] for step in value if str(step).strip()][:12]


def canonical_relationship_source(annotation: NewsEventAnnotation, item: dict[str, object]) -> tuple[str, str, str, list[str]]:
    catalogs = {
        "topic": [(value.lower(), value) for value in annotation.topics],
        "macro_factor": [(value.lower(), value) for value in annotation.macro_factors],
        "country": [(value.lower(), value) for value in annotation.countries],
        "region": [(value.lower(), value) for value in annotation.regions],
        "event": [(annotation.taxonomy.lower(), annotation.taxonomy)],
    }
    requested_type = str(item.get("source_type") or "").strip().lower()
    requested_id = str(item.get("source_id") or "").strip().lower()
    for source_id, label in catalogs.get(requested_type, []):
        if requested_id == source_id:
            return requested_type, source_id, label, []
    for source_type in (requested_type, "topic", "macro_factor", "country", "region", "event"):
        if catalogs.get(source_type):
            source_id, label = catalogs[source_type][0]
            return source_type, source_id, label, ["来源节点已按事件注释规范化"]
    return "event", "", "", ["事件注释中没有可用来源节点"]


def normalize_relationship_target(item: dict[str, object]) -> tuple[str, str, str, list[str]]:
    target_type = str(item.get("target_type") or "instrument").strip().lower()
    target_id = str(item.get("target_id") or item.get("symbol") or "").strip()
    warnings: list[str] = []
    if target_type in {"crypto_asset", "company", "entity"}:
        target_type = "instrument"
        warnings.append("目标类型已规范化为 instrument")
    if target_type in {"instrument", "index", "etf"}:
        candidates = [target_id]
        if ":" in target_id:
            candidates.append(target_id.rsplit(":", 1)[-1])
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,11}", target_id):
            candidates.append(f"FINNHUB:{target_id}")
        instrument = None
        for candidate in candidates:
            instrument = normalize_tag_symbol(candidate)
            if instrument:
                break
        if not instrument:
            return target_type, target_id, str(item.get("target_label") or target_id), [*warnings, "目标标的不在平台目录中"]
        return target_type, instrument["instrument_id"], instrument["name"], warnings
    return target_type, target_id.lower(), str(item.get("target_label") or target_id)[:160], warnings


def normalize_relationship_candidate(
    article_id: str, annotation: NewsEventAnnotation, item: dict[str, object], minimum: float,
) -> tuple[dict[str, object], list[str], list[str]]:
    source_type, source_id, source_label, source_warnings = canonical_relationship_source(annotation, item)
    target_type, target_id, target_label, target_warnings = normalize_relationship_target(item)
    confidence = normalized_ratio(item.get("confidence"))
    path = normalize_relationship_path(item.get("path"))
    evidence = str(item.get("evidence") or "").strip()
    direction_aliases = {"bullish": "positive", "bearish": "negative", "neutral": "neutral"}
    direction = direction_aliases.get(str(item.get("direction", "uncertain")).lower(), str(item.get("direction", "uncertain")).lower())
    payload = {
        "source_type": source_type, "source_id": source_id, "source_label": source_label,
        "target_type": target_type, "target_id": target_id, "target_label": target_label,
        "relation": str(item.get("relation") or item.get("channel") or "derived_impact")[:80],
        "direction": direction, "weight": normalized_ratio(item.get("weight"), confidence),
        "evidence": evidence[:1000], "provenance": f"news:{article_id}",
        "article_id": article_id, "confidence": confidence, "path": path,
    }
    errors: list[str] = []
    if not source_id:
        errors.append("事件注释中没有可用来源节点")
    if confidence < minimum:
        errors.append(f"置信度 {confidence:.2f} 低于阈值 {minimum:.2f}")
    if len(path) < 3:
        errors.append("影响路径必须至少包含三个节点")
    if not evidence:
        errors.append("缺少关系证据")
    try:
        NewsImpactGraphEdgeCreate(**{
            key: value for key, value in payload.items() if key in NewsImpactGraphEdgeCreate.model_fields
        })
    except Exception as exc:
        errors.append(f"结构校验失败：{str(exc).splitlines()[0]}")
    if "目标标的不在平台目录中" in target_warnings:
        errors.append("目标标的不在平台目录中")
    return payload, list(dict.fromkeys(errors)), [*source_warnings, *target_warnings]


def relationship_items_from_output(output: dict[str, object], annotation: NewsEventAnnotation | None) -> list[dict[str, object]]:
    raw = output.get("relationship_proposals", [])
    items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if not annotation:
        return items
    impacts = output.get("impacts", [])
    for impact in impacts if isinstance(impacts, list) else []:
        if not isinstance(impact, dict) or impact.get("link_type") != "derived" or impact.get("graph_edge_ids"):
            continue
        items.append({
            "target_type": impact.get("target_type", "instrument"),
            "target_id": impact.get("target_id") or impact.get("symbol"),
            "target_label": impact.get("target_label") or impact.get("target_id") or impact.get("symbol"),
            "relation": impact.get("channel", "derived_impact"), "direction": impact.get("direction", "uncertain"),
            "weight": impact.get("relevance", impact.get("confidence", 0)), "confidence": impact.get("confidence", 0),
            "evidence": impact.get("evidence", ""), "path": impact.get("path", []),
        })
    return items[:10]


def persist_agent_proposals(run: AgentRun, output: dict[str, object]) -> None:
    if run.test:
        return
    proposals = list(output.get("proposals", [])) if isinstance(output.get("proposals"), list) else []
    failed_relationships: list[dict[str, object]] = []
    relationship_stats = {"candidates": 0, "pending": 0, "failed": 0}
    if run.model and get_agent(run.agent_id).template_type == "news_tagger":
        article_id = str(output.get("article_id") or "")
        annotation = get_news_event_annotation(article_id) if article_id else None
        minimum = float(get_agent("news_impact_mapper").settings.get("relationship_proposal_confidence", 0.75))
        for item in relationship_items_from_output(output, annotation):
            relationship_stats["candidates"] += 1
            if not annotation:
                payload, errors, warnings = dict(item), ["新闻事件注释不存在"], []
            else:
                payload, errors, warnings = normalize_relationship_candidate(article_id, annotation, item, minimum)
            payload["normalization_warnings"] = warnings
            if errors:
                payload["validation_errors"] = errors
                failed_relationships.append({"payload": payload, "errors": errors})
                relationship_stats["failed"] += 1
                continue
            proposals.append({
                "resource_type": "impact-graph", "action": "create",
                "title": f"新增影响关系：{payload['source_label']} → {payload['target_label']}", "payload": payload,
            })
            relationship_stats["pending"] += 1
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        for failed in failed_relationships:
            payload = failed["payload"]
            key = f"{run.id}:impact-invalid:{json.dumps(payload, sort_keys=True)}"
            proposal_id = f"proposal-{sha256(key.encode()).hexdigest()[:14]}"
            connection.execute(
                "INSERT OR IGNORE INTO agent_proposals VALUES (?, ?, ?, 'impact-graph', 'create', ?, ?, 'failed', NULL, ?, ?, ?)",
                (proposal_id, run.id, run.agent_id, "影响关系候选校验失败",
                 json.dumps(payload, ensure_ascii=False), "；".join(failed["errors"])[:500], now, now),
            )
        for item in proposals[:20]:
            if not isinstance(item, dict) or item.get("resource_type") not in {"factors", "alerts", "watchlist", "impact-graph"} or item.get("action") != "create":
                continue
            proposal_key = f"{run.id}:{json.dumps(item, sort_keys=True)}"
            if item.get("resource_type") == "impact-graph":
                payload = item.get("payload", {})
                proposal_key = f"impact-graph:{payload.get('source_type')}:{payload.get('source_id')}:{payload.get('target_type')}:{payload.get('target_id')}:{payload.get('relation')}"
                edge_id = f"impact-edge-{sha256(proposal_key.removeprefix('impact-graph:').encode()).hexdigest()[:16]}"
                if connection.execute(
                    "SELECT 1 FROM news_impact_graph_edges WHERE id=? AND status IN ('pending','approved')", (edge_id,),
                ).fetchone():
                    continue
            proposal_id = f"proposal-{sha256(proposal_key.encode()).hexdigest()[:14]}"
            connection.execute(
                "INSERT OR IGNORE INTO agent_proposals VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, NULL)",
                (proposal_id, run.id, run.agent_id, item["resource_type"], item["action"], str(item.get("title", "Agent proposal"))[:200],
                 json.dumps(item.get("payload", {}), ensure_ascii=False), now),
            )
        if relationship_stats["candidates"]:
            mapper_run_id = f"agent-run-{sha256(f'{run.id}:news_impact_mapper'.encode()).hexdigest()[:16]}"
            row = connection.execute("SELECT output FROM agent_runs WHERE id=?", (mapper_run_id,)).fetchone()
            mapper_output = json_value(row["output"], {}) if row else {}
            mapper_output["relationship_validation"] = relationship_stats
            connection.execute("UPDATE agent_runs SET output=? WHERE id=?", (json.dumps(mapper_output, ensure_ascii=False), mapper_run_id))


def persist_news_tagger_output(run: AgentRun, agent: AgentConfig, output: dict[str, object], model: str | None) -> None:
    if run.test:
        return
    article_id = str(output.get("article_id") or "")
    tags = output.get("tags", [])
    if not article_id or not isinstance(tags, list):
        raise RuntimeError("News tagger output is missing article_id or tags")
    if news_symbol_override(article_id):
        with db_connect() as connection:
            connection.execute(
                "UPDATE news_tagging_jobs SET status='succeeded', trigger='manual', error=NULL, updated_at=? WHERE article_id=?",
                (datetime.now(timezone.utc).isoformat(), article_id),
            )
        return
    settings = agent.settings
    auto_threshold = float(settings.get("auto_apply_confidence", 0.85))
    review_threshold = float(settings.get("review_confidence", 0.60))
    secondary_threshold = float(settings.get("secondary_display_confidence", 0.75))
    version = int(settings.get("tagging_version", 1))
    limits = {"primary": int(settings.get("max_primary", 5)), "secondary": int(settings.get("max_secondary", 8))}
    origin = "ai" if model else "rule"
    pending: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """UPDATE news_article_symbol_tags SET status='superseded', updated_at=?
               WHERE article_id=? AND origin IN ('rule','ai') AND status IN ('accepted','pending')""",
            (now, article_id),
        )
        for item in tags:
            if not isinstance(item, dict):
                continue
            relation = str(item.get("relation", "primary"))
            instrument = normalize_tag_symbol(str(item.get("symbol", "")))
            if relation not in limits or not instrument or (instrument["symbol"], relation) in seen:
                continue
            if sum(existing_relation == relation for _, existing_relation in seen) >= limits[relation]:
                continue
            seen.add((instrument["symbol"], relation))
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
            accept_at = auto_threshold if relation == "primary" else secondary_threshold
            status = "accepted" if confidence >= accept_at else "pending" if confidence >= review_threshold else "rejected"
            direction = str(item.get("direction", "neutral"))
            if direction not in {"positive", "negative", "neutral", "mixed"}:
                direction = "neutral"
            saved = insert_news_symbol_tag(
                connection, article_id, instrument["symbol"], relation, confidence, direction, origin, status,
                str(item.get("evidence", "")), run.id, model, version,
            )
            if saved and status == "pending":
                pending.append(saved.model_dump(mode="json"))
        materialize_news_primary_symbols(article_id, connection)
        if pending:
            article = connection.execute(
                "SELECT title, source_name FROM news_articles WHERE id=?", (article_id,),
            ).fetchone()
            payload = {"article_id": article_id, "title": article["title"] if article else article_id,
                       "source": article["source_name"] if article else "", "tags": pending}
            proposal_id = f"proposal-{sha256(f'{run.id}:news-tags'.encode()).hexdigest()[:14]}"
            connection.execute(
                "INSERT OR IGNORE INTO agent_proposals VALUES (?, ?, ?, 'news-tags', 'apply', ?, ?, 'pending', NULL, NULL, ?, NULL)",
                (proposal_id, run.id, run.agent_id, f"审核新闻标的：{payload['title'][:120]}",
                 json.dumps(payload, ensure_ascii=False), now),
            )
        connection.execute(
            "UPDATE news_tagging_jobs SET status=?, run_id=?, error=NULL, updated_at=? WHERE article_id=?",
            ("review" if pending else "succeeded", run.id, now, article_id),
        )
    persist_news_annotation_pipeline(article_id, output, run.id, model)


def execute_agent_run(run_id: str) -> AgentRun:
    run = get_agent_run(run_id)
    if run.status != "queued":
        return run
    agent = get_agent(run.agent_id)
    started = datetime.now(timezone.utc)
    with db_connect() as connection:
        claimed = connection.execute("UPDATE agent_runs SET status='running', started_at=? WHERE id=? AND status='queued'", (started.isoformat(), run_id))
        if not claimed.rowcount:
            return get_agent_run(run_id)
        input_row = connection.execute("SELECT input_payload FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    context = json_value(input_row["input_payload"], {})
    output: dict[str, object] | None = None
    error: str | None = None
    model: str | None = None
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    input_snapshot: dict[str, object] | None = None
    try:
        rule_output = agent_rule_output(agent, run.symbol, context)
        output = rule_output
        model_requested = agent.runtime_mode in {"llm", "hybrid"} and not context.get("force_rule")
        prompts = agent_model_prompt(agent, rule_output, context) if model_requested else None
        model_request: dict[str, object] | None = None
        if prompts:
            if agent.model_route_id:
                route = get_model_route(agent.model_route_id)
                model_request = {
                    "route_id": route.id, "route_alias": route.alias, "provider_id": None,
                    "temperature": route.temperature, "max_tokens": route.max_tokens,
                    "system_prompt": prompts[0], "user_prompt": prompts[1],
                }
            else:
                provider_for_snapshot = get_agent_provider(agent.provider_id or "")
                model_request = {
                    "route_id": None, "route_alias": None, "provider_id": provider_for_snapshot.id,
                    "temperature": provider_for_snapshot.temperature, "max_tokens": provider_for_snapshot.max_tokens,
                    "system_prompt": prompts[0], "user_prompt": prompts[1],
                }
        input_snapshot = {
            "version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "trigger": {"type": run.trigger, "symbol": run.symbol, "context": context},
            "agent_config": {
                "id": agent.id, "template_type": agent.template_type, "runtime_mode": agent.runtime_mode,
                "model_route_id": agent.model_route_id, "provider_id": agent.provider_id,
                "fallback_to_rules": agent.fallback_to_rules, "symbol_scope": agent.symbol_scope,
                "symbols": agent.symbols, "settings": agent.settings,
                "system_prompt": agent.system_prompt, "task_prompt": agent.task_prompt,
            },
            "resolved_context": rule_output.get("article", context),
            "rule_baseline": rule_output,
            "model_request": model_request,
            "execution": {"model_attempted": False, "fallback_used": False, "model": None, "model_error": None},
            "truncated_fields": [],
        }
        input_snapshot = save_agent_input_snapshot(run.id, input_snapshot)
        if agent.runtime_mode in {"llm", "hybrid"} and not context.get("force_rule"):
            try:
                input_snapshot["execution"] = {"model_attempted": True, "fallback_used": False, "model": None, "model_error": None}
                save_agent_input_snapshot(run.id, input_snapshot)
                if agent.model_route_id:
                    output, usage, model = call_agent_model_route(agent.model_route_id, agent, rule_output, context, prompts)
                else:
                    provider = get_agent_provider(agent.provider_id or "", require_enabled=True)
                    model = provider.model
                    output, usage = call_agent_llm(provider, agent, rule_output, context, prompts)
                input_snapshot["execution"] = {"model_attempted": True, "fallback_used": False, "model": model, "model_error": None}
                save_agent_input_snapshot(run.id, input_snapshot)
            except Exception as exc:
                input_snapshot["execution"] = {
                    "model_attempted": True, "fallback_used": bool(agent.fallback_to_rules),
                    "model": model, "model_error": str(exc)[:1000],
                }
                save_agent_input_snapshot(run.id, input_snapshot)
                if not agent.fallback_to_rules:
                    raise
                error = f"LLM fallback: {str(exc)[:500]}"
                output = rule_output
                model = None
        if output and agent.template_type == "news_tagger":
            persist_news_tagger_output(run, agent, output, model)
        if output and agent.template_type == "economic_event_analyst" and context.get("event_id"):
            from app.modules.calendar.service import persist_analysis
            persist_analysis(str(context["event_id"]), output, run.id, model)
        finished = datetime.now(timezone.utc)
        status = "succeeded"
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        status = "failed"
        error = str(exc)[:1000]
        if agent.template_type == "news_tagger":
            article_id = str(context.get("article_id") or "")
            if article_id:
                with db_connect() as connection:
                    job = connection.execute("SELECT attempts FROM news_tagging_jobs WHERE article_id=?", (article_id,)).fetchone()
                    retry = bool(job and int(job["attempts"]) < 3)
                    connection.execute(
                        "UPDATE news_tagging_jobs SET status=?, error=?, updated_at=? WHERE article_id=?",
                        ("queued" if retry else "failed", error, finished.isoformat(), article_id),
                    )
        if agent.template_type == "economic_event_analyst" and context.get("event_id"):
            from app.modules.calendar.service import mark_analysis_failed
            mark_analysis_failed(str(context["event_id"]), error)
    latency_ms = int((finished - started).total_seconds() * 1000)
    with db_connect() as connection:
        connection.execute(
            """UPDATE agent_runs SET status=?, output=?, error=?, model=?, prompt_tokens=?, completion_tokens=?,
               latency_ms=?, finished_at=? WHERE id=?""",
            (status, json.dumps(output, ensure_ascii=False) if output else None, error, model, usage["prompt_tokens"], usage["completion_tokens"], latency_ms, finished.isoformat(), run_id),
        )
        if status == "succeeded" and not run.test:
            connection.execute("UPDATE agent_configs SET last_run_at=?, updated_at=? WHERE id=?", (finished.isoformat(), finished.isoformat(), agent.id))
    completed = get_agent_run(run_id)
    if output and status == "succeeded":
        persist_agent_proposals(completed, output)
        if not run.test and agent.notification_channel_ids:
            output_severity = str(output.get("risk_level", "info"))
            event = add_event_log(
                event_type="system",
                severity=output_severity if output_severity in {"info", "watch", "risk"} else "info",
                title=f"Agent 输出：{agent.name}",
                message=str(output.get("summary", "Agent run completed"))[:500],
                symbol=run.symbol,
            )
            deliver_event(event, agent.notification_channel_ids)
    return completed


def schedule_due_agents() -> int:
    now = datetime.now(timezone.utc)
    with db_connect() as connection:
        rows = connection.execute(
            """SELECT * FROM agent_configs WHERE enabled=1 AND schedule_enabled=1
               AND (next_run_at IS NULL OR next_run_at <= ?)""", (now.isoformat(),),
        ).fetchall()
    queued = 0
    for row in rows:
        agent = row_to_agent_config(row)
        with db_connect() as connection:
            active = connection.execute("SELECT 1 FROM agent_runs WHERE agent_id=? AND status IN ('queued','running') LIMIT 1", (agent.id,)).fetchone()
            next_run = (now + timedelta(seconds=agent.interval_seconds)).isoformat()
            connection.execute("UPDATE agent_configs SET next_run_at=? WHERE id=?", (next_run, agent.id))
        if not active:
            enqueue_agent_run(agent.id, trigger="schedule")
            queued += 1
    return queued


def enqueue_agent_event(trigger: str, symbol: str | None = None, context: dict[str, object] | None = None) -> int:
    queued = 0
    for agent in list_agents(enabled_only=True):
        if trigger in agent.triggers:
            enqueue_agent_run(agent.id, trigger=trigger, symbol=symbol, context=context)
            queued += 1
    from app.modules.ai.workflow import enqueue_workflow_event

    enqueue_workflow_event(trigger, symbol=symbol, context=context)
    return queued


async def agent_worker_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(schedule_due_agents)
            with db_connect() as connection:
                row = connection.execute("SELECT id FROM agent_runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                await asyncio.to_thread(execute_agent_run, row["id"])
                continue
            processed = await asyncio.to_thread(process_next_news_tagging_job)
            if processed:
                continue
        except Exception as exc:
            add_event_log(event_type="system", severity="error", title="Agent worker error", message=str(exc)[:500])
        await asyncio.sleep(2)


def process_next_news_tagging_job() -> bool:
    agent = get_agent("news_tagger")
    if not agent.enabled:
        return False
    now = datetime.now(timezone.utc)
    stale_before = (now - timedelta(minutes=10)).isoformat()
    with db_connect() as connection:
        stale_runs = connection.execute(
            "SELECT run_id FROM news_tagging_jobs WHERE status='running' AND updated_at<?", (stale_before,),
        ).fetchall()
        for stale in stale_runs:
            if stale["run_id"]:
                connection.execute(
                    """UPDATE agent_runs SET status='failed', error='Stale news tagging run recovered', finished_at=?
                       WHERE id=? AND status='running'""",
                    (now.isoformat(), stale["run_id"]),
                )
        connection.execute(
            """UPDATE news_tagging_jobs SET status='queued', error='Stale run recovered', run_id=NULL, updated_at=?
               WHERE status='running' AND updated_at<?""",
            (now.isoformat(), stale_before),
        )
    provider_ready = True
    if agent.runtime_mode in {"llm", "hybrid"}:
        try:
            if agent.model_route_id:
                provider_ready = bool(ordered_route_targets(get_model_route(agent.model_route_id, require_enabled=True)))
            else:
                provider = get_agent_provider(agent.provider_id or "", require_enabled=True)
                provider_ready = provider.secret_configured
        except HTTPException:
            provider_ready = False
    with db_connect() as connection:
        row = connection.execute(
            """SELECT article_id, trigger FROM news_tagging_jobs WHERE status IN ('queued','waiting_provider')
               ORDER BY CASE WHEN trigger='rule_backfill' THEN 0 ELSE 1 END, updated_at LIMIT 1"""
        ).fetchone()
        if not row:
            return False
        article_id = row["article_id"]
        force_rule = row["trigger"] == "rule_backfill"
        if provider_ready and not force_rule and agent.runtime_mode in {"llm", "hybrid"}:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            recent = int(connection.execute(
                "SELECT COUNT(*) total FROM agent_runs WHERE agent_id=? AND created_at>=? AND test=0",
                (agent.id, cutoff),
            ).fetchone()["total"])
            if recent >= int(agent.settings.get("max_requests_per_minute", 10)):
                return False
        if not provider_ready and not force_rule:
            connection.execute(
                "UPDATE news_tagging_jobs SET status='waiting_provider', error='模型服务或密钥未就绪', updated_at=? WHERE article_id=?",
                (datetime.now(timezone.utc).isoformat(), article_id),
            )
            return False
        claimed = connection.execute(
            """UPDATE news_tagging_jobs SET status='running', attempts=attempts+1, error=NULL, updated_at=?
               WHERE article_id=? AND status IN ('queued','waiting_provider')""",
            (datetime.now(timezone.utc).isoformat(), article_id),
        )
        if not claimed.rowcount:
            return False
    run = enqueue_agent_run(
        agent.id, trigger="news_ingested", context={"article_id": article_id, "force_rule": force_rule},
    )
    with db_connect() as connection:
        connection.execute("UPDATE news_tagging_jobs SET run_id=? WHERE article_id=?", (run.id, article_id))
    execute_agent_run(run.id)
    return True


def list_agent_proposals(status: str | None = None, limit: int = 100, resource_type: str | None = None) -> list[AgentProposal]:
    init_db()
    clauses: list[str] = []
    params: list[object] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if resource_type:
        clauses.append("resource_type=?")
        params.append(resource_type)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with db_connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM agent_proposals{where} ORDER BY created_at DESC LIMIT ?", params,
        ).fetchall()
    return [row_to_agent_proposal(row) for row in rows]


def agent_output_for_api(agent: AgentConfig, symbol: str | None = None) -> dict[str, object] | None:
    if agent.runtime_mode not in {"llm", "hybrid"}:
        return None
    with db_connect() as connection:
        if symbol:
            row = connection.execute(
                """SELECT output FROM agent_runs WHERE agent_id=? AND status='succeeded' AND test=0
                   AND error IS NULL AND model IS NOT NULL AND (symbol=? OR symbol IS NULL)
                   AND output IS NOT NULL ORDER BY finished_at DESC LIMIT 1""",
                (agent.id, symbol.upper()),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT output FROM agent_runs WHERE agent_id=? AND status='succeeded' AND test=0
                   AND error IS NULL AND model IS NOT NULL AND output IS NOT NULL
                   ORDER BY finished_at DESC LIMIT 1""",
                (agent.id,),
            ).fetchone()
    if row:
        cached = json_value(row["output"], None)
        if isinstance(cached, dict):
            return cached
    return None


def review_agent_proposal(
    proposal_id: str,
    approve: bool,
    actor: AuthUser,
    note: str = "",
    payload_override: dict[str, object] | None = None,
) -> AgentProposal:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent proposal not found")
    proposal = row_to_agent_proposal(row)
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Agent proposal has already been reviewed")
    now = datetime.now(timezone.utc).isoformat()
    status = "rejected"
    if proposal.resource_type == "news-tags":
        article_id = str(proposal.payload.get("article_id") or "")
        with db_connect() as connection:
            if approve:
                override = NewsTaggingReviewOverride(**(payload_override or {})) if payload_override else None
                connection.execute(
                    "UPDATE news_article_symbol_tags SET status='rejected', updated_at=? WHERE article_id=? AND status='pending'",
                    (now, article_id),
                )
                selected: list[tuple[str, str, str]] = []
                if override:
                    selected.extend((symbol, "primary", "管理员审核") for symbol in override.primary_symbols)
                    selected.extend((symbol, "secondary", "管理员审核") for symbol in override.secondary_symbols)
                else:
                    selected.extend(
                        (str(item.get("symbol", "")), str(item.get("relation", "primary")), str(item.get("evidence", "")))
                        for item in proposal.payload.get("tags", []) if isinstance(item, dict)
                    )
                if not selected:
                    raise HTTPException(status_code=422, detail="At least one reviewed symbol is required")
                for symbol, relation, evidence in selected:
                    if relation not in {"primary", "secondary"} or not normalize_tag_symbol(symbol):
                        raise HTTPException(status_code=422, detail=f"Unsupported reviewed symbol: {symbol}")
                    insert_news_symbol_tag(
                        connection, article_id, symbol, relation, 1.0, "neutral", "manual", "accepted",
                        evidence or "管理员审核", proposal.run_id,
                    )
                materialize_news_primary_symbols(article_id, connection)
                status = "executed"
            else:
                connection.execute(
                    "UPDATE news_article_symbol_tags SET status='rejected', updated_at=? WHERE article_id=? AND status='pending'",
                    (now, article_id),
                )
                materialize_news_primary_symbols(article_id, connection)
            connection.execute(
                "UPDATE news_tagging_jobs SET status='succeeded', updated_at=? WHERE article_id=?", (now, article_id),
            )
            connection.execute(
                "UPDATE agent_proposals SET status=?, reviewed_by=?, review_note=?, reviewed_at=? WHERE id=?",
                (status, actor.username, note, now, proposal_id),
            )
            updated = connection.execute("SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)).fetchone()
        return row_to_agent_proposal(updated)
    if approve:
        try:
            if proposal.resource_type == "factors":
                create_factor_definition(FactorDefinitionCreate(**proposal.payload))
            elif proposal.resource_type == "alerts":
                create_alert(AlertRuleCreate(**proposal.payload))
            elif proposal.resource_type == "watchlist":
                add_watchlist_symbol(WatchlistItemCreate(**proposal.payload))
            elif proposal.resource_type == "impact-graph":
                edge_fields = {
                    key: value for key, value in proposal.payload.items()
                    if key in NewsImpactGraphEdgeCreate.model_fields
                }
                edge = create_news_impact_graph_edge(NewsImpactGraphEdgeCreate(**edge_fields), actor)
                review_news_impact_graph_edge(edge.id, NewsImpactGraphEdgeReview(status="approved"), actor)
                article_id = str(proposal.payload.get("article_id") or "")
                if article_id:
                    persist_news_annotation_pipeline(article_id)
            else:
                raise HTTPException(status_code=422, detail="Unsupported proposal resource")
            status = "executed"
        except Exception:
            status = "failed"
            with db_connect() as connection:
                connection.execute("UPDATE agent_proposals SET status=?, reviewed_by=?, review_note=?, reviewed_at=? WHERE id=?", (status, actor.username, note, now, proposal_id))
            raise
    with db_connect() as connection:
        connection.execute("UPDATE agent_proposals SET status=?, reviewed_by=?, review_note=?, reviewed_at=? WHERE id=?", (status, actor.username, note, now, proposal_id))
        updated = connection.execute("SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)).fetchone()
    return row_to_agent_proposal(updated)
