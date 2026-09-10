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
    AuthUser,
    InformationSourceHealth,
    InformationSourceSummary,
    Literal,
    NewsDetail,
    NewsEvent,
    NewsEventAnnotation,
    NewsFeedPage,
    NewsImpactDiagnosticFacet,
    NewsImpactDiagnosticItem,
    NewsImpactDiagnosticPage,
    NewsImpactGraphEdge,
    NewsImpactGraphEdgeCreate,
    NewsImpactGraphEdgeReview,
    NewsImpactGraphNode,
    NewsImpactGraphView,
    NewsImpactGraphViewEdge,
    NewsImpactLink,
    NewsImpactOverrideUpdate,
    NewsRefreshResult,
    NewsSource,
    NewsSourceCreate,
    NewsSourceAttempt,
    NewsSourceDiagnostic,
    NewsSourceEvidence,
    NewsSourceFacet,
    NewsSourceMonitor,
    NewsSourceOverview,
    NewsSourceRun,
    NewsSourceTestResult,
    NewsSourceUpdate,
    NewsSymbolOverrideUpdate,
    NewsSymbolTag,
    NewsTaggingBackfillResult,
    NewsTaggingStatus,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
from app.modules.market.service import FinnhubMarketProvider, MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
credential_metadata_for_resource = service_call('app.modules.platform.service', 'credential_metadata_for_resource')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
get_active_market_provider_name = service_call('app.modules.market.service', 'get_active_market_provider_name')
get_agent = service_call('app.modules.ai.service', 'get_agent')
get_agent_provider = service_call('app.modules.ai.service', 'get_agent_provider')
get_model_route = service_call('app.modules.ai.service', 'get_model_route')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
json_value = service_call('app.modules.ai.service', 'json_value')
list_supported_market_symbols = service_call('app.modules.market.service', 'list_supported_market_symbols')
ordered_route_targets = service_call('app.modules.ai.service', 'ordered_route_targets')
resolve_api_key = service_call('app.modules.platform.service', 'resolve_api_key')
credential_cipher = service_call('app.modules.platform.service', 'credential_cipher')
ensure_credential_binding = service_call('app.modules.platform.service', 'ensure_credential_binding')
set_credential_secret = service_call('app.modules.platform.service', 'set_credential_secret')
sync_information_source_registry = service_call('app.modules.platform.service', 'sync_information_source_registry')
row_to_agent_proposal = service_call('app.modules.ai.service', 'row_to_agent_proposal')
sanitize_agent_input = service_call('app.modules.ai.service', 'sanitize_agent_input')
schedule_time_after = service_call('app.modules.operations.service', 'schedule_time_after')


def normalize_symbols(symbols: list[str] | str | None) -> list[str]:
    if symbols is None:
        return []
    if isinstance(symbols, str):
        raw = re.split(r"[,，\s]+", symbols)
    else:
        raw = symbols
    valid = {item.symbol for item in list_supported_market_symbols()}
    return sorted({item.strip().upper() for item in raw if item.strip().upper() in valid})


def list_news_sources() -> list[NewsSource]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT * FROM news_sources ORDER BY priority DESC, created_at DESC"
        ).fetchall()
    return [row_to_source(row) for row in rows]


def create_news_source(payload: NewsSourceCreate) -> NewsSource:
    init_db()
    if payload.api_key is not None:
        credential_cipher()
    symbols = ",".join(normalize_symbols(payload.symbols))
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with db_connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO news_sources (
                    name, type, category, adapter, provider, url, enabled, symbols, region, language,
                    auth_type, api_key_env, capabilities, config, priority, reliability_weight,
                    poll_interval_seconds, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.name.strip(), payload.type, payload.category, payload.adapter,
                    payload.provider.strip() or "custom", str(payload.url), int(payload.enabled),
                    symbols, payload.region.strip() or "global", payload.language.strip() or "en",
                    payload.auth_type, payload.api_key_env.strip(),
                    json.dumps(payload.capabilities, ensure_ascii=False),
                    json.dumps(payload.config, ensure_ascii=False), payload.priority,
                    payload.reliability_weight, max(60, payload.poll_interval_seconds), created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM news_sources WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="News source URL already exists") from exc
    credential_id = ensure_credential_binding(
        "information_source", str(row["id"]), payload.name.strip(), payload.api_key_env.strip()
    )
    if payload.api_key is not None and credential_id is not None:
        set_credential_secret(credential_id, payload.api_key.get_secret_value())
    sync_information_source_registry()
    return row_to_source(row)


def update_news_source(source_id: int, payload: NewsSourceUpdate) -> NewsSource:
    init_db()
    if payload.api_key is not None:
        credential_cipher()
    current_sources = {source.id: source for source in list_news_sources()}
    if source_id not in current_sources:
        raise HTTPException(status_code=404, detail="News source not found")
    current = current_sources[source_id]
    values = {
        "name": payload.name.strip() if payload.name is not None else current.name,
        "url": str(payload.url) if payload.url is not None else current.url,
        "type": payload.type if payload.type is not None else current.type,
        "category": payload.category if payload.category is not None else current.category,
        "adapter": payload.adapter if payload.adapter is not None else current.adapter,
        "provider": payload.provider.strip() if payload.provider is not None else current.provider,
        "enabled": int(payload.enabled) if payload.enabled is not None else int(current.enabled),
        "symbols": ",".join(normalize_symbols(payload.symbols)) if payload.symbols is not None else ",".join(current.symbols),
        "region": payload.region.strip() if payload.region is not None else current.region,
        "language": payload.language.strip() if payload.language is not None else current.language,
        "auth_type": payload.auth_type if payload.auth_type is not None else current.auth_type,
        "api_key_env": payload.api_key_env.strip() if payload.api_key_env is not None else current.api_key_env,
        "capabilities": payload.capabilities if payload.capabilities is not None else current.capabilities,
        "config": payload.config if payload.config is not None else current.config,
        "priority": payload.priority if payload.priority is not None else current.priority,
        "reliability_weight": payload.reliability_weight if payload.reliability_weight is not None else current.reliability_weight,
        "poll_interval": max(60, min(86400, payload.poll_interval_seconds)) if payload.poll_interval_seconds is not None else current.poll_interval_seconds,
    }
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE news_sources
            SET name=?, type=?, category=?, adapter=?, provider=?, url=?, enabled=?, symbols=?,
                region=?, language=?, auth_type=?, api_key_env=?, capabilities=?, config=?, priority=?,
                reliability_weight=?, poll_interval_seconds=?
            WHERE id=?
            """,
            (
                values["name"], values["type"], values["category"], values["adapter"],
                values["provider"], values["url"], values["enabled"], values["symbols"],
                values["region"], values["language"], values["auth_type"], values["api_key_env"],
                json.dumps(values["capabilities"], ensure_ascii=False),
                json.dumps(values["config"], ensure_ascii=False), values["priority"],
                values["reliability_weight"], values["poll_interval"], source_id,
            ),
        )
        row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (source_id,)).fetchone()
    credential_id = ensure_credential_binding(
        "information_source", str(source_id), str(values["name"]), str(values["api_key_env"])
    )
    if payload.api_key is not None and credential_id is not None:
        set_credential_secret(credential_id, payload.api_key.get_secret_value())
    sync_information_source_registry()
    return row_to_source(row)


def row_to_source(row: sqlite3.Row) -> NewsSource:
    api_key_env = row["api_key_env"] or ""
    try:
        capabilities = json.loads(row["capabilities"] or "[]")
    except json.JSONDecodeError:
        capabilities = [item for item in (row["capabilities"] or "").split(",") if item]
    try:
        source_config = json.loads(row["config"] or "{}")
    except json.JSONDecodeError:
        source_config = {}
    credential_id, credential_origin, credential_configured, secret_hint, test_status = credential_metadata_for_resource(
        "information_source", str(row["id"]), api_key_env
    )
    return NewsSource(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        category=row["category"],
        adapter=row["adapter"],
        provider=row["provider"],
        url=row["url"],
        enabled=bool(row["enabled"]),
        symbols=normalize_symbols(row["symbols"]),
        region=row["region"],
        language=row["language"],
        auth_type=row["auth_type"],
        api_key_env=api_key_env,
        api_key_configured=row["auth_type"] == "none" or not api_key_env or credential_configured,
        credential_id=credential_id,
        credential_origin=credential_origin,
        secret_hint=secret_hint,
        credential_test_status=test_status,
        capabilities=[str(item) for item in capabilities],
        config=source_config if isinstance(source_config, dict) else {},
        priority=int(row["priority"]),
        reliability_weight=float(row["reliability_weight"]),
        poll_interval_seconds=row["poll_interval_seconds"],
        last_fetched_at=row["last_fetched_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
    )


def information_source_summary() -> InformationSourceSummary:
    sources = list_news_sources()
    by_category: dict[str, int] = {}
    by_adapter: dict[str, int] = {}
    for source in sources:
        by_category[source.category] = by_category.get(source.category, 0) + 1
        by_adapter[source.adapter] = by_adapter.get(source.adapter, 0) + 1
    return InformationSourceSummary(
        total=len(sources),
        enabled=sum(source.enabled for source in sources),
        configured=sum(source.api_key_configured for source in sources),
        errors=sum(bool(source.last_error) for source in sources),
        by_category=by_category,
        by_adapter=by_adapter,
    )


def information_source_health() -> list[InformationSourceHealth]:
    health: list[InformationSourceHealth] = []
    for source in list_news_sources():
        if not source.enabled:
            status: Literal["online", "idle", "disabled", "unconfigured", "error"] = "disabled"
            message = "信息源已停用，可在资源管理中启用。"
        elif not information_source_is_ingestible(source):
            status = "online"
            message = "目录型集成已启用，不参与文章采集。"
        elif not source.api_key_configured:
            status = "unconfigured"
            message = f"缺少环境变量 {source.api_key_env}。"
        elif source.last_error:
            status = "error"
            message = source.last_error
        elif source.last_fetched_at:
            status = "online"
            message = "最近一次采集成功。"
        else:
            status = "idle"
            message = "已配置，等待首次采集。"
        health.append(InformationSourceHealth(
            id=source.id, name=source.name, category=source.category, adapter=source.adapter,
            status=status, message=message, last_checked_at=source.last_fetched_at,
        ))
    return health


def information_source_is_due(source: NewsSource, now: datetime | None = None) -> bool:
    if not source.last_fetched_at:
        return True
    try:
        last_fetched = datetime.fromisoformat(source.last_fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_fetched.tzinfo is None:
        last_fetched = last_fetched.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - last_fetched >= timedelta(seconds=source.poll_interval_seconds)


def information_source_is_ingestible(source: NewsSource) -> bool:
    return source.adapter in {"rss", "finnhub", "adanos", "gdelt", "cryptocompare", "alphavantage", "fred"}


def row_to_news_source_run(row: sqlite3.Row) -> NewsSourceRun:
    diagnostic = None
    if "diagnostic_json" in row.keys() and row["diagnostic_json"]:
        try:
            diagnostic = NewsSourceDiagnostic.model_validate(json.loads(row["diagnostic_json"]))
        except (ValueError, TypeError):
            diagnostic = None
    return NewsSourceRun(
        id=row["id"], source_id=row["source_id"], trigger=row["trigger"], status=row["status"],
        started_at=row["started_at"], finished_at=row["finished_at"], duration_ms=row["duration_ms"],
        fetched=row["fetched"], inserted=row["inserted"], error=row["error"], diagnostic=diagnostic,
    )


def list_news_source_runs(source_id: int, limit: int = 20) -> list[NewsSourceRun]:
    init_db()
    with db_connect() as connection:
        if connection.execute("SELECT 1 FROM news_sources WHERE id = ?", (source_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="News source not found")
        rows = connection.execute(
            "SELECT * FROM news_source_runs WHERE source_id = ? ORDER BY started_at DESC LIMIT ?",
            (source_id, max(1, min(200, limit))),
        ).fetchall()
    return [row_to_news_source_run(row) for row in rows]


def news_source_monitoring() -> list[NewsSourceMonitor]:
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    monitors: list[NewsSourceMonitor] = []
    with db_connect() as connection:
        sources = connection.execute("SELECT * FROM news_sources ORDER BY priority DESC, created_at DESC").fetchall()
        run_rows = connection.execute(
            "SELECT * FROM news_source_runs ORDER BY source_id, started_at DESC"
        ).fetchall()
    runs_by_source: dict[int, list[sqlite3.Row]] = {}
    for row in run_rows:
        runs_by_source.setdefault(row["source_id"], []).append(row)
    for row in sources:
        source = row_to_source(row)
        runs = runs_by_source.get(source.id, [])
        operational_runs = [run for run in runs if run["trigger"] != "test"]
        latest = operational_runs[0] if operational_runs else None
        latest_diagnostic = row_to_news_source_run(latest).diagnostic if latest else None
        recent = [run for run in operational_runs if run["started_at"] >= cutoff and run["status"] != "running"]
        successful = sum(run["status"] == "succeeded" for run in recent)
        consecutive_failures = 0
        for run in runs:
            if run["status"] == "failed":
                consecutive_failures += 1
            elif run["status"] != "running":
                break
        ingestible = information_source_is_ingestible(source)
        if latest and latest["status"] == "running":
            status = "running"
        elif not source.enabled:
            status = "paused"
        elif not ingestible:
            status = "unsupported"
        elif not source.api_key_configured:
            status = "unconfigured"
        elif source.last_error or (latest and latest["status"] == "failed"):
            status = "error"
        elif source.last_fetched_at:
            status = "healthy"
        else:
            status = "idle"
        monitors.append(NewsSourceMonitor(
            source_id=source.id, name=source.name, provider=source.provider, adapter=source.adapter,
            enabled=source.enabled, configured=source.api_key_configured, ingestible=ingestible, status=status,
            poll_interval_seconds=source.poll_interval_seconds,
            last_run_at=(latest["finished_at"] or latest["started_at"]) if latest else source.last_fetched_at,
            next_run_at=schedule_time_after(source.last_fetched_at, source.poll_interval_seconds)
            if source.enabled and source.api_key_configured and ingestible else None,
            last_status=latest["status"] if latest else None,
            last_duration_ms=latest["duration_ms"] if latest else None,
            last_fetched=latest["fetched"] if latest else 0,
            last_inserted=latest["inserted"] if latest else 0,
            runs_24h=len(recent),
            success_rate_24h=round(successful / len(recent) * 100, 1) if recent else None,
            consecutive_failures=consecutive_failures,
            last_error=source.last_error or (latest["error"] if latest else None),
            last_diagnostic=latest_diagnostic,
        ))
    return monitors


def news_source_overview() -> NewsSourceOverview:
    return NewsSourceOverview(sources=list_news_sources(), monitors=news_source_monitoring())


def refresh_due_news_sources(
    force: bool = False,
    trigger: Literal["schedule", "manual", "automation"] = "schedule",
    source_ids: list[int] | None = None,
) -> list[NewsRefreshResult]:
    selected_source_ids = {int(source_id) for source_id in (source_ids or []) if int(source_id) > 0}
    sources = [
        source for source in list_news_sources()
        if source.enabled and information_source_is_ingestible(source)
        and source.api_key_configured and (force or information_source_is_due(source))
        and (not selected_source_ids or source.id in selected_source_ids)
    ]
    return [refresh_source(source.id, trigger=trigger) for source in sources]


async def news_source_scheduler_loop() -> None:
    await asyncio.sleep(2)
    while True:
        try:
            await asyncio.to_thread(refresh_due_news_sources, False, "schedule")
        except Exception as exc:  # noqa: BLE001 - keep source scheduling alive
            add_event_log("news_refresh", "error", "新闻源调度失败", str(exc)[:240])
        await asyncio.sleep(5)


def strip_markup(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_datetime(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    for date_format in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, date_format).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()


def xml_text(element: ET.Element, names: list[str]) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
    for child in element:
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in {name.lower().split("}")[-1] for name in names} and child.text:
            return child.text.strip()
    return ""


def classify_article(title: str, summary: str) -> tuple[Literal["bullish", "bearish", "neutral", "mixed"], Literal["earnings", "macro", "regulatory", "product", "analyst", "crypto", "flow"], int]:
    text = f"{title} {summary}".lower()
    bullish_words = ["beat", "surge", "upgrade", "approval", "record", "growth", "profit", "rally", "上调", "增长", "批准", "突破"]
    bearish_words = ["miss", "drop", "lawsuit", "probe", "ban", "hack", "downgrade", "risk", "下调", "监管", "诉讼", "风险", "黑客"]
    bullish = sum(word in text for word in bullish_words)
    bearish = sum(word in text for word in bearish_words)
    if bullish and bearish:
        sentiment: Literal["bullish", "bearish", "neutral", "mixed"] = "mixed"
    elif bullish:
        sentiment = "bullish"
    elif bearish:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    if any(word in text for word in ["earnings", "revenue", "guidance", "财报", "业绩"]):
        event_type: Literal["earnings", "macro", "regulatory", "product", "analyst", "crypto", "flow"] = "earnings"
    elif any(word in text for word in ["fed", "inflation", "cpi", "rates", "宏观", "利率", "通胀"]):
        event_type = "macro"
    elif any(word in text for word in ["sec", "regulation", "lawsuit", "probe", "监管", "诉讼"]):
        event_type = "regulatory"
    elif any(word in text for word in ["bitcoin", "ethereum", "crypto", "btc", "eth", "加密"]):
        event_type = "crypto"
    elif any(word in text for word in ["etf", "flow", "fund", "资金流"]):
        event_type = "flow"
    elif any(word in text for word in ["upgrade", "downgrade", "target", "analyst", "分析师", "目标价"]):
        event_type = "analyst"
    else:
        event_type = "product"

    impact = 45 + bullish * 10 + bearish * 12
    if event_type in {"earnings", "regulatory", "crypto"}:
        impact += 12
    return sentiment, event_type, max(25, min(95, impact))


def extract_symbols(title: str, summary: str, source_symbols: list[str]) -> list[str]:
    text = f"{title} {summary}".upper()

    def contains_token(token: str) -> bool:
        return re.search(rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])", text) is not None

    supported = {
        item.symbol: item.name
        for provider in MARKET_PROVIDERS.values()
        for item in provider.list_symbols()
    }
    matched = list(source_symbols)
    matched.extend([
        symbol
        for symbol, name in supported.items()
        if contains_token(symbol) or contains_token(name.upper().split()[0])
    ])
    if contains_token("BITCOIN") or contains_token("BTC"):
        matched.append("BTCUSD")
    if contains_token("ETHEREUM") or contains_token("ETHER") or contains_token("ETH"):
        matched.append("ETHUSD")
    normalized_text = f"{title} {summary}".lower()
    aliases = {
        "AAPL": ["苹果公司", "苹果股价", "apple inc"],
        "NVDA": ["英伟达", "辉达", "nvidia"],
        "TSLA": ["特斯拉", "tesla"],
        "MSFT": ["微软", "microsoft"],
        "BTCUSD": ["比特币", "bitcoin"],
        "ETHUSD": ["以太坊", "ethereum", "ether"],
        "SPY": ["标普500 etf", "s&p 500 etf"],
        "QQQ": ["纳斯达克100 etf", "nasdaq 100 etf"],
        "SPX": ["标普500指数", "标普 500 指数", "s&p 500 index"],
        "NDX": ["纳斯达克100指数", "纳指100", "nasdaq 100 index"],
        "SHCOMP": ["上证指数", "上证综指", "shanghai composite"],
        "HSI": ["恒生指数", "hang seng index"],
    }
    for symbol, names in aliases.items():
        if any(
            name in normalized_text if any(ord(character) > 127 for character in name)
            else re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", normalized_text) is not None
            for name in names
        ):
            matched.append(symbol)
    return sorted(set(matched))


def news_instrument_catalog() -> dict[str, dict[str, str]]:
    from app.modules.market.charting import INSTRUMENTS

    catalog: dict[str, dict[str, str]] = {}
    for item in INSTRUMENTS:
        catalog[item.symbol.upper()] = {"instrument_id": item.id, "symbol": item.symbol.upper(), "name": item.name}
        if item.provider_symbol:
            catalog.setdefault(item.provider_symbol.upper(), catalog[item.symbol.upper()])
    for item in list_supported_market_symbols():
        catalog.setdefault(item.symbol.upper(), {
            "instrument_id": f"{get_active_market_provider_name().upper()}:{item.symbol.upper()}",
            "symbol": item.symbol.upper(), "name": item.name,
        })
    return catalog


def normalize_tag_symbol(symbol: str) -> dict[str, str] | None:
    normalized = str(symbol or "").strip().upper()
    catalog = news_instrument_catalog()
    candidate = catalog.get(normalized)
    if candidate:
        return candidate
    candidate = next((item for item in catalog.values() if item["instrument_id"].upper() == normalized), None)
    if candidate:
        return candidate
    try:
        from app.modules.market.charting import resolve_instrument

        instrument = resolve_instrument(normalized)
        return {"instrument_id": instrument.id, "symbol": instrument.symbol.upper(), "name": instrument.name}
    except HTTPException:
        return None


def row_to_news_symbol_tag(row: sqlite3.Row) -> NewsSymbolTag:
    return NewsSymbolTag(
        id=row["id"], article_id=row["article_id"], instrument_id=row["instrument_id"], symbol=row["symbol"],
        name=row["name"], relation=row["relation"], confidence=float(row["confidence"]), direction=row["direction"],
        origin=row["origin"], status=row["status"], evidence=row["evidence"] or "", agent_run_id=row["agent_run_id"],
        model=row["model"], tagging_version=int(row["tagging_version"]), created_at=row["created_at"], updated_at=row["updated_at"],
    )


def row_to_news_event_annotation(row: sqlite3.Row) -> NewsEventAnnotation:
    return NewsEventAnnotation(
        article_id=row["article_id"], taxonomy=row["taxonomy"], scope=row["scope"], summary=row["summary"],
        topics=json_value(row["topics"], []), countries=json_value(row["countries"], []),
        regions=json_value(row["regions"], []), actors=json_value(row["actors"], []),
        macro_factors=json_value(row["macro_factors"], []), horizon=row["horizon"],
        significance=int(row["significance"]), novelty=int(row["novelty"]),
        content_completeness=float(row["content_completeness"]), event_cluster_id=row["event_cluster_id"],
        origin=row["origin"], model=row["model"], pipeline_version=int(row["pipeline_version"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def row_to_news_impact_link(row: sqlite3.Row) -> NewsImpactLink:
    return NewsImpactLink(
        id=row["id"], article_id=row["article_id"], target_type=row["target_type"], target_id=row["target_id"],
        target_label=row["target_label"], symbol=row["symbol"], link_type=row["link_type"], channel=row["channel"],
        direction=row["direction"], relevance=float(row["relevance"]), impact_score=int(row["impact_score"]),
        confidence=float(row["confidence"]), horizon=row["horizon"], path=json_value(row["path"], []),
        evidence=row["evidence"], origin=row["origin"], status=row["status"],
        graph_edge_ids=json_value(row["graph_edge_ids"], []), agent_run_id=row["agent_run_id"], model=row["model"],
        pipeline_version=int(row["pipeline_version"]), created_at=row["created_at"], updated_at=row["updated_at"],
    )


def row_to_impact_graph_edge(row: sqlite3.Row) -> NewsImpactGraphEdge:
    return NewsImpactGraphEdge(**dict(row))


def normalized_ratio(value: object, default: float = 0.0) -> float:
    labels = {"low": 0.3, "medium": 0.6, "moderate": 0.6, "high": 0.85, "very_high": 0.95}
    if isinstance(value, str) and value.strip().lower() in labels:
        return labels[value.strip().lower()]
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def normalized_score(value: object, default: int = 0) -> int:
    labels = {"low": 30, "medium": 60, "moderate": 60, "high": 85, "very_high": 95}
    if isinstance(value, str) and value.strip().lower() in labels:
        return labels[value.strip().lower()]
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if 0 < number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def get_news_event_annotation(article_id: str) -> NewsEventAnnotation | None:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_event_annotations WHERE article_id=?", (article_id,)).fetchone()
    return row_to_news_event_annotation(row) if row else None


def list_news_impact_links(article_id: str, include_rejected: bool = False) -> list[NewsImpactLink]:
    init_db()
    status_filter = "" if include_rejected else " AND status IN ('accepted','pending')"
    with db_connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM news_impact_links WHERE article_id=?{status_filter} ORDER BY impact_score DESC, confidence DESC",
            (article_id,),
        ).fetchall()
    return [row_to_news_impact_link(row) for row in rows]


def list_news_impact_graph_edges(status: str | None = None, query: str = "") -> list[NewsImpactGraphEdge]:
    init_db()
    clauses: list[str] = []
    params: list[object] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if query.strip():
        clauses.append("(source_label LIKE ? OR target_label LIKE ? OR relation LIKE ?)")
        term = f"%{query.strip()}%"
        params.extend([term, term, term])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_connect() as connection:
        rows = connection.execute(f"SELECT * FROM news_impact_graph_edges{where} ORDER BY updated_at DESC", params).fetchall()
    return [row_to_impact_graph_edge(row) for row in rows]


def news_impact_graph_view(statuses: list[str] | None = None, query: str = "") -> NewsImpactGraphView:
    selected = set(statuses or ["approved", "pending"])
    needle = query.strip().lower()
    graph_edges: list[NewsImpactGraphViewEdge] = []
    node_values: dict[str, dict[str, object]] = {}
    counts = {status: 0 for status in ("approved", "pending", "failed", "rejected", "disabled")}

    def add_node(node_type: str, entity_id: str, label: str, *, incoming: bool = False) -> str:
        node_id = f"{node_type}:{entity_id}".lower()
        node = node_values.setdefault(node_id, {
            "id": node_id, "type": node_type, "entity_id": entity_id, "label": label,
            "edge_count": 0, "in_degree": 0, "out_degree": 0,
        })
        node["edge_count"] = int(node["edge_count"]) + 1
        degree_key = "in_degree" if incoming else "out_degree"
        node[degree_key] = int(node[degree_key]) + 1
        return node_id

    with db_connect() as connection:
        edge_rows = connection.execute(
            "SELECT * FROM news_impact_graph_edges ORDER BY updated_at DESC"
        ).fetchall()
        proposal_count_rows = connection.execute(
            """SELECT status, COUNT(*) AS total FROM agent_proposals
               WHERE resource_type='impact-graph' AND status!='executed' GROUP BY status"""
        ).fetchall()
        proposal_statuses = sorted(selected & {"pending", "failed", "rejected"})
        proposal_rows: list[sqlite3.Row] = []
        if proposal_statuses:
            placeholders = ",".join("?" for _ in proposal_statuses)
            proposal_rows = connection.execute(
                f"""SELECT * FROM agent_proposals WHERE resource_type='impact-graph'
                    AND status IN ({placeholders}) ORDER BY created_at DESC""",
                proposal_statuses,
            ).fetchall()

    for row in edge_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    for row in proposal_count_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + int(row["total"])

    for row in edge_rows:
        edge = row_to_impact_graph_edge(row)
        if edge.status not in selected:
            continue
        searchable = f"{edge.source_label} {edge.source_id} {edge.target_label} {edge.target_id} {edge.relation}".lower()
        if needle and needle not in searchable:
            continue
        source = add_node(edge.source_type, edge.source_id, edge.source_label)
        target = add_node(edge.target_type, edge.target_id, edge.target_label, incoming=True)
        graph_edges.append(NewsImpactGraphViewEdge(
            id=edge.id, source=source, target=target, relation=edge.relation, direction=edge.direction,
            weight=edge.weight, status=edge.status, evidence=edge.evidence, provenance=edge.provenance,
            created_by=edge.created_by, reviewed_by=edge.reviewed_by,
            created_at=edge.created_at, updated_at=edge.updated_at,
        ))

    for row in proposal_rows:
        proposal = row_to_agent_proposal(row)
        payload = proposal.payload
        source_id = str(payload.get("source_id") or "")
        target_id = str(payload.get("target_id") or "")
        if not source_id or not target_id:
            continue
        searchable = f"{payload.get('source_label', '')} {source_id} {payload.get('target_label', '')} {target_id} {payload.get('relation', '')}".lower()
        if needle and needle not in searchable:
            continue
        source = add_node(str(payload.get("source_type") or "event"), source_id, str(payload.get("source_label") or source_id))
        target = add_node(str(payload.get("target_type") or "market"), target_id, str(payload.get("target_label") or target_id), incoming=True)
        direction = str(payload.get("direction") or "uncertain")
        if direction not in {"positive", "negative", "neutral", "mixed", "uncertain"}:
            direction = "uncertain"
        path = payload.get("path")
        graph_edges.append(NewsImpactGraphViewEdge(
            id=f"proposal-edge:{proposal.id}", source=source, target=target,
            relation=str(payload.get("relation") or "待验证关系"), direction=direction,
            weight=normalized_ratio(payload.get("weight")), status=proposal.status,
            evidence=str(payload.get("evidence") or ""), provenance=str(payload.get("provenance") or ""),
            proposal_id=proposal.id, article_id=str(payload.get("article_id") or "") or None,
            confidence=normalized_ratio(payload.get("confidence")) if payload.get("confidence") is not None else None,
            path=[str(value) for value in path] if isinstance(path, list) else [],
            validation_errors=[str(value) for value in payload.get("validation_errors", [])] if isinstance(payload.get("validation_errors"), list) else [],
            normalization_warnings=[str(value) for value in payload.get("normalization_warnings", [])] if isinstance(payload.get("normalization_warnings"), list) else [],
            reviewed_by=proposal.reviewed_by, created_at=proposal.created_at,
            updated_at=proposal.reviewed_at or proposal.created_at,
        ))
    return NewsImpactGraphView(
        nodes=[NewsImpactGraphNode(**value) for value in node_values.values()], edges=graph_edges, counts=counts,
        total_nodes=len(node_values), total_edges=len(graph_edges), truncated=False,
    )


IMPACT_DIAGNOSTIC_REASONS = {
    "confidence": "置信度不足",
    "path": "影响路径不完整",
    "target": "目标不可识别",
    "evidence": "关系证据不足",
    "schema": "结构校验失败",
    "annotation": "事件标注缺失",
    "other": "其他问题",
}


def impact_diagnostic_reason_key(message: str) -> str:
    value = message.lower()
    if "置信度" in value or "confidence" in value:
        return "confidence"
    if "路径" in value or "path" in value or "节点" in value:
        return "path"
    if "目标" in value or "标的" in value or "target" in value or "tradable" in value:
        return "target"
    if "证据" in value or "evidence" in value:
        return "evidence"
    if "结构" in value or "schema" in value or "字段" in value:
        return "schema"
    if "标注" in value or "annotation" in value:
        return "annotation"
    return "other"


def list_news_impact_diagnostics(
    reason: str = "", query: str = "", limit: int = 50, offset: int = 0,
) -> NewsImpactDiagnosticPage:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """SELECT * FROM agent_proposals WHERE resource_type='impact-graph' AND status='failed'
               ORDER BY created_at DESC"""
        ).fetchall()

    facets = {key: 0 for key in IMPACT_DIAGNOSTIC_REASONS}
    matched: list[NewsImpactDiagnosticItem] = []
    needle = query.strip().lower()
    for row in rows:
        proposal = row_to_agent_proposal(row)
        raw_errors = proposal.payload.get("validation_errors")
        errors = [str(value) for value in raw_errors] if isinstance(raw_errors, list) else []
        if not errors and proposal.review_note:
            errors = [proposal.review_note]
        reason_keys = list(dict.fromkeys(impact_diagnostic_reason_key(value) for value in errors)) or ["other"]
        for key in reason_keys:
            facets[key] += 1
        searchable = " ".join([
            proposal.title, proposal.review_note or "", json.dumps(proposal.payload, ensure_ascii=False),
        ]).lower()
        if reason and reason not in reason_keys:
            continue
        if needle and needle not in searchable:
            continue
        matched.append(NewsImpactDiagnosticItem(
            id=proposal.id, run_id=proposal.run_id, title=proposal.title, payload=proposal.payload,
            validation_errors=errors, reason_keys=reason_keys, review_note=proposal.review_note,
            created_at=proposal.created_at,
        ))
    page = matched[offset:offset + limit]
    return NewsImpactDiagnosticPage(
        items=page,
        facets=[NewsImpactDiagnosticFacet(key=key, label=label, count=facets[key])
                for key, label in IMPACT_DIAGNOSTIC_REASONS.items() if facets[key]],
        total=len(matched), limit=limit, offset=offset,
    )


def create_news_impact_graph_edge(payload: NewsImpactGraphEdgeCreate, actor: AuthUser) -> NewsImpactGraphEdge:
    now = datetime.now(timezone.utc).isoformat()
    edge_id = f"impact-edge-{sha256(f'{payload.source_type}:{payload.source_id}:{payload.target_type}:{payload.target_id}:{payload.relation}'.encode()).hexdigest()[:16]}"
    target_id, target_label = payload.target_id.strip(), payload.target_label.strip()
    if payload.target_type in {"instrument", "index", "etf"}:
        instrument = normalize_tag_symbol(target_id)
        if not instrument:
            raise HTTPException(status_code=422, detail="影响关系目标不是平台支持的标的")
        target_id, target_label = instrument["instrument_id"], instrument["name"]
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO news_impact_graph_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,NULL,?,?)
               ON CONFLICT(id) DO UPDATE SET source_label=excluded.source_label, target_label=excluded.target_label,
               direction=excluded.direction, weight=excluded.weight, evidence=excluded.evidence,
               provenance=excluded.provenance, status='pending', reviewed_by=NULL, updated_at=excluded.updated_at""",
            (edge_id, payload.source_type, payload.source_id.lower(), payload.source_label.strip(), payload.target_type,
             target_id, target_label, payload.relation, payload.direction, payload.weight, payload.evidence.strip(),
             payload.provenance.strip(), actor.username, now, now),
        )
        row = connection.execute("SELECT * FROM news_impact_graph_edges WHERE id=?", (edge_id,)).fetchone()
    return row_to_impact_graph_edge(row)


def review_news_impact_graph_edge(edge_id: str, payload: NewsImpactGraphEdgeReview, actor: AuthUser) -> NewsImpactGraphEdge:
    with db_connect() as connection:
        updated = connection.execute(
            "UPDATE news_impact_graph_edges SET status=?, reviewed_by=?, updated_at=? WHERE id=?",
            (payload.status, actor.username, datetime.now(timezone.utc).isoformat(), edge_id),
        )
        if not updated.rowcount:
            raise HTTPException(status_code=404, detail="影响关系不存在")
        row = connection.execute("SELECT * FROM news_impact_graph_edges WHERE id=?", (edge_id,)).fetchone()
    return row_to_impact_graph_edge(row)


def rule_news_event_annotation(row: sqlite3.Row, model_payload: dict[str, object] | None = None,
                               model: str | None = None) -> NewsEventAnnotation:
    payload = model_payload or {}
    text = f"{row['title']} {row['summary']}".lower()
    rules = [
        ("geopolitical", ["war", "conflict", "sanction", "iran", "israel", "战争", "冲突", "制裁", "伊朗", "以色列"]),
        ("monetary_policy", ["rate hike", "rate cut", "interest rate", "federal reserve", "加息", "降息", "利率", "美联储"]),
        ("supply_chain", ["supply chain", "shortage", "capacity expansion", "data center", "供应链", "短缺", "扩产", "数据中心"]),
        ("macro_data", ["inflation", "cpi", "gdp", "payroll", "通胀", "消费者价格", "国内生产总值", "非农"]),
        ("commodity", ["oil", "crude", "gold", "copper", "原油", "黄金", "铜价"]),
        ("regulatory", ["regulator", "sec ", "antitrust", "监管", "反垄断"]),
        ("earnings", ["earnings", "revenue", "profit", "财报", "营收", "利润"]),
        ("product", ["launch", "product", "platform", "发布", "产品", "平台"]),
        ("flow", ["fund flow", "inflow", "outflow", "资金流", "流入", "流出"]),
    ]
    taxonomy = str(payload.get("taxonomy") or "")
    if taxonomy not in {item[0] for item in rules} | {"corporate_action", "other"}:
        taxonomy = next((name for name, words in rules if any(word in text for word in words)), "other")
    topic_rules = {
        "middle_east_tension": ["iran", "israel", "middle east", "伊朗", "以色列", "中东"],
        "interest_rate_expectations": ["rate hike", "rate cut", "interest rate", "加息", "降息", "利率"],
        "data_center_expansion": ["data center", "datacenter", "数据中心"],
        "ai_compute_demand": ["ai infrastructure", "gpu", "accelerator", "ai 基础设施", "算力", "加速卡"],
        "crude_oil": ["oil", "crude", "原油", "油价"],
        "inflation": ["inflation", "cpi", "通胀", "消费者价格"],
    }
    topics = [key for key, words in topic_rules.items() if any(word in text for word in words)]
    topics = [str(item).strip().lower() for item in payload.get("topics", topics) if str(item).strip()][:12]
    factors = list(payload.get("macro_factors") or [])
    if "interest_rate_expectations" in topics and "discount_rate" not in factors:
        factors.append("discount_rate")
    if "middle_east_tension" in topics and "geopolitical_risk" not in factors:
        factors.append("geopolitical_risk")
    if "data_center_expansion" in topics and "ai_compute_demand" not in factors:
        factors.append("ai_compute_demand")
    countries = list(payload.get("countries") or [])
    if any(word in text for word in ["iran", "伊朗"]) and "IR" not in countries:
        countries.append("IR")
    if any(word in text for word in ["united states", "u.s.", "美国"]) and "US" not in countries:
        countries.append("US")
    scope = str(payload.get("scope") or "")
    if scope not in {"company", "sector", "market", "macro", "global"}:
        scope = "global" if taxonomy == "geopolitical" else "macro" if taxonomy in {"monetary_policy", "macro_data", "commodity"} else "sector" if taxonomy == "supply_chain" else "company"
    horizon = str(payload.get("horizon") or "short")
    if horizon not in {"intraday", "short", "medium", "long"}:
        horizon = "short"
    actors = [str(item).strip() for item in payload.get("actors", []) if str(item).strip()][:12]
    regions = [str(item).strip() for item in payload.get("regions", []) if str(item).strip()][:8]
    if taxonomy == "geopolitical" and not regions:
        regions = ["Middle East"] if "middle_east_tension" in topics else []
    cluster_basis = json.dumps([taxonomy, sorted(topics), sorted(countries), sorted(actors)], ensure_ascii=False)
    cluster_id = f"event-{sha256(cluster_basis.encode()).hexdigest()[:16]}"
    with db_connect() as connection:
        prior = connection.execute(
            "SELECT COUNT(*) total FROM news_event_annotations WHERE event_cluster_id=? AND article_id!=?",
            (cluster_id, row["id"]),
        ).fetchone()["total"]
    now = datetime.now(timezone.utc).isoformat()
    return NewsEventAnnotation(
        article_id=row["id"], taxonomy=taxonomy, scope=scope,
        summary=str(payload.get("summary") or row["summary"] or row["title"])[:1000], topics=topics,
        countries=countries[:8], regions=regions, actors=actors, macro_factors=[str(item).lower() for item in factors][:10],
        horizon=horizon, significance=normalized_score(payload.get("significance"), int(row["impact_score"])),
        novelty=max(10, 100 - int(prior) * 25),
        content_completeness=normalized_ratio(payload.get("content_completeness"), 0.65 if row["summary"] else 0.3),
        event_cluster_id=str(payload.get("event_cluster_id") or cluster_id), origin="ai" if model else "rule",
        model=model, pipeline_version=2, created_at=now, updated_at=now,
    )


def save_news_event_annotation(annotation: NewsEventAnnotation, connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO news_event_annotations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(article_id) DO UPDATE SET taxonomy=excluded.taxonomy, scope=excluded.scope,
           summary=excluded.summary, topics=excluded.topics, countries=excluded.countries, regions=excluded.regions,
           actors=excluded.actors, macro_factors=excluded.macro_factors, horizon=excluded.horizon,
           significance=excluded.significance, novelty=excluded.novelty, content_completeness=excluded.content_completeness,
           event_cluster_id=excluded.event_cluster_id, origin=excluded.origin, model=excluded.model,
           pipeline_version=excluded.pipeline_version, updated_at=excluded.updated_at""",
        (annotation.article_id, annotation.taxonomy, annotation.scope, annotation.summary,
         json.dumps(annotation.topics, ensure_ascii=False), json.dumps(annotation.countries, ensure_ascii=False),
         json.dumps(annotation.regions, ensure_ascii=False), json.dumps(annotation.actors, ensure_ascii=False),
         json.dumps(annotation.macro_factors, ensure_ascii=False), annotation.horizon, annotation.significance,
         annotation.novelty, annotation.content_completeness, annotation.event_cluster_id, annotation.origin,
         annotation.model, annotation.pipeline_version, annotation.created_at, annotation.updated_at),
    )


def _validated_impact_status(link_type: str, confidence: float, relevance: float, impact_score: int,
                             approved_path: bool) -> str:
    if link_type in {"direct", "legacy_unspecified"} and confidence >= 0.85:
        return "accepted"
    if link_type == "derived" and approved_path and confidence >= 0.90 and relevance >= 0.75 and impact_score >= 70:
        return "accepted"
    return "pending" if confidence >= 0.65 else "rejected"


def record_news_pipeline_stage(agent_id: str, article_id: str, parent_run_id: str,
                               stage_input: dict[str, object], stage_output: dict[str, object], model: str | None) -> None:
    agent = get_agent(agent_id)
    now = datetime.now(timezone.utc).isoformat()
    run_id = f"agent-run-{sha256(f'{parent_run_id}:{agent_id}'.encode()).hexdigest()[:16]}"
    snapshot = {
        "version": 2, "captured_at": now, "pipeline_parent_run_id": parent_run_id,
        "agent_config": {"id": agent.id, "template_type": agent.template_type, "settings": agent.settings},
        "stage_input": sanitize_agent_input(stage_input),
        "execution": {"type": "deterministic_pipeline", "model_attempted": False},
    }
    with db_connect() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO agent_runs
               (id, agent_id, agent_name, trigger, symbol, status, runtime_mode, input_payload, input_snapshot,
                output, error, model, prompt_tokens, completion_tokens, latency_ms, test, created_at, started_at, finished_at)
               VALUES (?,?,?,'pipeline',NULL,'succeeded','rule',?,?,?,NULL,NULL,0,0,0,0,?,?,?)""",
            (run_id, agent.id, agent.name,
             json.dumps({"article_id": article_id, "parent_run_id": parent_run_id}, ensure_ascii=False),
             json.dumps(snapshot, ensure_ascii=False), json.dumps(stage_output, ensure_ascii=False), now, now, now),
        )


def persist_news_annotation_pipeline(article_id: str, output: dict[str, object] | None = None,
                                     run_id: str | None = None, model: str | None = None) -> tuple[NewsEventAnnotation, list[NewsImpactLink]]:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_articles WHERE id=?", (article_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="News event not found")
    output = output or {}
    model_annotation = output.get("event_annotation") if isinstance(output.get("event_annotation"), dict) else None
    annotation = get_news_event_annotation(article_id) if model_annotation is None else None
    annotation = annotation or rule_news_event_annotation(row, model_annotation, model)
    direct_tags = list_news_symbol_tags(article_id)
    candidates: list[dict[str, object]] = []
    for tag in direct_tags:
        candidates.append({
            "target_type": "instrument", "target_id": tag.instrument_id, "target_label": tag.name,
            "symbol": tag.symbol, "link_type": "direct" if tag.origin != "legacy" else "legacy_unspecified",
            "channel": "entity_mention", "direction": tag.direction, "relevance": tag.confidence,
            "impact_score": max(int(row["impact_score"]), int(tag.confidence * 100)), "confidence": tag.confidence,
            "horizon": annotation.horizon, "path": [tag.name], "evidence": tag.evidence,
            "origin": "ai" if tag.origin == "ai" else "legacy" if tag.origin == "legacy" else "rule",
            "graph_edge_ids": [], "approved_path": True,
        })
    model_impacts = output.get("impacts", [])
    if isinstance(model_impacts, list):
        candidates.extend(item for item in model_impacts[:20] if isinstance(item, dict))
    source_keys = {annotation.taxonomy, *annotation.topics, *annotation.macro_factors, *[item.lower() for item in annotation.countries], *[item.lower() for item in annotation.regions]}
    with db_connect() as connection:
        placeholders = ",".join("?" for _ in source_keys)
        edge_rows = connection.execute(
            f"SELECT * FROM news_impact_graph_edges WHERE status='approved' AND lower(source_id) IN ({placeholders})",
            list(source_keys),
        ).fetchall() if source_keys else []
        approved_edge_ids = {row["id"] for row in connection.execute(
            "SELECT id FROM news_impact_graph_edges WHERE status='approved'"
        ).fetchall()}
    for edge_row in edge_rows:
        edge = row_to_impact_graph_edge(edge_row)
        instrument = normalize_tag_symbol(edge.target_id) if edge.target_type in {"instrument", "index", "etf"} else None
        candidates.append({
            "target_type": edge.target_type, "target_id": edge.target_id, "target_label": edge.target_label,
            "symbol": instrument["symbol"] if instrument else "", "link_type": "derived", "channel": edge.relation,
            "direction": edge.direction, "relevance": edge.weight,
            "impact_score": int(round(annotation.significance * edge.weight)), "confidence": min(0.98, edge.weight + 0.10),
            "horizon": annotation.horizon, "path": [edge.source_label, edge.relation, edge.target_label],
            "evidence": edge.evidence, "origin": "ai" if model else "rule", "graph_edge_ids": [edge.id],
            "approved_path": True,
        })
    now = datetime.now(timezone.utc).isoformat()
    seen: set[tuple[str, str]] = set()
    saved: list[NewsImpactLink] = []
    with db_connect() as connection:
        save_news_event_annotation(annotation, connection)
        connection.execute(
            "UPDATE news_impact_links SET status='superseded', updated_at=? WHERE article_id=? AND origin IN ('rule','ai') AND status IN ('accepted','pending')",
            (now, article_id),
        )
        for item in candidates:
            target_type = str(item.get("target_type", "instrument"))
            target_id = str(item.get("target_id") or item.get("symbol") or "").strip()
            link_type = str(item.get("link_type", "derived"))
            if target_type not in {"instrument", "sector", "industry", "index", "etf", "asset_class", "commodity", "currency", "country", "market"} or link_type not in {"direct", "derived", "legacy_unspecified"}:
                continue
            instrument = normalize_tag_symbol(target_id) if target_type in {"instrument", "index", "etf"} else None
            if target_type in {"instrument", "index", "etf"} and not instrument:
                continue
            target_id = instrument["instrument_id"] if instrument else target_id.lower()
            key = (target_type, target_id)
            if key in seen or len(saved) >= 20:
                continue
            seen.add(key)
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
            relevance = max(0.0, min(1.0, float(item.get("relevance", confidence))))
            impact_score = max(0, min(100, int(item.get("impact_score", annotation.significance))))
            graph_edge_ids = [str(edge_id) for edge_id in item.get("graph_edge_ids", [])]
            path = [str(step) for step in item.get("path", []) if str(step).strip()]
            approved_path = bool(graph_edge_ids) and set(graph_edge_ids).issubset(approved_edge_ids) and len(path) >= 3
            status = _validated_impact_status(link_type, confidence, relevance, impact_score, approved_path)
            direction = str(item.get("direction", "uncertain"))
            if direction not in {"positive", "negative", "neutral", "mixed", "uncertain"}:
                direction = "uncertain"
            impact_key = f"{article_id}:{target_type}:{target_id}:{link_type}:{run_id or 'rule'}"
            impact_id = f"impact-{sha256(impact_key.encode()).hexdigest()[:18]}"
            connection.execute(
                """INSERT OR REPLACE INTO news_impact_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (impact_id, article_id, target_type, target_id, instrument["name"] if instrument else str(item.get("target_label") or target_id),
                 instrument["symbol"] if instrument else str(item.get("symbol", "")), link_type, str(item.get("channel", "unspecified"))[:80],
                 direction, relevance, impact_score, confidence, str(item.get("horizon", annotation.horizon)),
                 json.dumps(path, ensure_ascii=False), str(item.get("evidence", ""))[:1000],
                 str(item.get("origin", "ai" if model else "rule")), status,
                 json.dumps(graph_edge_ids), run_id, model, 2, now, now),
            )
            saved_row = connection.execute("SELECT * FROM news_impact_links WHERE id=?", (impact_id,)).fetchone()
            saved.append(row_to_news_impact_link(saved_row))
        materialize_news_primary_symbols(article_id, connection)
    if run_id:
        record_news_pipeline_stage("news_event_extractor", article_id, run_id,
                                   {"title": row["title"], "summary": row["summary"]}, annotation.model_dump(mode="json"), model)
        record_news_pipeline_stage("news_impact_mapper", article_id, run_id,
                                   {"annotation": annotation.model_dump(mode="json"), "candidate_count": len(candidates)},
                                   {"impacts": [item.model_dump(mode="json") for item in saved]}, model)
        record_news_pipeline_stage("news_impact_validator", article_id, run_id,
                                   {"impacts": [item.model_dump(mode="json") for item in saved]},
                                   {"accepted": sum(item.status == "accepted" for item in saved),
                                    "pending": sum(item.status == "pending" for item in saved),
                                    "rejected": sum(item.status == "rejected" for item in saved)}, model)
    return annotation, saved


def update_news_impact_override(article_id: str, payload: NewsImpactOverrideUpdate, actor: AuthUser) -> NewsDetail:
    init_db()
    with db_connect() as connection:
        article = connection.execute("SELECT * FROM news_articles WHERE id=?", (article_id,)).fetchone()
    if not article:
        raise HTTPException(status_code=404, detail="News event not found")
    annotation = get_news_event_annotation(article_id) or rule_news_event_annotation(article)
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        save_news_event_annotation(annotation, connection)
        connection.execute("UPDATE news_impact_links SET status='superseded', updated_at=? WHERE article_id=? AND status IN ('accepted','pending')", (now, article_id))
        for position, item in enumerate(payload.impacts):
            target_type = str(item.get("target_type", "instrument"))
            target_id = str(item.get("target_id") or item.get("symbol") or "").strip()
            if target_type not in {"instrument", "sector", "industry", "index", "etf", "asset_class", "commodity", "currency", "country", "market"} or not target_id:
                raise HTTPException(status_code=422, detail=f"第 {position + 1} 条影响目标无效")
            instrument = normalize_tag_symbol(target_id) if target_type in {"instrument", "index", "etf"} else None
            if target_type in {"instrument", "index", "etf"} and not instrument:
                raise HTTPException(status_code=422, detail=f"平台不支持目标 {target_id}")
            resolved_id = instrument["instrument_id"] if instrument else target_id.lower()
            direction = str(item.get("direction", "uncertain"))
            if direction not in {"positive", "negative", "neutral", "mixed", "uncertain"}:
                raise HTTPException(status_code=422, detail=f"第 {position + 1} 条影响方向无效")
            impact_id = f"impact-manual-{sha256(f'{article_id}:{target_type}:{resolved_id}:{position}'.encode()).hexdigest()[:16]}"
            connection.execute(
                "INSERT OR REPLACE INTO news_impact_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (impact_id, article_id, target_type, resolved_id, instrument["name"] if instrument else str(item.get("target_label") or target_id),
                 instrument["symbol"] if instrument else "", str(item.get("link_type", "derived")),
                 str(item.get("channel", "manual_review"))[:80], direction,
                 max(0.0, min(1.0, float(item.get("relevance", 1)))), max(0, min(100, int(item.get("impact_score", annotation.significance)))),
                 max(0.0, min(1.0, float(item.get("confidence", 1)))), str(item.get("horizon", annotation.horizon)),
                 json.dumps(item.get("path", []), ensure_ascii=False), str(item.get("evidence", payload.note))[:1000],
                 "manual", "accepted", json.dumps(item.get("graph_edge_ids", [])), None, None, 2, now, now),
            )
        connection.execute(
            """INSERT INTO news_article_symbol_overrides VALUES (?,1,?,?,?,?)
               ON CONFLICT(article_id) DO UPDATE SET locked=1,note=excluded.note,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
            (article_id, payload.note.strip(), actor.username, now, now),
        )
        materialize_news_primary_symbols(article_id, connection)
    return get_news_detail(article_id)


def list_news_symbol_tags(article_id: str, include_rejected: bool = False) -> list[NewsSymbolTag]:
    init_db()
    query = "SELECT * FROM news_article_symbol_tags WHERE article_id = ?"
    params: list[object] = [article_id]
    if not include_rejected:
        query += " AND status IN ('accepted', 'pending')"
    query += " ORDER BY CASE relation WHEN 'primary' THEN 0 ELSE 1 END, confidence DESC, symbol"
    with db_connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [row_to_news_symbol_tag(row) for row in rows]


def materialize_news_primary_symbols(article_id: str, connection: sqlite3.Connection | None = None) -> list[str]:
    owns_connection = connection is None
    target = connection or db_connect()
    rows = target.execute(
        """SELECT DISTINCT symbol FROM news_article_symbol_tags
           WHERE article_id = ? AND relation = 'primary' AND status = 'accepted' ORDER BY symbol""",
        (article_id,),
    ).fetchall()
    symbols = [row["symbol"] for row in rows]
    impact_rows = target.execute(
        """SELECT DISTINCT symbol FROM news_impact_links
           WHERE article_id=? AND target_type='instrument' AND status='accepted' AND symbol != ''
             AND (link_type='direct' OR (link_type='derived' AND confidence>=0.90 AND relevance>=0.75 AND impact_score>=70))""",
        (article_id,),
    ).fetchall()
    symbols = sorted(set(symbols) | {row["symbol"] for row in impact_rows})
    target.execute("UPDATE news_articles SET symbols = ? WHERE id = ?", (",".join(symbols), article_id))
    if owns_connection:
        target.commit()
        target.close()
    return symbols


def news_symbol_override(article_id: str) -> sqlite3.Row | None:
    with db_connect() as connection:
        return connection.execute(
            "SELECT * FROM news_article_symbol_overrides WHERE article_id=? AND locked=1", (article_id,),
        ).fetchone()


def insert_news_symbol_tag(
    connection: sqlite3.Connection,
    article_id: str,
    symbol: str,
    relation: str,
    confidence: float,
    direction: str,
    origin: str,
    status: str,
    evidence: str,
    agent_run_id: str | None = None,
    model: str | None = None,
    tagging_version: int = 1,
) -> NewsSymbolTag | None:
    instrument = normalize_tag_symbol(symbol)
    if not instrument:
        return None
    now = datetime.now(timezone.utc).isoformat()
    identity = f"{article_id}:{instrument['symbol']}:{relation}:{tagging_version}:{origin}"
    tag_id = f"news-tag-{sha256(identity.encode()).hexdigest()[:18]}"
    connection.execute(
        """INSERT INTO news_article_symbol_tags
           (id, article_id, instrument_id, symbol, name, relation, confidence, direction, origin, status,
            evidence, agent_run_id, model, tagging_version, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence, direction=excluded.direction,
             status=excluded.status, evidence=excluded.evidence, agent_run_id=excluded.agent_run_id,
             model=excluded.model, updated_at=excluded.updated_at""",
        (tag_id, article_id, instrument["instrument_id"], instrument["symbol"], instrument["name"], relation,
         max(0.0, min(1.0, float(confidence))), direction, origin, status, evidence[:500], agent_run_id, model,
         tagging_version, now, now),
    )
    row = connection.execute("SELECT * FROM news_article_symbol_tags WHERE id = ?", (tag_id,)).fetchone()
    return row_to_news_symbol_tag(row) if row else None


def update_news_symbol_override(
    article_id: str,
    payload: NewsSymbolOverrideUpdate,
    actor: AuthUser,
) -> NewsDetail:
    primary = list(dict.fromkeys(payload.primary_instrument_ids))
    secondary = list(dict.fromkeys(payload.secondary_instrument_ids))
    overlap = set(primary) & set(secondary)
    if overlap:
        raise HTTPException(status_code=422, detail="A symbol cannot be both primary and secondary")
    selected: list[tuple[dict[str, str], str]] = []
    for value, relation in [*((item, "primary") for item in primary), *((item, "secondary") for item in secondary)]:
        instrument = normalize_tag_symbol(value)
        if not instrument:
            raise HTTPException(status_code=422, detail=f"Unsupported instrument: {value}")
        selected.append((instrument, relation))
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        article = connection.execute("SELECT id FROM news_articles WHERE id=? AND enabled=1", (article_id,)).fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="News event not found")
        connection.execute(
            """UPDATE news_article_symbol_tags SET status='superseded', updated_at=?
               WHERE article_id=? AND status IN ('accepted','pending')""",
            (now, article_id),
        )
        for instrument, relation in selected:
            insert_news_symbol_tag(
                connection, article_id, instrument["instrument_id"], relation, 1.0, "neutral", "manual",
                "accepted", "管理员人工关联",
            )
        connection.execute(
            """INSERT INTO news_article_symbol_overrides
               (article_id, locked, note, updated_by, created_at, updated_at)
               VALUES (?, 1, ?, ?, ?, ?)
               ON CONFLICT(article_id) DO UPDATE SET locked=1, note=excluded.note,
                 updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (article_id, payload.note, actor.username, now, now),
        )
        materialize_news_primary_symbols(article_id, connection)
        connection.execute(
            """INSERT INTO news_tagging_jobs
               (article_id, status, trigger, attempts, run_id, error, tagging_version, created_at, updated_at)
               VALUES (?, 'succeeded', 'manual', 0, NULL, NULL, 1, ?, ?)
               ON CONFLICT(article_id) DO UPDATE SET status='succeeded', trigger='manual', error=NULL, updated_at=excluded.updated_at""",
            (article_id, now, now),
        )
    return get_news_detail(article_id)


def clear_news_symbol_override(article_id: str) -> NewsDetail:
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        article = connection.execute("SELECT id FROM news_articles WHERE id=? AND enabled=1", (article_id,)).fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="News event not found")
        connection.execute("DELETE FROM news_article_symbol_overrides WHERE article_id=?", (article_id,))
        connection.execute(
            "UPDATE news_article_symbol_tags SET status='superseded', updated_at=? WHERE article_id=? AND origin='manual' AND status IN ('accepted','pending')",
            (now, article_id),
        )
        materialize_news_primary_symbols(article_id, connection)
    enqueue_news_tagging([article_id], trigger="manual", force=True)
    return get_news_detail(article_id)


def record_initial_news_tags(
    article_id: str,
    symbols: list[str],
    upstream_symbols: set[str],
    connection: sqlite3.Connection | None = None,
) -> None:
    if not symbols:
        return
    owns_connection = connection is None
    target = connection or db_connect()
    try:
        for symbol in symbols[:5]:
            upstream = symbol in upstream_symbols
            insert_news_symbol_tag(
                target, article_id, symbol, "primary", 0.98 if upstream else 0.92, "neutral",
                "upstream" if upstream else "rule", "accepted",
                "上游逐条标的" if upstream else "正文精确代码或名称匹配",
            )
        materialize_news_primary_symbols(article_id, target)
        if owns_connection:
            target.commit()
    finally:
        if owns_connection:
            target.close()


def enqueue_news_tagging(article_ids: list[str], trigger: str = "news_ingested", force: bool = False) -> int:
    if not article_ids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    queued = 0
    with db_connect() as connection:
        for article_id in dict.fromkeys(article_ids):
            exists = connection.execute("SELECT 1 FROM news_articles WHERE id = ?", (article_id,)).fetchone()
            if not exists:
                continue
            locked = connection.execute(
                "SELECT 1 FROM news_article_symbol_overrides WHERE article_id=? AND locked=1", (article_id,),
            ).fetchone()
            if locked:
                continue
            current = connection.execute("SELECT status FROM news_tagging_jobs WHERE article_id = ?", (article_id,)).fetchone()
            if current and not force and current["status"] in {"queued", "running", "waiting_provider", "review", "succeeded"}:
                continue
            connection.execute(
                """INSERT INTO news_tagging_jobs
                   (article_id, status, trigger, attempts, run_id, error, tagging_version, created_at, updated_at)
                   VALUES (?, 'queued', ?, 0, NULL, NULL, 1, ?, ?)
                   ON CONFLICT(article_id) DO UPDATE SET status='queued', trigger=excluded.trigger,
                     attempts=CASE WHEN ? THEN 0 ELSE news_tagging_jobs.attempts END,
                     run_id=NULL, error=NULL, updated_at=excluded.updated_at""",
                (article_id, trigger, now, now, int(force)),
            )
            queued += 1
    return queued


def news_tagging_status() -> NewsTaggingStatus:
    init_db()
    agent = get_agent("news_tagger")
    provider_ready = False
    if agent.model_route_id:
        try:
            provider_ready = bool(ordered_route_targets(get_model_route(agent.model_route_id, require_enabled=True)))
        except (HTTPException, RuntimeError):
            provider_ready = False
    elif agent.provider_id:
        try:
            provider = get_agent_provider(agent.provider_id)
            provider_ready = provider.enabled and provider.secret_configured
        except HTTPException:
            provider_ready = False
    with db_connect() as connection:
        jobs = {row["status"]: row["total"] for row in connection.execute(
            "SELECT status, COUNT(*) total FROM news_tagging_jobs GROUP BY status"
        ).fetchall()}
        total_articles = int(connection.execute("SELECT COUNT(*) total FROM news_articles WHERE enabled=1").fetchone()["total"])
        tags = {row["status"]: row["total"] for row in connection.execute(
            "SELECT status, COUNT(*) total FROM news_article_symbol_tags GROUP BY status"
        ).fetchall()}
        impact_counts = {(row["status"], row["link_type"]): row["total"] for row in connection.execute(
            "SELECT status, link_type, COUNT(*) total FROM news_impact_links GROUP BY status, link_type"
        ).fetchall()}
        annotated_articles = int(connection.execute("SELECT COUNT(*) total FROM news_event_annotations").fetchone()["total"])
    accepted = int(tags.get("accepted", 0))
    pending = int(tags.get("pending", 0))
    return NewsTaggingStatus(
        provider_ready=provider_ready, agent_mode=agent.runtime_mode, total_articles=total_articles,
        queued=int(jobs.get("queued", 0)), running=int(jobs.get("running", 0)),
        waiting_provider=int(jobs.get("waiting_provider", 0)), needs_review=int(jobs.get("review", 0)),
        succeeded=int(jobs.get("succeeded", 0)), failed=int(jobs.get("failed", 0)),
        accepted_tags=accepted, pending_tags=pending,
        auto_accept_rate=round(accepted / (accepted + pending) * 100, 1) if accepted + pending else None,
        annotated_articles=annotated_articles,
        accepted_impacts=sum(int(total) for (status, _kind), total in impact_counts.items() if status == "accepted"),
        pending_impacts=sum(int(total) for (status, _kind), total in impact_counts.items() if status == "pending"),
        accepted_derived_impacts=int(impact_counts.get(("accepted", "derived"), 0)),
        pipeline_agents={stage: get_agent(stage).runtime_mode for stage in ("news_event_extractor", "news_impact_mapper", "news_impact_validator")},
    )


def queue_news_tagging_backfill(
    days: int = 30,
    batch_size: int = 8,
    scope: str = "recent",
    engine: str = "agent",
) -> NewsTaggingBackfillResult:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, min(90, days)))).isoformat()
    with db_connect() as connection:
        if scope == "unlinked":
            rows = connection.execute(
                """SELECT a.id FROM news_articles a
                   LEFT JOIN news_article_symbol_overrides o ON o.article_id=a.id AND o.locked=1
                   WHERE a.enabled=1 AND trim(a.symbols)='' AND o.article_id IS NULL
                   ORDER BY a.published_at DESC"""
            ).fetchall()
        elif scope == "impact_priority":
            rows = connection.execute(
                """SELECT a.id FROM news_articles a
                   LEFT JOIN news_article_symbol_overrides o ON o.article_id=a.id AND o.locked=1
                   LEFT JOIN news_sources s ON s.id=a.source_id
                   WHERE a.enabled=1 AND a.published_at >= ? AND a.impact_score >= 60
                     AND o.article_id IS NULL
                     AND (coalesce(s.category, 'news') != 'filing' OR trim(a.symbols) != '')
                   ORDER BY a.impact_score DESC, a.published_at DESC""",
                (cutoff,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT a.id FROM news_articles a
                   LEFT JOIN news_article_symbol_overrides o ON o.article_id=a.id AND o.locked=1
                   WHERE a.enabled=1 AND a.published_at >= ? AND o.article_id IS NULL ORDER BY a.published_at DESC""",
                (cutoff,),
            ).fetchall()
    ids = [row["id"] for row in rows]
    trigger = "rule_backfill" if engine == "rule" else "backfill"
    queued = enqueue_news_tagging(ids, trigger=trigger, force=True)
    return NewsTaggingBackfillResult(days=days, matched=len(ids), queued=queued, skipped=len(ids) - queued)


def source_articles_to_events(symbol: str, limit: int) -> list[NewsEvent]:
    return source_articles_to_events_filtered(symbol, limit)


def stored_article_symbols(value: str | None) -> list[str]:
    return sorted({item.strip().upper() for item in (value or "").split(",") if item.strip()})


def equivalent_news_symbols(symbol: str) -> list[str]:
    normalized = symbol.strip().upper()
    groups = ({"BTCUSD", "BTCUSDT"}, {"ETHUSD", "ETHUSDT"})
    return sorted(next((group for group in groups if normalized in group), {normalized}))


def news_association_metadata(
    connection: sqlite3.Connection,
    article_ids: list[str],
) -> tuple[dict[str, list[NewsSymbolTag]], dict[str, str]]:
    if not article_ids:
        return {}, {}
    placeholders = ",".join("?" for _ in article_ids)
    tag_rows = connection.execute(
        f"""SELECT * FROM news_article_symbol_tags
            WHERE article_id IN ({placeholders}) AND status IN ('accepted', 'pending')
            ORDER BY article_id, CASE relation WHEN 'primary' THEN 0 ELSE 1 END, confidence DESC, symbol""",
        article_ids,
    ).fetchall()
    status_rows = connection.execute(
        f"SELECT article_id, status FROM news_tagging_jobs WHERE article_id IN ({placeholders})",
        article_ids,
    ).fetchall()
    tags: dict[str, list[NewsSymbolTag]] = {}
    for row in tag_rows:
        tags.setdefault(str(row["article_id"]), []).append(row_to_news_symbol_tag(row))
    return tags, {str(row["article_id"]): str(row["status"]) for row in status_rows}


def news_article_row_to_event(
    row: sqlite3.Row,
    symbol: str | None = None,
    association_tags: list[NewsSymbolTag] | None = None,
    association_status: str = "unprocessed",
) -> NewsEvent:
    symbols = stored_article_symbols(row["symbols"])
    manual_association = bool(row["manual_association"]) if "manual_association" in row.keys() else False
    return NewsEvent(
        id=row["id"],
        source_id=row["source_id"],
        symbol=symbol or (symbols[0] if symbols else "MARKET"),
        symbols=symbols,
        title=row["title"],
        summary=row["summary"],
        source=row["source_name"],
        url=row["url"],
        published_at=row["published_at"],
        event_type=row["event_type"],
        sentiment=row["sentiment"],
        impact_score=row["impact_score"],
        price_reaction="待观察",
        manual_association=manual_association,
        association_tags=association_tags or [],
        association_status="manual" if manual_association else association_status,
    )


def news_feed_page(
    source_ids: list[int] | None = None,
    symbol: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> NewsFeedPage:
    init_db()
    selected_source_ids = sorted({int(source_id) for source_id in (source_ids or []) if int(source_id) > 0})
    base_symbol = symbol.strip().upper() if symbol else None
    scope_clauses = ["a.enabled = 1"]
    scope_params: list[object] = []
    if base_symbol:
        aliases = equivalent_news_symbols(base_symbol)
        symbol_conditions = " OR ".join("',' || a.symbols || ',' LIKE ?" for _ in aliases)
        scope_clauses.append(f"({symbol_conditions})")
        scope_params.extend(f"%,{item},%" for item in aliases)
    scope_where = " AND ".join(scope_clauses)
    item_clauses = list(scope_clauses)
    item_params = list(scope_params)
    if selected_source_ids:
        item_clauses.append(f"a.source_id IN ({','.join('?' for _ in selected_source_ids)})")
        item_params.extend(selected_source_ids)
    item_where = " AND ".join(item_clauses)
    page_limit = max(1, min(100, limit))
    page_offset = max(0, offset)
    with db_connect() as connection:
        total = int(connection.execute(
            f"SELECT COUNT(*) AS total FROM news_articles a WHERE {item_where}", item_params,
        ).fetchone()["total"])
        rows = connection.execute(
            f"""SELECT a.*, EXISTS(
                    SELECT 1 FROM news_article_symbol_overrides o WHERE o.article_id=a.id AND o.locked=1
                ) AS manual_association FROM news_articles a WHERE {item_where}
                ORDER BY a.published_at DESC, a.id DESC LIMIT ? OFFSET ?""",
            [*item_params, page_limit, page_offset],
        ).fetchall()
        association_tags, association_statuses = news_association_metadata(
            connection, [str(row["id"]) for row in rows],
        )
        facet_rows = connection.execute(
            f"""SELECT s.id AS source_id, s.name, COUNT(a.id) AS total,
                       s.provider, s.adapter
                FROM news_sources s
                LEFT JOIN news_articles a ON a.source_id = s.id AND {scope_where}
                WHERE s.category = 'news' OR s.capabilities LIKE '%"news"%'
                GROUP BY s.id, s.name, s.provider, s.adapter
                ORDER BY total DESC, s.name""",
            scope_params,
        ).fetchall()
    return NewsFeedPage(
        items=[news_article_row_to_event(
            row, base_symbol, association_tags.get(str(row["id"]), []),
            association_statuses.get(str(row["id"]), "unprocessed"),
        ) for row in rows],
        total=total,
        limit=page_limit,
        offset=page_offset,
        source_counts=[
            NewsSourceFacet(
                source_id=row["source_id"], name=row["name"], count=row["total"],
                provider=row["provider"], adapter=row["adapter"],
            )
            for row in facet_rows
        ],
    )


def source_articles_to_events_filtered(
    symbol: str,
    limit: int,
    source_ids: list[int] | None = None,
) -> list[NewsEvent]:
    init_db()
    base_symbol = symbol.upper()
    aliases = equivalent_news_symbols(base_symbol)
    selected_source_ids = sorted({int(source_id) for source_id in (source_ids or []) if int(source_id) > 0})
    source_filter = ""
    params: list[object] = [f"%,{item},%" for item in aliases]
    if selected_source_ids:
        source_filter = f" AND source_id IN ({','.join('?' for _ in selected_source_ids)})"
        params.extend(selected_source_ids)
    params.append(limit)
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT a.*, EXISTS(
                SELECT 1 FROM news_article_symbol_overrides o WHERE o.article_id=a.id AND o.locked=1
            ) AS manual_association FROM news_articles a
            WHERE a.enabled = 1 AND ({' OR '.join("',' || a.symbols || ',' LIKE ?" for _ in aliases)})
            {source_filter}
            ORDER BY published_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        association_tags, association_statuses = news_association_metadata(
            connection, [str(row["id"]) for row in rows],
        )
    return [news_article_row_to_event(
        row, base_symbol, association_tags.get(str(row["id"]), []),
        association_statuses.get(str(row["id"]), "unprocessed"),
    ) for row in rows]


def latest_market_news(limit: int = 10) -> list[NewsEvent]:
    from app.modules.integrations.service import resolve_candidates

    init_db()
    source_ids = [item.legacy_source_id for item in resolve_candidates("GLOBAL", "news") if item.legacy_source_id is not None]
    if not source_ids:
        return []
    placeholders = ",".join("?" for _ in source_ids)
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM news_articles
            WHERE enabled = 1 AND source_id IN ({placeholders})
            ORDER BY published_at DESC, impact_score DESC
            LIMIT ?
            """,
            [*source_ids, limit],
        ).fetchall()
        association_tags, association_statuses = news_association_metadata(
            connection, [str(row["id"]) for row in rows],
        )
    events: list[NewsEvent] = []
    for row in rows:
        events.append(news_article_row_to_event(
            row, association_tags=association_tags.get(str(row["id"]), []),
            association_status=association_statuses.get(str(row["id"]), "unprocessed"),
        ))
    return events


def list_news_events(
    symbol: str,
    limit: int = 7,
    source_ids: list[int] | None = None,
) -> list[NewsEvent]:
    """Return persisted symbol news in chart-compatible chronological order."""

    bounded_limit = max(1, min(200, limit))
    persisted = source_articles_to_events_filtered(
        symbol.upper(), bounded_limit, source_ids or []
    )
    return sorted(persisted, key=lambda item: item.published_at)


def event_to_detail(
    event: NewsEvent,
    related_symbols: list[str] | None = None,
    symbol_tags: list[NewsSymbolTag] | None = None,
    tagging_status: str = "unprocessed",
    manual_override: bool = False,
    override_note: str = "",
    override_updated_by: str | None = None,
    override_updated_at: str | None = None,
    event_annotation: NewsEventAnnotation | None = None,
    impact_links: list[NewsImpactLink] | None = None,
    pipeline_status: dict[str, object] | None = None,
) -> NewsDetail:
    direction = {
        "bullish": "偏正面",
        "bearish": "偏负面",
        "mixed": "多空混合",
        "neutral": "中性",
    }[event.sentiment]
    key_points = [
        f"事件类型：{event.event_type}",
        f"情绪判断：{direction}",
        f"影响分：{event.impact_score}",
        f"价格反应：{event.price_reaction}",
    ]
    classification_summary = f"规则分类：{direction}，影响分 {event.impact_score}。"
    event_payload = event.model_dump() if hasattr(event, "model_dump") else event.dict()
    return NewsDetail(
        **event_payload,
        related_symbols=related_symbols or ([event.symbol] if event.symbol else []),
        classification_summary=classification_summary,
        key_points=key_points,
        detail=event.summary,
        symbol_tags=symbol_tags or [],
        tagging_status=tagging_status,
        manual_override=manual_override,
        override_note=override_note,
        override_updated_by=override_updated_by,
        override_updated_at=override_updated_at,
        event_annotation=event_annotation,
        impact_links=impact_links or [],
        pipeline_status=pipeline_status or {},
    )


def get_news_detail(news_id: str) -> NewsDetail:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_articles WHERE id = ? AND enabled = 1", (news_id,)).fetchone()
        job = connection.execute("SELECT status FROM news_tagging_jobs WHERE article_id = ?", (news_id,)).fetchone()
        override = connection.execute(
            "SELECT * FROM news_article_symbol_overrides WHERE article_id=? AND locked=1", (news_id,),
        ).fetchone()
    if row:
        symbols = stored_article_symbols(row["symbols"])
        primary_symbol = symbols[0] if symbols else ""
        event = NewsEvent(
            id=row["id"],
            source_id=row["source_id"],
            symbol=primary_symbol,
            title=row["title"],
            summary=row["summary"],
            source=row["source_name"],
            url=row["url"],
            published_at=row["published_at"],
            event_type=row["event_type"],
            sentiment=row["sentiment"],
            impact_score=row["impact_score"],
            price_reaction="待观察",
            manual_association=bool(override),
        )
        tags = list_news_symbol_tags(news_id)
        return event_to_detail(
            event, related_symbols=symbols, symbol_tags=tags,
            tagging_status="manual" if override else (job["status"] if job else "unprocessed"),
            manual_override=bool(override), override_note=override["note"] if override else "",
            override_updated_by=override["updated_by"] if override else None,
            override_updated_at=override["updated_at"] if override else None,
            event_annotation=get_news_event_annotation(news_id), impact_links=list_news_impact_links(news_id),
            pipeline_status={"version": 2, "status": "manual" if override else (job["status"] if job else "unprocessed")},
        )

    raise HTTPException(status_code=404, detail="News event not found")


def information_source_headers(source: NewsSource) -> dict[str, str]:
    user_agent = os.getenv("SEC_USER_AGENT", "AppTrading/0.1 contact=admin@example.com")
    headers = {"User-Agent": user_agent if "sec.gov" in source.url else "AppTrading/0.1 Information Source Reader"}
    key = resolve_api_key("information_source", str(source.id), source.api_key_env)
    if key and source.auth_type == "header":
        headers["X-API-Key"] = key
    elif key and source.auth_type == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif key and source.adapter == "cryptocompare":
        headers["Authorization"] = f"Apikey {key}"
    return headers


def information_source_url(source: NewsSource) -> str:
    if source.auth_type != "query":
        return source.url
    key = resolve_api_key("information_source", str(source.id), source.api_key_env)
    if not key:
        raise RuntimeError(f"Missing required environment variable {source.api_key_env}")
    key_name = {"finnhub": "token", "alphavantage": "apikey", "fred": "api_key"}.get(source.adapter, "api_key")
    parsed = urlparse(source.url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[key_name] = key
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_json_document(source: NewsSource) -> object:
    request = UrlRequest(information_source_url(source), headers=information_source_headers(source))
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def timestamp_datetime(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return parse_datetime(str(value) if value else None)


def fetch_api_items(source: NewsSource) -> list[dict[str, object]]:
    payload = fetch_json_document(source)
    items: list[dict[str, object]] = []
    if source.adapter == "finnhub":
        rows = payload if isinstance(payload, list) else []
        for row in rows[:50]:
            if isinstance(row, dict) and row.get("headline") and row.get("url"):
                related = str(row.get("related", "")).split(",")
                items.append({"title": row["headline"], "summary": row.get("summary") or row["headline"],
                              "url": row["url"], "published_at": timestamp_datetime(row.get("datetime")), "symbols": related})
    elif source.adapter == "gdelt":
        rows = payload.get("articles", []) if isinstance(payload, dict) else []
        for row in rows[:50]:
            if isinstance(row, dict) and row.get("title") and row.get("url"):
                items.append({"title": row["title"], "summary": f"{row.get('domain', 'GDELT')} · {row.get('sourcecountry', 'global')}",
                              "url": row["url"], "published_at": parse_datetime(str(row.get("seendate", "")))})
    elif source.adapter == "cryptocompare":
        rows = payload.get("Data", []) if isinstance(payload, dict) else []
        for row in rows[:50]:
            if isinstance(row, dict) and row.get("title") and row.get("url"):
                items.append({"title": row["title"], "summary": strip_markup(str(row.get("body", ""))) or row["title"],
                              "url": row["url"], "published_at": timestamp_datetime(row.get("published_on")),
                              "symbols": ["BTCUSD", "ETHUSD"]})
    elif source.adapter == "alphavantage":
        rows = payload.get("feed", []) if isinstance(payload, dict) else []
        for row in rows[:50]:
            if isinstance(row, dict) and row.get("title") and row.get("url"):
                tickers = [str(item.get("ticker", "")) for item in row.get("ticker_sentiment", []) if isinstance(item, dict)]
                items.append({"title": row["title"], "summary": row.get("summary") or row["title"], "url": row["url"],
                              "published_at": parse_datetime(str(row.get("time_published", ""))), "symbols": tickers})
    elif source.adapter == "fred":
        rows = payload.get("releases", []) if isinstance(payload, dict) else []
        for row in rows[:50]:
            if isinstance(row, dict) and row.get("name"):
                release_id = row.get("id", "")
                items.append({"title": row["name"], "summary": f"FRED economic release: {row['name']}",
                              "url": f"https://fred.stlouisfed.org/releases/{release_id}",
                              "published_at": parse_datetime(str(row.get("realtime_start", ""))), "symbols": ["SPY", "QQQ"]})
    return items


ADANOS_SOURCE_CONFIG = {
    "reddit": ("/reddit/stocks/v1/compare", "Reddit", "mentions", "Mentions"),
    "x": ("/x/stocks/v1/compare", "X.com", "mentions", "Mentions"),
    "news": ("/news/stocks/v1/compare", "News", "mentions", "Mentions"),
    "polymarket": ("/polymarket/stocks/v1/compare", "Polymarket", "trade_count", "Trades"),
}


def adanos_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def adanos_sentiment(bullish_percent: float | None, trend: str) -> Literal["bullish", "bearish", "neutral", "mixed"]:
    if bullish_percent is None:
        return "neutral"
    if bullish_percent >= 60:
        return "mixed" if trend == "falling" else "bullish"
    if bullish_percent <= 40:
        return "mixed" if trend == "rising" else "bearish"
    return "neutral"


def fetch_adanos_items(source: NewsSource) -> list[dict[str, object]]:
    key = resolve_api_key("information_source", str(source.id), source.api_key_env)
    if not key:
        raise RuntimeError(f"Missing required environment variable {source.api_key_env}")
    configured_base = os.getenv("ADANOS_API_BASE_URL", "").strip()
    base_url = (configured_base or source.url).rstrip("/")
    raw_days = source.config.get("days", 7)
    try:
        days = max(1, min(30, int(raw_days)))
    except (TypeError, ValueError):
        days = 7
    default_symbols = list(FinnhubMarketProvider.supported_symbols)
    symbols = (source.symbols or default_symbols)[:20]
    query = urlencode({"tickers": ",".join(symbols), "days": days})
    published_at = datetime.now(timezone.utc).isoformat()
    items: list[dict[str, object]] = []
    errors: list[str] = []

    for source_key, (path, label, metric_field, metric_label) in ADANOS_SOURCE_CONFIG.items():
        endpoint = f"{base_url}{path}?{query}"
        request = UrlRequest(
            endpoint,
            headers={"User-Agent": "AppTrading/0.1 Information Source Reader", "X-API-Key": key},
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404:
                continue
            errors.append(f"{label}: HTTP {exc.code}")
            continue
        except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{label}: {exc}")
            continue

        rows = payload.get("stocks", []) if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("ticker", "")).strip().upper()
            if symbol not in symbols:
                continue
            buzz_score = adanos_number(row.get("buzz_score"))
            metric_value = adanos_number(row.get(metric_field))
            if buzz_score is None or metric_value is None:
                continue
            bullish_percent = adanos_number(row.get("bullish_pct"))
            trend = str(row.get("trend", "")).lower()
            sentiment = adanos_sentiment(bullish_percent, trend)
            company_name = str(row.get("company_name") or symbol)
            bullish_label = f"{bullish_percent:.1f}% bullish" if bullish_percent is not None else "sentiment mix unavailable"
            summary = (
                f"{company_name}: buzz {buzz_score:.1f}, {bullish_label}, "
                f"{metric_label.lower()} {int(round(metric_value))}, trend {trend or 'unknown'}."
            )
            items.append(
                {
                    "title": f"{symbol} {label} sentiment: {bullish_label}",
                    "summary": summary,
                    "url": f"https://adanos.org/?symbol={symbol}&source={source_key}",
                    "published_at": published_at,
                    "symbols": [symbol],
                    "sentiment": sentiment,
                    "impact_score": max(25, min(95, int(round(35 + buzz_score * 0.6)))),
                    "event_type": "flow",
                }
            )
    if not items and errors:
        raise RuntimeError("Adanos requests failed: " + "; ".join(errors))
    return items


class NewsSourceDiagnosticError(RuntimeError):
    def __init__(self, diagnostic: NewsSourceDiagnostic):
        super().__init__(f"{diagnostic.code}: {diagnostic.message}")
        self.diagnostic = diagnostic


def diagnostic_evidence(**values: object) -> NewsSourceEvidence:
    return NewsSourceEvidence(**{key: value for key, value in values.items() if value is not None})


def rss_failure_diagnostic(
    exc: Exception,
    attempts: list[NewsSourceAttempt],
    evidence: NewsSourceEvidence | None = None,
) -> NewsSourceDiagnostic:
    status = exc.code if isinstance(exc, HTTPError) else None
    reason = exc.reason if isinstance(exc, URLError) else exc
    text = str(reason or exc)
    lowered = text.lower()
    if isinstance(exc, HTTPError):
        retryable = status in {408, 429} or bool(status and status >= 500)
        suggestion = "检查源站访问权限和请求频率。" if status in {401, 403, 429} else "稍后重试并检查源站服务状态。"
        return NewsSourceDiagnostic(
            stage="response", category="http", code=f"RSS_HTTP_{status}",
            message=f"RSS 服务器返回 HTTP {status}。", suggestion=suggestion,
            retryable=retryable, attempt_count=len(attempts), attempts=attempts,
            evidence=evidence or NewsSourceEvidence(http_status=status),
        )
    if "handshake" in lowered and ("timed out" in lowered or "timeout" in lowered):
        category, code, message = "tls", "RSS_TLS_TIMEOUT", "RSS 源的 TLS 握手超时。"
        suggestion = "检查源站 TLS 服务、容器网络出口和 CA 证书，稍后重试。"
    elif isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        category, code, message = "timeout", "RSS_REQUEST_TIMEOUT", "连接或读取 RSS 响应超时。"
        suggestion = "检查源站可用性、网络出口和刷新频率，必要时稍后重试。"
    elif isinstance(reason, ssl.SSLError) or "ssl" in lowered or "certificate" in lowered or "handshake" in lowered:
        category, code, message = "tls", "RSS_TLS_FAILURE", "RSS 源的 TLS 握手或证书校验失败。"
        suggestion = "检查源站证书链、系统时间和容器 CA 证书，不要关闭证书校验。"
    elif isinstance(reason, socket.gaierror) or "name or service not known" in lowered or "nodename nor servname" in lowered:
        category, code, message = "dns", "RSS_DNS_FAILURE", "无法解析 RSS 源域名。"
        suggestion = "检查域名拼写、容器 DNS 配置和网络出口。"
    else:
        category, code, message = "connection", "RSS_CONNECTION_FAILURE", "无法连接 RSS 源。"
        suggestion = "检查地址、代理、防火墙和源站可用性。"
    return NewsSourceDiagnostic(
        stage="request", category=category, code=code, message=message, suggestion=suggestion,
        retryable=True, attempt_count=len(attempts), attempts=attempts,
        evidence=evidence or NewsSourceEvidence(),
    )


def fetch_rss_response(source: NewsSource) -> tuple[bytes, NewsSourceEvidence, list[NewsSourceAttempt]]:
    attempts: list[NewsSourceAttempt] = []
    request = UrlRequest(source.url, headers=information_source_headers(source))
    for attempt_number in range(1, 4):
        started = perf_counter()
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read()
                headers = getattr(response, "headers", None)
                content_type = headers.get_content_type() if headers and hasattr(headers, "get_content_type") else None
                response_url = response.geturl() if hasattr(response, "geturl") else str(source.url)
                final_host = urlparse(response_url).hostname
                evidence = diagnostic_evidence(
                    http_status=getattr(response, "status", 200), final_host=final_host,
                    content_type=content_type, content_length=len(body), content_sha256=sha256(body).hexdigest(),
                )
            attempts.append(NewsSourceAttempt(
                attempt=attempt_number, duration_ms=int((perf_counter() - started) * 1000), outcome="succeeded",
            ))
            return body, evidence, attempts
        except Exception as exc:  # noqa: BLE001 - normalize urllib, TLS, DNS, and socket failures
            duration_ms = int((perf_counter() - started) * 1000)
            host = urlparse(source.url).hostname
            status = exc.code if isinstance(exc, HTTPError) else None
            evidence = diagnostic_evidence(
                http_status=status, final_host=host,
                content_type=exc.headers.get_content_type() if isinstance(exc, HTTPError) and exc.headers else None,
            )
            provisional = rss_failure_diagnostic(exc, [], evidence)
            attempts.append(NewsSourceAttempt(
                attempt=attempt_number, duration_ms=duration_ms, outcome="failed",
                code=provisional.code, message=provisional.message,
            ))
            diagnostic = rss_failure_diagnostic(exc, attempts, evidence)
            if not diagnostic.retryable or attempt_number == 3:
                raise NewsSourceDiagnosticError(diagnostic) from exc
            sleep(0.5 if attempt_number == 1 else 1.5)
    raise AssertionError("RSS retry loop exited unexpectedly")


def fetch_rss_items_with_diagnostic(source: NewsSource) -> tuple[list[dict[str, object]], NewsSourceDiagnostic]:
    body, evidence, attempts = fetch_rss_response(source)
    if not body.strip():
        raise NewsSourceDiagnosticError(NewsSourceDiagnostic(
            stage="response", category="content", code="RSS_EMPTY_RESPONSE",
            message="RSS 源返回了空响应。", suggestion="检查 Feed 地址是否正确以及源站是否生成了内容。",
            attempt_count=len(attempts), attempts=attempts, evidence=evidence,
        ))
    if evidence.content_type in {"text/html", "application/xhtml+xml"}:
        raise NewsSourceDiagnosticError(NewsSourceDiagnostic(
            stage="response", category="content", code="RSS_UNEXPECTED_CONTENT_TYPE",
            message=f"地址返回 {evidence.content_type}，不是 RSS/Atom Feed。",
            suggestion="检查地址是否指向 Feed，而不是登录页、验证页或普通网页。",
            attempt_count=len(attempts), attempts=attempts, evidence=evidence,
        ))

    parsed = feedparser.parse(body)
    entries = list(parsed.entries[:50])
    bozo_exception = getattr(parsed, "bozo_exception", None)
    position = getattr(bozo_exception, "position", None)
    evidence.parser_line = position[0] if isinstance(position, tuple) and len(position) > 0 else None
    evidence.parser_column = position[1] if isinstance(position, tuple) and len(position) > 1 else None
    evidence.feed_type = str(parsed.get("version") or "unknown")
    evidence.total_entries = len(parsed.entries)
    if not entries:
        malformed = bool(getattr(parsed, "bozo", False))
        raise NewsSourceDiagnosticError(NewsSourceDiagnostic(
            stage="parse" if malformed else "validate", category="xml" if malformed else "feed",
            code="RSS_MALFORMED_XML" if malformed else "RSS_NO_ENTRIES",
            message="Feed XML 无法解析。" if malformed else "Feed 中没有发现 RSS item 或 Atom entry。",
            suggestion="使用 Feed 校验工具检查 XML 结构和命名空间。" if malformed else "确认地址是有效 Feed，并检查源站当前是否发布条目。",
            attempt_count=len(attempts), attempts=attempts, evidence=evidence,
        ))

    parsed_items: list[dict[str, object]] = []
    for entry in entries:
        title = strip_markup(str(entry.get("title") or ""))
        summary_value = entry.get("summary") or entry.get("description") or ""
        if not summary_value and entry.get("content"):
            summary_value = entry.get("content")[0].get("value", "")
        summary = strip_markup(str(summary_value))
        link = str(entry.get("link") or "").strip()
        published_at = parse_datetime(str(entry.get("published") or entry.get("updated") or entry.get("created") or ""))
        if title and link:
            parsed_items.append({"title": title, "summary": summary or title, "url": link, "published_at": published_at})
    evidence.valid_entries = len(parsed_items)
    if not parsed_items:
        raise NewsSourceDiagnosticError(NewsSourceDiagnostic(
            stage="validate", category="entry", code="RSS_NO_VALID_ENTRIES",
            message=f"发现 {len(entries)} 个条目，但没有条目同时包含标题和链接。",
            suggestion="检查 Feed 条目的 title/link 字段，或修正源站输出格式。",
            attempt_count=len(attempts), attempts=attempts, evidence=evidence,
        ))
    warning = bool(getattr(parsed, "bozo", False))
    diagnostic = NewsSourceDiagnostic(
        stage="complete", category="success", code="RSS_OK_WITH_PARSE_WARNING" if warning else "RSS_OK",
        message=f"Feed 可用，解析到 {len(parsed_items)} 个有效条目。",
        suggestion="Feed 存在可恢复的格式警告，建议校验 XML。" if warning else "无需处理。",
        attempt_count=len(attempts), attempts=attempts, evidence=evidence,
    )
    return parsed_items, diagnostic


def fetch_rss_items(source: NewsSource) -> list[dict[str, object]]:
    items, _diagnostic = fetch_rss_items_with_diagnostic(source)
    return items


class DiagnosedNewsItems(list[dict[str, object]]):
    def __init__(self, items: list[dict[str, object]], diagnostic: NewsSourceDiagnostic | None = None):
        super().__init__(items)
        self.diagnostic = diagnostic


def fetch_information_source_items(source: NewsSource) -> list[dict[str, object]]:
    if source.adapter == "rss":
        items, diagnostic = fetch_rss_items_with_diagnostic(source)
        return DiagnosedNewsItems(items, diagnostic)
    if source.adapter == "adanos":
        return fetch_adanos_items(source)
    if source.adapter in {"finnhub", "gdelt", "cryptocompare", "alphavantage", "fred"}:
        return fetch_api_items(source)
    raise RuntimeError(f"Adapter {source.adapter} is catalog-only and does not ingest articles")


def refresh_source(
    source_id: int,
    trigger: Literal["schedule", "manual", "automation"] = "manual",
) -> NewsRefreshResult:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="News source not found")

    source = row_to_source(row)
    if not source.enabled:
        return NewsRefreshResult(
            source_id=source_id, fetched=0, inserted=0, error="Source is disabled", status="skipped"
        )

    started = datetime.now(timezone.utc)
    run_id = f"news-run-{secrets.token_hex(10)}"
    stale_before = (started - timedelta(minutes=10)).isoformat()
    try:
        with db_connect() as connection:
            connection.execute(
                """UPDATE news_source_runs
                   SET status = 'failed', finished_at = ?, error = 'Stale run was recovered'
                   WHERE source_id = ? AND status = 'running' AND started_at < ?""",
                (started.isoformat(), source_id, stale_before),
            )
            connection.execute(
                """INSERT INTO news_source_runs (id, source_id, trigger, status, started_at)
                   VALUES (?, ?, ?, 'running', ?)""",
                (run_id, source_id, trigger, started.isoformat()),
            )
            connection.execute(
                "DELETE FROM news_source_runs WHERE started_at < ? AND status != 'running'",
                ((started - timedelta(days=30)).isoformat(),),
            )
    except sqlite3.IntegrityError:
        return NewsRefreshResult(
            source_id=source_id, fetched=0, inserted=0,
            error="Source refresh is already running", status="skipped",
        )

    fetched_at = started.isoformat()
    try:
        if not source.api_key_configured:
            raise NewsSourceDiagnosticError(NewsSourceDiagnostic(
                stage="configuration", category="configuration", code="SOURCE_CREDENTIAL_MISSING",
                message=f"缺少所需凭据 {source.api_key_env}。", suggestion="在凭据管理页面配置密钥后重新检测。",
            ))
        items = fetch_information_source_items(source)
        diagnostic = getattr(items, "diagnostic", None)
        inserted = 0
        inserted_article_ids: list[str] = []
        with db_connect() as connection:
            for item in items:
                item_symbols = normalize_symbols(item.get("symbols", []))
                symbols = extract_symbols(str(item["title"]), str(item["summary"]), item_symbols)
                sentiment, event_type, impact = classify_article(str(item["title"]), str(item["summary"]))
                if item.get("sentiment") in {"bullish", "bearish", "neutral", "mixed"}:
                    sentiment = item["sentiment"]
                if item.get("event_type") in {"earnings", "macro", "regulatory", "product", "analyst", "crypto", "flow"}:
                    event_type = item["event_type"]
                if isinstance(item.get("impact_score"), (int, float)):
                    impact = max(25, min(95, int(item["impact_score"])))
                if source.category == "filing":
                    event_type, impact = "regulatory", max(impact, 70)
                elif source.category == "macro":
                    event_type, impact = "macro", max(impact, 65)
                elif "crypto" in source.capabilities:
                    event_type = "crypto"
                article_id = sha256(f"{source.id}:{item['url'] or item['title']}".encode("utf-8")).hexdigest()[:20]
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO news_articles (
                        id, source_id, source_name, title, summary, url, published_at,
                        fetched_at, symbols, sentiment, impact_score, event_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_id,
                        source.id,
                        source.name,
                        item["title"],
                        item["summary"],
                        item["url"],
                        item["published_at"],
                        fetched_at,
                        ",".join(symbols),
                        sentiment,
                        impact,
                        event_type,
                    ),
                )
                inserted += cursor.rowcount
                if cursor.rowcount:
                    inserted_article_ids.append(article_id)
                    record_initial_news_tags(article_id, symbols, set(item_symbols), connection)
            connection.execute(
                "UPDATE news_sources SET last_fetched_at = ?, last_error = NULL WHERE id = ?",
                (fetched_at, source.id),
            )
        enqueue_news_tagging(inserted_article_ids)
        finished = datetime.now(timezone.utc)
        with db_connect() as connection:
            connection.execute(
                """UPDATE news_source_runs SET status = 'succeeded', finished_at = ?, duration_ms = ?,
                   fetched = ?, inserted = ?, error = NULL, diagnostic_json = ? WHERE id = ?""",
                (finished.isoformat(), int((finished - started).total_seconds() * 1000), len(items), inserted,
                 diagnostic.model_dump_json() if diagnostic else None, run_id),
            )
        return NewsRefreshResult(
            source_id=source_id, fetched=len(items), inserted=inserted, run_id=run_id,
            status="succeeded", diagnostic=diagnostic,
        )
    except Exception as exc:  # noqa: BLE001 - source adapters surface network and parser errors differently
        if isinstance(exc, NewsSourceDiagnosticError):
            diagnostic = exc.diagnostic
        elif isinstance(exc, sqlite3.Error):
            diagnostic = NewsSourceDiagnostic(
                stage="persist", category="database", code="NEWS_DATABASE_FAILURE",
                message="新闻条目写入数据库失败。", suggestion="检查数据库锁、磁盘空间和文件权限。",
            )
        else:
            diagnostic = NewsSourceDiagnostic(
                stage="request", category="unknown", code="SOURCE_UNEXPECTED_FAILURE",
                message=str(exc)[:400] or exc.__class__.__name__, suggestion="查看服务日志并检查信息源适配器配置。",
            )
        message = f"{diagnostic.code}: {diagnostic.message}"[:240]
        finished = datetime.now(timezone.utc)
        with db_connect() as connection:
            connection.execute(
                "UPDATE news_sources SET last_fetched_at = ?, last_error = ? WHERE id = ?",
                (fetched_at, message, source.id),
            )
            connection.execute(
                """UPDATE news_source_runs SET status = 'failed', finished_at = ?, duration_ms = ?,
                   error = ?, diagnostic_json = ? WHERE id = ?""",
                (finished.isoformat(), int((finished - started).total_seconds() * 1000), message,
                 diagnostic.model_dump_json(), run_id),
            )
        return NewsRefreshResult(
            source_id=source_id, fetched=0, inserted=0, error=message, run_id=run_id,
            status="failed", diagnostic=diagnostic,
        )


def test_news_source(source_id: int) -> NewsSourceTestResult:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="News source not found")
    source = row_to_source(row)
    if source.adapter != "rss":
        raise HTTPException(status_code=409, detail="Connection diagnostics currently support RSS/Atom sources only")

    started = datetime.now(timezone.utc)
    run_id = f"news-test-{secrets.token_hex(10)}"
    try:
        with db_connect() as connection:
            connection.execute(
                """INSERT INTO news_source_runs (id, source_id, trigger, status, started_at)
                   VALUES (?, ?, 'test', 'running', ?)""",
                (run_id, source_id, started.isoformat()),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Source refresh or connection test is already running") from exc

    try:
        items, diagnostic = fetch_rss_items_with_diagnostic(source)
        status: Literal["succeeded", "failed"] = "succeeded"
        error = None
    except NewsSourceDiagnosticError as exc:
        items, diagnostic, status = [], exc.diagnostic, "failed"
        error = f"{diagnostic.code}: {diagnostic.message}"[:240]
    except Exception as exc:  # noqa: BLE001 - preserve a diagnostic for unexpected parser failures
        items, status = [], "failed"
        diagnostic = NewsSourceDiagnostic(
            stage="request", category="unknown", code="SOURCE_UNEXPECTED_FAILURE",
            message=str(exc)[:400] or exc.__class__.__name__, suggestion="查看服务日志并检查 RSS 地址。",
        )
        error = f"{diagnostic.code}: {diagnostic.message}"[:240]

    finished = datetime.now(timezone.utc)
    with db_connect() as connection:
        connection.execute(
            """UPDATE news_source_runs SET status = ?, finished_at = ?, duration_ms = ?, fetched = ?,
               inserted = 0, error = ?, diagnostic_json = ? WHERE id = ?""",
            (status, finished.isoformat(), int((finished - started).total_seconds() * 1000), len(items),
             error, diagnostic.model_dump_json(), run_id),
        )
    return NewsSourceTestResult(
        source_id=source_id, status=status, fetched=len(items), run_id=run_id, diagnostic=diagnostic,
    )
