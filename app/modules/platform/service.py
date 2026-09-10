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
    AdminResourceDescriptor,
    AdminResourcePage,
    AppSettings,
    AppSettingsUpdate,
    AuthSessionInfo,
    AuthUser,
    AutomationSettings,
    CredentialBinding,
    CredentialCreate,
    CredentialOrigin,
    CredentialProfile,
    CredentialTestStatus,
    CredentialUpdate,
    CredentialVaultStatus,
    FilterPreferences,
    Literal,
    SetupRequest,
    UserCreate,
    UserPreferences,
    UserPreferencesUpdate,
    credentials,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
from app.modules.infrastructure.database import PASSWORD_ITERATIONS, SESSION_COOKIE, SESSION_HOURS
from app.modules.market.service import MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
data_catalog_items = service_call('app.modules.research.service', 'data_catalog_items')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
get_active_market_provider_name = service_call('app.modules.market.service', 'get_active_market_provider_name')
get_market_provider = service_call('app.modules.market.service', 'get_market_provider')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_watchlist_snapshots = service_call('app.modules.market.service', 'list_watchlist_snapshots')
schedule_credential_test = service_call('app.modules.ai.service', 'schedule_credential_test')
set_active_market_provider = service_call('app.modules.market.service', 'set_active_market_provider')


def sync_information_source_registry() -> None:
    with db_connect() as connection:
        active_provider_row = connection.execute(
            "SELECT value FROM app_config WHERE key = 'active_market_provider'"
        ).fetchone()
        ensure_data_source_schema(connection, active_provider_row["value"] if active_provider_row else "coinbase")


def credential_cipher() -> Fernet:
    master_key = os.getenv("APP_CREDENTIAL_MASTER_KEY", "").strip()
    if not master_key:
        raise HTTPException(status_code=503, detail="APP_CREDENTIAL_MASTER_KEY is not configured")
    try:
        return Fernet(master_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="APP_CREDENTIAL_MASTER_KEY is invalid") from exc


def validate_credential_env_alias(value: str) -> str:
    env_alias = value.strip().upper()
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_alias):
        raise HTTPException(status_code=422, detail="Credential environment alias must use uppercase letters, numbers, and underscores")
    return env_alias


def credential_origin_from_row(row: sqlite3.Row) -> tuple[CredentialOrigin, bool, str]:
    encrypted_value = row["encrypted_value"]
    if encrypted_value:
        try:
            credential_cipher().decrypt(encrypted_value.encode("ascii"))
            return "database", True, row["secret_hint"] or ""
        except (HTTPException, InvalidToken, ValueError, UnicodeEncodeError):
            return "error", False, ""
    if os.getenv(row["env_alias"], ""):
        return "environment", True, "环境变量"
    return "none", False, ""


def row_to_credential_profile(row: sqlite3.Row, bindings: list[sqlite3.Row] | None = None) -> CredentialProfile:
    origin, configured, hint = credential_origin_from_row(row)
    binding_rows = bindings or []
    return CredentialProfile(
        id=row["id"],
        name=row["name"],
        env_alias=row["env_alias"],
        configured=configured,
        origin=origin,
        secret_hint=hint,
        version=int(row["version"]),
        binding_count=len(binding_rows),
        bindings=[
            CredentialBinding(
                resource_type=item["resource_type"],
                resource_id=item["resource_id"],
                resource_name=item["resource_name"],
            )
            for item in binding_rows
        ],
        test_status=row["test_status"],
        test_message=row["test_message"],
        last_tested_at=row["last_tested_at"],
        last_rotated_at=row["last_rotated_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_credential(credential_id: str) -> CredentialProfile:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM api_credentials WHERE id = ?", (credential_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Credential not found")
        bindings = connection.execute(
            "SELECT * FROM credential_bindings WHERE credential_id = ? ORDER BY resource_type, resource_name",
            (credential_id,),
        ).fetchall()
    return row_to_credential_profile(row, bindings)


def list_credentials() -> list[CredentialProfile]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM api_credentials ORDER BY name COLLATE NOCASE").fetchall()
        binding_rows = connection.execute(
            "SELECT * FROM credential_bindings ORDER BY resource_type, resource_name"
        ).fetchall()
    bindings_by_credential: dict[str, list[sqlite3.Row]] = {}
    for binding in binding_rows:
        bindings_by_credential.setdefault(binding["credential_id"], []).append(binding)
    return [row_to_credential_profile(row, bindings_by_credential.get(row["id"], [])) for row in rows]


def create_credential(payload: CredentialCreate) -> CredentialProfile:
    init_db()
    if payload.secret is not None:
        credential_cipher()
    env_alias = validate_credential_env_alias(payload.env_alias)
    now = datetime.now(timezone.utc).isoformat()
    credential_id = f"credential-{sha256(f'{env_alias}:{now}'.encode()).hexdigest()[:16]}"
    try:
        with db_connect() as connection:
            connection.execute(
                """
                INSERT INTO api_credentials (
                    id, name, env_alias, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (credential_id, payload.name.strip(), env_alias, now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Credential environment alias already exists") from exc
    if payload.secret is not None:
        set_credential_secret(credential_id, payload.secret.get_secret_value())
    return get_credential(credential_id)


def update_credential(credential_id: str, payload: CredentialUpdate) -> CredentialProfile:
    current = get_credential(credential_id)
    name = payload.name.strip() if payload.name is not None else current.name
    env_alias = validate_credential_env_alias(payload.env_alias) if payload.env_alias is not None else current.env_alias
    now = datetime.now(timezone.utc).isoformat()
    try:
        with db_connect() as connection:
            connection.execute(
                "UPDATE api_credentials SET name = ?, env_alias = ?, updated_at = ? WHERE id = ?",
                (name, env_alias, now, credential_id),
            )
            bindings = connection.execute(
                "SELECT resource_type, resource_id FROM credential_bindings WHERE credential_id = ?",
                (credential_id,),
            ).fetchall()
            for binding in bindings:
                if binding["resource_type"] == "information_source":
                    connection.execute("UPDATE news_sources SET api_key_env = ? WHERE id = ?", (env_alias, binding["resource_id"]))
                elif binding["resource_type"] == "agent_provider":
                    connection.execute("UPDATE agent_provider_profiles SET api_key_env = ? WHERE id = ?", (env_alias, binding["resource_id"]))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Credential environment alias already exists") from exc
    return get_credential(credential_id)


def ensure_credential_binding(resource_type: str, resource_id: str, resource_name: str, env_alias: str) -> str | None:
    if not env_alias.strip():
        with db_connect() as connection:
            connection.execute(
                "DELETE FROM credential_bindings WHERE resource_type = ? AND resource_id = ?",
                (resource_type, str(resource_id)),
            )
        return None
    normalized_alias = validate_credential_env_alias(env_alias)
    now = datetime.now(timezone.utc).isoformat()
    credential_id = f"credential-{sha256(normalized_alias.encode('utf-8')).hexdigest()[:16]}"
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT id FROM api_credentials WHERE env_alias = ?",
            (normalized_alias,),
        ).fetchone()
        if existing:
            credential_id = existing["id"]
        else:
            connection.execute(
                """
                INSERT INTO api_credentials (
                    id, name, env_alias, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (credential_id, normalized_alias.replace("_API_KEY", "").replace("_", " ").title(), normalized_alias, now, now),
            )
        connection.execute(
            """
            INSERT INTO credential_bindings (
                credential_id, resource_type, resource_id, resource_name, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(resource_type, resource_id) DO UPDATE SET
                credential_id = excluded.credential_id,
                resource_name = excluded.resource_name
            """,
            (credential_id, resource_type, str(resource_id), resource_name, now),
        )
    return credential_id


def credential_id_for_resource(resource_type: str, resource_id: str) -> str | None:
    try:
        with db_connect() as connection:
            row = connection.execute(
                "SELECT credential_id FROM credential_bindings WHERE resource_type = ? AND resource_id = ?",
                (resource_type, str(resource_id)),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["credential_id"] if row else None


def resolve_api_key(resource_type: str, resource_id: str, env_alias: str = "") -> str:
    try:
        with db_connect() as connection:
            row = connection.execute(
                """
                SELECT c.*
                FROM credential_bindings b
                JOIN api_credentials c ON c.id = b.credential_id
                WHERE b.resource_type = ? AND b.resource_id = ?
                """,
                (resource_type, str(resource_id)),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        if row["encrypted_value"]:
            try:
                return credential_cipher().decrypt(row["encrypted_value"].encode("ascii")).decode("utf-8")
            except InvalidToken as exc:
                raise RuntimeError(f"Credential {row['id']} cannot be decrypted") from exc
        return os.getenv(row["env_alias"], "").strip()
    return os.getenv(env_alias, "").strip() if env_alias else ""


def credential_metadata_for_resource(resource_type: str, resource_id: str, env_alias: str = "") -> tuple[str | None, CredentialOrigin, bool, str, CredentialTestStatus]:
    try:
        with db_connect() as connection:
            row = connection.execute(
                """
                SELECT c.*
                FROM credential_bindings b
                JOIN api_credentials c ON c.id = b.credential_id
                WHERE b.resource_type = ? AND b.resource_id = ?
                """,
                (resource_type, str(resource_id)),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        configured = bool(os.getenv(env_alias, "")) if env_alias else False
        return None, "environment" if configured else "none", configured, "环境变量" if configured else "", "unknown"
    origin, configured, hint = credential_origin_from_row(row)
    return row["id"], origin, configured, hint, row["test_status"]


def set_credential_secret(credential_id: str, secret: str) -> CredentialProfile:
    if not secret:
        raise HTTPException(status_code=422, detail="Credential secret cannot be empty")
    get_credential(credential_id)
    encrypted = credential_cipher().encrypt(secret.encode("utf-8")).decode("ascii")
    hint = f"••••{secret[-4:]}" if len(secret) >= 4 else "••••"
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE api_credentials
            SET encrypted_value = ?, secret_hint = ?, version = version + 1,
                test_status = 'pending', test_message = '等待连接检测',
                last_rotated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (encrypted, hint, now, now, credential_id),
        )
    invalidate_credential_caches(credential_id)
    schedule_credential_test(credential_id)
    return get_credential(credential_id)


def remove_credential_secret(credential_id: str) -> CredentialProfile:
    get_credential(credential_id)
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE api_credentials
            SET encrypted_value = NULL, secret_hint = '', version = version + 1,
                test_status = 'unknown', test_message = '已恢复环境变量回退',
                last_rotated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, credential_id),
        )
    invalidate_credential_caches(credential_id)
    return get_credential(credential_id)


def set_resource_secret(resource_type: str, resource_id: str, resource_name: str, env_alias: str, secret: str) -> CredentialProfile:
    credential_cipher()
    credential_id = ensure_credential_binding(resource_type, resource_id, resource_name, env_alias)
    if credential_id is None:
        raise HTTPException(status_code=422, detail="API key environment alias is required before saving a secret")
    return set_credential_secret(credential_id, secret)


def invalidate_credential_caches(credential_id: str) -> None:
    try:
        with db_connect() as connection:
            rows = connection.execute(
                "SELECT resource_type, resource_id FROM credential_bindings WHERE credential_id = ?",
                (credential_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return
    if any(
        row["resource_type"] == "market_provider" and row["resource_id"] == "finnhub"
        for row in rows
    ):
        provider = MARKET_PROVIDERS.get("finnhub")
        if provider is not None:
            provider._snapshot_cache = []
            provider._snapshot_cached_at = None


def credential_vault_status() -> CredentialVaultStatus:
    credentials = list_credentials()
    master_key_configured = bool(os.getenv("APP_CREDENTIAL_MASTER_KEY", "").strip())
    try:
        credential_cipher()
        writable = True
    except HTTPException:
        writable = False
    return CredentialVaultStatus(
        writable=writable,
        master_key_configured=master_key_configured,
        total=len(credentials),
        configured=sum(item.configured for item in credentials),
        database_overrides=sum(item.origin == "database" for item in credentials),
        environment_fallbacks=sum(item.origin == "environment" for item in credentials),
        errors=sum(item.origin == "error" for item in credentials),
    )


def normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", value):
        raise HTTPException(status_code=422, detail="Username must be 3-40 characters using letters, numbers, dot, dash, or underscore")
    return value


def password_digest(password: str, salt_hex: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    return pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations).hex()


def row_to_auth_user(row: sqlite3.Row) -> AuthUser:
    return AuthUser(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_user_preferences(user_id: str) -> UserPreferences:
    init_db()
    with db_connect() as connection:
        row = connection.execute(
            "SELECT theme, updated_at FROM user_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return UserPreferences()
    return UserPreferences(theme=row["theme"], updated_at=row["updated_at"])


def set_user_preferences(user_id: str, payload: UserPreferencesUpdate) -> UserPreferences:
    init_db()
    updated_at = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        if connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            INSERT INTO user_preferences (user_id, theme, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                theme = excluded.theme,
                updated_at = excluded.updated_at
            """,
            (user_id, payload.theme, updated_at),
        )
    return UserPreferences(theme=payload.theme, updated_at=updated_at)


def auth_setup_required() -> bool:
    init_db()
    with db_connect() as connection:
        return connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def insert_user(connection: sqlite3.Connection, payload: UserCreate) -> AuthUser:
    username = normalize_username(payload.username)
    salt = secrets.token_hex(16)
    now = datetime.now(timezone.utc).isoformat()
    user_id = sha256(f"user:{username}:{now}:{secrets.token_hex(8)}".encode("utf-8")).hexdigest()[:20]
    try:
        connection.execute(
            """
            INSERT INTO users (
                id, username, display_name, role, enabled, password_salt,
                password_hash, password_iterations, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                payload.display_name.strip(),
                payload.role,
                int(payload.enabled),
                salt,
                password_digest(payload.password, salt),
                PASSWORD_ITERATIONS,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_auth_user(row)


def create_initial_admin(payload: SetupRequest) -> AuthUser:
    init_db()
    with db_connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            raise HTTPException(status_code=409, detail="Initial setup has already been completed")
        return insert_user(
            connection,
            UserCreate(
                username=payload.username,
                display_name=payload.display_name,
                password=payload.password,
                role="admin",
            ),
        )


def authenticate_user(username: str, password: str) -> AuthUser | None:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()
    if row is None or not row["enabled"]:
        return None
    actual = password_digest(password, row["password_salt"], int(row["password_iterations"]))
    if not hmac.compare_digest(actual, row["password_hash"]):
        return None
    return row_to_auth_user(row)


def create_auth_session(user: AuthUser) -> tuple[str, AuthSessionInfo]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(hours=SESSION_HOURS)
    with db_connect() as connection:
        connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (created.isoformat(),))
        connection.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (sha256(token.encode("utf-8")).hexdigest(), user.id, csrf_token, created.isoformat(), expires.isoformat()),
        )
    return token, AuthSessionInfo(
        user=user,
        csrf_token=csrf_token,
        expires_at=expires.isoformat(),
        preferences=get_user_preferences(user.id),
    )


def lookup_auth_session(token: str | None) -> tuple[AuthUser, str, str] | None:
    if not token:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        row = connection.execute(
            """
            SELECT u.*, s.csrf_token, s.expires_at
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.enabled = 1
            """,
            (sha256(token.encode("utf-8")).hexdigest(), now),
        ).fetchone()
    if row is None:
        return None
    return row_to_auth_user(row), row["csrf_token"], row["expires_at"]


def revoke_auth_session(token: str | None) -> None:
    if not token:
        return
    with db_connect() as connection:
        connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (sha256(token.encode("utf-8")).hexdigest(),))


def add_admin_audit(
    user: AuthUser,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: str,
    before_state: dict[str, object] | None = None,
    after_state: dict[str, object] | None = None,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    audit_id = sha256(f"audit:{user.id}:{action}:{resource_type}:{resource_id}:{created_at}:{secrets.token_hex(4)}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            "INSERT INTO admin_audit_logs (id, user_id, username, action, resource_type, resource_id, detail, before_state, after_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                user.id,
                user.username,
                action,
                resource_type,
                resource_id,
                detail[:1000],
                json.dumps(before_state, ensure_ascii=False, default=str) if before_state is not None else None,
                json.dumps(after_state, ensure_ascii=False, default=str) if after_state is not None else None,
                created_at,
            ),
        )


def current_request_user(request: Request) -> AuthUser:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def current_admin_user(request: Request) -> AuthUser:
    user = current_request_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


def set_session_cookie(response: Response, token: str) -> None:
    secure = os.getenv("APP_COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


ADMIN_RESOURCE_CONFIG: dict[str, dict[str, object]] = {
    "users": {"name": "用户", "category": "账号权限", "table": "users", "id": "id", "search": ["username", "display_name", "role"], "order": "updated_at", "hidden": ["password_salt", "password_hash", "password_iterations"], "actions": ["view", "edit", "enable", "reset-password"], "status": "enabled"},
    "auth-sessions": {"name": "登录会话", "category": "账号权限", "table": "auth_sessions", "id": "token_hash", "search": ["user_id"], "order": "created_at", "hidden": ["token_hash", "csrf_token"], "actions": ["view", "revoke"]},
    "admin-audit": {"name": "管理审计", "category": "账号权限", "table": "admin_audit_logs", "id": "id", "search": ["username", "action", "resource_type", "detail"], "order": "created_at", "json": ["before_state", "after_state"], "actions": ["view", "export"]},
    "settings": {"name": "系统设置", "category": "系统配置", "table": "app_config", "id": "key", "search": ["key", "value"], "order": "updated_at", "actions": ["view", "edit"]},
    "watchlist": {"name": "自选标的", "category": "交易配置", "table": "watchlist_items", "id": "provider || ':' || symbol", "search": ["provider", "symbol"], "order": "created_at", "actions": ["view", "enable", "bulk-status"], "status": "enabled"},
    "screener-presets": {"name": "筛选模板", "category": "交易配置", "table": "screener_presets", "id": "id", "search": ["name", "filters"], "order": "created_at", "json": ["filters"], "actions": ["view", "edit", "enable", "bulk-status"], "status": "enabled"},
    "news-sources": {"name": "信息源", "category": "数据接入", "table": "news_sources", "id": "id", "search": ["name", "provider", "category", "adapter", "url", "symbols", "capabilities"], "order": "priority", "json": ["capabilities", "config"], "actions": ["view", "edit", "enable", "refresh", "bulk-status"], "status": "enabled"},
    "factors": {"name": "因子", "category": "交易配置", "table": "factor_definitions", "id": "id", "search": ["id", "name", "category", "description", "signal"], "order": "created_at", "actions": ["view", "edit", "enable", "bulk-status"], "status": "enabled"},
    "strategies": {"name": "策略", "category": "交易配置", "table": "strategy_templates", "id": "id", "search": ["name", "category", "description"], "order": "created_at", "json": ["factor_ids", "default_filters", "risk_controls", "validation_rules"], "actions": ["view", "edit", "enable", "validate", "bulk-status"], "status": "enabled"},
    "alerts": {"name": "预警规则", "category": "风险执行", "table": "alert_rules", "id": "id", "search": ["symbol", "name", "metric"], "order": "created_at", "actions": ["view", "edit", "enable", "evaluate", "bulk-status"], "status": "enabled"},
    "notification-channels": {"name": "通知渠道", "category": "风险执行", "table": "notification_channels", "id": "id", "search": ["name", "type", "target"], "order": "created_at", "actions": ["view", "edit", "enable", "test", "bulk-status"], "status": "enabled"},
    "portfolio-positions": {"name": "模拟持仓", "category": "风险执行", "table": "portfolio_positions", "id": "id", "search": ["symbol", "side", "note"], "order": "created_at", "actions": ["view", "edit", "enable", "bulk-status"], "status": "enabled"},
    "news-articles": {"name": "新闻文章", "category": "内容数据", "table": "news_articles", "id": "id", "search": ["title", "summary", "source_name", "symbols", "sentiment"], "order": "published_at", "actions": ["view", "hide", "export", "bulk-status"], "status": "enabled"},
    "backtests": {"name": "回测记录", "category": "研究历史", "table": "backtest_runs", "id": "id", "search": ["symbol", "factor_id", "factor_name", "interval"], "order": "created_at", "json": ["equity_curve", "signal_points", "request_payload"], "actions": ["view", "export", "rerun"]},
    "forward-tests": {"name": "前向测试", "category": "研究历史", "table": "forward_test_runs", "id": "id", "search": ["symbol", "factor_id", "factor_name", "interval", "pass_fail"], "order": "generated_at", "json": ["equity_curve", "checks", "request_payload"], "actions": ["view", "export", "rerun"]},
    "walk-forward-tests": {"name": "滚动样本外", "category": "研究历史", "table": "walk_forward_runs", "id": "id", "search": ["symbol", "factor_id", "factor_name", "interval", "pass_fail"], "order": "generated_at", "json": ["windows", "checks", "actions", "request_payload"], "actions": ["view", "export", "rerun"]},
    "stress-tests": {"name": "压力测试", "category": "研究历史", "table": "stress_test_runs", "id": "id", "search": ["symbol", "factor_id", "factor_name", "pass_fail"], "order": "generated_at", "json": ["scenarios", "actions", "request_payload"], "actions": ["view", "export", "rerun"]},
    "strategy-validations": {"name": "策略验证", "category": "研究历史", "table": "strategy_validation_runs", "id": "id", "search": ["strategy_id", "strategy_name", "pass_fail"], "order": "generated_at", "json": ["regime", "filter_validation", "symbols", "backtest_ids", "forward_test_ids", "checks", "request_payload"], "actions": ["view", "export", "rerun"]},
    "automation-runs": {"name": "自动化运行", "category": "系统历史", "table": "automation_runs", "id": "id", "search": ["status", "summary"], "order": "finished_at", "actions": ["view", "export", "rerun"]},
    "market-regimes": {"name": "市场状态快照", "category": "系统历史", "table": "market_regime_snapshots", "id": "id", "search": ["label", "risk_level", "summary"], "order": "generated_at", "json": ["evidence", "actions"], "actions": ["view", "export"]},
    "events": {"name": "事件日志", "category": "系统历史", "table": "event_logs", "id": "id", "search": ["event_type", "severity", "symbol", "title", "message"], "order": "created_at", "actions": ["view", "export"]},
    "notification-deliveries": {"name": "通知投递", "category": "系统历史", "table": "notification_deliveries", "id": "id", "search": ["channel_id", "event_id", "error"], "order": "delivered_at", "actions": ["view", "export"]},
    "market-data": {"name": "K线与指标", "category": "虚拟资源", "virtual": True, "actions": ["view", "refresh", "export"]},
    "research-reports": {"name": "研究报告", "category": "虚拟资源", "virtual": True, "actions": ["view", "refresh", "export"]},
    "data-catalog": {"name": "数据目录", "category": "虚拟资源", "virtual": True, "actions": ["view", "refresh", "export"]},
}


DEDICATED_RESOURCE_PAGES: dict[str, str] = {
    "watchlist": "/watchlist",
    "screener-presets": "/market-tools",
    "news-sources": "/admin/news-sources",
    "factors": "/factors",
    "strategies": "/factors",
    "alerts": "/alerts",
    "notification-channels": "/alerts",
    "portfolio-positions": "/alerts",
}


def admin_resource_config(resource_type: str) -> dict[str, object]:
    config = ADMIN_RESOURCE_CONFIG.get(resource_type)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown resource type")
    return config


def decode_admin_row(row: sqlite3.Row, config: dict[str, object]) -> dict[str, object]:
    item: dict[str, object] = dict(row)
    for field in config.get("hidden", []):
        item.pop(str(field), None)
    for field in config.get("json", []):
        key = str(field)
        value = item.get(key)
        if isinstance(value, str) and value:
            try:
                item[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    status_field = config.get("status")
    if status_field and status_field in item:
        item[str(status_field)] = bool(item[str(status_field)])
    if config.get("table") == "news_sources":
        credential_id, origin, configured, hint, test_status = credential_metadata_for_resource(
            "information_source", str(item.get("id", "")), str(item.get("api_key_env", ""))
        )
        item.update({
            "credential_id": credential_id,
            "api_key_configured": configured,
            "credential_origin": origin,
            "secret_hint": hint,
            "credential_test_status": test_status,
        })
    return item


def virtual_admin_resource_items(resource_type: str) -> list[dict[str, object]]:
    if resource_type == "market-data":
        provider = get_active_market_provider_name()
        return [
            {
                "_resource_id": item.symbol,
                "symbol": item.symbol,
                "name": item.name,
                "asset_class": item.asset_class,
                "market": item.market,
                "provider": provider,
                "candles_endpoint": f"/api/candles/{item.symbol}",
                "indicators_endpoint": f"/api/indicators/{item.symbol}",
            }
            for item in get_market_provider().list_symbols()
        ]
    if resource_type == "research-reports":
        reports = [
            ("research-health", "研究健康", "/api/research-health"),
            ("research-readiness", "研究就绪度", "/api/research-readiness"),
            ("data-quality", "数据质量", "/api/data-quality"),
            ("data-freshness", "数据新鲜度", "/api/data-freshness"),
            ("strategy-leaderboard", "策略排行榜", "/api/strategy-leaderboard"),
            ("strategy-validation-coverage", "策略验证覆盖", "/api/strategy-validation-coverage"),
            ("strategy-operating-plan", "策略操作计划", "/api/strategy-operating-plan"),
            ("market-state-controls", "市场状态控制", "/api/market-state-controls"),
            ("regime-scenario-risk", "市场状态情景风险", "/api/regime-scenario-risk"),
        ]
        return [{"_resource_id": report_id, "id": report_id, "name": name, "endpoint": endpoint, "kind": "derived-report"} for report_id, name, endpoint in reports]
    if resource_type == "data-catalog":
        return [{"_resource_id": item.id, **item.model_dump(mode="json")} for item in data_catalog_items()]
    raise HTTPException(status_code=404, detail="Unknown virtual resource type")


def admin_resource_descriptor(resource_type: str) -> AdminResourceDescriptor:
    config = admin_resource_config(resource_type)
    if config.get("virtual"):
        total = len(virtual_admin_resource_items(resource_type))
        actions = [str(item) for item in config.get("actions", [])]
        return AdminResourceDescriptor(
            id=resource_type,
            name=str(config["name"]),
            category=str(config["category"]),
            mutable=False,
            total=total,
            actions=actions,
            management_mode="read_only",
        )
    table = str(config["table"])
    status_field = config.get("status")
    with db_connect() as connection:
        total = int(connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"])
        active = int(connection.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {status_field} = 1").fetchone()["total"]) if status_field else None
    actions = [str(item) for item in config.get("actions", [])]
    return AdminResourceDescriptor(
        id=resource_type,
        name=str(config["name"]),
        category=str(config["category"]),
        mutable=any(action in actions for action in ("edit", "enable", "hide", "bulk-status")),
        status_field=str(status_field) if status_field else None,
        total=total,
        active=active,
        actions=actions,
        management_mode="dedicated" if resource_type in DEDICATED_RESOURCE_PAGES else ("read_only" if not any(action in actions for action in ("edit", "enable", "hide", "bulk-status")) else "governance"),
        canonical_href=DEDICATED_RESOURCE_PAGES.get(resource_type),
    )


def list_admin_resources() -> list[AdminResourceDescriptor]:
    init_db()
    return [admin_resource_descriptor(resource_type) for resource_type in ADMIN_RESOURCE_CONFIG if resource_type not in DEDICATED_RESOURCE_PAGES]


def require_governance_managed_resource(resource_type: str) -> None:
    canonical_href = DEDICATED_RESOURCE_PAGES.get(resource_type)
    if canonical_href:
        raise HTTPException(
            status_code=409,
            detail={"message": "Resource is managed by a dedicated module", "canonical_href": canonical_href},
        )


def query_admin_resource(
    resource_type: str,
    page: int = 1,
    page_size: int = 25,
    query: str = "",
    enabled: bool | None = None,
    source_type: str = "",
    sort: str = "",
    direction: Literal["asc", "desc"] = "desc",
) -> AdminResourcePage:
    config = admin_resource_config(resource_type)
    safe_page = max(1, page)
    safe_page_size = max(1, min(100, page_size))
    if config.get("virtual"):
        items = virtual_admin_resource_items(resource_type)
        if query.strip():
            needle = query.strip().lower()
            items = [item for item in items if needle in json.dumps(item, ensure_ascii=False).lower()]
        if sort and items and sort in items[0]:
            items.sort(key=lambda item: (item.get(sort) is None, str(item.get(sort, ""))), reverse=direction == "desc")
        start = (safe_page - 1) * safe_page_size
        return AdminResourcePage(
            resource=admin_resource_descriptor(resource_type),
            page=safe_page,
            page_size=safe_page_size,
            total=len(items),
            items=items[start : start + safe_page_size],
        )
    table = str(config["table"])
    id_expression = str(config["id"])
    conditions: list[str] = []
    params: list[object] = []
    if query.strip():
        search_fields = [str(item) for item in config.get("search", [])]
        if search_fields:
            conditions.append("(" + " OR ".join(f"CAST({field} AS TEXT) LIKE ?" for field in search_fields) + ")")
            params.extend([f"%{query.strip()}%"] * len(search_fields))
    status_field = config.get("status")
    if enabled is not None and status_field:
        conditions.append(f"{status_field} = ?")
        params.append(int(enabled))
    if resource_type == "news-sources" and source_type.strip():
        conditions.append("type = ?")
        params.append(source_type.strip())
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with db_connect() as connection:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        order = sort if sort in columns else str(config["order"])
        total = int(connection.execute(f"SELECT COUNT(*) AS total FROM {table}{where_clause}", params).fetchone()["total"])
        rows = connection.execute(
            f"SELECT *, {id_expression} AS _resource_id FROM {table}{where_clause} ORDER BY {order} {direction.upper()} LIMIT ? OFFSET ?",
            [*params, safe_page_size, (safe_page - 1) * safe_page_size],
        ).fetchall()
    return AdminResourcePage(
        resource=admin_resource_descriptor(resource_type),
        page=safe_page,
        page_size=safe_page_size,
        total=total,
        items=[decode_admin_row(row, config) for row in rows],
    )


def get_admin_resource_item(resource_type: str, resource_id: str) -> dict[str, object]:
    config = admin_resource_config(resource_type)
    if config.get("virtual"):
        item = next((item for item in virtual_admin_resource_items(resource_type) if str(item["_resource_id"]) == resource_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return item
    with db_connect() as connection:
        row = connection.execute(
            f"SELECT *, {config['id']} AS _resource_id FROM {config['table']} WHERE {config['id']} = ?",
            (resource_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return decode_admin_row(row, config)


def get_config_value(key: str, default: str) -> str:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_config_value(key: str, value: str) -> None:
    init_db()
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO app_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, datetime.now(timezone.utc).isoformat()),
        )


def get_automation_settings() -> AutomationSettings:
    enabled = get_config_value("automation_enabled", "false").lower() == "true"
    try:
        interval = int(get_config_value("automation_interval_seconds", "300"))
    except ValueError:
        interval = 300
    return AutomationSettings(enabled=enabled, interval_seconds=max(60, min(86400, interval)))


def set_automation_settings(settings: AutomationSettings) -> AutomationSettings:
    interval = max(60, min(86400, settings.interval_seconds))
    set_config_value("automation_enabled", "true" if settings.enabled else "false")
    set_config_value("automation_interval_seconds", str(interval))
    updated = get_automation_settings()
    add_event_log(
        event_type="system",
        severity="info",
        title="自动化设置已更新",
        message=f"自动化 {'开启' if updated.enabled else '关闭'}，间隔 {updated.interval_seconds} 秒。",
    )
    return updated


def get_filter_preferences() -> FilterPreferences:
    signal = get_config_value("default_signal_filter", "all")
    if signal not in {"all", "strong_buy", "buy", "neutral", "sell", "strong_sell"}:
        signal = "all"
    try:
        rsi_min = int(get_config_value("default_rsi_min", "0"))
    except ValueError:
        rsi_min = 0
    positive_only = get_config_value("default_positive_only", "false").lower() == "true"
    return FilterPreferences(
        signal_filter=signal,  # type: ignore[arg-type]
        rsi_min=max(0, min(100, rsi_min)),
        positive_only=positive_only,
    )


def get_app_settings() -> AppSettings:
    init_db()
    provider_name = get_active_market_provider_name()
    with db_connect() as connection:
        source_count = connection.execute("SELECT COUNT(*) AS total FROM news_sources").fetchone()["total"]
        channel_count = connection.execute("SELECT COUNT(*) AS total FROM notification_channels").fetchone()["total"]
    return AppSettings(
        active_market_provider=provider_name,
        automation=get_automation_settings(),
        filters=get_filter_preferences(),
        news_sources=int(source_count),
        notification_channels=int(channel_count),
        watchlist_symbols=len(list_watchlist_snapshots()),
    )


def update_app_settings(payload: AppSettingsUpdate) -> AppSettings:
    if payload.active_market_provider is not None:
        set_active_market_provider(payload.active_market_provider)
    if payload.automation_enabled is not None or payload.automation_interval_seconds is not None:
        current = get_automation_settings()
        set_automation_settings(
            AutomationSettings(
                enabled=current.enabled if payload.automation_enabled is None else payload.automation_enabled,
                interval_seconds=current.interval_seconds if payload.automation_interval_seconds is None else payload.automation_interval_seconds,
            )
        )
    if payload.signal_filter is not None:
        set_config_value("default_signal_filter", payload.signal_filter)
    if payload.rsi_min is not None:
        set_config_value("default_rsi_min", str(max(0, min(100, payload.rsi_min))))
    if payload.positive_only is not None:
        set_config_value("default_positive_only", "true" if payload.positive_only else "false")
    updated = get_app_settings()
    add_event_log(
        event_type="system",
        severity="info",
        title="应用设置已更新",
        message=f"行情源 {updated.active_market_provider}，默认筛选 {updated.filters.signal_filter} / RSI {updated.filters.rsi_min}。",
    )
    return updated
