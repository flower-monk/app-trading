"""Research domain: factors implementation."""

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
    Candle,
    FactorDefinition,
    FactorDefinitionCreate,
    FactorDefinitionUpdate,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
from app.modules.market.service import MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
init_db = service_call('app.modules.infrastructure.database', 'init_db')


def slugify_factor_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or sha256(name.encode("utf-8")).hexdigest()[:10]


def row_to_factor(row: sqlite3.Row) -> FactorDefinition:
    return FactorDefinition(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        description=row["description"],
        signal=row["signal"],
        horizon=row["horizon"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
    )


def list_factor_definitions(enabled_only: bool = False) -> list[FactorDefinition]:
    init_db()
    with db_connect() as connection:
        if enabled_only:
            rows = connection.execute(
                "SELECT * FROM factor_definitions WHERE enabled = 1 ORDER BY created_at, name"
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM factor_definitions ORDER BY created_at, name").fetchall()
    return [row_to_factor(row) for row in rows]


def get_factor_definition(factor_id: str, require_enabled: bool = False) -> FactorDefinition:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM factor_definitions WHERE id = ?", (factor_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Factor definition not found")
    factor = row_to_factor(row)
    if require_enabled and not factor.enabled:
        raise HTTPException(status_code=400, detail="Factor definition is disabled")
    return factor


def create_factor_definition(payload: FactorDefinitionCreate) -> FactorDefinition:
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    base_id = slugify_factor_id(payload.name)
    factor_id = base_id
    suffix = 2
    with db_connect() as connection:
        while connection.execute("SELECT 1 FROM factor_definitions WHERE id = ?", (factor_id,)).fetchone():
            factor_id = f"{base_id}-{suffix}"
            suffix += 1
        connection.execute(
            """
            INSERT INTO factor_definitions (
                id, name, category, description, signal, horizon, enabled, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                factor_id,
                payload.name.strip(),
                payload.category.strip() or "自定义",
                payload.description.strip(),
                payload.signal,
                payload.horizon.strip() or "3-10 days",
                int(payload.enabled),
                created_at,
            ),
        )
        row = connection.execute("SELECT * FROM factor_definitions WHERE id = ?", (factor_id,)).fetchone()
    add_event_log(
        event_type="system",
        severity="info",
        title="因子已创建",
        message=f"{payload.name.strip()} 已加入因子库。",
    )
    return row_to_factor(row)


def update_factor_definition(factor_id: str, payload: FactorDefinitionUpdate) -> FactorDefinition:
    current = get_factor_definition(factor_id)
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE factor_definitions
            SET name = ?, category = ?, description = ?, signal = ?, horizon = ?, enabled = ?
            WHERE id = ?
            """,
            (
                current.name if payload.name is None else payload.name.strip(),
                current.category if payload.category is None else payload.category.strip(),
                current.description if payload.description is None else payload.description.strip(),
                current.signal if payload.signal is None else payload.signal,
                current.horizon if payload.horizon is None else payload.horizon.strip(),
                int(current.enabled if payload.enabled is None else payload.enabled),
                factor_id,
            ),
        )
        row = connection.execute("SELECT * FROM factor_definitions WHERE id = ?", (factor_id,)).fetchone()
    return row_to_factor(row)


def factor_name(factor_id: str) -> str:
    try:
        return get_factor_definition(factor_id).name
    except HTTPException:
        return factor_id


def backtest_position(factor_id: str, history: list[Candle], index: int, has_recent_positive_news: bool) -> int:
    candle = history[index]
    if index < 20:
        return 0
    previous = history[index - 1]
    recent = history[max(0, index - 20) : index + 1]
    highs = [item.high for item in recent[:-1]]
    ranges = [abs(item.high - item.low) for item in recent]
    avg_range = sum(ranges[:-1]) / max(1, len(ranges) - 1)
    current_range = ranges[-1]
    momentum = candle.close / history[index - 10].close - 1 if index >= 10 else 0

    if factor_id == "momentum-news":
        return int(momentum > 0.015 and candle.close > candle.ma20 and (has_recent_positive_news or candle.rsi >= 55))
    if factor_id == "vol-compression":
        compressed = current_range < avg_range * 0.85
        breakout = candle.close >= max(highs) * 0.995 and candle.volume > previous.volume * 0.92
        return int(breakout or (compressed and candle.close > candle.ma20))
    if factor_id == "rsi-reversion":
        return int(previous.rsi < 38 and candle.close > previous.close)
    if factor_id == "event-drift":
        return int(has_recent_positive_news and candle.close > candle.ma20 and candle.close > previous.close)
    return int(momentum > 0.015 and candle.close > candle.ma20 and (has_recent_positive_news or candle.rsi >= 55))


def simulate_factor_window(
    factor_id: str,
    history: list[Candle],
    start_index: int,
    end_index: int,
    initial_capital: float,
    fee_rate: float,
    has_recent_positive_news: bool,
) -> tuple[float, float, int, list[float]]:
    equity = initial_capital
    equity_curve = [round(equity, 2)]
    trade_returns: list[float] = []
    position = 0
    entry_equity = equity
    start = max(21, start_index)
    end = min(len(history) - 1, end_index)
    for index in range(start, end + 1):
        desired = backtest_position(factor_id, history, index - 1, has_recent_positive_news)
        if desired != position:
            equity *= 1 - fee_rate
            if position and not desired:
                trade_returns.append(equity / entry_equity - 1)
            if desired and not position:
                entry_equity = equity
            position = desired
        if position:
            equity *= 1 + (history[index].close / history[index - 1].close - 1)
        equity_curve.append(round(equity, 2))
    if position:
        trade_returns.append(equity / entry_equity - 1)
    peak = equity_curve[0]
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak if peak else 0.0)
    return_pct = (equity / initial_capital - 1) * 100 if initial_capital else 0.0
    return round(return_pct, 2), round(max_drawdown * 100, 2), len(trade_returns), equity_curve


def simulate_parameter_window(
    factor_id: str,
    history: list[Candle],
    min_rsi: float,
    min_trend: int,
    initial_capital: float,
    fee_rate: float,
    has_recent_positive_news: bool,
) -> tuple[float, float, int, float]:
    equity = initial_capital
    equity_curve = [round(equity, 2)]
    position = 0
    entry_equity = equity
    trade_returns: list[float] = []
    active_bars = 0
    for index in range(21, len(history)):
        previous = history[index - 1]
        trend_proxy = 50
        if previous.ma60:
            trend_proxy += int(max(-30, min(30, (previous.close / previous.ma60 - 1) * 250)))
        desired = backtest_position(factor_id, history, index - 1, has_recent_positive_news)
        desired = int(desired and previous.rsi >= min_rsi and trend_proxy >= min_trend)
        if desired != position:
            equity *= 1 - fee_rate
            if position and not desired:
                trade_returns.append(equity / entry_equity - 1)
            if desired and not position:
                entry_equity = equity
            position = desired
        if position:
            active_bars += 1
            equity *= 1 + (history[index].close / history[index - 1].close - 1)
        equity_curve.append(round(equity, 2))
    if position:
        trade_returns.append(equity / entry_equity - 1)
    peak = equity_curve[0]
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak if peak else 0.0)
    total_return = (equity / initial_capital - 1) * 100 if initial_capital else 0.0
    exposure = active_bars / max(1, len(history) - 21) * 100
    return round(total_return, 2), round(max_drawdown * 100, 2), len(trade_returns), round(exposure, 1)
