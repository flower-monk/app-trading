"""Research domain: experiments implementation."""

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
    BacktestForwardConsistencyReport,
    BacktestRunRequest,
    BacktestRunResult,
    ConsistencyCheck,
    FactorDefinition,
    ForwardLeaderboard,
    ForwardLeaderboardItem,
    ForwardTestRequest,
    ForwardTestResult,
    Literal,
    MarketRegime,
    ParameterOptimizationReport,
    ParameterOptimizationRequest,
    ParameterOptimizationResult,
    RobustnessFactorReport,
    RobustnessReport,
    StrategyBacktestMonitorItem,
    StrategyBacktestMonitorReport,
    StrategyDriftFactor,
    StrategyDriftReport,
    StrategyForwardMonitorItem,
    StrategyForwardMonitorReport,
    StrategyRevalidationPlanItem,
    StrategyRevalidationPlanReport,
    StrategySensitivityItem,
    StrategySensitivityReport,
    StrategyValidationCoverageItem,
    StrategyValidationCoverageReport,
    StressLeaderboard,
    StressLeaderboardItem,
    StressScenarioResult,
    StressTestRequest,
    StressTestResult,
    WalkForwardLeaderboard,
    WalkForwardLeaderboardItem,
    WalkForwardRequest,
    WalkForwardResult,
    WalkForwardWindowResult,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
candles = service_call('app.modules.market.service', 'get_market_candles')
news_events = service_call('app.modules.news.service', 'list_news_events')
from app.modules.market.service import MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
dedupe = service_call('app.modules.market.service', 'dedupe')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_strategy_templates = service_call('app.modules.market.service', 'list_strategy_templates')
list_supported_market_symbols = service_call('app.modules.market.service', 'list_supported_market_symbols')
row_to_backtest = service_call('app.modules.trading.service', 'row_to_backtest')

# Deferred internal references keep research submodules independently importable.
get_factor_definition = service_call('app.modules.research.factors', 'get_factor_definition')
iso_age_days = service_call('app.modules.research.strategy', 'iso_age_days')
lightweight_market_regime_report = service_call('app.modules.research.catalog', 'lightweight_market_regime_report')
list_factor_definitions = service_call('app.modules.research.factors', 'list_factor_definitions')
market_regime_report = service_call('app.modules.research.catalog', 'market_regime_report')
run_backtest = service_call('app.modules.research.backtesting', 'run_backtest')
simulate_factor_window = service_call('app.modules.research.factors', 'simulate_factor_window')
simulate_parameter_window = service_call('app.modules.research.factors', 'simulate_parameter_window')
strategy_evidence_pack_report = service_call('app.modules.research.strategy', 'strategy_evidence_pack_report')
strategy_lifecycle_report = service_call('app.modules.research.strategy', 'strategy_lifecycle_report')
strategy_regime_matrix_report = service_call('app.modules.research.strategy', 'strategy_regime_matrix_report')


def parameter_optimization_report(payload: ParameterOptimizationRequest) -> ParameterOptimizationReport:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    history = candles(symbol, interval=payload.interval, limit=max(100, min(320, payload.limit)))
    initial_capital = max(100.0, payload.initial_capital)
    fee_rate = max(0.0, payload.fee_bps) / 10000
    has_recent_positive_news = any(event.sentiment == "bullish" and event.impact_score >= 55 for event in news_events(symbol, limit=20))
    candidates: list[ParameterOptimizationResult] = []
    for min_rsi in [35, 45, 55, 65]:
        for min_trend in [35, 50, 65, 80]:
            total_return, drawdown, trades, exposure = simulate_parameter_window(
                payload.factor_id,
                history,
                min_rsi,
                min_trend,
                initial_capital,
                fee_rate,
                has_recent_positive_news,
            )
            score = int(max(0, min(100, 50 + total_return * 1.4 - drawdown * 1.8 + min(12, trades * 2) - max(0, exposure - 70) * 0.5)))
            if total_return > 0 and drawdown <= 14 and trades >= 1:
                status: Literal["pass", "watch", "fail"] = "pass"
            elif total_return > -3 and drawdown <= 22:
                status = "watch"
            else:
                status = "fail"
            candidates.append(
                ParameterOptimizationResult(
                    rank=0,
                    min_rsi=float(min_rsi),
                    min_trend=min_trend,
                    total_return=total_return,
                    max_drawdown=drawdown,
                    trades=trades,
                    exposure_rate=exposure,
                    score=score,
                    status=status,
                )
            )
    ranked = sorted(candidates, key=lambda item: (item.score, item.total_return, -item.max_drawdown), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked)]
    best = ranked[0] if ranked else None
    pass_count = sum(item.status == "pass" for item in ranked)
    fail_count = sum(item.status == "fail" for item in ranked)
    if best and best.status == "pass" and pass_count >= 2:
        report_status: Literal["pass", "watch", "fail"] = "pass"
    elif best and best.status in {"pass", "watch"}:
        report_status = "watch"
    else:
        report_status = "fail"
    actions: list[str] = []
    if best:
        actions.append(f"优先验证 RSI >= {best.min_rsi:.0f}、趋势 >= {best.min_trend} 的参数组合。")
    if fail_count >= len(ranked) / 2:
        actions.append("多数参数组合失败，先复核因子逻辑或市场状态适配。")
    if best and best.exposure_rate >= 75:
        actions.append("最佳组合暴露率偏高，增加退出条件或仓位上限。")
    if not actions:
        actions.append("将前两名参数组合纳入前向和滚动样本外复核。")
    generated_at = datetime.now(timezone.utc).isoformat()
    report_id = sha256(f"parameter-optimization:{symbol}:{payload.factor_id}:{payload.interval}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    return ParameterOptimizationReport(
        id=report_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        generated_at=generated_at,
        interval=payload.interval,
        status=report_status,
        summary=f"{symbol} · {factor.name} 参数优化：{pass_count} 组通过，最佳分数 {best.score if best else 0}/100。",
        best=best,
        results=ranked[:8],
        actions=dedupe(actions),
    )


def row_to_forward_test(row: sqlite3.Row) -> ForwardTestResult:
    return ForwardTestResult(
        id=row["id"],
        symbol=row["symbol"],
        factor_id=row["factor_id"],
        factor_name=row["factor_name"],
        interval=row["interval"],
        train_bars=row["train_bars"],
        forward_bars=row["forward_bars"],
        train_return=row["train_return"],
        forward_return=row["forward_return"],
        degradation=row["degradation"],
        max_drawdown=row["max_drawdown"],
        pass_fail=row["pass_fail"],
        generated_at=row["generated_at"],
        equity_curve=json.loads(row["equity_curve"]),
        checks=json.loads(row["checks"]),
    )


def run_forward_test(payload: ForwardTestRequest) -> ForwardTestResult:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    train_bars = max(80, min(200, payload.train_bars))
    forward_bars = max(20, min(80, payload.forward_bars))
    total_bars = min(240, train_bars + forward_bars)
    history = candles(symbol, interval=payload.interval, limit=total_bars)
    split_index = max(40, len(history) - forward_bars)
    initial_capital = max(100.0, payload.initial_capital)
    fee_rate = max(0.0, payload.fee_bps) / 10000
    has_recent_positive_news = any(event.sentiment == "bullish" and event.impact_score >= 55 for event in news_events(symbol, limit=20))
    train_return, _, train_trades, _ = simulate_factor_window(
        payload.factor_id,
        history,
        1,
        split_index - 1,
        initial_capital,
        fee_rate,
        has_recent_positive_news,
    )
    forward_return, forward_drawdown, forward_trades, forward_equity = simulate_factor_window(
        payload.factor_id,
        history,
        split_index,
        len(history) - 1,
        initial_capital,
        fee_rate,
        has_recent_positive_news,
    )
    degradation = round(train_return - forward_return, 2)
    checks: list[str] = [
        f"训练段收益 {train_return:+.2f}%，前向段收益 {forward_return:+.2f}%。",
        f"训练交易 {train_trades} 笔，前向交易 {forward_trades} 笔。",
        f"前向最大回撤 {forward_drawdown:.2f}%。",
    ]
    if forward_return > 0 and forward_drawdown <= 12 and degradation <= max(8, abs(train_return) * 0.75):
        pass_fail: Literal["pass", "watch", "fail"] = "pass"
        checks.append("前向表现通过基础稳定性检查。")
    elif forward_return > -3 and forward_drawdown <= 18:
        pass_fail = "watch"
        checks.append("前向表现可观察，但需要扩大样本或降低仓位。")
    else:
        pass_fail = "fail"
        checks.append("前向测试未通过，暂不建议纳入自动化策略。")
    generated_at = datetime.now(timezone.utc).isoformat()
    run_id = sha256(f"forward:{symbol}:{payload.factor_id}:{payload.interval}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    result = ForwardTestResult(
        id=run_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        interval=payload.interval,
        train_bars=split_index,
        forward_bars=len(history) - split_index,
        train_return=train_return,
        forward_return=forward_return,
        degradation=degradation,
        max_drawdown=forward_drawdown,
        pass_fail=pass_fail,
        generated_at=generated_at,
        equity_curve=forward_equity,
        checks=checks,
    )
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO forward_test_runs (
                id, symbol, factor_id, factor_name, interval, train_bars, forward_bars,
                train_return, forward_return, degradation, max_drawdown, pass_fail,
                generated_at, equity_curve, checks, request_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.symbol,
                result.factor_id,
                result.factor_name,
                result.interval,
                result.train_bars,
                result.forward_bars,
                result.train_return,
                result.forward_return,
                result.degradation,
                result.max_drawdown,
                result.pass_fail,
                result.generated_at,
                json.dumps(result.equity_curve),
                json.dumps(result.checks, ensure_ascii=False),
                payload.model_dump_json(),
            ),
        )
    add_event_log(
        event_type="forward_test",
        severity="info" if result.pass_fail == "pass" else "watch" if result.pass_fail == "watch" else "risk",
        symbol=result.symbol,
        title="前向测试完成",
        message=f"{result.factor_name} 前向 {result.forward_return:+.2f}%，状态 {result.pass_fail}。",
    )
    return result


def list_forward_tests(symbol: str, limit: int = 10) -> list[ForwardTestResult]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM forward_test_runs
            WHERE symbol = ?
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (symbol.upper(), max(1, min(50, limit))),
        ).fetchall()
    return [row_to_forward_test(row) for row in rows]


def forward_leaderboard_report(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> ForwardLeaderboard:
    init_db()
    max_items = max(1, min(30, limit))
    clauses: list[str] = []
    params: list[str | int] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if factor_id:
        clauses.append("factor_id = ?")
        params.append(factor_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM forward_test_runs
            {where_sql}
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (*params, max_items * 6),
        ).fetchall()
    latest_by_pair: dict[tuple[str, str], ForwardTestResult] = {}
    for row in rows:
        result = row_to_forward_test(row)
        latest_by_pair.setdefault((result.symbol, result.factor_id), result)
    items: list[ForwardLeaderboardItem] = []
    for result in latest_by_pair.values():
        score = 50 + int(result.forward_return * 1.2) - int(max(0, result.degradation) * 0.8) - int(result.max_drawdown * 1.3)
        if result.pass_fail == "pass":
            score += 18
            recommendation = "优先保留"
            actions = ["继续滚动前向跟踪。"]
        elif result.pass_fail == "watch":
            score += 4
            recommendation = "观察验证"
            actions = ["扩大前向样本或降低模拟权重。"]
        else:
            score -= 20
            recommendation = "暂停新增"
            actions = ["回到过滤器和参数假设重新验证。"]
        if result.degradation > 15:
            actions.append("训练到前向衰减偏高，检查过拟合。")
        if result.max_drawdown > 18:
            actions.append("前向回撤偏大，加入风险阈值或压力测试复核。")
        items.append(
            ForwardLeaderboardItem(
                rank=0,
                symbol=result.symbol,
                factor_id=result.factor_id,
                factor_name=result.factor_name,
                status=result.pass_fail,
                score=max(0, min(100, score)),
                train_return=result.train_return,
                forward_return=result.forward_return,
                degradation=result.degradation,
                max_drawdown=result.max_drawdown,
                generated_at=result.generated_at,
                recommendation=recommendation,
                actions=dedupe(actions),
            )
        )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.forward_return), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[:max_items])]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    pass_count = sum(item.status == "pass" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    summary = f"前向排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    if not ranked:
        status = "watch"
        summary = "前向排行榜暂无记录，先运行前向测试。"
    return ForwardLeaderboard(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=summary,
        items=ranked,
    )


def strategy_backtest_monitor_report(limit: int = 12) -> StrategyBacktestMonitorReport:
    init_db()
    strategies = list_strategy_templates()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM backtest_runs
            ORDER BY created_at DESC
            LIMIT 300
            """
        ).fetchall()
    latest_by_pair: dict[tuple[str, str], BacktestRunResult] = {}
    for row in rows:
        result = row_to_backtest(row)
        latest_by_pair.setdefault((result.factor_id, result.symbol), result)

    items: list[StrategyBacktestMonitorItem] = []
    for strategy in strategies:
        factor_ids = set(strategy.factor_ids)
        relevant = [item for (factor_id, _symbol), item in latest_by_pair.items() if factor_id in factor_ids]
        tested_factor_ids = {item.factor_id for item in relevant}
        symbols = {item.symbol for item in relevant}
        positive_count = sum(item.total_return > 0 for item in relevant)
        latest_run_at = max((item.created_at for item in relevant), default=None)
        freshness_days = iso_age_days(latest_run_at)
        actions: list[str] = []

        if not strategy.enabled:
            status: Literal["pass", "watch", "fail"] = "fail"
            actions.append("策略已停用，回测观察暂停。")
        elif not factor_ids:
            status = "fail"
            actions.append("策略未绑定因子，无法生成回测观察。")
        elif not relevant:
            status = "fail"
            actions.append("缺少回测样本，先为绑定因子运行回测。")
        else:
            avg_return = sum(item.total_return for item in relevant) / len(relevant)
            avg_sharpe = sum(item.sharpe for item in relevant) / len(relevant)
            worst_drawdown = max(item.max_drawdown for item in relevant)
            if avg_return <= 0 or avg_sharpe < 0 or worst_drawdown > 25:
                status = "fail"
                actions.append("回测收益、Sharpe 或回撤未达标，暂停策略晋级。")
            elif len(tested_factor_ids) < len(factor_ids) or positive_count < len(relevant) or (freshness_days is not None and freshness_days > 14):
                status = "watch"
                if len(tested_factor_ids) < len(factor_ids):
                    actions.append("补齐未覆盖因子的回测。")
                if positive_count < len(relevant):
                    actions.append("存在非正收益回测样本，降低策略权重。")
                if freshness_days is not None and freshness_days > 14:
                    actions.append("回测样本超过 14 天，加入复验队列。")
            else:
                status = "pass"
                actions.append("回测观察通过，可进入前向和滚动验证。")

        average_return = round(sum(item.total_return for item in relevant) / len(relevant), 2) if relevant else None
        average_sharpe = round(sum(item.sharpe for item in relevant) / len(relevant), 2) if relevant else None
        average_win_rate = round(sum(item.win_rate for item in relevant) / len(relevant), 1) if relevant else None
        max_drawdown = round(max((item.max_drawdown for item in relevant), default=0.0), 2) if relevant else None
        coverage_score = int((len(tested_factor_ids) / max(1, len(factor_ids))) * 25)
        sample_score = min(20, len(relevant) * 4)
        return_score = int(max(-20, min(25, average_return or 0)))
        sharpe_score = int(max(-10, min(20, (average_sharpe or 0) * 10)))
        drawdown_penalty = int(max_drawdown or 0)
        stale_penalty = 8 if freshness_days is not None and freshness_days > 14 else 0
        disabled_penalty = 20 if not strategy.enabled else 0
        score = max(0, min(100, 45 + coverage_score + sample_score + return_score + sharpe_score - drawdown_penalty - stale_penalty - disabled_penalty))

        items.append(
            StrategyBacktestMonitorItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=score,
                factor_count=len(factor_ids),
                tested_factor_count=len(tested_factor_ids),
                symbol_count=len(symbols),
                run_count=len(relevant),
                positive_count=positive_count,
                average_return=average_return,
                average_sharpe=average_sharpe,
                average_win_rate=average_win_rate,
                max_drawdown=max_drawdown,
                latest_run_at=latest_run_at,
                freshness_days=freshness_days,
                actions=dedupe(actions),
            )
        )

    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.run_count), reverse=True)
    max_items = max(1, min(50, limit))
    ranked = ranked[:max_items]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先补齐或暂停失败策略，避免直接进入前向验证。")
    if watch_count:
        actions.append("观察策略需要扩展样本、刷新回测或降低权重。")
    if not actions:
        actions.append("策略回测观察健康，可继续前向验证。")
    return StrategyBacktestMonitorReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"策略回测观察：{sum(item.status == 'pass' for item in ranked)} 个通过，{watch_count} 个观察，{fail_count} 个失败或缺样本。",
        items=ranked,
        actions=dedupe(actions),
    )


def strategy_forward_monitor_report(limit: int = 12) -> StrategyForwardMonitorReport:
    init_db()
    strategies = list_strategy_templates()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM forward_test_runs
            ORDER BY generated_at DESC
            LIMIT 300
            """
        ).fetchall()
    latest_by_pair: dict[tuple[str, str], ForwardTestResult] = {}
    for row in rows:
        result = row_to_forward_test(row)
        latest_by_pair.setdefault((result.factor_id, result.symbol), result)

    items: list[StrategyForwardMonitorItem] = []
    for strategy in strategies:
        factor_ids = set(strategy.factor_ids)
        relevant = [item for (factor_id, _symbol), item in latest_by_pair.items() if factor_id in factor_ids]
        tested_factor_ids = {item.factor_id for item in relevant}
        symbols = {item.symbol for item in relevant}
        pass_count = sum(item.pass_fail == "pass" for item in relevant)
        watch_count = sum(item.pass_fail == "watch" for item in relevant)
        fail_count = sum(item.pass_fail == "fail" for item in relevant)
        latest_run_at = max((item.generated_at for item in relevant), default=None)
        freshness_days = iso_age_days(latest_run_at)
        actions: list[str] = []

        if not strategy.enabled:
            status: Literal["pass", "watch", "fail"] = "fail"
            actions.append("策略已停用，前向观察暂停。")
        elif not factor_ids:
            status = "fail"
            actions.append("策略未绑定因子，无法生成前向观察。")
        elif not relevant:
            status = "fail"
            actions.append("缺少前向测试样本，先为绑定因子运行前向测试。")
        elif fail_count:
            status = "fail"
            actions.append("存在失败的前向样本，暂停策略晋级并复核参数。")
        elif watch_count or len(tested_factor_ids) < len(factor_ids) or (freshness_days is not None and freshness_days > 7):
            status = "watch"
            if watch_count:
                actions.append("存在观察状态前向样本，降低模拟权重。")
            if len(tested_factor_ids) < len(factor_ids):
                actions.append("补齐未覆盖因子的前向测试。")
            if freshness_days is not None and freshness_days > 7:
                actions.append("前向样本超过 7 天，加入复验队列。")
        else:
            status = "pass"
            actions.append("前向观察通过，可继续小规模模拟和滚动复验。")

        average_forward_return = round(sum(item.forward_return for item in relevant) / len(relevant), 2) if relevant else None
        average_degradation = round(sum(item.degradation for item in relevant) / len(relevant), 2) if relevant else None
        max_drawdown = round(max((item.max_drawdown for item in relevant), default=0.0), 2) if relevant else None
        coverage_score = int((len(tested_factor_ids) / max(1, len(factor_ids))) * 35)
        pass_score = int((pass_count / max(1, len(relevant))) * 35) if relevant else 0
        return_score = int(max(-10, min(20, average_forward_return or 0)))
        drawdown_penalty = int(max_drawdown or 0)
        stale_penalty = 10 if freshness_days is not None and freshness_days > 7 else 0
        disabled_penalty = 20 if not strategy.enabled else 0
        score = max(0, min(100, 40 + coverage_score + pass_score + return_score - drawdown_penalty - stale_penalty - disabled_penalty))

        items.append(
            StrategyForwardMonitorItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=score,
                factor_count=len(factor_ids),
                tested_factor_count=len(tested_factor_ids),
                symbol_count=len(symbols),
                pass_count=pass_count,
                watch_count=watch_count,
                fail_count=fail_count,
                average_forward_return=average_forward_return,
                average_degradation=average_degradation,
                max_drawdown=max_drawdown,
                latest_run_at=latest_run_at,
                freshness_days=freshness_days,
                actions=dedupe(actions),
            )
        )

    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.tested_factor_count), reverse=True)
    max_items = max(1, min(50, limit))
    ranked = ranked[:max_items]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先处理失败或缺样本策略，避免直接进入模拟。")
    if watch_count:
        actions.append("观察策略补齐样本、降低权重并设置 7 天复验。")
    if not actions:
        actions.append("策略前向观察健康，继续滚动复验。")
    return StrategyForwardMonitorReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"策略前向观察：{sum(item.status == 'pass' for item in ranked)} 个通过，{watch_count} 个观察，{fail_count} 个失败或缺样本。",
        items=ranked,
        actions=dedupe(actions),
    )


def strategy_sensitivity_report(limit: int = 12) -> StrategySensitivityReport:
    init_db()
    strategies = list_strategy_templates()
    with db_connect() as connection:
        backtest_rows = connection.execute(
            """
            SELECT * FROM backtest_runs
            ORDER BY created_at DESC
            LIMIT 500
            """
        ).fetchall()
        forward_rows = connection.execute(
            """
            SELECT * FROM forward_test_runs
            ORDER BY generated_at DESC
            LIMIT 500
            """
        ).fetchall()
    backtests = [row_to_backtest(row) for row in backtest_rows]
    forwards = [row_to_forward_test(row) for row in forward_rows]
    items: list[StrategySensitivityItem] = []
    for strategy in strategies:
        factor_ids = set(strategy.factor_ids)
        relevant_backtests = [item for item in backtests if item.factor_id in factor_ids]
        relevant_forwards = [item for item in forwards if item.factor_id in factor_ids]
        tested_factor_ids = {item.factor_id for item in relevant_backtests} | {item.factor_id for item in relevant_forwards}
        symbols = {item.symbol for item in relevant_backtests} | {item.symbol for item in relevant_forwards}
        intervals = {item.interval for item in relevant_backtests} | {item.interval for item in relevant_forwards}
        window_variants = len({(item.train_bars, item.forward_bars, item.interval) for item in relevant_forwards})
        backtest_returns = [item.total_return for item in relevant_backtests]
        forward_degradations = [item.degradation for item in relevant_forwards]
        drawdowns = [item.max_drawdown for item in relevant_backtests] + [item.max_drawdown for item in relevant_forwards]
        positive_backtest_rate = round(sum(item.total_return > 0 for item in relevant_backtests) / len(relevant_backtests) * 100, 1) if relevant_backtests else 0.0
        forward_pass_rate = round(sum(item.pass_fail == "pass" for item in relevant_forwards) / len(relevant_forwards) * 100, 1) if relevant_forwards else 0.0
        return_dispersion = round(max(backtest_returns) - min(backtest_returns), 2) if len(backtest_returns) >= 2 else None
        degradation_dispersion = round(max(forward_degradations) - min(forward_degradations), 2) if len(forward_degradations) >= 2 else None
        worst_drawdown = round(max(drawdowns), 2) if drawdowns else None
        evidence: list[str] = []
        actions: list[str] = []
        score = 40

        if not strategy.enabled:
            score -= 20
            actions.append("策略已停用，敏感性观察暂停。")
        if not factor_ids:
            score -= 22
            actions.append("策略未绑定因子，无法计算敏感性。")
        coverage_ratio = len(tested_factor_ids) / max(1, len(factor_ids))
        score += int(coverage_ratio * 18)
        score += min(12, len(symbols) * 3)
        score += min(10, len(intervals) * 4)
        score += min(10, window_variants * 3)

        if relevant_backtests:
            score += int((positive_backtest_rate - 50) * 0.18)
            evidence.append(f"回测 {len(relevant_backtests)} 条，正收益率 {positive_backtest_rate:.1f}%。")
        else:
            score -= 16
            actions.append("补齐至少一个绑定因子的回测样本。")
        if relevant_forwards:
            score += int((forward_pass_rate - 40) * 0.22)
            evidence.append(f"前向 {len(relevant_forwards)} 条，通过率 {forward_pass_rate:.1f}%。")
        else:
            score -= 18
            actions.append("补齐前向测试，确认样本外稳定性。")
        if return_dispersion is not None:
            evidence.append(f"回测收益离散 {return_dispersion:.2f}pp。")
            if return_dispersion > 35:
                score -= 16
                actions.append("回测收益离散过大，检查是否依赖单一标的或区间。")
            elif return_dispersion > 18:
                score -= 7
                actions.append("回测收益有明显离散，扩大样本后再提高权重。")
        if degradation_dispersion is not None:
            evidence.append(f"前向衰减离散 {degradation_dispersion:.2f}pp。")
            if degradation_dispersion > 25:
                score -= 14
                actions.append("前向衰减离散偏高，复核参数稳定性。")
        if worst_drawdown is not None:
            evidence.append(f"最差回撤 {worst_drawdown:.2f}%。")
            if worst_drawdown > 25:
                score -= 16
                actions.append("最差回撤偏高，加入仓位或退出约束。")
            elif worst_drawdown > 16:
                score -= 7
                actions.append("回撤需要观察，敏感性通过前不扩大模拟。")
        if len(symbols) < 2:
            score -= 8
            actions.append("覆盖标的少于 2 个，样本外证据不足。")
        if len(intervals) < 2:
            actions.append("建议补充不同周期的回测或前向测试。")
        if window_variants < 2:
            actions.append("建议补充不同训练/前向窗口组合。")

        clipped_score = max(0, min(100, score))
        if clipped_score >= 75 and forward_pass_rate >= 60 and len(symbols) >= 2:
            status: Literal["pass", "watch", "fail"] = "pass"
        elif clipped_score >= 45 and (relevant_backtests or relevant_forwards):
            status = "watch"
        else:
            status = "fail"
        if not actions:
            actions.append("敏感性覆盖可用，按复验周期刷新不同样本。")
        items.append(
            StrategySensitivityItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=clipped_score,
                factor_count=len(factor_ids),
                tested_factor_count=len(tested_factor_ids),
                symbol_count=len(symbols),
                interval_count=len(intervals),
                backtest_count=len(relevant_backtests),
                forward_count=len(relevant_forwards),
                positive_backtest_rate=positive_backtest_rate,
                forward_pass_rate=forward_pass_rate,
                return_dispersion=return_dispersion,
                degradation_dispersion=degradation_dispersion,
                worst_drawdown=worst_drawdown,
                window_variants=window_variants,
                evidence=dedupe(evidence)[:5],
                actions=dedupe(actions)[:5],
            )
        )

    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score), reverse=True)[: max(1, min(50, limit))]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    score = int(round(sum(item.score for item in ranked) / max(1, len(ranked)))) if ranked else 0
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先补齐 fail 策略的回测、前向和窗口敏感性样本。")
    if watch_count:
        actions.append("watch 策略只保留观察权重，等待更多样本确认。")
    if not actions:
        actions.append("策略敏感性整体可控，继续滚动刷新不同样本。")
    return StrategySensitivityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"策略敏感性 {score}/100：{sum(item.status == 'pass' for item in ranked)} 个通过，{watch_count} 个观察，{fail_count} 个失败。",
        items=ranked,
        actions=dedupe(actions),
    )


def strategy_validation_coverage_report(limit: int = 12) -> StrategyValidationCoverageReport:
    init_db()
    strategies = list_strategy_templates()
    table_specs = {
        "backtest": ("backtest_runs", "created_at"),
        "forward": ("forward_test_runs", "generated_at"),
        "walk-forward": ("walk_forward_runs", "generated_at"),
        "stress": ("stress_test_runs", "generated_at"),
    }
    runs_by_stage: dict[str, list[sqlite3.Row]] = {}
    with db_connect() as connection:
        for stage, (table, timestamp_column) in table_specs.items():
            runs_by_stage[stage] = connection.execute(
                f"SELECT factor_id, symbol, {timestamp_column} AS generated_at FROM {table} ORDER BY {timestamp_column} DESC"
            ).fetchall()

    items: list[StrategyValidationCoverageItem] = []
    for strategy in strategies:
        factor_ids = set(strategy.factor_ids)
        relevant_by_stage = {
            stage: [row for row in rows if row["factor_id"] in factor_ids]
            for stage, rows in runs_by_stage.items()
        }
        factors_by_stage = {
            stage: {row["factor_id"] for row in rows}
            for stage, rows in relevant_by_stage.items()
        }
        symbols = {
            row["symbol"]
            for rows in relevant_by_stage.values()
            for row in rows
        }
        fully_validated = set.intersection(*factors_by_stage.values()) if factors_by_stage else set()
        missing_tasks: list[str] = []
        for factor_id in sorted(factor_ids):
            for stage in table_specs:
                if factor_id not in factors_by_stage[stage]:
                    missing_tasks.append(f"{stage}:{factor_id}")

        stage_coverage = [len(factors_by_stage[stage]) / max(1, len(factor_ids)) for stage in table_specs]
        evidence_score = int(round(sum(stage_coverage) / len(stage_coverage) * 80)) if stage_coverage else 0
        symbol_score = min(20, len(symbols) * 10)
        score = evidence_score + symbol_score
        if not strategy.enabled:
            score -= 20
        if not factor_ids:
            score = 0
        score = max(0, min(100, score))

        actions: list[str] = []
        evidence = [
            f"回测 {len(factors_by_stage['backtest'])}/{len(factor_ids)} 因子，{len(relevant_by_stage['backtest'])} 条。",
            f"前向 {len(factors_by_stage['forward'])}/{len(factor_ids)} 因子，{len(relevant_by_stage['forward'])} 条。",
            f"滚动样本外 {len(factors_by_stage['walk-forward'])}/{len(factor_ids)} 因子，{len(relevant_by_stage['walk-forward'])} 条。",
            f"压力测试 {len(factors_by_stage['stress'])}/{len(factor_ids)} 因子，{len(relevant_by_stage['stress'])} 条。",
        ]
        if not strategy.enabled:
            actions.append("策略已停用，仅保留历史验证证据。")
        if not factor_ids:
            actions.append("先为策略绑定至少一个因子。")
        if len(symbols) < 2:
            actions.append("将验证扩展到至少 2 个标的，降低单标的依赖。")
        for stage, label in (
            ("backtest", "回测"),
            ("forward", "前向测试"),
            ("walk-forward", "滚动样本外"),
            ("stress", "压力测试"),
        ):
            missing_count = len(factor_ids - factors_by_stage[stage])
            if missing_count:
                actions.append(f"补齐 {missing_count} 个因子的{label}证据。")

        complete = bool(factor_ids) and len(fully_validated) == len(factor_ids) and len(symbols) >= 2
        has_baseline = bool(factor_ids) and factors_by_stage["backtest"] == factor_ids and bool(factors_by_stage["forward"])
        if strategy.enabled and complete:
            status: Literal["pass", "watch", "fail"] = "pass"
        elif strategy.enabled and has_baseline and score >= 40:
            status = "watch"
        else:
            status = "fail"
        if not actions:
            actions.append("验证覆盖完整，按复验周期刷新四类证据。")

        items.append(
            StrategyValidationCoverageItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=score,
                factor_count=len(factor_ids),
                symbol_count=len(symbols),
                backtest_factor_count=len(factors_by_stage["backtest"]),
                forward_factor_count=len(factors_by_stage["forward"]),
                walk_forward_factor_count=len(factors_by_stage["walk-forward"]),
                stress_factor_count=len(factors_by_stage["stress"]),
                fully_validated_factor_count=len(fully_validated),
                backtest_run_count=len(relevant_by_stage["backtest"]),
                forward_run_count=len(relevant_by_stage["forward"]),
                walk_forward_run_count=len(relevant_by_stage["walk-forward"]),
                stress_run_count=len(relevant_by_stage["stress"]),
                missing_tasks=missing_tasks[:20],
                evidence=evidence,
                actions=dedupe(actions)[:6],
            )
        )

    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.fully_validated_factor_count), reverse=True)
    ranked = ranked[: max(1, min(50, limit))]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    score = int(round(sum(item.score for item in ranked) / max(1, len(ranked)))) if ranked else 0
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("先处理缺少回测基线或前向证据的策略。")
    if watch_count:
        actions.append("为观察策略补齐滚动样本外、压力测试和跨标的覆盖。")
    if not actions:
        actions.append("策略验证覆盖完整，维持定期复验。")
    return StrategyValidationCoverageReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"策略验证覆盖 {score}/100：{sum(item.status == 'pass' for item in ranked)} 个完整，{watch_count} 个待补强，{fail_count} 个缺基线。",
        items=ranked,
        actions=dedupe(actions),
    )


def strategy_revalidation_plan_report(limit: int = 12) -> StrategyRevalidationPlanReport:
    regime = lightweight_market_regime_report()
    matrix = strategy_regime_matrix_report(current=regime)
    sensitivity = {item.strategy_id: item for item in strategy_sensitivity_report(limit=50).items}
    evidence = {item.strategy_id: item for item in strategy_evidence_pack_report().items}
    lifecycle = {item.strategy_id: item for item in strategy_lifecycle_report().items}
    items: list[StrategyRevalidationPlanItem] = []
    for matrix_item in matrix.items:
        current_cell = next((cell for cell in matrix_item.cells if cell.regime_label == regime.label), None)
        sensitivity_item = sensitivity.get(matrix_item.strategy_id)
        evidence_item = evidence.get(matrix_item.strategy_id)
        lifecycle_item = lifecycle.get(matrix_item.strategy_id)
        due_tasks: list[str] = []
        reasons: list[str] = []
        actions: list[str] = []
        priority = 0

        regime_decision: Literal["open", "reduced", "paused", "blocked", "missing"] = current_cell.decision if current_cell else "missing"
        if regime.risk_level in {"watch", "risk"}:
            priority += 18 if regime.risk_level == "watch" else 28
            reasons.append(f"当前市场状态为 {regime.risk_level}。")
            due_tasks.append("regime-check")
        if regime_decision in {"reduced", "paused"}:
            priority += 18
            reasons.append(f"状态矩阵要求 {regime_decision}。")
            due_tasks.append("forward")
        elif regime_decision == "blocked":
            priority += 32
            reasons.append("状态矩阵阻断该策略。")
            due_tasks.extend(["forward", "walk-forward", "stress"])
        elif regime_decision == "missing":
            priority += 16
            reasons.append("缺少当前状态矩阵结论。")
            due_tasks.append("regime-check")

        sensitivity_status: Literal["pass", "watch", "fail", "missing"] = sensitivity_item.status if sensitivity_item else "missing"
        if sensitivity_item is None:
            priority += 20
            reasons.append("缺少策略敏感性报告。")
            due_tasks.extend(["backtest", "forward"])
        elif sensitivity_item.status == "fail":
            priority += 26
            reasons.append("策略敏感性失败。")
            due_tasks.extend(["backtest", "forward", "walk-forward"])
        elif sensitivity_item.status == "watch":
            priority += 12
            reasons.append("策略敏感性观察。")
            due_tasks.append("sensitivity")
        if sensitivity_item and sensitivity_item.symbol_count < 2:
            priority += 8
            reasons.append("敏感性标的覆盖不足。")
            due_tasks.append("backtest")
        if sensitivity_item and sensitivity_item.window_variants < 2:
            priority += 8
            reasons.append("训练/前向窗口组合不足。")
            due_tasks.append("forward")

        evidence_status: Literal["pass", "watch", "fail", "missing"] = evidence_item.status if evidence_item else "missing"
        if evidence_item is None:
            priority += 20
            reasons.append("缺少策略证据包。")
            due_tasks.append("evidence-pack")
        elif evidence_item.status == "fail":
            priority += 28
            reasons.extend(evidence_item.gaps[:2])
            due_tasks.extend(["strategy-validation", "risk-review"])
        elif evidence_item.status == "watch":
            priority += 12
            reasons.extend(evidence_item.gaps[:1])
            due_tasks.append("strategy-validation")

        lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"] = lifecycle_item.stage if lifecycle_item else "missing"
        if lifecycle_stage in {"draft", "validation"}:
            priority += 10
            reasons.append(f"生命周期仍为 {lifecycle_stage}。")
            due_tasks.append("promotion-gate")
        elif lifecycle_stage == "paused":
            priority += 16
            reasons.append("策略处于 paused。")
            due_tasks.append("risk-review")

        if not due_tasks:
            priority += 4
            reasons.append("当前无需强制复验，保持滚动观察。")
            actions.append("按常规周期刷新策略验证。")
        else:
            actions.append("按优先级执行复验任务，完成后刷新操作计划。")
        if "forward" in due_tasks:
            actions.append("优先补前向测试，确认样本外表现。")
        if "walk-forward" in due_tasks:
            actions.append("增加滚动样本外验证，检查窗口稳定性。")
        if "stress" in due_tasks:
            actions.append("补压力测试，确认极端状态下回撤。")

        clipped_priority = max(0, min(100, priority))
        if clipped_priority >= 65 or evidence_status == "fail" or regime_decision == "blocked":
            status: Literal["pass", "watch", "fail"] = "fail"
        elif clipped_priority >= 25 or due_tasks:
            status = "watch"
        else:
            status = "pass"
        items.append(
            StrategyRevalidationPlanItem(
                rank=0,
                strategy_id=matrix_item.strategy_id,
                strategy_name=matrix_item.strategy_name,
                category=matrix_item.category,
                status=status,
                priority=clipped_priority,
                current_regime=regime.label,
                regime_decision=regime_decision,
                sensitivity_status=sensitivity_status,
                evidence_status=evidence_status,
                lifecycle_stage=lifecycle_stage,
                due_tasks=dedupe(due_tasks),
                reasons=dedupe(reasons)[:5],
                actions=dedupe(actions)[:5],
            )
        )

    ranked = sorted(items, key=lambda item: item.priority, reverse=True)[: max(1, min(50, limit))]
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked)]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    score = max(0, 100 - int(round(sum(item.priority for item in ranked) / max(1, len(ranked)))))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先处理 fail 复验项，再允许策略进入模拟扩容。")
    if watch_count:
        actions.append("watch 复验项保留观察权重，完成前不扩大自动化。")
    if not actions:
        actions.append("当前状态下策略复验压力较低，保持常规复验周期。")
    return StrategyRevalidationPlanReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        current_regime=regime,
        summary=f"策略状态复验计划 {score}/100：{len(ranked)} 个策略中 {fail_count} 个高优先级，{watch_count} 个观察。",
        items=ranked,
        actions=dedupe(actions),
    )


def classify_walk_forward_window(forward_return: float, max_drawdown: float, degradation: float) -> Literal["pass", "watch", "fail"]:
    if forward_return > 0 and max_drawdown <= 12 and degradation <= max(8, abs(forward_return) * 2):
        return "pass"
    if forward_return > -3 and max_drawdown <= 18:
        return "watch"
    return "fail"


def row_to_walk_forward(row: sqlite3.Row) -> WalkForwardResult:
    return WalkForwardResult(
        id=row["id"],
        symbol=row["symbol"],
        factor_id=row["factor_id"],
        factor_name=row["factor_name"],
        interval=row["interval"],
        train_bars=row["train_bars"],
        forward_bars=row["forward_bars"],
        window_count=row["window_count"],
        pass_rate=row["pass_rate"],
        average_forward_return=row["average_forward_return"],
        worst_forward_return=row["worst_forward_return"],
        max_drawdown=row["max_drawdown"],
        stability_score=row["stability_score"],
        pass_fail=row["pass_fail"],
        generated_at=row["generated_at"],
        windows=[WalkForwardWindowResult(**item) for item in json.loads(row["windows"])],
        checks=json.loads(row["checks"]),
        actions=json.loads(row["actions"]),
    )


def run_walk_forward_test(payload: WalkForwardRequest) -> WalkForwardResult:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    train_bars = max(60, min(160, payload.train_bars))
    forward_bars = max(20, min(60, payload.forward_bars))
    window_count = max(3, min(8, payload.windows))
    history_limit = min(360, train_bars + forward_bars * window_count + 30)
    history = candles(symbol, interval=payload.interval, limit=history_limit)
    if len(history) < train_bars + forward_bars * window_count:
        window_count = max(2, (len(history) - train_bars) // forward_bars)
    if window_count < 2:
        raise HTTPException(status_code=400, detail="Not enough candles for walk-forward test")
    initial_capital = max(100.0, payload.initial_capital)
    fee_rate = max(0.0, payload.fee_bps) / 10000
    has_recent_positive_news = any(event.sentiment == "bullish" and event.impact_score >= 55 for event in news_events(symbol, limit=20))
    base_start = max(1, len(history) - (train_bars + forward_bars * window_count))
    windows: list[WalkForwardWindowResult] = []
    for window_index in range(window_count):
        train_start = base_start + window_index * forward_bars
        train_end = train_start + train_bars - 1
        forward_start = train_end + 1
        forward_end = min(len(history) - 1, forward_start + forward_bars - 1)
        train_return, _, _, _ = simulate_factor_window(
            payload.factor_id,
            history,
            train_start,
            train_end,
            initial_capital,
            fee_rate,
            has_recent_positive_news,
        )
        forward_return, forward_drawdown, forward_trades, _ = simulate_factor_window(
            payload.factor_id,
            history,
            forward_start,
            forward_end,
            initial_capital,
            fee_rate,
            has_recent_positive_news,
        )
        degradation = round(train_return - forward_return, 2)
        windows.append(
            WalkForwardWindowResult(
                window=window_index + 1,
                train_start=history[train_start].time,
                train_end=history[train_end].time,
                forward_start=history[forward_start].time,
                forward_end=history[forward_end].time,
                train_return=train_return,
                forward_return=forward_return,
                degradation=degradation,
                max_drawdown=forward_drawdown,
                trades=forward_trades,
                pass_fail=classify_walk_forward_window(forward_return, forward_drawdown, degradation),
            )
        )
    pass_count = sum(1 for item in windows if item.pass_fail == "pass")
    watch_count = sum(1 for item in windows if item.pass_fail == "watch")
    fail_count = sum(1 for item in windows if item.pass_fail == "fail")
    average_forward = round(sum(item.forward_return for item in windows) / len(windows), 2)
    worst_forward = min(item.forward_return for item in windows)
    max_drawdown = max(item.max_drawdown for item in windows)
    average_degradation = sum(max(0.0, item.degradation) for item in windows) / len(windows)
    pass_rate = round(pass_count / len(windows) * 100, 1)
    stability_score = int(max(0, min(100, pass_rate + watch_count * 8 - fail_count * 15 + average_forward * 2 - max_drawdown - average_degradation * 0.5)))
    if pass_rate >= 60 and average_forward > 0 and max_drawdown <= 15:
        pass_fail: Literal["pass", "watch", "fail"] = "pass"
    elif pass_rate >= 35 and average_forward > -3 and max_drawdown <= 22:
        pass_fail = "watch"
    else:
        pass_fail = "fail"
    checks = [
        f"滚动窗口 {len(windows)} 段，通过 {pass_count} 段，观察 {watch_count} 段，失败 {fail_count} 段。",
        f"样本外均值 {average_forward:+.2f}%，最差窗口 {worst_forward:+.2f}%，最大回撤 {max_drawdown:.2f}%。",
        f"稳定性评分 {stability_score}/100。",
    ]
    actions: list[str] = []
    if pass_fail == "pass":
        actions.append("可进入策略准入评估，但仍需结合当前市场状态和组合风险。")
    elif pass_fail == "watch":
        actions.append("降低策略权重，并检查失败窗口对应的市场状态或新闻冲击。")
        actions.append("扩大窗口数量或调整过滤器后再次验证。")
    else:
        actions.append("暂缓推广该因子，优先复核信号条件、费用假设和风险过滤器。")
        actions.append("将失败窗口加入压力测试场景。")
    generated_at = datetime.now(timezone.utc).isoformat()
    run_id = sha256(f"walk-forward:{symbol}:{payload.factor_id}:{payload.interval}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    result = WalkForwardResult(
        id=run_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        interval=payload.interval,
        train_bars=train_bars,
        forward_bars=forward_bars,
        window_count=len(windows),
        pass_rate=pass_rate,
        average_forward_return=average_forward,
        worst_forward_return=round(worst_forward, 2),
        max_drawdown=round(max_drawdown, 2),
        stability_score=stability_score,
        pass_fail=pass_fail,
        generated_at=generated_at,
        windows=windows,
        checks=checks,
        actions=actions,
    )
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO walk_forward_runs (
                id, symbol, factor_id, factor_name, interval, train_bars, forward_bars,
                window_count, pass_rate, average_forward_return, worst_forward_return,
                max_drawdown, stability_score, pass_fail, generated_at, windows, checks, actions, request_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.symbol,
                result.factor_id,
                result.factor_name,
                result.interval,
                result.train_bars,
                result.forward_bars,
                result.window_count,
                result.pass_rate,
                result.average_forward_return,
                result.worst_forward_return,
                result.max_drawdown,
                result.stability_score,
                result.pass_fail,
                result.generated_at,
                json.dumps([item.model_dump() for item in result.windows], ensure_ascii=False),
                json.dumps(result.checks, ensure_ascii=False),
                json.dumps(result.actions, ensure_ascii=False),
                payload.model_dump_json(),
            ),
        )
    add_event_log(
        event_type="walk_forward_test",
        severity="info" if result.pass_fail == "pass" else "watch" if result.pass_fail == "watch" else "risk",
        symbol=result.symbol,
        title="滚动样本外验证完成",
        message=f"{result.factor_name} 通过率 {result.pass_rate:.1f}%，状态 {result.pass_fail}。",
    )
    return result


def list_walk_forward_tests(symbol: str, limit: int = 10) -> list[WalkForwardResult]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM walk_forward_runs
            WHERE symbol = ?
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (symbol.upper(), max(1, min(50, limit))),
        ).fetchall()
    return [row_to_walk_forward(row) for row in rows]


def walk_forward_leaderboard_report(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> WalkForwardLeaderboard:
    init_db()
    max_items = max(1, min(30, limit))
    clauses: list[str] = []
    params: list[str | int] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if factor_id:
        clauses.append("factor_id = ?")
        params.append(factor_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM walk_forward_runs
            {where_sql}
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (*params, max_items * 6),
        ).fetchall()
    latest_by_pair: dict[tuple[str, str], WalkForwardResult] = {}
    for row in rows:
        result = row_to_walk_forward(row)
        latest_by_pair.setdefault((result.symbol, result.factor_id), result)
    items: list[WalkForwardLeaderboardItem] = []
    for result in latest_by_pair.values():
        score = int(
            result.stability_score * 0.55
            + result.pass_rate * 0.25
            + max(-20, min(20, result.average_forward_return * 2))
            - max(0, result.max_drawdown * 0.8)
            + max(-12, min(8, result.worst_forward_return))
        )
        if result.pass_fail == "pass":
            score += 12
            recommendation = "优先保留"
            actions = ["保持滚动窗口复验节奏。"]
        elif result.pass_fail == "watch":
            score += 2
            recommendation = "观察验证"
            actions = ["降低权重，并复核失败或观察窗口。"]
        else:
            score -= 18
            recommendation = "暂停推广"
            actions = ["回到过滤器、参数和市场状态切片重新验证。"]
        if result.pass_rate < 50:
            actions.append("通过率偏低，扩大样本或增加过滤条件。")
        if result.worst_forward_return < -5:
            actions.append("最差窗口亏损明显，加入压力测试场景。")
        items.append(
            WalkForwardLeaderboardItem(
                rank=0,
                symbol=result.symbol,
                factor_id=result.factor_id,
                factor_name=result.factor_name,
                status=result.pass_fail,
                score=max(0, min(100, score)),
                pass_rate=result.pass_rate,
                average_forward_return=result.average_forward_return,
                worst_forward_return=result.worst_forward_return,
                max_drawdown=result.max_drawdown,
                stability_score=result.stability_score,
                window_count=result.window_count,
                generated_at=result.generated_at,
                recommendation=recommendation,
                actions=dedupe(actions),
            )
        )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.pass_rate), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[:max_items])]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    pass_count = sum(item.status == "pass" for item in ranked)
    if not ranked:
        status: Literal["pass", "watch", "fail"] = "watch"
        summary = "滚动样本外排行榜暂无记录，先运行滚动验证。"
    elif fail_count:
        status = "fail"
        summary = f"滚动样本外排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    elif watch_count:
        status = "watch"
        summary = f"滚动样本外排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    else:
        status = "pass"
        summary = f"滚动样本外排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    return WalkForwardLeaderboard(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=summary,
        items=ranked,
    )


def row_to_stress_test(row: sqlite3.Row) -> StressTestResult:
    return StressTestResult(
        id=row["id"],
        symbol=row["symbol"],
        factor_id=row["factor_id"],
        factor_name=row["factor_name"],
        generated_at=row["generated_at"],
        base_return=row["base_return"],
        base_drawdown=row["base_drawdown"],
        worst_return=row["worst_return"],
        worst_drawdown=row["worst_drawdown"],
        pass_fail=row["pass_fail"],
        scenarios=[StressScenarioResult(**item) for item in json.loads(row["scenarios"])],
        actions=json.loads(row["actions"]),
    )


def classify_stress_result(stressed_return: float, stressed_drawdown: float) -> Literal["pass", "watch", "fail"]:
    if stressed_return >= -2 and stressed_drawdown <= 14:
        return "pass"
    if stressed_return >= -8 and stressed_drawdown <= 22:
        return "watch"
    return "fail"


def run_stress_test(payload: StressTestRequest) -> StressTestResult:
    init_db()
    base = run_backtest(
        BacktestRunRequest(
            symbol=payload.symbol,
            factor_id=payload.factor_id,
            interval=payload.interval,
            limit=180,
            initial_capital=payload.initial_capital,
            fee_bps=2,
        )
    )
    factor = get_factor_definition(payload.factor_id)
    regime = market_regime_report()
    scenarios_config = [
        ("风险偏好骤降", "收益 -8%，回撤 +8%", -8.0, 8.0, ["模拟 risk_off 下买盘撤退。"]),
        ("波动率放大", "收益 -5%，回撤 +12%", -5.0, 12.0, ["模拟波动率突然上升导致止损密集触发。"]),
        ("流动性收缩", "收益 -3%，回撤 +6%，费用加倍", -3.0, 6.0, ["模拟成交滑点扩大和低流动性环境。"]),
    ]
    if regime.risk_level in {"watch", "risk"}:
        scenarios_config.append(("当前状态延续", "按当前风险等级追加压力", -4.0 if regime.risk_level == "watch" else -10.0, 6.0 if regime.risk_level == "watch" else 14.0, [regime.summary]))

    scenarios: list[StressScenarioResult] = []
    for name, shock, return_shock, drawdown_shock, notes in scenarios_config:
        stressed_return = round(base.total_return + return_shock, 2)
        stressed_drawdown = round(base.max_drawdown + drawdown_shock, 2)
        status = classify_stress_result(stressed_return, stressed_drawdown)
        scenarios.append(
            StressScenarioResult(
                name=name,
                shock=shock,
                stressed_return=stressed_return,
                stressed_drawdown=stressed_drawdown,
                pass_fail=status,
                notes=notes,
            )
        )
    worst_return = min(item.stressed_return for item in scenarios)
    worst_drawdown = max(item.stressed_drawdown for item in scenarios)
    failures = sum(1 for item in scenarios if item.pass_fail == "fail")
    watches = sum(1 for item in scenarios if item.pass_fail == "watch")
    if failures:
        pass_fail: Literal["pass", "watch", "fail"] = "fail"
        actions = ["暂停自动化执行", "降低仓位或重新校准过滤器", "优先运行更长窗口前向测试"]
    elif watches:
        pass_fail = "watch"
        actions = ["仅进入观察模式", "提高预警频率", "等待市场状态改善后再扩大样本"]
    else:
        pass_fail = "pass"
        actions = ["可进入小规模模拟", "保持压力测试随市场状态更新", "继续监控前向测试衰减"]
    generated_at = datetime.now(timezone.utc).isoformat()
    result_id = sha256(f"stress:{base.symbol}:{base.factor_id}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    result = StressTestResult(
        id=result_id,
        symbol=base.symbol,
        factor_id=base.factor_id,
        factor_name=factor.name,
        generated_at=generated_at,
        base_return=base.total_return,
        base_drawdown=base.max_drawdown,
        worst_return=worst_return,
        worst_drawdown=worst_drawdown,
        pass_fail=pass_fail,
        scenarios=scenarios,
        actions=actions,
    )
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO stress_test_runs (
                id, symbol, factor_id, factor_name, generated_at, base_return, base_drawdown,
                worst_return, worst_drawdown, pass_fail, scenarios, actions, request_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.symbol,
                result.factor_id,
                result.factor_name,
                result.generated_at,
                result.base_return,
                result.base_drawdown,
                result.worst_return,
                result.worst_drawdown,
                result.pass_fail,
                json.dumps([item.model_dump() for item in result.scenarios], ensure_ascii=False),
                json.dumps(result.actions, ensure_ascii=False),
                payload.model_dump_json(),
            ),
        )
    add_event_log(
        event_type="stress_test",
        severity="info" if pass_fail == "pass" else "watch" if pass_fail == "watch" else "risk",
        symbol=result.symbol,
        title="压力测试完成",
        message=f"{result.factor_name} 最差收益 {result.worst_return:+.2f}%，状态 {pass_fail}。",
    )
    return result


def list_stress_tests(symbol: str, limit: int = 10) -> list[StressTestResult]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM stress_test_runs
            WHERE symbol = ?
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (symbol.upper(), max(1, min(50, limit))),
        ).fetchall()
    return [row_to_stress_test(row) for row in rows]


def stress_leaderboard_report(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> StressLeaderboard:
    init_db()
    max_items = max(1, min(30, limit))
    clauses: list[str] = []
    params: list[str | int] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if factor_id:
        clauses.append("factor_id = ?")
        params.append(factor_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM stress_test_runs
            {where_sql}
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (*params, max_items * 6),
        ).fetchall()
    latest_by_pair: dict[tuple[str, str], StressTestResult] = {}
    for row in rows:
        result = row_to_stress_test(row)
        latest_by_pair.setdefault((result.symbol, result.factor_id), result)
    items: list[StressLeaderboardItem] = []
    for result in latest_by_pair.values():
        pass_scenarios = sum(item.pass_fail == "pass" for item in result.scenarios)
        watch_scenarios = sum(item.pass_fail == "watch" for item in result.scenarios)
        fail_scenarios = sum(item.pass_fail == "fail" for item in result.scenarios)
        score = 55 + pass_scenarios * 8 + watch_scenarios * 2 - fail_scenarios * 20 + int(result.worst_return) - int(result.worst_drawdown * 1.2)
        if result.pass_fail == "pass":
            score += 14
            recommendation = "韧性较好"
            actions = ["维持压力测试随状态变化复验。"]
        elif result.pass_fail == "watch":
            score += 2
            recommendation = "限制权重"
            actions = ["仅小权重观察，等待压力场景改善。"]
        else:
            score -= 18
            recommendation = "暂停新增"
            actions = ["暂停新增模拟，优先处理失败压力场景。"]
        if result.worst_drawdown > 22:
            actions.append("压力回撤过大，降低止损和仓位上限。")
        if result.worst_return < -8:
            actions.append("最差收益偏弱，复核交易信号和市场状态过滤器。")
        items.append(
            StressLeaderboardItem(
                rank=0,
                symbol=result.symbol,
                factor_id=result.factor_id,
                factor_name=result.factor_name,
                status=result.pass_fail,
                score=max(0, min(100, score)),
                base_return=result.base_return,
                worst_return=result.worst_return,
                worst_drawdown=result.worst_drawdown,
                scenario_count=len(result.scenarios),
                pass_scenarios=pass_scenarios,
                generated_at=result.generated_at,
                recommendation=recommendation,
                actions=dedupe(actions),
            )
        )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.worst_return), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[:max_items])]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    pass_count = sum(item.status == "pass" for item in ranked)
    if not ranked:
        status: Literal["pass", "watch", "fail"] = "watch"
        summary = "压力测试排行榜暂无记录，先运行压力测试。"
    elif fail_count:
        status = "fail"
        summary = f"压力测试排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    elif watch_count:
        status = "watch"
        summary = f"压力测试排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    else:
        status = "pass"
        summary = f"压力测试排行榜：{pass_count} 个通过，{watch_count} 个观察，{fail_count} 个失败。"
    return StressLeaderboard(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=summary,
        items=ranked,
    )


def latest_backtest_for_factor(symbol: str, factor_id: str) -> BacktestRunResult | None:
    init_db()
    with db_connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM backtest_runs
            WHERE symbol = ? AND factor_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol.upper(), factor_id),
        ).fetchone()
    return row_to_backtest(row) if row else None


def latest_forward_for_factor(symbol: str, factor_id: str) -> ForwardTestResult | None:
    init_db()
    with db_connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM forward_test_runs
            WHERE symbol = ? AND factor_id = ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (symbol.upper(), factor_id),
        ).fetchone()
    return row_to_forward_test(row) if row else None


def latest_walk_forward_for_factor(symbol: str, factor_id: str) -> WalkForwardResult | None:
    init_db()
    with db_connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM walk_forward_runs
            WHERE symbol = ? AND factor_id = ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (symbol.upper(), factor_id),
        ).fetchone()
    return row_to_walk_forward(row) if row else None


def latest_stress_for_factor(symbol: str, factor_id: str) -> StressTestResult | None:
    init_db()
    with db_connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM stress_test_runs
            WHERE symbol = ? AND factor_id = ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (symbol.upper(), factor_id),
        ).fetchone()
    return row_to_stress_test(row) if row else None


def classify_backtest_robustness(run: BacktestRunResult | None) -> Literal["pass", "watch", "fail", "missing"]:
    if run is None:
        return "missing"
    if run.total_return > 0 and run.max_drawdown <= 15 and run.sharpe >= 0.5 and run.trades >= 1:
        return "pass"
    if run.total_return > -5 and run.max_drawdown <= 24 and run.trades >= 1:
        return "watch"
    return "fail"


def backtest_forward_consistency_report(symbol: str, factor_id: str = "momentum-news") -> BacktestForwardConsistencyReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    factor = get_factor_definition(factor_id, require_enabled=True)
    backtest = latest_backtest_for_factor(base_symbol, factor.id)
    forward = latest_forward_for_factor(base_symbol, factor.id)
    walk_forward = latest_walk_forward_for_factor(base_symbol, factor.id)
    stress = latest_stress_for_factor(base_symbol, factor.id)
    score = 50
    checks: list[ConsistencyCheck] = []
    actions: list[str] = []

    backtest_status = classify_backtest_robustness(backtest)
    if backtest is None:
        score -= 25
        checks.append(ConsistencyCheck(name="回测基线", status="missing", message="缺少最新回测记录。"))
        actions.append("先运行一次标准回测建立训练基线。")
    else:
        checks.append(
            ConsistencyCheck(
                name="回测基线",
                status=backtest_status,
                message=f"收益 {backtest.total_return:+.2f}%，回撤 {backtest.max_drawdown:.2f}%，Sharpe {backtest.sharpe:.2f}，交易 {backtest.trades} 次。",
            )
        )
        if backtest_status == "pass":
            score += 12
        elif backtest_status == "watch":
            score += 2
            actions.append("回测仅观察，扩大样本或检查交易次数。")
        else:
            score -= 18
            actions.append("回测基线失败，先不要进入前向模拟。")

    if forward is None:
        score -= 20
        checks.append(ConsistencyCheck(name="前向验证", status="missing", message="缺少最新前向测试记录。"))
        actions.append("运行前向测试，确认样本外收益和衰减。")
    else:
        checks.append(
            ConsistencyCheck(
                name="前向验证",
                status=forward.pass_fail,
                message=f"训练 {forward.train_return:+.2f}%，前向 {forward.forward_return:+.2f}%，衰减 {forward.degradation:.2f}pp，回撤 {forward.max_drawdown:.2f}%。",
            )
        )
        if forward.pass_fail == "pass":
            score += 18
        elif forward.pass_fail == "watch":
            score += 4
            actions.append("前向验证观察，降低仓位并延长观察窗口。")
        else:
            score -= 24
            actions.append("前向验证失败，暂停该因子的策略推广。")

    degradation: float | None = None
    if backtest is not None and forward is not None:
        degradation = round(backtest.total_return - forward.forward_return, 2)
        if backtest.total_return > 0 and forward.forward_return < 0:
            score -= 22
            checks.append(ConsistencyCheck(name="收益一致性", status="fail", message=f"回测为正但前向为负，落差 {degradation:.2f}pp。"))
            actions.append("检查是否存在过拟合、新闻时点偏差或参数过紧。")
        elif degradation > 15:
            score -= 10
            checks.append(ConsistencyCheck(name="收益一致性", status="watch", message=f"前向收益明显低于回测，落差 {degradation:.2f}pp。"))
            actions.append("缩小仓位并等待更多前向样本。")
        else:
            score += 8
            checks.append(ConsistencyCheck(name="收益一致性", status="pass", message=f"回测与前向落差 {degradation:.2f}pp，未见明显断裂。"))

    if walk_forward is None:
        score -= 12
        checks.append(ConsistencyCheck(name="滚动样本外", status="missing", message="缺少 walk-forward 验证。"))
        actions.append("运行滚动样本外验证，检查多个窗口稳定性。")
    else:
        checks.append(
            ConsistencyCheck(
                name="滚动样本外",
                status=walk_forward.pass_fail,
                message=f"通过率 {walk_forward.pass_rate:.1f}%，稳定性 {walk_forward.stability_score}/100，最差前向 {walk_forward.worst_forward_return:+.2f}%。",
            )
        )
        if walk_forward.pass_fail == "pass":
            score += 14
        elif walk_forward.pass_fail == "watch":
            score += 3
            actions.append("滚动样本外仍需观察，优先复核失败窗口。")
        else:
            score -= 18
            actions.append("滚动样本外失败，重新评估因子参数。")

    if stress is None:
        score -= 10
        checks.append(ConsistencyCheck(name="压力验证", status="missing", message="缺少压力测试。"))
        actions.append("运行压力测试，确认风险偏好和流动性冲击下的表现。")
    else:
        checks.append(
            ConsistencyCheck(
                name="压力验证",
                status=stress.pass_fail,
                message=f"基础 {stress.base_return:+.2f}%，最差 {stress.worst_return:+.2f}%，最大压力回撤 {stress.worst_drawdown:.2f}%。",
            )
        )
        if stress.pass_fail == "pass":
            score += 10
        elif stress.pass_fail == "watch":
            score += 2
            actions.extend(stress.actions[:1])
        else:
            score -= 16
            actions.extend(stress.actions[:2])

    missing_count = sum(check.status == "missing" for check in checks)
    fail_count = sum(check.status == "fail" for check in checks)
    watch_count = sum(check.status == "watch" for check in checks)
    clipped_score = max(0, min(100, score))
    if fail_count or clipped_score < 45:
        status: Literal["pass", "watch", "fail"] = "fail"
        recommendation = "暂停推广"
    elif missing_count or watch_count or clipped_score < 75:
        status = "watch"
        recommendation = "补齐验证"
    else:
        status = "pass"
        recommendation = "可进入小规模前向"
    if not actions:
        actions.append("保持当前前向测试节奏，继续监控状态切换和新闻风险。")
    return BacktestForwardConsistencyReport(
        symbol=base_symbol,
        factor_id=factor.id,
        factor_name=factor.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=clipped_score,
        recommendation=recommendation,
        backtest_return=backtest.total_return if backtest else None,
        forward_return=forward.forward_return if forward else None,
        walk_forward_pass_rate=walk_forward.pass_rate if walk_forward else None,
        stress_worst_return=stress.worst_return if stress else None,
        degradation=degradation,
        summary=f"{base_symbol} · {factor.name} 回测-前向一致性 {status}：得分 {clipped_score}/100，缺失 {missing_count} 项，失败 {fail_count} 项。",
        checks=checks,
        actions=dedupe(actions),
    )


def classify_regime_alignment(factor: FactorDefinition, regime: MarketRegime) -> Literal["pass", "watch", "fail"]:
    if regime.risk_level == "risk" and factor.signal == "long":
        return "fail"
    if regime.risk_level == "watch" and factor.signal == "long":
        return "watch"
    if regime.label in {"range", "volatile"} and factor.id in {"momentum-news", "vol-compression"}:
        return "watch"
    return "pass"


def robustness_report(symbol: str) -> RobustnessReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    regime = market_regime_report()
    factors: list[RobustnessFactorReport] = []
    for factor in list_factor_definitions(enabled_only=True):
        latest_backtest = latest_backtest_for_factor(base_symbol, factor.id)
        latest_forward = latest_forward_for_factor(base_symbol, factor.id)
        latest_walk_forward = latest_walk_forward_for_factor(base_symbol, factor.id)
        latest_stress = latest_stress_for_factor(base_symbol, factor.id)
        backtest_status = classify_backtest_robustness(latest_backtest)
        forward_status: Literal["pass", "watch", "fail", "missing"] = latest_forward.pass_fail if latest_forward else "missing"
        walk_forward_status: Literal["pass", "watch", "fail", "missing"] = latest_walk_forward.pass_fail if latest_walk_forward else "missing"
        stress_status: Literal["pass", "watch", "fail", "missing"] = latest_stress.pass_fail if latest_stress else "missing"
        regime_status = classify_regime_alignment(factor, regime)
        checks = [backtest_status, forward_status, walk_forward_status, stress_status, regime_status]
        missing_count = sum(item == "missing" for item in checks)
        fail_count = sum(item == "fail" for item in checks)
        watch_count = sum(item == "watch" for item in checks)
        score = 100 - missing_count * 15 - fail_count * 28 - watch_count * 12
        score = max(0, min(100, score))
        if fail_count:
            status: Literal["pass", "watch", "fail"] = "fail"
        elif watch_count or missing_count:
            status = "watch"
        else:
            status = "pass"
        notes: list[str] = []
        actions: list[str] = []
        if latest_backtest:
            notes.append(f"最近回测 {latest_backtest.total_return:+.2f}%，回撤 {latest_backtest.max_drawdown:.2f}%。")
        else:
            notes.append("缺少最近回测。")
            actions.append("先运行一次基础回测。")
        if latest_forward:
            notes.append(f"前向 {latest_forward.forward_return:+.2f}%，状态 {latest_forward.pass_fail}。")
        else:
            notes.append("缺少前向测试。")
            actions.append("补充前向测试验证样本外衰减。")
        if latest_walk_forward:
            notes.append(f"滚动样本外通过率 {latest_walk_forward.pass_rate:.1f}%，稳定性 {latest_walk_forward.stability_score}/100。")
        else:
            notes.append("缺少滚动样本外验证。")
            actions.append("补充 walk-forward，检查多段样本外稳定性。")
        if latest_stress:
            notes.append(f"压力最差收益 {latest_stress.worst_return:+.2f}%，状态 {latest_stress.pass_fail}。")
        else:
            notes.append("缺少压力测试。")
            actions.append("补充压力测试覆盖风险偏好、波动和流动性冲击。")
        if regime_status == "fail":
            actions.append("当前市场状态不适合直接启用该因子。")
        elif regime_status == "watch":
            actions.append("仅观察或降低仓位，等待市场状态改善。")
        elif not actions:
            actions.append("可进入小规模模拟并持续监控前向衰减。")
        factors.append(
            RobustnessFactorReport(
                factor_id=factor.id,
                factor_name=factor.name,
                signal=factor.signal,
                status=status,
                score=score,
                backtest_status=backtest_status,
                forward_status=forward_status,
                walk_forward_status=walk_forward_status,
                stress_status=stress_status,
                regime_status=regime_status,
                latest_backtest_return=latest_backtest.total_return if latest_backtest else None,
                latest_backtest_drawdown=latest_backtest.max_drawdown if latest_backtest else None,
                latest_forward_return=latest_forward.forward_return if latest_forward else None,
                latest_walk_forward_pass_rate=latest_walk_forward.pass_rate if latest_walk_forward else None,
                latest_stress_worst_return=latest_stress.worst_return if latest_stress else None,
                notes=notes,
                actions=actions,
            )
        )
    fail_total = sum(item.status == "fail" for item in factors)
    pass_total = sum(item.status == "pass" for item in factors)
    if fail_total:
        report_status: Literal["pass", "watch", "fail"] = "fail"
    elif pass_total and pass_total == len(factors):
        report_status = "pass"
    else:
        report_status = "watch"
    return RobustnessReport(
        symbol=base_symbol,
        generated_at=datetime.now(timezone.utc).isoformat(),
        regime=regime,
        status=report_status,
        summary=f"{base_symbol} 启用因子 {len(factors)} 个，稳定通过 {pass_total} 个，需观察 {sum(item.status == 'watch' for item in factors)} 个，失败 {fail_total} 个。",
        factors=factors,
    )


def strategy_drift_report(symbol: str) -> StrategyDriftReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    regime = market_regime_report()
    factors: list[StrategyDriftFactor] = []
    for factor in list_factor_definitions(enabled_only=True):
        latest_backtest = latest_backtest_for_factor(base_symbol, factor.id)
        latest_forward = latest_forward_for_factor(base_symbol, factor.id)
        latest_walk_forward = latest_walk_forward_for_factor(base_symbol, factor.id)
        latest_stress = latest_stress_for_factor(base_symbol, factor.id)
        regime_alignment = classify_regime_alignment(factor, regime)
        evidence: list[str] = []
        actions: list[str] = []
        score = 100
        degradation: float | None = None
        if latest_backtest:
            evidence.append(f"回测基准 {latest_backtest.total_return:+.2f}%，回撤 {latest_backtest.max_drawdown:.2f}%。")
            if latest_backtest.total_return < 0:
                score -= 20
        else:
            score -= 28
            evidence.append("缺少回测基准。")
            actions.append("先运行基础回测建立基准。")
        if latest_forward:
            evidence.append(f"最近前向 {latest_forward.forward_return:+.2f}%，状态 {latest_forward.pass_fail}。")
            if latest_backtest:
                degradation = round(latest_backtest.total_return - latest_forward.forward_return, 2)
                evidence.append(f"收益衰减 {degradation:+.2f} 个百分点。")
                if degradation > 12:
                    score -= 26
                    actions.append("前向收益明显低于回测，复核过滤器和入场条件。")
                elif degradation > 6:
                    score -= 12
                    actions.append("前向收益有衰减，降低权重并继续观察。")
            if latest_forward.pass_fail == "fail":
                score -= 24
                actions.append("前向测试失败，暂缓进入自动化执行。")
            elif latest_forward.pass_fail == "watch":
                score -= 10
        else:
            score -= 20
            evidence.append("缺少最近前向测试。")
            actions.append("补充前向测试确认样本外表现。")
        if latest_walk_forward:
            evidence.append(f"滚动样本外通过率 {latest_walk_forward.pass_rate:.1f}%，稳定性 {latest_walk_forward.stability_score}/100。")
            if latest_walk_forward.pass_fail == "fail":
                score -= 26
                actions.append("滚动样本外失败，优先检查失效窗口。")
            elif latest_walk_forward.pass_fail == "watch":
                score -= 12
                actions.append("滚动样本外仅观察，扩大窗口后再评估。")
            if latest_walk_forward.pass_rate < 40:
                score -= 12
        else:
            score -= 18
            evidence.append("缺少滚动样本外验证。")
            actions.append("补充 walk-forward 检查多段稳定性。")
        if latest_stress:
            evidence.append(f"压力最差收益 {latest_stress.worst_return:+.2f}%，状态 {latest_stress.pass_fail}。")
            if latest_stress.pass_fail == "fail":
                score -= 22
                actions.append("压力测试失败，降低仓位或暂停策略推广。")
            elif latest_stress.pass_fail == "watch":
                score -= 10
        else:
            score -= 14
            evidence.append("缺少压力测试。")
            actions.append("补充压力测试覆盖风险偏好、波动和流动性冲击。")
        if regime_alignment == "fail":
            score -= 24
            actions.append("当前市场状态与因子方向冲突。")
        elif regime_alignment == "watch":
            score -= 10
            actions.append("当前市场状态需要观察，建议缩小策略权重。")
        evidence.append(f"市场状态 {regime.label} / {regime.risk_level}：{regime.summary}")
        score = max(0, min(100, score))
        if score >= 72 and not any("失败" in item or "冲突" in item for item in actions):
            status: Literal["pass", "watch", "fail"] = "pass"
        elif score >= 45:
            status = "watch"
        else:
            status = "fail"
        if not actions:
            actions.append("漂移风险可控，可继续小规模模拟并监控下一次前向结果。")
        factors.append(
            StrategyDriftFactor(
                factor_id=factor.id,
                factor_name=factor.name,
                status=status,
                drift_score=score,
                baseline_return=latest_backtest.total_return if latest_backtest else None,
                forward_return=latest_forward.forward_return if latest_forward else None,
                return_degradation=degradation,
                walk_forward_pass_rate=latest_walk_forward.pass_rate if latest_walk_forward else None,
                stress_worst_return=latest_stress.worst_return if latest_stress else None,
                regime_alignment=regime_alignment,
                evidence=evidence,
                actions=dedupe(actions),
            )
        )
    if not factors:
        report_status: Literal["pass", "watch", "fail"] = "fail"
        score = 0
        summary = "没有启用因子，无法监控策略漂移。"
    else:
        score = int(sum(item.drift_score for item in factors) / len(factors))
        fail_count = sum(item.status == "fail" for item in factors)
        watch_count = sum(item.status == "watch" for item in factors)
        if fail_count:
            report_status = "fail"
        elif watch_count:
            report_status = "watch"
        else:
            report_status = "pass"
        summary = f"{base_symbol} 漂移评分 {score}/100，稳定 {sum(item.status == 'pass' for item in factors)} 个，观察 {watch_count} 个，失败 {fail_count} 个。"
    return StrategyDriftReport(
        symbol=base_symbol,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=report_status,
        score=score,
        summary=summary,
        regime=regime,
        factors=sorted(factors, key=lambda item: item.drift_score),
    )
