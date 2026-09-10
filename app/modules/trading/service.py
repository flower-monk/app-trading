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
    AiBriefing,
    AlertEvaluation,
    AlertRule,
    AlertRuleCreate,
    AlertRuleUpdate,
    AutomationRunRecord,
    AutomationRunResult,
    BacktestRunResult,
    BaseModel,
    EventLog,
    Literal,
    NewsEvent,
    NotificationChannel,
    NotificationChannelCreate,
    NotificationChannelUpdate,
    NotificationDelivery,
    PortfolioPosition,
    PortfolioPositionCreate,
    PortfolioPositionUpdate,
    PortfolioRiskContribution,
    PortfolioRiskReport,
    PortfolioSummary,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
get_market_indicators = service_call('app.modules.market.service', 'get_market_indicators')
get_market_provider_status = service_call('app.modules.market.service', 'get_market_provider_status')
list_news_events = service_call('app.modules.news.service', 'list_news_events')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
enqueue_agent_event = service_call('app.modules.ai.service', 'enqueue_agent_event')
get_market_breadth = service_call('app.modules.market.service', 'get_market_breadth')
get_market_provider = service_call('app.modules.market.service', 'get_market_provider')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_watchlist_snapshots = service_call('app.modules.market.service', 'list_watchlist_snapshots')
refresh_due_news_sources = service_call('app.modules.news.service', 'refresh_due_news_sources')


def row_to_backtest(row: sqlite3.Row) -> BacktestRunResult:
    return BacktestRunResult(
        id=row["id"],
        symbol=row["symbol"],
        factor_id=row["factor_id"],
        factor_name=row["factor_name"],
        interval=row["interval"],
        initial_capital=row["initial_capital"],
        final_equity=row["final_equity"],
        total_return=row["total_return"],
        max_drawdown=row["max_drawdown"],
        sharpe=row["sharpe"],
        win_rate=row["win_rate"],
        trades=row["trades"],
        exposure=row["exposure"],
        created_at=row["created_at"],
        equity_curve=json.loads(row["equity_curve"]),
        signal_points=json.loads(row["signal_points"]),
    )


def row_to_alert(row: sqlite3.Row) -> AlertRule:
    return AlertRule(
        id=row["id"],
        symbol=row["symbol"],
        name=row["name"],
        metric=row["metric"],
        interval=row["interval"] if "interval" in row.keys() else "1h",
        parameters=json.loads(row["parameters"]) if "parameters" in row.keys() and row["parameters"] else {},
        operator=row["operator"],
        threshold=row["threshold"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        last_triggered_at=row["last_triggered_at"],
    )


def row_to_automation_run(row: sqlite3.Row) -> AutomationRunRecord:
    return AutomationRunRecord(
        id=row["id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        news_sources=row["news_sources"],
        inserted_news=row["inserted_news"],
        triggered_alerts=row["triggered_alerts"],
        summary=row["summary"],
    )


def row_to_event_log(row: sqlite3.Row) -> EventLog:
    return EventLog(
        id=row["id"],
        event_type=row["event_type"],
        severity=row["severity"],
        symbol=row["symbol"],
        title=row["title"],
        message=row["message"],
        created_at=row["created_at"],
    )


def row_to_channel(row: sqlite3.Row) -> NotificationChannel:
    return NotificationChannel(
        id=row["id"],
        name=row["name"],
        type="webhook",
        target=row["target"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        last_delivery_at=row["last_delivery_at"],
        last_error=row["last_error"],
    )


def row_to_delivery(row: sqlite3.Row) -> NotificationDelivery:
    return NotificationDelivery(
        id=row["id"],
        channel_id=row["channel_id"],
        event_id=row["event_id"],
        success=bool(row["success"]),
        status_code=row["status_code"],
        error=row["error"],
        delivered_at=row["delivered_at"],
    )


def _position_instrument(instrument_id: str = "", symbol: str = ""):
    from app.modules.market.charting import resolve_instrument

    identifier = instrument_id.strip() or symbol.strip()
    if not identifier:
        raise HTTPException(status_code=422, detail="请选择模拟持仓标的")
    instrument = resolve_instrument(identifier)
    if not instrument.positionable:
        raise HTTPException(status_code=422, detail="该标的不可用于模拟持仓")
    return instrument


def _position_unit(instrument, code: str = ""):
    units = instrument.quantity_units
    selected = next((item for item in units if item.code == code), None) if code else (units[0] if units else None)
    if selected is None:
        raise HTTPException(status_code=422, detail="数量单位不适用于当前标的")
    return selected


def _position_latest_price(instrument) -> float:
    snapshots = {item.symbol.upper(): item for item in get_market_provider().watchlist()}
    symbol = instrument.symbol.upper()
    snapshot = snapshots.get(symbol) or snapshots.get(symbol.replace("USDT", "USD"))
    if snapshot and snapshot.price > 0:
        return float(snapshot.price)
    from app.modules.market.charting import latest_instrument_quote

    return float(latest_instrument_quote(instrument.id).price)


def create_portfolio_position(payload: PortfolioPositionCreate) -> PortfolioPosition:
    init_db()
    instrument = _position_instrument(payload.instrument_id, payload.symbol)
    unit = _position_unit(instrument, payload.quantity_unit)
    average_price = payload.average_price if payload.average_price is not None else _position_latest_price(instrument)
    if payload.quantity <= 0 or average_price <= 0:
        raise HTTPException(status_code=400, detail="Quantity and average price must be positive")
    created_at = datetime.now(timezone.utc).isoformat()
    position_id = sha256(f"position:{instrument.id}:{payload.side}:{payload.quantity}:{average_price}:{created_at}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO portfolio_positions (
                id, instrument_id, symbol, side, quantity, quantity_unit, unit_multiplier,
                average_price, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                instrument.id,
                instrument.symbol,
                payload.side,
                payload.quantity,
                unit.code,
                unit.multiplier,
                average_price,
                payload.note.strip(),
                created_at,
            ),
        )
    return next(position for position in get_portfolio_summary().positions if position.id == position_id)


def update_portfolio_position(position_id: str, payload: PortfolioPositionUpdate) -> PortfolioPosition:
    init_db()
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM portfolio_positions WHERE id = ?", (position_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Position not found")
        instrument = _position_instrument(
            current["instrument_id"] if payload.instrument_id is None and payload.symbol is None else (payload.instrument_id or ""),
            current["symbol"] if payload.instrument_id is None and payload.symbol is None else (payload.symbol or ""),
        )
        quantity = current["quantity"] if payload.quantity is None else payload.quantity
        instrument_changed = payload.instrument_id is not None or payload.symbol is not None
        requested_unit = "" if instrument_changed and payload.quantity_unit is None else (
            current["quantity_unit"] if payload.quantity_unit is None else payload.quantity_unit
        )
        unit = _position_unit(instrument, requested_unit)
        average_price = current["average_price"] if payload.average_price is None else payload.average_price
        if quantity <= 0 or average_price <= 0:
            raise HTTPException(status_code=400, detail="Quantity and average price must be positive")
        connection.execute(
            """UPDATE portfolio_positions
               SET instrument_id = ?, symbol = ?, side = ?, quantity = ?, quantity_unit = ?,
                   unit_multiplier = ?, average_price = ?, note = ? WHERE id = ?""",
            (
                instrument.id,
                instrument.symbol,
                current["side"] if payload.side is None else payload.side,
                quantity,
                unit.code,
                unit.multiplier,
                average_price,
                current["note"] if payload.note is None else payload.note.strip(),
                position_id,
            ),
        )
    return next(position for position in get_portfolio_summary().positions if position.id == position_id)


def delete_portfolio_position(position_id: str) -> dict[str, bool]:
    init_db()
    with db_connect() as connection:
        cursor = connection.execute("UPDATE portfolio_positions SET enabled = 0 WHERE id = ?", (position_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"deleted": False, "disabled": True}


def get_portfolio_summary() -> PortfolioSummary:
    init_db()
    snapshots = {item.symbol: item for item in get_market_provider().watchlist()}
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM portfolio_positions WHERE enabled = 1 ORDER BY created_at DESC").fetchall()

    positions: list[PortfolioPosition] = []
    for row in rows:
        try:
            instrument = _position_instrument(row["instrument_id"] or "", row["symbol"])
        except HTTPException:
            instrument = _position_instrument("", row["symbol"])
        symbol = instrument.symbol
        unit = _position_unit(instrument, row["quantity_unit"] or "")
        unit_multiplier = float(row["unit_multiplier"] or unit.multiplier)
        base_quantity = float(row["quantity"]) * unit_multiplier
        snapshot = snapshots.get(symbol) or snapshots.get(symbol.replace("USDT", "USD"))
        current_price = float(snapshot.price) if snapshot else None
        if current_price is None:
            try:
                current_price = _position_latest_price(instrument)
            except (HTTPException, RuntimeError, ValueError):
                current_price = None
        stale_price = current_price is None
        mark_price = current_price if current_price is not None else row["average_price"]
        signed_qty = base_quantity if row["side"] == "long" else -base_quantity
        market_value = signed_qty * mark_price
        cost_basis = signed_qty * row["average_price"]
        unrealized_pnl = market_value - cost_basis
        pnl_pct = unrealized_pnl / abs(cost_basis) * 100 if cost_basis else 0.0
        positions.append(
            PortfolioPosition(
                id=row["id"],
                instrument_id=instrument.id,
                symbol=symbol,
                name=instrument.name,
                asset_class=instrument.asset_class,
                currency=instrument.currency,
                side=row["side"],
                quantity=row["quantity"],
                quantity_unit=unit.code,
                unit_label=unit.label,
                unit_multiplier=unit_multiplier,
                base_quantity=round(base_quantity, 8),
                average_price=row["average_price"],
                current_price=round(current_price, 2) if current_price is not None else None,
                market_value=round(market_value, 2),
                cost_basis=round(cost_basis, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                unrealized_pnl_percent=round(pnl_pct, 2),
                stale_price=stale_price,
                note=row["note"],
                created_at=row["created_at"],
            )
        )

    total_market_value = sum(item.market_value for item in positions)
    total_cost_basis = sum(item.cost_basis for item in positions)
    total_pnl = total_market_value - total_cost_basis
    total_pnl_pct = total_pnl / abs(total_cost_basis) * 100 if total_cost_basis else 0.0
    long_exposure = sum(max(0, item.market_value) for item in positions)
    short_exposure = abs(sum(min(0, item.market_value) for item in positions))
    return PortfolioSummary(
        total_market_value=round(total_market_value, 2),
        total_cost_basis=round(total_cost_basis, 2),
        total_unrealized_pnl=round(total_pnl, 2),
        total_unrealized_pnl_percent=round(total_pnl_pct, 2),
        long_exposure=round(long_exposure, 2),
        short_exposure=round(short_exposure, 2),
        positions=positions,
    )


def get_portfolio_risk_report() -> PortfolioRiskReport:
    summary = get_portfolio_summary()
    gross_exposure = summary.long_exposure + summary.short_exposure
    net_exposure = summary.long_exposure - summary.short_exposure
    net_exposure_percent = net_exposure / gross_exposure * 100 if gross_exposure else 0.0
    weights = [
        (abs(position.market_value) / gross_exposure * 100 if gross_exposure else 0.0, position)
        for position in summary.positions
    ]
    weights.sort(key=lambda item: item[0], reverse=True)
    largest_weight, largest_position = weights[0] if weights else (0.0, None)
    concentration_score = sum((weight / 100) ** 2 for weight, _ in weights) * 100 if weights else 0.0
    stale_count = sum(1 for position in summary.positions if position.stale_price)

    warnings: list[str] = []
    actions: list[str] = []
    if not summary.positions:
        warnings.append("当前没有模拟持仓，无法评估组合风险。")
        actions.append("添加至少一个持仓后再查看集中度和方向敞口。")
    if largest_position and largest_weight >= 45:
        warnings.append(f"{largest_position.symbol} 单仓权重 {largest_weight:.1f}%，存在集中度风险。")
        actions.append(f"复核 {largest_position.symbol} 的止损线和新闻事件风险。")
    if abs(net_exposure_percent) >= 80 and gross_exposure:
        warnings.append(f"净方向敞口 {net_exposure_percent:.1f}%，组合对单边行情较敏感。")
        actions.append("考虑增加对冲或降低同方向仓位。")
    if summary.total_unrealized_pnl_percent <= -3:
        warnings.append(f"组合浮亏 {summary.total_unrealized_pnl_percent:.2f}%，需要检查回撤控制。")
        actions.append("优先评估亏损贡献最大的持仓，避免扩大回撤。")
    if stale_count:
        warnings.append(f"{stale_count} 个持仓缺少当前行情源价格，估值可能滞后。")
        actions.append("切换支持该标的的行情源或移除无法估值的持仓。")
    if not warnings:
        warnings.append("组合未触发集中度、方向敞口或估值风险阈值。")
        actions.append("保持预警规则覆盖主要持仓，并定期复核组合敞口。")

    contributions: list[PortfolioRiskContribution] = []
    for weight, position in weights:
        flag: Literal["ok", "watch", "risk"] = "ok"
        if weight >= 45 or position.unrealized_pnl_percent <= -5 or position.stale_price:
            flag = "risk"
        elif weight >= 30 or position.unrealized_pnl_percent <= -2:
            flag = "watch"
        contributions.append(
            PortfolioRiskContribution(
                symbol=position.symbol,
                side=position.side,
                weight_percent=round(weight, 1),
                market_value=position.market_value,
                unrealized_pnl=position.unrealized_pnl,
                risk_flag=flag,
            )
        )

    risk_level: Literal["ok", "watch", "risk"] = "ok"
    if any(item.risk_flag == "risk" for item in contributions) or summary.total_unrealized_pnl_percent <= -3:
        risk_level = "risk"
    elif any(item.risk_flag == "watch" for item in contributions) or abs(net_exposure_percent) >= 60:
        risk_level = "watch"

    return PortfolioRiskReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        risk_level=risk_level,
        gross_exposure=round(gross_exposure, 2),
        net_exposure=round(net_exposure, 2),
        net_exposure_percent=round(net_exposure_percent, 1),
        largest_position_symbol=largest_position.symbol if largest_position else None,
        largest_position_weight=round(largest_weight, 1),
        concentration_score=round(concentration_score, 1),
        stale_price_count=stale_count,
        warnings=warnings[:5],
        actions=actions[:5],
        contributions=contributions,
    )


def add_event_log(
    event_type: Literal["alert_triggered", "news_refresh", "automation_run", "forward_test", "walk_forward_test", "strategy_validation", "stress_test", "sensitivity_test", "research_pipeline", "research_backfill", "system"],
    severity: Literal["info", "watch", "risk", "error"],
    title: str,
    message: str,
    symbol: str | None = None,
    created_at: str | None = None,
) -> EventLog:
    init_db()
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    event_id = sha256(f"{event_type}:{symbol}:{title}:{message}:{timestamp}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO event_logs (
                id, event_type, severity, symbol, title, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, event_type, severity, symbol, title, message, timestamp),
        )
        row = connection.execute("SELECT * FROM event_logs WHERE id = ?", (event_id,)).fetchone()
    return row_to_event_log(row)


def list_notification_channels() -> list[NotificationChannel]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM notification_channels ORDER BY created_at DESC").fetchall()
    return [row_to_channel(row) for row in rows]


def create_notification_channel(payload: NotificationChannelCreate) -> NotificationChannel:
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    channel_id = sha256(f"channel:{payload.name}:{payload.target}:{created_at}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO notification_channels (
                id, name, type, target, enabled, created_at
            )
            VALUES (?, ?, 'webhook', ?, ?, ?)
            """,
            (channel_id, payload.name.strip(), str(payload.target), int(payload.enabled), created_at),
        )
        row = connection.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
    return row_to_channel(row)


def update_notification_channel(channel_id: str, payload: NotificationChannelUpdate) -> NotificationChannel:
    init_db()
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Notification channel not found")
        connection.execute(
            """
            UPDATE notification_channels
            SET name = ?, target = ?, enabled = ?, last_error = ?
            WHERE id = ?
            """,
            (
                payload.name.strip() if payload.name is not None else current["name"],
                str(payload.target) if payload.target is not None else current["target"],
                int(payload.enabled) if payload.enabled is not None else current["enabled"],
                None if payload.target is not None else current["last_error"],
                channel_id,
            ),
        )
        row = connection.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
    add_event_log(
        event_type="system",
        severity="info",
        title="通知通道已更新",
        message=f"{row['name']} 当前为 {'启用' if row['enabled'] else '停用'} 状态。",
    )
    return row_to_channel(row)


def delete_notification_channel(channel_id: str) -> dict[str, bool]:
    init_db()
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Notification channel not found")
        cursor = connection.execute("UPDATE notification_channels SET enabled = 0 WHERE id = ?", (channel_id,))
    add_event_log(
        event_type="system",
        severity="info",
        title="通知通道已停用",
        message=f"{current['name']} 已停用，可在管理中心恢复。",
    )
    return {"deleted": False, "disabled": cursor.rowcount > 0}


def list_notification_deliveries(channel_id: str | None = None, limit: int = 50) -> list[NotificationDelivery]:
    init_db()
    with db_connect() as connection:
        if channel_id:
            rows = connection.execute(
                "SELECT * FROM notification_deliveries WHERE channel_id = ? ORDER BY delivered_at DESC LIMIT ?",
                (channel_id, max(1, min(200, limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM notification_deliveries ORDER BY delivered_at DESC LIMIT ?",
                (max(1, min(200, limit)),),
            ).fetchall()
    return [row_to_delivery(row) for row in rows]


def record_delivery(
    channel_id: str,
    event_id: str,
    success: bool,
    status_code: int | None,
    error: str | None,
    delivered_at: str,
) -> NotificationDelivery:
    delivery_id = sha256(f"delivery:{channel_id}:{event_id}:{delivered_at}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO notification_deliveries (
                id, channel_id, event_id, success, status_code, error, delivered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (delivery_id, channel_id, event_id, int(success), status_code, error, delivered_at),
        )
        connection.execute(
            "UPDATE notification_channels SET last_delivery_at = ?, last_error = ? WHERE id = ?",
            (delivered_at, None if success else error, channel_id),
        )
        row = connection.execute("SELECT * FROM notification_deliveries WHERE id = ?", (delivery_id,)).fetchone()
    return row_to_delivery(row)


def test_notification_channel(channel_id: str) -> NotificationDelivery:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")
    channel = row_to_channel(row)
    delivered_at = datetime.now(timezone.utc).isoformat()
    event_id = f"test-{sha256(delivered_at.encode('utf-8')).hexdigest()[:12]}"
    payload = json.dumps(
        {"event_type": "system", "severity": "info", "title": "App Trading 通知测试", "message": "管理中心测试消息", "created_at": delivered_at}
    ).encode("utf-8")
    request = UrlRequest(channel.target, data=payload, headers={"Content-Type": "application/json", "User-Agent": "AppTrading/0.1"}, method="POST")
    try:
        with urlopen(request, timeout=8) as response:
            return record_delivery(channel.id, event_id, True, response.status, None, delivered_at)
    except HTTPError as exc:
        return record_delivery(channel.id, event_id, False, exc.code, str(exc)[:240], delivered_at)
    except URLError as exc:
        return record_delivery(channel.id, event_id, False, None, str(exc)[:240], delivered_at)


def deliver_event(event: EventLog, channel_ids: list[str] | None = None) -> list[NotificationDelivery]:
    channels = [
        channel for channel in list_notification_channels()
        if channel.enabled and (channel_ids is None or channel.id in channel_ids)
    ]
    deliveries: list[NotificationDelivery] = []
    for channel in channels:
        delivered_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(
            {
                "event_type": event.event_type,
                "severity": event.severity,
                "symbol": event.symbol,
                "title": event.title,
                "message": event.message,
                "created_at": event.created_at,
            }
        ).encode("utf-8")
        request = UrlRequest(channel.target, data=payload, headers={"Content-Type": "application/json", "User-Agent": "AppTrading/0.1"}, method="POST")
        try:
            with urlopen(request, timeout=8) as response:
                deliveries.append(record_delivery(channel.id, event.id, True, response.status, None, delivered_at))
        except HTTPError as exc:
            deliveries.append(record_delivery(channel.id, event.id, False, exc.code, str(exc)[:240], delivered_at))
        except URLError as exc:
            deliveries.append(record_delivery(channel.id, event.id, False, None, str(exc)[:240], delivered_at))
    return deliveries


def list_event_logs(symbol: str | None = None, limit: int = 50) -> list[EventLog]:
    init_db()
    with db_connect() as connection:
        if symbol:
            rows = connection.execute(
                """
                SELECT * FROM event_logs
                WHERE symbol = ? OR symbol IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol.upper(), max(1, min(200, limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM event_logs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, limit)),),
            ).fetchall()
    return [row_to_event_log(row) for row in rows]


def record_automation_run(result: AutomationRunResult) -> AutomationRunRecord:
    status: Literal["completed", "warning", "failed"] = "completed"
    if any(item.error for item in result.news_results):
        status = "failed"
    elif result.triggered_alerts:
        status = "warning"
    summary = f"新闻源 {len(result.news_results)} 个，新增新闻 {result.inserted_news} 条，触发预警 {result.triggered_alerts} 条。"
    with db_connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO automation_runs (
                id, started_at, finished_at, status, news_sources, inserted_news, triggered_alerts, summary
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.started_at,
                result.finished_at,
                status,
                len(result.news_results),
                result.inserted_news,
                result.triggered_alerts,
                summary,
            ),
        )
        row = connection.execute("SELECT * FROM automation_runs WHERE id = ?", (result.id,)).fetchone()
    return row_to_automation_run(row)


def list_automation_runs(limit: int = 20) -> list[AutomationRunRecord]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT * FROM automation_runs ORDER BY finished_at DESC LIMIT ?",
            (max(1, min(100, limit)),),
        ).fetchall()
    return [row_to_automation_run(row) for row in rows]


def list_alert_rules(symbol: str | None = None) -> list[AlertRule]:
    init_db()
    with db_connect() as connection:
        if symbol:
            rows = connection.execute(
                "SELECT * FROM alert_rules WHERE symbol = ? ORDER BY created_at DESC",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM alert_rules ORDER BY created_at DESC").fetchall()
    return [row_to_alert(row) for row in rows]


def create_alert_rule(payload: AlertRuleCreate) -> AlertRule:
    init_db()
    symbol = payload.symbol.strip().upper()
    valid_symbols = {item.symbol for item in get_market_provider().list_symbols()}
    if symbol not in valid_symbols:
        from app.modules.market.charting import INDICATOR_BY_ID, resolve_instrument

        resolve_instrument(symbol)
        if payload.metric not in {"price", "news_impact"} and payload.metric not in INDICATOR_BY_ID:
            raise HTTPException(status_code=422, detail="Unknown indicator metric")
    created_at = datetime.now(timezone.utc).isoformat()
    alert_id = sha256(
        f"{symbol}:{payload.metric}:{payload.operator}:{payload.threshold}:{created_at}".encode("utf-8")
    ).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO alert_rules (
                id, symbol, name, metric, operator, threshold, enabled, interval, parameters, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id, symbol, payload.name.strip() or f"{symbol} {payload.metric}",
                payload.metric, payload.operator, payload.threshold, int(payload.enabled),
                payload.interval, json.dumps(payload.parameters), created_at,
            ),
        )
        row = connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()
    return row_to_alert(row)


def evaluate_alert_rules(symbol: str | None = None) -> list[AlertEvaluation]:
    return [evaluate_alert_rule(rule) for rule in list_alert_rules(symbol) if rule.enabled]


def update_alert_rule(alert_id: str, payload: AlertRuleUpdate) -> AlertRule:
    init_db()
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        connection.execute(
            """
            UPDATE alert_rules
            SET name = ?, operator = ?, threshold = ?, enabled = ?, interval = ?, parameters = ?
            WHERE id = ?
            """,
            (
                payload.name.strip() if payload.name is not None else current["name"],
                payload.operator if payload.operator is not None else current["operator"],
                payload.threshold if payload.threshold is not None else current["threshold"],
                int(payload.enabled) if payload.enabled is not None else current["enabled"],
                payload.interval if payload.interval is not None else current["interval"],
                json.dumps(payload.parameters) if payload.parameters is not None else current["parameters"],
                alert_id,
            ),
        )
        row = connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()
    add_event_log(
        event_type="system",
        severity="info",
        symbol=row["symbol"],
        title="预警规则已更新",
        message=f"{row['name']} 当前为 {'启用' if row['enabled'] else '停用'} 状态。",
    )
    return row_to_alert(row)


def delete_alert_rule(alert_id: str) -> dict[str, bool]:
    init_db()
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        cursor = connection.execute("UPDATE alert_rules SET enabled = 0 WHERE id = ?", (alert_id,))
    add_event_log(
        event_type="system",
        severity="info",
        symbol=current["symbol"],
        title="预警规则已停用",
        message=f"{current['name']} 已停用，可在管理中心恢复。",
    )
    return {"deleted": False, "disabled": cursor.rowcount > 0}


def alert_current_value(rule: AlertRule) -> float:
    if rule.metric == "price" and ":" not in rule.symbol:
        snapshot = next((item for item in get_market_provider().watchlist() if item.symbol == rule.symbol), None)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"No market snapshot for {rule.symbol}")
        return snapshot.price
    if rule.metric == "rsi" and ":" not in rule.symbol:
        rsi_card = next((item for item in get_market_indicators(rule.symbol) if item.label.startswith("RSI")), None)
        if rsi_card is None:
            raise HTTPException(status_code=404, detail=f"No RSI indicator for {rule.symbol}")
        return float(rsi_card.value)
    if rule.metric == "news_impact":
        try:
            from app.modules.market.charting import resolve_instrument
            news_symbol = resolve_instrument(rule.symbol).symbol
        except HTTPException:
            news_symbol = rule.symbol
        latest_news = list_news_events(news_symbol, limit=10)
        return max((item.impact_score for item in latest_news), default=0)
    from app.modules.market.charting import latest_chart_price, latest_indicator_value

    if rule.metric == "price":
        return latest_chart_price(rule.symbol, rule.interval)
    return latest_indicator_value(rule.symbol, rule.metric, rule.interval, rule.parameters)


def evaluate_alert_rule(rule: AlertRule) -> AlertEvaluation:
    value = alert_current_value(rule)
    triggered = value >= rule.threshold if rule.operator == ">=" else value <= rule.threshold
    evaluated_at = datetime.now(timezone.utc).isoformat()
    if triggered and rule.enabled:
        with db_connect() as connection:
            connection.execute("UPDATE alert_rules SET last_triggered_at = ? WHERE id = ?", (evaluated_at, rule.id))
        rule.last_triggered_at = evaluated_at
    metric_label = {"price": "价格", "rsi": "RSI", "news_impact": "新闻影响分"}.get(rule.metric, rule.metric)
    message = f"{rule.symbol} {metric_label} {value:.2f} {rule.operator} {rule.threshold:.2f}"
    result = AlertEvaluation(rule=rule, current_value=round(value, 2), triggered=triggered and rule.enabled, message=message, evaluated_at=evaluated_at)
    if result.triggered:
        event = add_event_log(
            event_type="alert_triggered",
            severity="risk" if rule.metric in {"price", "news_impact"} else "watch",
            symbol=rule.symbol,
            title=f"预警触发：{rule.name}",
            message=message,
            created_at=evaluated_at,
        )
        deliver_event(event)
    return result


def run_automation_cycle() -> AutomationRunResult:
    init_db()
    started_at = datetime.now(timezone.utc).isoformat()
    news_results = refresh_due_news_sources(trigger="automation")
    alert_results = evaluate_alert_rules()
    finished_at = datetime.now(timezone.utc).isoformat()
    inserted_news = sum(item.inserted for item in news_results)
    triggered_alerts = sum(item.triggered for item in alert_results)
    run_id = sha256(f"automation:{started_at}:{finished_at}".encode("utf-8")).hexdigest()[:20]
    add_event_log(
        event_type="automation_run",
        severity="watch" if triggered_alerts else "info",
        title="自动化盯盘完成",
        message=f"刷新新闻源 {len(news_results)} 个，新增新闻 {inserted_news} 条，触发预警 {triggered_alerts} 条。",
        created_at=finished_at,
    )
    for result in news_results:
        severity: Literal["info", "watch", "risk", "error"] = "error" if result.error else "info"
        add_event_log(
            event_type="news_refresh",
            severity=severity,
            title=f"新闻源刷新 #{result.source_id}",
            message=result.error or f"抓取 {result.fetched} 条，新增 {result.inserted} 条。",
            created_at=finished_at,
        )
    result = AutomationRunResult(
        id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        news_results=news_results,
        alert_results=alert_results,
        triggered_alerts=triggered_alerts,
        inserted_news=inserted_news,
    )
    record_automation_run(result)
    if inserted_news:
        enqueue_agent_event("high_impact_news", context={"inserted_news": inserted_news})
    if triggered_alerts:
        enqueue_agent_event("alert_triggered", context={"triggered_alerts": triggered_alerts})
    return result


def model_payload(model: BaseModel) -> dict:
    if hasattr(model, "model_dump_json"):
        return json.loads(model.model_dump_json())
    return json.loads(model.json())


def market_stream_payload() -> dict:
    provider = get_market_provider_status()
    watchlist_items = list_watchlist_snapshots()
    breadth = get_market_breadth()
    return {
        "type": "market_snapshot",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": model_payload(provider),
        "watchlist": [model_payload(item) for item in watchlist_items],
        "breadth": model_payload(breadth),
    }


def build_ai_briefing() -> AiBriefing:
    snapshots = list_watchlist_snapshots()
    breadth = get_market_breadth()
    portfolio_summary = get_portfolio_summary()
    active_alerts = [rule for rule in list_alert_rules() if rule.enabled]
    top_movers = sorted(snapshots, key=lambda item: abs(item.change_percent), reverse=True)[:3]
    weakest = sorted(snapshots, key=lambda item: item.trend_score)[:2]
    strongest = sorted(snapshots, key=lambda item: item.trend_score, reverse=True)[:2]

    news_candidates: list[NewsEvent] = []
    for snapshot in snapshots[:6]:
        news_candidates.extend(list_news_events(snapshot.symbol, limit=2))
    key_news = sorted(news_candidates, key=lambda item: item.impact_score, reverse=True)[:3]

    risk_level: Literal["info", "watch", "risk", "opportunity"] = "info"
    if breadth.risk_level == "risk" or portfolio_summary.total_unrealized_pnl_percent <= -2:
        risk_level = "risk"
    elif breadth.risk_level == "watch" or any(item.change_percent < -1.5 for item in snapshots):
        risk_level = "watch"
    elif breadth.risk_level == "constructive":
        risk_level = "opportunity"

    opportunities = [
        f"{item.symbol} 趋势评分 {item.trend_score}，涨跌幅 {item.change_percent:+.2f}%"
        for item in strongest
        if item.trend_score >= 60
    ]
    if not opportunities:
        opportunities.append("暂无高置信趋势机会，优先等待自选池出现更集中的强势信号。")

    risks = [
        f"{item.symbol} 趋势评分 {item.trend_score}，需要关注破位或负面新闻确认"
        for item in weakest
        if item.trend_score <= 45 or item.change_percent < 0
    ]
    if portfolio_summary.total_unrealized_pnl_percent < 0:
        risks.append(f"模拟组合未实现盈亏 {portfolio_summary.total_unrealized_pnl_percent:.2f}%，需要复核仓位集中度。")
    if not risks:
        risks.append("当前自选池未出现显著系统性风险，保持常规预警监控。")

    actions = [
        f"优先复核 {top_movers[0].symbol} 的新闻标记与 K 线位置。" if top_movers else "先添加自选标的以形成盯盘池。",
        f"当前 {len(active_alerts)} 条预警启用，建议对高波动标的补充价格或 RSI 规则。",
        "回测最新推荐因子前，先确认因子库中仅保留当前研究要用的启用因子。",
    ]

    evidence = [
        f"市场广度：{breadth.advancers}/{breadth.decliners}，均涨跌 {breadth.average_change:+.2f}%",
        f"自选数量：{breadth.total}",
        f"模拟组合敞口：Long {portfolio_summary.long_exposure:.2f} / Short {portfolio_summary.short_exposure:.2f}",
    ]
    evidence.extend([f"新闻：{item.title}（影响 {item.impact_score}）" for item in key_news])

    summary = (
        f"当前自选池 A/D 为 {breadth.advancers}/{breadth.decliners}，"
        f"均涨跌 {breadth.average_change:+.2f}%，整体状态为 {breadth.risk_level}。"
        f"需要重点跟踪 {', '.join(item.symbol for item in top_movers) if top_movers else '暂无标的'}。"
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    return AiBriefing(
        id=sha256(f"briefing:{generated_at}:{breadth.total}:{breadth.average_change}".encode("utf-8")).hexdigest()[:20],
        title="规则盯盘简报",
        summary=summary,
        risk_level=risk_level,
        generated_at=generated_at,
        opportunities=opportunities[:4],
        risks=risks[:4],
        actions=actions,
        evidence=evidence[:8],
    )
