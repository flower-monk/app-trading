"""Research domain: backtesting implementation."""

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
    BacktestBenchmarkReport,
    BacktestRunRequest,
    BacktestRunResult,
    BacktestSummary,
    Candle,
    Literal,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
candles = service_call('app.modules.market.service', 'get_market_candles')
news_events = service_call('app.modules.news.service', 'list_news_events')
from app.modules.market.service import MARKET_PROVIDERS
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
dedupe = service_call('app.modules.market.service', 'dedupe')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_supported_market_symbols = service_call('app.modules.market.service', 'list_supported_market_symbols')
row_to_backtest = service_call('app.modules.trading.service', 'row_to_backtest')

# Deferred internal references keep research submodules independently importable.
backtest_position = service_call('app.modules.research.factors', 'backtest_position')
get_factor_definition = service_call('app.modules.research.factors', 'get_factor_definition')
latest_backtest_for_factor = service_call('app.modules.research.experiments', 'latest_backtest_for_factor')


def run_backtest(payload: BacktestRunRequest) -> BacktestRunResult:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    limit = max(60, min(240, payload.limit))
    initial_capital = max(100.0, payload.initial_capital)
    fee_rate = max(0.0, payload.fee_bps) / 10000
    history = candles(symbol, interval=payload.interval, limit=limit)
    positive_news = [event for event in news_events(symbol, limit=20) if event.sentiment == "bullish" and event.impact_score >= 55]
    has_recent_positive_news = bool(positive_news)

    equity = initial_capital
    equity_curve = [round(equity, 2)]
    returns: list[float] = []
    trade_returns: list[float] = []
    signal_points: list[dict[str, str | float]] = []
    position = 0
    entry_equity = equity
    bars_in_position = 0

    for index in range(1, len(history)):
        desired = backtest_position(payload.factor_id, history, index - 1, has_recent_positive_news)
        if desired != position:
            equity *= 1 - fee_rate
            signal_points.append(
                {
                    "time": history[index].time,
                    "action": "buy" if desired else "sell",
                    "price": history[index].open,
                    "equity": round(equity, 2),
                }
            )
            if position and not desired:
                trade_returns.append(equity / entry_equity - 1)
            if desired and not position:
                entry_equity = equity
            position = desired
        if position:
            daily_return = history[index].close / history[index - 1].close - 1
            equity *= 1 + daily_return
            bars_in_position += 1
            returns.append(daily_return)
        else:
            returns.append(0)
        equity_curve.append(round(equity, 2))

    if position:
        trade_returns.append(equity / entry_equity - 1)

    peak = equity_curve[0]
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = (peak - value) / peak if peak else 0
        max_drawdown = max(max_drawdown, drawdown)

    avg_return = sum(returns) / len(returns) if returns else 0.0
    variance = sum((item - avg_return) ** 2 for item in returns) / len(returns) if returns else 0.0
    sharpe = (avg_return / sqrt(variance) * sqrt(252)) if variance else 0.0
    win_rate = sum(item > 0 for item in trade_returns) / len(trade_returns) * 100 if trade_returns else 0.0
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = sha256(f"{symbol}:{payload.factor_id}:{payload.interval}:{created_at}".encode("utf-8")).hexdigest()[:20]
    result = BacktestRunResult(
        id=run_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        interval=payload.interval,
        initial_capital=round(initial_capital, 2),
        final_equity=round(equity, 2),
        total_return=round((equity / initial_capital - 1) * 100, 2),
        max_drawdown=round(max_drawdown * 100, 2),
        sharpe=round(sharpe, 2),
        win_rate=round(win_rate, 1),
        trades=len(trade_returns),
        exposure=round(bars_in_position / max(1, len(history) - 1) * 100, 1),
        created_at=created_at,
        equity_curve=equity_curve,
        signal_points=signal_points[-40:],
    )
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO backtest_runs (
                id, symbol, factor_id, factor_name, interval, initial_capital,
                final_equity, total_return, max_drawdown, sharpe, win_rate,
                trades, exposure, created_at, equity_curve, signal_points, request_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.symbol,
                result.factor_id,
                result.factor_name,
                result.interval,
                result.initial_capital,
                result.final_equity,
                result.total_return,
                result.max_drawdown,
                result.sharpe,
                result.win_rate,
                result.trades,
                result.exposure,
                result.created_at,
                json.dumps(result.equity_curve),
                json.dumps(result.signal_points),
                payload.model_dump_json(),
            ),
        )
    return result


def list_backtest_runs(symbol: str, limit: int = 10) -> list[BacktestRunResult]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM backtest_runs
            WHERE symbol = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (symbol.upper(), max(1, min(50, limit))),
        ).fetchall()
    return [row_to_backtest(row) for row in rows]


def backtest_summaries(symbol: str) -> list[BacktestSummary]:
    return [
        BacktestSummary(
            id=item.id,
            name=f"{item.symbol} · {item.factor_name}",
            symbol=item.symbol,
            factor=item.factor_name,
            total_return=item.total_return,
            max_drawdown=item.max_drawdown,
            sharpe=item.sharpe,
            win_rate=item.win_rate,
            trades=item.trades,
            status="completed",
            equity_curve=item.equity_curve,
        )
        for item in list_backtest_runs(symbol, limit=3)
    ]


def curve_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = (peak - value) / peak if peak else 0.0
        max_drawdown = max(max_drawdown, drawdown)
    return round(max_drawdown * 100, 2)


def annualized_sharpe(returns: list[float]) -> float:
    if not returns:
        return 0.0
    avg_return = sum(returns) / len(returns)
    variance = sum((item - avg_return) ** 2 for item in returns) / len(returns)
    return round((avg_return / sqrt(variance) * sqrt(252)) if variance else 0.0, 2)


def buy_hold_benchmark(history: list[Candle], initial_capital: float) -> tuple[list[float], list[float]]:
    if not history:
        return [round(initial_capital, 2)], []
    equity = initial_capital
    curve = [round(equity, 2)]
    returns: list[float] = []
    for index in range(1, len(history)):
        bar_return = history[index].close / history[index - 1].close - 1
        equity *= 1 + bar_return
        returns.append(bar_return)
        curve.append(round(equity, 2))
    return curve, returns


def backtest_benchmark_report(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 180,
) -> BacktestBenchmarkReport:
    base_symbol = symbol.upper()
    factor = get_factor_definition(factor_id, require_enabled=True)
    latest = latest_backtest_for_factor(base_symbol, factor_id)
    if latest is None or latest.interval != interval:
        latest = run_backtest(
            BacktestRunRequest(
                symbol=base_symbol,
                factor_id=factor_id,
                interval=interval,
                limit=limit,
            )
        )
    history = candles(base_symbol, interval=interval, limit=max(60, min(240, limit)))
    benchmark_curve, benchmark_returns = buy_hold_benchmark(history, latest.initial_capital)
    benchmark_return = round((benchmark_curve[-1] / benchmark_curve[0] - 1) * 100, 2) if benchmark_curve and benchmark_curve[0] else 0.0
    benchmark_drawdown = curve_drawdown(benchmark_curve)
    benchmark_sharpe = annualized_sharpe(benchmark_returns)
    alpha = round(latest.total_return - benchmark_return, 2)
    drawdown_delta = round(latest.max_drawdown - benchmark_drawdown, 2)
    sharpe_delta = round(latest.sharpe - benchmark_sharpe, 2)
    checks = [
        f"策略收益 {latest.total_return:+.2f}%，买入持有 {benchmark_return:+.2f}%，alpha {alpha:+.2f}pp。",
        f"策略回撤 {latest.max_drawdown:.2f}%，基准回撤 {benchmark_drawdown:.2f}%，差值 {drawdown_delta:+.2f}pp。",
        f"策略 Sharpe {latest.sharpe:.2f}，基准 Sharpe {benchmark_sharpe:.2f}，差值 {sharpe_delta:+.2f}。",
    ]
    actions: list[str] = []
    if alpha >= 3 and sharpe_delta >= 0 and drawdown_delta <= 6:
        status: Literal["pass", "watch", "fail"] = "pass"
        actions.append("相对基准通过：可继续结合前向和压力测试评估。")
    elif alpha > -2 and drawdown_delta <= 12:
        status = "watch"
        actions.append("相对基准边际不足：检查过滤器、持仓暴露和交易成本。")
    else:
        status = "fail"
        actions.append("未跑赢基准或回撤劣化明显：暂不扩大模拟权重。")
    if latest.exposure > 85 and alpha <= 3:
        actions.append("策略暴露接近满仓但 alpha 不高，可能只是跟随基准。")
    summary = f"{base_symbol} · {factor.name} 基准对照 {status}：alpha {alpha:+.2f}pp，回撤差 {drawdown_delta:+.2f}pp。"
    return BacktestBenchmarkReport(
        symbol=base_symbol,
        factor_id=factor.id,
        factor_name=factor.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        interval=interval,
        strategy_return=latest.total_return,
        benchmark_return=benchmark_return,
        alpha=alpha,
        strategy_drawdown=latest.max_drawdown,
        benchmark_drawdown=benchmark_drawdown,
        drawdown_delta=drawdown_delta,
        strategy_sharpe=latest.sharpe,
        benchmark_sharpe=benchmark_sharpe,
        sharpe_delta=sharpe_delta,
        trades=latest.trades,
        exposure=latest.exposure,
        summary=summary,
        checks=checks,
        actions=dedupe(actions),
    )
