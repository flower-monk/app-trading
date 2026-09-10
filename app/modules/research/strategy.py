"""Research domain: strategy implementation."""

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
    BacktestRunRequest,
    BacktestRunResult,
    Candle,
    DataQualityReport,
    FactorAdmissionCheck,
    FactorAdmissionQueueItem,
    FactorAdmissionQueueReport,
    FactorAdmissionReport,
    FactorAdmissionRequest,
    FactorCorrelationPair,
    FactorCorrelationReport,
    FactorExposureItem,
    FactorLeaderboard,
    FactorLeaderboardItem,
    FilterValidationRequest,
    FilterValidationResult,
    ForwardTestRequest,
    ForwardTestResult,
    Literal,
    MarketRegime,
    MarketStateControlItem,
    MarketStateControlReport,
    ParameterOptimizationRequest,
    PortfolioRiskReport,
    ReadinessCheck,
    RegimeAttributionReport,
    RegimeAttributionSlice,
    RegimeExposureItem,
    RegimeExposureReport,
    RegimeFactorFitItem,
    RegimeFactorFitReport,
    RegimeRiskGate,
    RegimeRiskGateCheck,
    RegimeScenarioRiskItem,
    RegimeScenarioRiskReport,
    ResearchPipelineRequest,
    ResearchPipelineResult,
    ResearchReadiness,
    RevalidationQueueItem,
    RevalidationQueueReport,
    SensitivityScenarioResult,
    SensitivityTestRequest,
    SensitivityTestResult,
    StrategyAllocationItem,
    StrategyAllocationReport,
    StrategyCrowdingPair,
    StrategyCrowdingReport,
    StrategyDriftFactor,
    StrategyEvidencePackItem,
    StrategyEvidencePackReport,
    StrategyLeaderboard,
    StrategyLeaderboardItem,
    StrategyLifecycleItem,
    StrategyLifecycleReport,
    StrategyOperatingPlanItem,
    StrategyOperatingPlanReport,
    StrategyPromotionGate,
    StrategyRegimeFitFactor,
    StrategyRegimeFitItem,
    StrategyRegimeFitReport,
    StrategyRegimeMatrixCell,
    StrategyRegimeMatrixItem,
    StrategyRegimeMatrixReport,
    StrategyRiskControlItem,
    StrategyRiskControlReport,
    StrategyTemplate,
    StrategyValidationDecayItem,
    StrategyValidationDecayReport,
    StrategyValidationRequest,
    StrategyValidationResult,
    StressTestRequest,
    TradeReadinessCheck,
    TradeReadinessReport,
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
filter_request_from_dict = service_call('app.modules.market.service', 'filter_request_from_dict')
get_portfolio_risk_report = service_call('app.modules.trading.service', 'get_portfolio_risk_report')
get_strategy_template = service_call('app.modules.market.service', 'get_strategy_template')
get_system_health = service_call('app.modules.operations.service', 'get_system_health')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_strategy_templates = service_call('app.modules.market.service', 'list_strategy_templates')
list_supported_market_symbols = service_call('app.modules.market.service', 'list_supported_market_symbols')
list_watchlist_snapshots = service_call('app.modules.market.service', 'list_watchlist_snapshots')
run_screener = service_call('app.modules.market.service', 'run_screener')

# Deferred internal references keep research submodules independently importable.
backtest_position = service_call('app.modules.research.factors', 'backtest_position')
classify_backtest_robustness = service_call('app.modules.research.experiments', 'classify_backtest_robustness')
data_catalog_items = service_call('app.modules.research.catalog', 'data_catalog_items')
data_quality_report = service_call('app.modules.research.catalog', 'data_quality_report')
get_factor_definition = service_call('app.modules.research.factors', 'get_factor_definition')
latest_backtest_for_factor = service_call('app.modules.research.experiments', 'latest_backtest_for_factor')
latest_forward_for_factor = service_call('app.modules.research.experiments', 'latest_forward_for_factor')
latest_stress_for_factor = service_call('app.modules.research.experiments', 'latest_stress_for_factor')
latest_walk_forward_for_factor = service_call('app.modules.research.experiments', 'latest_walk_forward_for_factor')
lightweight_data_quality_report = service_call('app.modules.research.catalog', 'lightweight_data_quality_report')
lightweight_market_regime_report = service_call('app.modules.research.catalog', 'lightweight_market_regime_report')
list_factor_definitions = service_call('app.modules.research.factors', 'list_factor_definitions')
market_regime_report = service_call('app.modules.research.catalog', 'market_regime_report')
market_regime_transition_report = service_call('app.modules.research.catalog', 'market_regime_transition_report')
parameter_optimization_report = service_call('app.modules.research.experiments', 'parameter_optimization_report')
robustness_report = service_call('app.modules.research.experiments', 'robustness_report')
run_backtest = service_call('app.modules.research.backtesting', 'run_backtest')
run_forward_test = service_call('app.modules.research.experiments', 'run_forward_test')
run_stress_test = service_call('app.modules.research.experiments', 'run_stress_test')
strategy_backtest_monitor_report = service_call('app.modules.research.experiments', 'strategy_backtest_monitor_report')
strategy_drift_report = service_call('app.modules.research.experiments', 'strategy_drift_report')
strategy_forward_monitor_report = service_call('app.modules.research.experiments', 'strategy_forward_monitor_report')
validate_filters = service_call('app.modules.research.catalog', 'validate_filters')


def run_research_pipeline(payload: ResearchPipelineRequest) -> ResearchPipelineResult:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    filter_validation = validate_filters(payload.filters)
    backtest = run_backtest(
        BacktestRunRequest(
            symbol=symbol,
            factor_id=payload.factor_id,
            interval=payload.interval,
            limit=180,
            initial_capital=payload.initial_capital,
            fee_bps=2,
        )
    )
    forward = run_forward_test(
        ForwardTestRequest(
            symbol=symbol,
            factor_id=payload.factor_id,
            interval=payload.interval,
            train_bars=140,
            forward_bars=40,
            initial_capital=payload.initial_capital,
            fee_bps=2,
        )
    )
    stress = run_stress_test(
        StressTestRequest(
            symbol=symbol,
            factor_id=payload.factor_id,
            interval=payload.interval,
            initial_capital=payload.initial_capital,
        )
    )
    report = robustness_report(symbol)
    factor_report = next((item for item in report.factors if item.factor_id == payload.factor_id), None)
    if factor_report is None:
        raise HTTPException(status_code=404, detail="Factor robustness report not found")
    fail_count = sum(
        [
            not filter_validation.valid,
            factor_report.backtest_status == "fail",
            factor_report.forward_status == "fail",
            factor_report.stress_status == "fail",
            factor_report.regime_status == "fail",
        ]
    )
    watch_count = sum(
        [
            filter_validation.valid and filter_validation.warnings != [],
            factor_report.backtest_status in {"watch", "missing"},
            factor_report.forward_status in {"watch", "missing"},
            factor_report.stress_status in {"watch", "missing"},
            factor_report.regime_status == "watch",
        ]
    )
    if fail_count:
        pass_fail: Literal["pass", "watch", "fail"] = "fail"
        actions = ["不要进入自动化执行", "先调整过滤器或风险条件", "扩大样本后重新验证"]
    elif watch_count:
        pass_fail = "watch"
        actions = ["进入观察池", "小仓位模拟而不是实盘执行", "持续跟踪前向衰减和压力状态"]
    else:
        pass_fail = "pass"
        actions = ["可进入小规模模拟", "保留预警和压力测试复核", "定期重新运行一键验证"]
    generated_at = datetime.now(timezone.utc).isoformat()
    run_id = sha256(f"pipeline:{symbol}:{payload.factor_id}:{payload.interval}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    result = ResearchPipelineResult(
        id=run_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        generated_at=generated_at,
        pass_fail=pass_fail,
        summary=f"{symbol} · {factor.name} 一键验证 {pass_fail}：回测 {backtest.total_return:+.2f}%，前向 {forward.forward_return:+.2f}%，压力最差 {stress.worst_return:+.2f}%。",
        filter_validation=filter_validation,
        backtest=backtest,
        forward_test=forward,
        stress_test=stress,
        robustness=factor_report,
        actions=actions,
    )
    add_event_log(
        event_type="research_pipeline",
        severity="info" if pass_fail == "pass" else "watch" if pass_fail == "watch" else "risk",
        symbol=symbol,
        title="一键验证完成",
        message=result.summary,
    )
    return result


def factor_admission_report(payload: FactorAdmissionRequest) -> FactorAdmissionReport:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    checks: list[FactorAdmissionCheck] = []
    blockers: list[str] = []
    actions: list[str] = []

    def add_check(name: str, status: Literal["pass", "watch", "fail"], detail: str, action: str) -> None:
        checks.append(FactorAdmissionCheck(name=name, status=status, detail=detail, action=action))
        if status == "fail":
            blockers.append(f"{name}: {detail}")
        if status != "pass":
            actions.append(action)

    filter_result = validate_filters(payload.filters)
    add_check(
        "过滤器",
        "pass" if filter_result.valid and not filter_result.warnings else "watch" if filter_result.valid else "fail",
        f"命中 {filter_result.match_count} 个；{'; '.join(filter_result.warnings[:2]) if filter_result.warnings else '结构可用'}。",
        filter_result.suggestions[0] if filter_result.suggestions else "保存为模板并进入回测验证。",
    )

    robustness = robustness_report(symbol)
    factor_robustness = next((item for item in robustness.factors if item.factor_id == payload.factor_id), None)
    if factor_robustness:
        add_check(
            "稳健性",
            factor_robustness.status,
            f"分数 {factor_robustness.score}/100，回测 {factor_robustness.backtest_status}，前向 {factor_robustness.forward_status}，滚动 {factor_robustness.walk_forward_status}，压力 {factor_robustness.stress_status}。",
            factor_robustness.actions[0] if factor_robustness.actions else "补齐缺失研究记录。",
        )
    else:
        add_check("稳健性", "fail", "未找到该因子的稳健性报告。", "先启用因子并运行基础验证。")

    drift = strategy_drift_report(symbol)
    factor_drift = next((item for item in drift.factors if item.factor_id == payload.factor_id), None)
    if factor_drift:
        add_check(
            "漂移",
            factor_drift.status,
            f"漂移分 {factor_drift.drift_score}/100，前向衰减 {factor_drift.return_degradation if factor_drift.return_degradation is not None else '--'}。",
            factor_drift.actions[0] if factor_drift.actions else "持续监控下一次前向结果。",
        )
    else:
        add_check("漂移", "watch", "缺少漂移检查。", "补齐回测和前向记录。")

    allocation = strategy_allocation_report(symbol)
    allocation_item = next((item for item in allocation.items if item.factor_id == payload.factor_id), None)
    if allocation_item:
        allocation_status: Literal["pass", "watch", "fail"] = "pass" if allocation_item.status in {"increase", "hold"} and allocation_item.target_weight > 0 else "watch" if allocation_item.status == "reduce" else "fail"
        add_check(
            "配置",
            allocation_status,
            f"建议 {allocation_item.status}，目标 {allocation_item.target_weight:.1f}%，信心 {allocation_item.confidence}/100。",
            allocation_item.controls[0] if allocation_item.controls else "按配置建议限制权重。",
        )
    else:
        add_check("配置", "fail", "配置建议未覆盖该因子。", "先提升因子稳健性或降低拥挤度。")

    correlation = factor_correlation_report(symbol, interval=payload.interval)
    crowded_pairs = [
        item for item in correlation.pairs
        if item.status != "pass" and payload.factor_id in {item.factor_a_id, item.factor_b_id}
    ]
    if any(item.status == "fail" for item in crowded_pairs):
        corr_status: Literal["pass", "watch", "fail"] = "fail"
    elif crowded_pairs:
        corr_status = "watch"
    else:
        corr_status = "pass"
    add_check(
        "相关性",
        corr_status,
        f"拥挤度 {correlation.crowding_score}/100，相关风险 {len(crowded_pairs)} 对。",
        correlation.actions[0] if correlation.actions else "保持因子分散。",
    )

    attribution = regime_attribution_report(symbol, factor_id=payload.factor_id, interval=payload.interval)
    add_check(
        "市场状态归因",
        attribution.status,
        f"最佳 {attribution.best_regime or '--'}，最弱 {attribution.worst_regime or '--'}。",
        attribution.actions[0] if attribution.actions else "继续观察状态切片表现。",
    )

    optimization = parameter_optimization_report(
        ParameterOptimizationRequest(symbol=symbol, factor_id=payload.factor_id, interval=payload.interval)
    )
    add_check(
        "参数优化",
        optimization.status,
        f"最佳分数 {optimization.best.score if optimization.best else 0}/100，前 8 组 {len(optimization.results)}。",
        optimization.actions[0] if optimization.actions else "复核前两名参数组合。",
    )

    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(0, 100 - fail_count * 22 - watch_count * 9)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    if status == "pass":
        actions.append("准入通过：进入小规模模拟，并保持漂移和市场状态监控。")
    elif status == "watch":
        actions.append("准入观察：降低权重，补齐 watch 项后再扩大模拟。")
    else:
        actions.append("准入失败：先处理阻断项，不进入新增模拟。")
    generated_at = datetime.now(timezone.utc).isoformat()
    report_id = sha256(f"admission:{symbol}:{payload.factor_id}:{payload.interval}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    return FactorAdmissionReport(
        id=report_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        generated_at=generated_at,
        status=status,
        score=score,
        summary=f"{symbol} · {factor.name} 准入 {status}，分数 {score}/100，阻断 {fail_count}，观察 {watch_count}。",
        checks=checks,
        blockers=blockers,
        actions=dedupe(actions)[:8],
    )


def factor_admission_queue_report(
    limit: int = 6,
    factor_id: str | None = None,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
) -> FactorAdmissionQueueReport:
    init_db()
    max_items = max(1, min(10, limit))
    watchlist_items = list_watchlist_snapshots()
    enabled_factors = list_factor_definitions(enabled_only=True)
    if factor_id:
        enabled_factors = [get_factor_definition(factor_id, require_enabled=True)]
    candidates: list[FactorAdmissionQueueItem] = []
    for snapshot in watchlist_items[: max(3, min(6, max_items * 2))]:
        robustness = robustness_report(snapshot.symbol)
        drift = strategy_drift_report(snapshot.symbol)
        allocation = strategy_allocation_report(snapshot.symbol)
        correlation = factor_correlation_report(snapshot.symbol, interval=interval)
        robustness_by_factor = {item.factor_id: item for item in robustness.factors}
        drift_by_factor = {item.factor_id: item for item in drift.factors}
        allocation_by_factor = {item.factor_id: item for item in allocation.items}
        for factor in enabled_factors:
            factor_robustness = robustness_by_factor.get(factor.id)
            factor_drift = drift_by_factor.get(factor.id)
            factor_allocation = allocation_by_factor.get(factor.id)
            if not factor_robustness or not factor_drift or not factor_allocation:
                continue
            crowded_pairs = [
                item for item in correlation.pairs
                if item.status != "pass" and factor.id in {item.factor_a_id, item.factor_b_id}
            ]
            score = int(
                factor_robustness.score * 0.34
                + factor_drift.drift_score * 0.24
                + factor_allocation.confidence * 0.25
                + correlation.crowding_score * 0.17
            )
            if any(item.status == "fail" for item in crowded_pairs):
                score -= 18
            elif crowded_pairs:
                score -= 8
            blockers: list[str] = []
            actions: list[str] = []
            if factor_robustness.status == "fail":
                blockers.append("稳健性失败")
                actions.extend(factor_robustness.actions[:1])
            if factor_drift.status == "fail":
                blockers.append("漂移失败")
                actions.extend(factor_drift.actions[:1])
            if factor_allocation.status == "avoid":
                blockers.append("配置建议规避")
                actions.extend(factor_allocation.controls[:1])
            if any(item.status == "fail" for item in crowded_pairs):
                blockers.append("因子相关性拥挤")
                actions.extend(correlation.actions[:1])
            if not actions:
                actions.append("生成完整准入包并进入小规模模拟复核。")
            score = max(0, min(100, score))
            if blockers:
                status: Literal["pass", "watch", "fail"] = "fail"
            elif score >= 72 and factor_allocation.target_weight > 0:
                status = "pass"
            elif score >= 45:
                status = "watch"
            else:
                status = "fail"
            candidates.append(
                FactorAdmissionQueueItem(
                    rank=0,
                    symbol=snapshot.symbol,
                    name=snapshot.name,
                    factor_id=factor.id,
                    factor_name=factor.name,
                    status=status,
                    score=score,
                    target_weight=factor_allocation.target_weight,
                    recommendation=f"{factor_allocation.status} · 目标 {factor_allocation.target_weight:.1f}%",
                    blockers=dedupe(blockers),
                    actions=dedupe(actions),
                )
            )
    ranked = sorted(candidates, key=lambda item: (item.status == "pass", item.score, item.target_weight), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[:max_items])]
    fail_count = sum(item.status == "fail" for item in ranked)
    pass_count = sum(item.status == "pass" for item in ranked)
    if ranked and pass_count:
        status: Literal["pass", "watch", "fail"] = "pass"
    elif ranked and fail_count < len(ranked):
        status = "watch"
    else:
        status = "fail"
    return FactorAdmissionQueueReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"准入队列：{pass_count} 个可优先准入，{sum(item.status == 'watch' for item in ranked)} 个观察，{fail_count} 个阻断。",
        items=ranked,
    )


def iso_age_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def revalidation_queue_report(
    limit: int = 8,
    stale_days: int = 7,
    factor_id: str | None = None,
) -> RevalidationQueueReport:
    init_db()
    max_items = max(1, min(20, limit))
    stale_window = max(1, min(60, stale_days))
    factors = list_factor_definitions(enabled_only=True)
    if factor_id:
        factors = [get_factor_definition(factor_id, require_enabled=True)]
    regime = market_regime_report()
    items: list[RevalidationQueueItem] = []
    for snapshot in list_watchlist_snapshots()[:8]:
        for factor in factors:
            latest_backtest = latest_backtest_for_factor(snapshot.symbol, factor.id)
            latest_forward = latest_forward_for_factor(snapshot.symbol, factor.id)
            latest_walk = latest_walk_forward_for_factor(snapshot.symbol, factor.id)
            latest_stress = latest_stress_for_factor(snapshot.symbol, factor.id)
            latest_dates = [
                latest_backtest.created_at if latest_backtest else None,
                latest_forward.generated_at if latest_forward else None,
                latest_walk.generated_at if latest_walk else None,
                latest_stress.generated_at if latest_stress else None,
            ]
            latest_run_at = max([item for item in latest_dates if item], default=None)
            due_tasks: list[str] = []
            reasons: list[str] = []
            priority = 0
            if latest_backtest is None:
                due_tasks.append("backtest")
                reasons.append("缺少回测")
                priority += 30
            elif classify_backtest_robustness(latest_backtest) == "fail":
                due_tasks.append("backtest")
                reasons.append("最近回测失败")
                priority += 24
            if latest_forward is None:
                due_tasks.append("forward")
                reasons.append("缺少前向")
                priority += 28
            elif latest_forward.pass_fail == "fail":
                due_tasks.append("forward")
                reasons.append("最近前向失败")
                priority += 24
            if latest_walk is None:
                due_tasks.append("walk-forward")
                reasons.append("缺少滚动样本外")
                priority += 26
            elif latest_walk.pass_fail == "fail":
                due_tasks.append("walk-forward")
                reasons.append("最近滚动验证失败")
                priority += 22
            if latest_stress is None:
                due_tasks.append("stress")
                reasons.append("缺少压力测试")
                priority += 22
            elif latest_stress.pass_fail == "fail":
                due_tasks.append("stress")
                reasons.append("最近压力测试失败")
                priority += 22
            age = iso_age_days(latest_run_at)
            if age is not None and age >= stale_window and not due_tasks:
                due_tasks.extend(["forward", "walk-forward"])
                reasons.append(f"最近研究记录已 {age} 天")
                priority += min(24, age)
            if regime.risk_level in {"watch", "risk"} and due_tasks:
                priority += 12 if regime.risk_level == "watch" else 20
                reasons.append(f"市场状态 {regime.risk_level}")
            if not due_tasks:
                continue
            if any("失败" in item for item in reasons):
                status: Literal["pass", "watch", "fail"] = "fail"
            else:
                status = "watch"
            actions = [f"优先执行 {', '.join(due_tasks[:3])}。"]
            if "walk-forward" in due_tasks:
                actions.append("滚动样本外用于确认多段稳定性。")
            if regime.risk_level in {"watch", "risk"}:
                actions.append("市场状态偏谨慎，复验通过前降低策略权重。")
            items.append(
                RevalidationQueueItem(
                    rank=0,
                    symbol=snapshot.symbol,
                    name=snapshot.name,
                    factor_id=factor.id,
                    factor_name=factor.name,
                    status=status,
                    priority=priority,
                    due_tasks=dedupe(due_tasks),
                    latest_run_at=latest_run_at,
                    reason="；".join(reasons),
                    actions=actions,
                )
            )
    ranked = sorted(items, key=lambda item: item.priority, reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[:max_items])]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    summary = f"复验队列：{len(ranked)} 个待处理，失败复验 {fail_count}，过期/缺失 {watch_count}。"
    if not ranked:
        summary = "复验队列为空：当前核心研究记录没有缺失或过期。"
    return RevalidationQueueReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=summary,
        stale_days=stale_window,
        items=ranked,
    )


def classify_sensitivity_scenario(run: BacktestRunResult) -> Literal["pass", "watch", "fail"]:
    if run.total_return > 0 and run.max_drawdown <= 15 and run.trades >= 1:
        return "pass"
    if run.total_return > -5 and run.max_drawdown <= 24:
        return "watch"
    return "fail"


def run_sensitivity_test(payload: SensitivityTestRequest) -> SensitivityTestResult:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    scenarios_config: list[tuple[str, Literal["15m", "1h", "4h", "1d"], float]] = [
        ("短周期", "15m", 2),
        ("基准周期", "1h", 2),
        ("长周期", "4h", 2),
        ("高费率", "1h", 8),
    ]
    scenarios: list[SensitivityScenarioResult] = []
    for name, interval, fee_bps in scenarios_config:
        run = run_backtest(
            BacktestRunRequest(
                symbol=symbol,
                factor_id=payload.factor_id,
                interval=interval,
                limit=180,
                initial_capital=payload.initial_capital,
                fee_bps=fee_bps,
            )
        )
        status = classify_sensitivity_scenario(run)
        notes = [
            f"收益 {run.total_return:+.2f}%，回撤 {run.max_drawdown:.2f}%。",
            f"Sharpe {run.sharpe:.2f}，交易 {run.trades} 笔。",
        ]
        if fee_bps > 2:
            notes.append("用于检查交易成本上升后的稳定性。")
        scenarios.append(
            SensitivityScenarioResult(
                name=name,
                interval=interval,
                fee_bps=fee_bps,
                total_return=run.total_return,
                max_drawdown=run.max_drawdown,
                sharpe=run.sharpe,
                trades=run.trades,
                pass_fail=status,
                notes=notes,
            )
        )
    returns = [item.total_return for item in scenarios]
    drawdowns = [item.max_drawdown for item in scenarios]
    return_spread = round(max(returns) - min(returns), 2)
    drawdown_spread = round(max(drawdowns) - min(drawdowns), 2)
    failures = sum(item.pass_fail == "fail" for item in scenarios)
    watches = sum(item.pass_fail == "watch" for item in scenarios)
    if failures or return_spread > 35 or drawdown_spread > 18:
        pass_fail: Literal["pass", "watch", "fail"] = "fail"
        actions = ["参数敏感性偏高，暂不进入自动化", "缩小交易频率差异后重新测试", "检查手续费和滑点假设"]
    elif watches or return_spread > 22 or drawdown_spread > 12:
        pass_fail = "watch"
        actions = ["进入观察池", "优先采用最稳定周期", "扩大样本后复测参数敏感性"]
    else:
        pass_fail = "pass"
        actions = ["参数稳定性通过基础检查", "可结合前向和压力测试进入小规模模拟", "定期在市场状态变化后复测"]
    generated_at = datetime.now(timezone.utc).isoformat()
    result_id = sha256(f"sensitivity:{symbol}:{payload.factor_id}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    result = SensitivityTestResult(
        id=result_id,
        symbol=symbol,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        generated_at=generated_at,
        pass_fail=pass_fail,
        return_spread=return_spread,
        drawdown_spread=drawdown_spread,
        scenarios=scenarios,
        actions=actions,
    )
    add_event_log(
        event_type="sensitivity_test",
        severity="info" if pass_fail == "pass" else "watch" if pass_fail == "watch" else "risk",
        symbol=symbol,
        title="参数敏感性测试完成",
        message=f"{factor.name} 收益离散 {return_spread:.2f}%，回撤离散 {drawdown_spread:.2f}%，状态 {pass_fail}。",
    )
    return result


def factor_leaderboard_report(symbol: str) -> FactorLeaderboard:
    report = robustness_report(symbol)
    items: list[FactorLeaderboardItem] = []
    for factor in report.factors:
        backtest_return = factor.latest_backtest_return
        backtest_drawdown = factor.latest_backtest_drawdown
        forward_return = factor.latest_forward_return
        stress_worst_return = factor.latest_stress_worst_return
        score = factor.score
        reasons = [f"稳健性 {factor.score}/100，状态 {factor.status}。"]
        if backtest_return is not None:
            score += min(18, max(-18, int(backtest_return / 2)))
            reasons.append(f"最近回测 {backtest_return:+.2f}%。")
        else:
            score -= 12
            reasons.append("缺少回测记录。")
        if backtest_drawdown is not None:
            score -= min(16, max(0, int(backtest_drawdown / 2)))
            reasons.append(f"最大回撤 {backtest_drawdown:.2f}%。")
        if forward_return is not None:
            score += min(16, max(-16, int(forward_return / 2)))
            reasons.append(f"前向收益 {forward_return:+.2f}%。")
        else:
            score -= 12
            reasons.append("缺少前向测试。")
        if stress_worst_return is not None:
            score += min(12, max(-18, int(stress_worst_return / 3)))
            reasons.append(f"压力最差收益 {stress_worst_return:+.2f}%。")
        else:
            score -= 12
            reasons.append("缺少压力测试。")
        if factor.status == "fail":
            recommendation = "暂不启用"
            score -= 20
        elif factor.status == "pass" and score >= 75:
            recommendation = "优先模拟"
        elif factor.status == "watch" or score >= 50:
            recommendation = "观察验证"
        else:
            recommendation = "先补样本"
        items.append(
            FactorLeaderboardItem(
                rank=0,
                factor_id=factor.factor_id,
                factor_name=factor.factor_name,
                status=factor.status,
                composite_score=max(0, min(100, score)),
                robustness_score=factor.score,
                backtest_return=backtest_return,
                backtest_drawdown=backtest_drawdown,
                forward_return=forward_return,
                stress_worst_return=stress_worst_return,
                recommendation=recommendation,
                reasons=reasons,
            )
        )
    ranked = sorted(items, key=lambda item: (item.composite_score, item.robustness_score), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked)]
    ready_count = sum(item.recommendation == "优先模拟" for item in ranked)
    return FactorLeaderboard(
        symbol=report.symbol,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=f"{report.symbol} 因子排行榜：{ready_count} 个优先模拟，{sum(item.recommendation == '观察验证' for item in ranked)} 个观察验证，{sum(item.recommendation in {'先补样本', '暂不启用'} for item in ranked)} 个需补充或暂停。",
        items=ranked,
    )


def allocation_risk_budget(regime: MarketRegime, portfolio_risk: PortfolioRiskReport) -> tuple[float, list[str]]:
    notes: list[str] = []
    if regime.risk_level == "risk":
        budget = 0.25
        notes.append("市场状态为 risk，策略总预算限制为 25%。")
    elif regime.risk_level == "watch":
        budget = 0.45
        notes.append("市场状态为 watch，策略总预算限制为 45%。")
    elif regime.risk_level == "constructive":
        budget = 0.75
        notes.append("市场状态 constructive，允许较高模拟预算。")
    else:
        budget = 0.60
        notes.append("市场状态 neutral，采用中性模拟预算。")
    if portfolio_risk.gross_exposure > 0:
        if abs(portfolio_risk.net_exposure_percent) >= 80:
            budget *= 0.65
            notes.append(f"组合净敞口 {portfolio_risk.net_exposure_percent:.1f}%，下调策略预算。")
        if portfolio_risk.concentration_score >= 35:
            budget *= 0.75
            notes.append(f"组合集中度评分 {portfolio_risk.concentration_score:.1f}，降低单因子权重。")
    return round(max(0.1, min(0.85, budget)), 2), notes


def strategy_allocation_report(symbol: str) -> StrategyAllocationReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    leaderboard = factor_leaderboard_report(base_symbol)
    drift = strategy_drift_report(base_symbol)
    portfolio_risk = get_portfolio_risk_report()
    budget, portfolio_notes = allocation_risk_budget(drift.regime, portfolio_risk)
    drift_by_factor = {item.factor_id: item for item in drift.factors}
    eligible_items: list[tuple[FactorLeaderboardItem, StrategyDriftFactor, int]] = []
    allocation_items: list[StrategyAllocationItem] = []
    actions: list[str] = []
    for factor in leaderboard.items:
        factor_drift = drift_by_factor.get(factor.factor_id)
        if not factor_drift:
            continue
        confidence = int(round((factor.composite_score * 0.55) + (factor_drift.drift_score * 0.45)))
        rationale = [
            f"因子综合分 {factor.composite_score}/100，稳健性 {factor.robustness_score}/100。",
            f"漂移分 {factor_drift.drift_score}/100，漂移状态 {factor_drift.status}。",
        ]
        controls = ["保留价格、RSI 和新闻冲击预警。"]
        if factor.stress_worst_return is not None:
            rationale.append(f"压力最差收益 {factor.stress_worst_return:+.2f}%。")
        if factor_drift.return_degradation is not None:
            rationale.append(f"回测到前向衰减 {factor_drift.return_degradation:+.2f}pp。")
        if factor.status == "fail" or factor_drift.status == "fail" or confidence < 42:
            allocation_items.append(
                StrategyAllocationItem(
                    factor_id=factor.factor_id,
                    factor_name=factor.factor_name,
                    status="avoid",
                    target_weight=0.0,
                    max_weight=0.0,
                    confidence=max(0, min(100, confidence)),
                    rationale=rationale,
                    controls=dedupe(controls + factor_drift.actions[:2]),
                )
            )
            continue
        eligible_items.append((factor, factor_drift, confidence))
    total_score = sum(max(1, confidence) for _, _, confidence in eligible_items)
    for factor, factor_drift, confidence in eligible_items:
        raw_weight = budget * (confidence / total_score) if total_score else 0
        max_weight = 0.30 if factor.status == "pass" and factor_drift.status == "pass" else 0.18
        target_weight = round(min(max_weight, raw_weight), 3)
        if confidence >= 72 and factor_drift.status == "pass":
            status: Literal["increase", "hold", "reduce", "avoid"] = "increase"
        elif confidence >= 55:
            status = "hold"
        else:
            status = "reduce"
        controls = ["单因子触发失败时降至 0。", "前向或滚动验证转 fail 时暂停新增。"]
        if drift.regime.risk_level in {"watch", "risk"}:
            controls.append("市场状态未完全配合，限制加仓节奏。")
        allocation_items.append(
            StrategyAllocationItem(
                factor_id=factor.factor_id,
                factor_name=factor.factor_name,
                status=status,
                target_weight=round(target_weight * 100, 1),
                max_weight=round(max_weight * 100, 1),
                confidence=max(0, min(100, confidence)),
                rationale=[
                    f"因子综合分 {factor.composite_score}/100，稳健性 {factor.robustness_score}/100。",
                    f"漂移分 {factor_drift.drift_score}/100，漂移状态 {factor_drift.status}。",
                    f"建议占策略预算 {target_weight / budget * 100:.1f}%。" if budget else "当前无可用策略预算。",
                ],
                controls=controls,
            )
        )
    allocation_items.sort(key=lambda item: (item.target_weight, item.confidence), reverse=True)
    total_target = round(sum(item.target_weight for item in allocation_items), 1)
    if not allocation_items or all(item.target_weight == 0 for item in allocation_items):
        report_status: Literal["pass", "watch", "fail"] = "fail"
        actions.append("没有因子满足配置条件，先补齐回测、前向、滚动样本外和压力测试。")
    elif any(item.status == "increase" for item in allocation_items) and drift.regime.risk_level != "risk":
        report_status = "pass"
        actions.append("可按建议权重进入小规模模拟，并继续监控漂移报告。")
    else:
        report_status = "watch"
        actions.append("仅维持观察或小权重模拟，等待验证覆盖和市场状态改善。")
    if portfolio_risk.warnings:
        actions.extend(portfolio_risk.actions[:2])
    summary = f"{base_symbol} 策略预算 {budget * 100:.0f}%，建议目标权重合计 {total_target:.1f}%，状态 {report_status}。"
    return StrategyAllocationReport(
        symbol=base_symbol,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=report_status,
        total_target_weight=total_target,
        risk_budget=round(budget * 100, 1),
        summary=summary,
        regime=drift.regime,
        items=allocation_items,
        portfolio_notes=portfolio_notes + portfolio_risk.warnings[:3],
        actions=dedupe(actions),
    )


def trade_readiness_report(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    min_rsi: float | None = None,
    min_trend: int | None = None,
    signal: str | None = None,
) -> TradeReadinessReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    factor = get_factor_definition(factor_id, require_enabled=True)
    checks: list[TradeReadinessCheck] = []
    blockers: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    def add_check(name: str, status: Literal["pass", "watch", "fail"], detail: str, action: str) -> None:
        checks.append(TradeReadinessCheck(name=name, status=status, detail=detail, action=action))
        if status == "fail":
            blockers.append(f"{name}: {detail}")
        elif status == "watch":
            warnings.append(f"{name}: {detail}")
        if action:
            actions.append(action)

    filter_payload = FilterValidationRequest(
        min_rsi=min_rsi,
        min_trend=min_trend,
        signal=signal,
    )
    filter_result = validate_filters(filter_payload)
    add_check(
        "过滤器",
        "pass" if filter_result.valid and not filter_result.warnings else "watch" if filter_result.valid else "fail",
        f"当前条件命中 {filter_result.match_count} 个标的。",
        filter_result.suggestions[0] if filter_result.suggestions else "保持过滤器并进入准入复核。",
    )

    regime = market_regime_report()
    add_check(
        "市场状态",
        "fail" if regime.risk_level == "risk" else "watch" if regime.risk_level == "watch" else "pass",
        f"{regime.label} / {regime.risk_level}；{regime.summary}",
        regime.actions[0] if regime.actions else "维持当前状态监控。",
    )

    robustness = robustness_report(base_symbol)
    factor_robustness = next((item for item in robustness.factors if item.factor_id == factor_id), None)
    if factor_robustness:
        add_check(
            "稳健性",
            factor_robustness.status,
            f"分数 {factor_robustness.score}/100，回测 {factor_robustness.backtest_status}，前向 {factor_robustness.forward_status}，滚动 {factor_robustness.walk_forward_status}，压力 {factor_robustness.stress_status}。",
            factor_robustness.actions[0] if factor_robustness.actions else "定期复验稳健性。",
        )
    else:
        add_check("稳健性", "fail", "该因子没有稳健性结果。", "先运行回测、前向、滚动和压力测试。")

    drift = strategy_drift_report(base_symbol)
    factor_drift = next((item for item in drift.factors if item.factor_id == factor_id), None)
    if factor_drift:
        add_check(
            "漂移",
            factor_drift.status,
            f"漂移分 {factor_drift.drift_score}/100，状态匹配 {factor_drift.regime_alignment}。",
            factor_drift.actions[0] if factor_drift.actions else "继续跟踪前向衰减。",
        )
    else:
        add_check("漂移", "watch", "缺少该因子的漂移检查。", "补齐回测与前向记录后重新评估。")

    allocation = strategy_allocation_report(base_symbol)
    allocation_item = next((item for item in allocation.items if item.factor_id == factor_id), None)
    recommended_weight = 0.0
    max_weight = 0.0
    if allocation_item:
        recommended_weight = allocation_item.target_weight
        max_weight = allocation_item.max_weight
        allocation_status: Literal["pass", "watch", "fail"] = "pass" if allocation_item.status in {"increase", "hold"} and allocation_item.target_weight > 0 else "watch" if allocation_item.status == "reduce" else "fail"
        add_check(
            "仓位配置",
            allocation_status,
            f"建议 {allocation_item.status}，目标 {allocation_item.target_weight:.1f}%，上限 {allocation_item.max_weight:.1f}%，信心 {allocation_item.confidence}/100。",
            allocation_item.controls[0] if allocation_item.controls else "按目标权重限制模拟仓位。",
        )
    else:
        add_check("仓位配置", "fail", "配置模型没有给出该因子的正权重。", "暂不新增仓位，先提升因子验证质量。")

    revalidation = revalidation_queue_report(limit=10, factor_id=factor_id)
    revalidation_hits = [item for item in revalidation.items if item.symbol == base_symbol and item.factor_id == factor_id]
    if revalidation_hits:
        hit = revalidation_hits[0]
        add_check(
            "复验缺口",
            hit.status,
            f"待补 {', '.join(hit.due_tasks)}；优先级 {hit.priority}。",
            hit.actions[0] if hit.actions else "先补齐复验任务。",
        )
    else:
        add_check("复验缺口", "pass", "未发现该标的/因子的缺失、过期或失败复验任务。", "维持复验节奏。")

    latest_news = news_events(base_symbol, limit=5)
    negative_news = [item for item in latest_news if item.sentiment == "bearish" and item.impact_score >= 70]
    high_impact_news = [item for item in latest_news if item.impact_score >= 80]
    if negative_news:
        news_status: Literal["pass", "watch", "fail"] = "fail"
        news_detail = f"{len(negative_news)} 条高影响负面新闻。"
    elif high_impact_news:
        news_status = "watch"
        news_detail = f"{len(high_impact_news)} 条高影响新闻，需确认价格反应。"
    else:
        news_status = "pass"
        news_detail = "近端新闻未形成高影响负面阻断。"
    add_check("新闻风险", news_status, news_detail, "把关键新闻标记与价格突破放在同一时间轴复核。")

    portfolio_risk = get_portfolio_risk_report()
    if portfolio_risk.warnings:
        portfolio_status: Literal["pass", "watch", "fail"] = "watch"
        portfolio_detail = portfolio_risk.warnings[0]
    else:
        portfolio_status = "pass"
        portfolio_detail = "模拟组合未触发集中度或净敞口警告。"
    add_check(
        "组合风险",
        portfolio_status,
        portfolio_detail,
        portfolio_risk.actions[0] if portfolio_risk.actions else "保持组合风险监控。",
    )

    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(0, min(100, 100 - fail_count * 24 - watch_count * 9))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
        decision: Literal["go", "size_down", "hold", "block"] = "block"
        recommended_weight = 0.0
        actions.append("交易门禁阻断：处理 fail 项后再重新评估。")
    elif watch_count >= 3:
        status = "watch"
        decision = "hold"
        recommended_weight = round(min(recommended_weight, max_weight * 0.5), 1)
        actions.append("观察项偏多：先保持观察或纸面交易，不新增实盘。")
    elif watch_count:
        status = "watch"
        decision = "size_down"
        recommended_weight = round(min(recommended_weight, max_weight * 0.65), 1)
        actions.append("允许小仓位模拟，但需要保留预警和复验任务。")
    else:
        status = "pass"
        decision = "go"
        actions.append("门禁通过：可按建议权重进入小规模模拟。")
    summary = f"{base_symbol} · {factor.name} 交易门禁 {decision}，分数 {score}/100，阻断 {fail_count}，观察 {watch_count}。"
    return TradeReadinessReport(
        symbol=base_symbol,
        factor_id=factor.id,
        factor_name=factor.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        decision=decision,
        score=score,
        recommended_weight=round(max(0.0, recommended_weight), 1),
        max_weight=round(max_weight, 1),
        summary=summary,
        checks=checks,
        blockers=dedupe(blockers),
        warnings=dedupe(warnings),
        actions=dedupe(actions)[:8],
    )


def regime_risk_gate_report(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
) -> RegimeRiskGate:
    base_symbol = symbol.upper()
    readiness = trade_readiness_report(symbol=base_symbol, factor_id=factor_id, interval=interval)
    allocation = strategy_allocation_report(base_symbol)
    transition = market_regime_transition_report()
    checks: list[RegimeRiskGateCheck] = []
    blockers: list[str] = []
    actions: list[str] = []

    def add_check(name: str, status: Literal["pass", "watch", "fail"], detail: str, action: str) -> None:
        checks.append(RegimeRiskGateCheck(name=name, status=status, detail=detail, action=action))
        if status == "fail":
            blockers.append(f"{name}: {detail}")
        if action:
            actions.append(action)

    regime = transition.current
    add_check(
        "市场状态",
        "fail" if regime.risk_level == "risk" else "watch" if regime.risk_level == "watch" else "pass",
        f"{regime.label} / {regime.risk_level}；广度 {regime.breadth_score:+.1f}，波动 {regime.volatility_score:.2f}%。",
        regime.actions[0] if regime.actions else "继续监控市场状态。",
    )
    add_check(
        "状态迁移",
        transition.status,
        f"迁移分 {transition.transition_score}/100；{transition.summary}",
        transition.actions[0] if transition.actions else "等待下一次状态确认。",
    )
    add_check(
        "交易门禁",
        readiness.status,
        f"{readiness.decision}，分数 {readiness.score}/100，建议权重 {readiness.recommended_weight:.1f}%。",
        readiness.actions[0] if readiness.actions else "保持交易门禁监控。",
    )
    add_check(
        "策略预算",
        allocation.status,
        f"预算 {allocation.risk_budget:.1f}%，目标合计 {allocation.total_target_weight:.1f}%。",
        allocation.actions[0] if allocation.actions else "按预算上限限制新增模拟。",
    )

    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(0, min(100, 100 - fail_count * 28 - watch_count * 10 - int(transition.transition_score * 0.25)))
    allowed_weight = readiness.recommended_weight
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
        decision: Literal["open", "reduced", "paused", "blocked"] = "blocked"
        allowed_weight = 0.0
        actions.append("状态风险闸门阻断：先处理 fail 项，不新增模拟。")
    elif transition.status == "fail" or regime.risk_level == "risk":
        status = "fail"
        decision = "paused"
        allowed_weight = 0.0
        actions.append("状态切换风险偏高：暂停新增策略。")
    elif watch_count >= 2 or readiness.decision in {"hold", "block"}:
        status = "watch"
        decision = "paused"
        allowed_weight = 0.0
        actions.append("观察项偏多：保持跟踪，不新增仓位。")
    elif watch_count or readiness.decision == "size_down" or transition.status == "watch":
        status = "watch"
        decision = "reduced"
        allowed_weight = round(min(allowed_weight, readiness.max_weight * 0.5), 1)
        actions.append("允许降权模拟，等待市场状态确认。")
    else:
        status = "pass"
        decision = "open"
        actions.append("状态风险闸门通过，可按交易门禁权重执行小规模模拟。")
    summary = f"{base_symbol} · {readiness.factor_name} 状态风险闸门 {decision}，分数 {score}/100，允许权重 {allowed_weight:.1f}%。"
    return RegimeRiskGate(
        symbol=base_symbol,
        factor_id=readiness.factor_id,
        factor_name=readiness.factor_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        decision=decision,
        score=score,
        allowed_weight=round(max(0.0, allowed_weight), 1),
        regime=regime,
        transition=transition,
        summary=summary,
        checks=checks,
        blockers=dedupe(blockers),
        actions=dedupe(actions)[:8],
    )


def pearson_correlation(left: list[int], right: list[int]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    denominator = (left_var * right_var) ** 0.5
    return round(numerator / denominator, 3) if denominator else 0.0


def factor_correlation_report(symbol: str, interval: Literal["15m", "1h", "4h", "1d"] = "1h", limit: int = 180) -> FactorCorrelationReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    factor_defs = list_factor_definitions(enabled_only=True)
    history = candles(base_symbol, interval=interval, limit=max(80, min(260, limit)))
    has_recent_positive_news = any(event.sentiment == "bullish" and event.impact_score >= 55 for event in news_events(base_symbol, limit=20))
    start = 21
    factor_signals: dict[str, list[int]] = {}
    exposures: list[FactorExposureItem] = []
    for factor in factor_defs:
        signals = [backtest_position(factor.id, history, index, has_recent_positive_news) for index in range(start, len(history))]
        factor_signals[factor.id] = signals
        active_signals = sum(signals)
        signal_changes = sum(1 for previous, current in zip(signals, signals[1:]) if previous != current)
        exposures.append(
            FactorExposureItem(
                factor_id=factor.id,
                factor_name=factor.name,
                exposure_rate=round(active_signals / max(1, len(signals)) * 100, 1),
                active_signals=active_signals,
                signal_changes=signal_changes,
            )
        )
    factor_by_id = {factor.id: factor for factor in factor_defs}
    pairs: list[FactorCorrelationPair] = []
    for left_index, left_factor in enumerate(factor_defs):
        for right_factor in factor_defs[left_index + 1 :]:
            left_signals = factor_signals[left_factor.id]
            right_signals = factor_signals[right_factor.id]
            correlation = pearson_correlation(left_signals, right_signals)
            overlap = sum(1 for left, right in zip(left_signals, right_signals) if left and right)
            union = sum(1 for left, right in zip(left_signals, right_signals) if left or right)
            overlap_rate = round(overlap / max(1, union) * 100, 1)
            if abs(correlation) >= 0.82 or overlap_rate >= 70:
                pair_status: Literal["pass", "watch", "fail"] = "fail"
            elif abs(correlation) >= 0.58 or overlap_rate >= 45:
                pair_status = "watch"
            else:
                pair_status = "pass"
            pairs.append(
                FactorCorrelationPair(
                    factor_a_id=left_factor.id,
                    factor_a_name=left_factor.name,
                    factor_b_id=right_factor.id,
                    factor_b_name=right_factor.name,
                    correlation=correlation,
                    overlap_rate=overlap_rate,
                    status=pair_status,
                )
            )
    pairs.sort(key=lambda item: (abs(item.correlation), item.overlap_rate), reverse=True)
    max_correlation = max((abs(item.correlation) for item in pairs), default=0.0)
    fail_count = sum(item.status == "fail" for item in pairs)
    watch_count = sum(item.status == "watch" for item in pairs)
    avg_exposure = sum(item.exposure_rate for item in exposures) / max(1, len(exposures))
    crowding_score = int(max(0, min(100, 100 - fail_count * 22 - watch_count * 10 - max(0, avg_exposure - 55) * 0.4 - max_correlation * 18)))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        crowded = pairs[0]
        actions.append(f"优先拆分 {crowded.factor_a_name} 与 {crowded.factor_b_name}，避免重复信号叠加。")
        actions.append("配置建议中降低高相关因子的合计权重。")
    elif watch_count:
        actions.append("保留相关性观察，运行更多市场状态下的样本验证。")
    else:
        actions.append("因子相关性可控，可继续按配置建议小规模模拟。")
    if any(item.exposure_rate >= 75 for item in exposures):
        actions.append("存在高暴露因子，检查是否长期满仓导致回测乐观。")
    regime = market_regime_report()
    return FactorCorrelationReport(
        symbol=base_symbol,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        crowding_score=crowding_score,
        max_correlation=round(max_correlation, 3),
        summary=f"{base_symbol} 因子拥挤度 {crowding_score}/100，最高相关 {max_correlation:.2f}，观察 {watch_count} 对，失败 {fail_count} 对。",
        regime=regime,
        exposures=sorted(exposures, key=lambda item: item.exposure_rate, reverse=True),
        pairs=pairs,
        actions=dedupe(actions),
    )


def regime_exposure_report(interval: Literal["15m", "1h", "4h", "1d"] = "1h") -> RegimeExposureReport:
    regime = market_regime_report()
    items: list[RegimeExposureItem] = []
    for snapshot in list_watchlist_snapshots():
        allocation = strategy_allocation_report(snapshot.symbol)
        correlation = factor_correlation_report(snapshot.symbol, interval=interval)
        target_weight = round(sum(item.target_weight for item in allocation.items if item.status != "avoid"), 1)
        dominant = max(allocation.items, key=lambda item: item.target_weight, default=None)
        notes = [
            f"风险预算 {allocation.risk_budget:.1f}%，目标权重 {target_weight:.1f}%。",
            f"因子拥挤度 {correlation.crowding_score}/100，状态 {correlation.status}。",
        ]
        if allocation.status == "fail" or correlation.status == "fail":
            status: Literal["pass", "watch", "fail"] = "fail"
        elif allocation.status == "watch" or correlation.status == "watch" or target_weight > allocation.risk_budget * 100:
            status = "watch"
        else:
            status = "pass"
        items.append(
            RegimeExposureItem(
                symbol=snapshot.symbol,
                allocation_status=allocation.status,
                correlation_status=correlation.status,
                risk_budget=round(allocation.risk_budget, 1),
                target_weight=target_weight,
                crowding_score=correlation.crowding_score,
                dominant_factor=dominant.factor_name if dominant and dominant.target_weight > 0 else None,
                status=status,
                notes=notes,
            )
        )
    total_target = round(sum(item.target_weight for item in items), 1)
    average_budget = round(sum(item.risk_budget for item in items) / max(1, len(items)), 1)
    average_crowding = int(round(sum(item.crowding_score for item in items) / max(1, len(items)))) if items else 0
    fail_count = sum(item.status == "fail" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    score = int(max(0, min(100, average_crowding - fail_count * 18 - watch_count * 8 - max(0, total_target - average_budget * max(1, len(items))) * 0.2)))
    if fail_count or regime.risk_level == "risk":
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count or regime.risk_level == "watch":
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if regime.risk_level in {"watch", "risk"}:
        actions.append("当前市场状态偏谨慎，降低新增仓位并优先处理 fail 暴露。")
    if fail_count:
        actions.append("暂停 fail 标的的新增策略预算，先拆分拥挤因子或补齐验证。")
    if watch_count:
        actions.append("watch 标的维持小规模前向观察，不扩大目标权重。")
    if total_target > average_budget * max(1, len(items)):
        actions.append("总目标权重高于平均风险预算，检查是否多标的重复暴露。")
    if not actions:
        actions.append("组合暴露与当前市场状态匹配，可继续按门禁执行。")
    return RegimeExposureReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        regime=regime,
        total_target_weight=total_target,
        average_risk_budget=average_budget,
        average_crowding_score=average_crowding,
        summary=f"市场状态暴露 {status}：{regime.label}/{regime.risk_level}，总目标权重 {total_target:.1f}%，平均拥挤度 {average_crowding}/100。",
        items=items,
        actions=dedupe(actions),
    )


def classify_candle_regime(history: list[Candle], index: int) -> Literal["risk_on", "risk_off", "trend", "range", "volatile"]:
    start = max(0, index - 20)
    recent = history[start : index + 1]
    if len(recent) < 8:
        return "range"
    latest = history[index]
    change = latest.close / recent[0].close - 1
    volatility = (max(item.high for item in recent) - min(item.low for item in recent)) / latest.close if latest.close else 0
    if volatility >= 0.075:
        return "volatile"
    if change <= -0.035 and latest.rsi < 45:
        return "risk_off"
    if change >= 0.035 and latest.rsi >= 55:
        return "risk_on"
    if latest.close > latest.ma20 > latest.ma60 and latest.rsi >= 52:
        return "trend"
    return "range"


def regime_attribution_report(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 220,
) -> RegimeAttributionReport:
    init_db()
    base_symbol = symbol.upper()
    valid_symbols = {item.symbol for item in list_supported_market_symbols()}
    if base_symbol not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol not found")
    factor = get_factor_definition(factor_id, require_enabled=True)
    history = candles(base_symbol, interval=interval, limit=max(100, min(320, limit)))
    has_recent_positive_news = any(event.sentiment == "bullish" and event.impact_score >= 55 for event in news_events(base_symbol, limit=20))
    buckets: dict[str, dict[str, object]] = {
        label: {"returns": [], "equity": [10000.0], "trades": 0, "wins": 0, "active": 0, "bars": 0}
        for label in ["risk_on", "risk_off", "trend", "range", "volatile"]
    }
    previous_position_by_regime = {label: 0 for label in buckets}
    entry_equity_by_regime = {label: 10000.0 for label in buckets}
    for index in range(21, len(history)):
        regime_label = classify_candle_regime(history, index)
        bucket = buckets[regime_label]
        position = backtest_position(factor_id, history, index - 1, has_recent_positive_news)
        previous_position = previous_position_by_regime[regime_label]
        equity_curve = bucket["equity"]  # type: ignore[assignment]
        equity = float(equity_curve[-1])  # type: ignore[index]
        if position != previous_position:
            if previous_position and not position:
                trade_return = equity / entry_equity_by_regime[regime_label] - 1
                bucket["trades"] = int(bucket["trades"]) + 1
                if trade_return > 0:
                    bucket["wins"] = int(bucket["wins"]) + 1
            if position and not previous_position:
                entry_equity_by_regime[regime_label] = equity
            previous_position_by_regime[regime_label] = position
        if position:
            equity *= 1 + (history[index].close / history[index - 1].close - 1)
            bucket["active"] = int(bucket["active"]) + 1
        equity_curve.append(round(equity, 2))  # type: ignore[union-attr]
        bucket["bars"] = int(bucket["bars"]) + 1
    slices: list[RegimeAttributionSlice] = []
    for label, bucket in buckets.items():
        equity_curve = bucket["equity"]  # type: ignore[assignment]
        bars = int(bucket["bars"])
        if bars == 0:
            continue
        start_equity = float(equity_curve[0])  # type: ignore[index]
        end_equity = float(equity_curve[-1])  # type: ignore[index]
        total_return = (end_equity / start_equity - 1) * 100 if start_equity else 0.0
        peak = start_equity
        max_drawdown = 0.0
        for value in equity_curve:  # type: ignore[union-attr]
            peak = max(peak, float(value))
            max_drawdown = max(max_drawdown, (peak - float(value)) / peak if peak else 0.0)
        trades = int(bucket["trades"])
        win_rate = int(bucket["wins"]) / trades * 100 if trades else 0.0
        exposure = int(bucket["active"]) / bars * 100 if bars else 0.0
        if total_return > 0 and max_drawdown * 100 <= 12:
            status: Literal["pass", "watch", "fail"] = "pass"
        elif total_return > -3 and max_drawdown * 100 <= 20:
            status = "watch"
        else:
            status = "fail"
        notes = [
            f"样本 {bars} 根，暴露 {exposure:.1f}%。",
            f"收益 {total_return:+.2f}%，回撤 {max_drawdown * 100:.2f}%。",
        ]
        slices.append(
            RegimeAttributionSlice(
                regime_label=label,  # type: ignore[arg-type]
                bars=bars,
                total_return=round(total_return, 2),
                max_drawdown=round(max_drawdown * 100, 2),
                trades=trades,
                win_rate=round(win_rate, 1),
                exposure_rate=round(exposure, 1),
                status=status,
                notes=notes,
            )
        )
    slices.sort(key=lambda item: item.total_return, reverse=True)
    best = slices[0] if slices else None
    worst = slices[-1] if slices else None
    fail_count = sum(item.status == "fail" for item in slices)
    watch_count = sum(item.status == "watch" for item in slices)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if worst and worst.status == "fail":
        actions.append(f"{worst.regime_label} 中表现最弱，当前状态接近时降低或暂停该因子。")
    if best and best.status == "pass":
        actions.append(f"{best.regime_label} 中表现最好，可作为该因子的优先市场状态。")
    if any(item.exposure_rate >= 75 for item in slices):
        actions.append("部分状态下暴露率偏高，检查是否缺少退出条件。")
    if not actions:
        actions.append("按市场状态持续观察该因子表现差异。")
    return RegimeAttributionReport(
        symbol=base_symbol,
        factor_id=factor.id,
        factor_name=factor.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        interval=interval,
        status=status,
        summary=f"{base_symbol} · {factor.name} 状态归因：最佳 {best.regime_label if best else '--'}，最弱 {worst.regime_label if worst else '--'}，状态 {status}。",
        best_regime=best.regime_label if best else None,
        worst_regime=worst.regime_label if worst else None,
        slices=slices,
        actions=dedupe(actions),
    )


def regime_factor_fit_report(
    symbol: str,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 6,
) -> RegimeFactorFitReport:
    base_symbol = symbol.upper()
    regime = market_regime_report()
    leaderboard = factor_leaderboard_report(base_symbol)
    items: list[RegimeFactorFitItem] = []
    current_regime = regime.label
    for factor in leaderboard.items:
        attribution = regime_attribution_report(base_symbol, factor_id=factor.factor_id, interval=interval)
        score = int(factor.composite_score * 0.55 + factor.robustness_score * 0.25)
        reasons = [
            f"排行榜综合分 {factor.composite_score}/100，稳健性 {factor.robustness_score}/100。",
            f"当前市场状态 {current_regime}。",
        ]
        actions: list[str] = []
        if attribution.best_regime == current_regime:
            regime_fit: Literal["aligned", "neutral", "misaligned"] = "aligned"
            score += 18
            reasons.append(f"历史归因显示 {current_regime} 是该因子的最佳状态。")
        elif attribution.worst_regime == current_regime:
            regime_fit = "misaligned"
            score -= 22
            reasons.append(f"历史归因显示 {current_regime} 是该因子的最弱状态。")
            actions.append("当前市场状态下先降权或暂停该因子。")
        else:
            regime_fit = "neutral"
            score += 4
            reasons.append(f"{current_regime} 不是该因子的极端优势或弱势状态。")
        if attribution.status == "pass":
            score += 8
            reasons.append("状态归因整体通过。")
        elif attribution.status == "watch":
            score -= 6
            reasons.append("状态归因需要观察。")
            actions.append("补充前向样本后再扩大权重。")
        else:
            score -= 16
            reasons.append("状态归因失败。")
            actions.append("检查该因子在不同状态下的退出条件。")
        if factor.status == "fail":
            score -= 12
            actions.append("因子基础稳健性失败，不进入实盘候选。")
        clipped_score = max(0, min(100, score))
        if regime_fit == "misaligned" or attribution.status == "fail" or factor.status == "fail" or clipped_score < 45:
            status: Literal["pass", "watch", "fail"] = "fail"
            recommendation = "当前状态暂停"
        elif regime_fit == "aligned" and clipped_score >= 70 and factor.status == "pass":
            status = "pass"
            recommendation = "当前状态优先"
        else:
            status = "watch"
            recommendation = "当前状态观察"
        if not actions:
            actions.append("保留在模拟候选中，观察状态切换后的得分变化。")
        items.append(
            RegimeFactorFitItem(
                rank=0,
                factor_id=factor.factor_id,
                factor_name=factor.factor_name,
                current_regime=current_regime,
                status=status,
                score=clipped_score,
                regime_fit=regime_fit,
                leaderboard_score=factor.composite_score,
                robustness_score=factor.robustness_score,
                best_regime=attribution.best_regime,
                worst_regime=attribution.worst_regime,
                recommendation=recommendation,
                reasons=dedupe(reasons),
                actions=dedupe(actions + attribution.actions[:1]),
            )
        )
    ranked = sorted(items, key=lambda item: (item.score, item.leaderboard_score), reverse=True)
    limited = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[: max(1, min(12, limit))])]
    pass_count = sum(item.status == "pass" for item in limited)
    fail_count = sum(item.status == "fail" for item in limited)
    status: Literal["pass", "watch", "fail"]
    if fail_count >= max(1, len(limited) // 2 + 1):
        status = "fail"
    elif pass_count:
        status = "pass"
    else:
        status = "watch"
    return RegimeFactorFitReport(
        symbol=base_symbol,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        regime=regime,
        summary=f"{base_symbol} 当前 {current_regime} 状态下，{pass_count} 个因子优先，{sum(item.status == 'watch' for item in limited)} 个观察，{fail_count} 个暂停。",
        items=limited,
    )


def strategy_leaderboard_report(regime: MarketRegime | None = None) -> StrategyLeaderboard:
    strategies = list_strategy_templates()
    validations = list_strategy_validations(limit=100)
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in validations:
        if validation.strategy_id not in latest_by_strategy:
            latest_by_strategy[validation.strategy_id] = validation
    regime = regime or lightweight_market_regime_report()
    items: list[StrategyLeaderboardItem] = []
    for strategy in strategies:
        validation = latest_by_strategy.get(strategy.id)
        score = 45
        reasons: list[str] = []
        if strategy.enabled:
            score += 10
            reasons.append("策略已启用。")
        else:
            score -= 20
            reasons.append("策略已停用。")
        if strategy.factor_ids:
            score += min(15, len(strategy.factor_ids) * 5)
            reasons.append(f"绑定 {len(strategy.factor_ids)} 个因子。")
        else:
            score -= 15
            reasons.append("未绑定因子。")
        if strategy.default_filters:
            score += min(10, len(strategy.default_filters) * 2)
            reasons.append("包含默认过滤器。")
        else:
            score -= 5
            reasons.append("缺少默认过滤器。")
        if validation:
            latest_status: Literal["pass", "watch", "fail", "missing"] = validation.pass_fail
            reasons.append(f"最近验证 {validation.pass_fail}，回测均值 {validation.average_backtest_return:+.2f}%，前向均值 {validation.average_forward_return:+.2f}%。")
            if validation.pass_fail == "pass":
                score += 25
            elif validation.pass_fail == "watch":
                score += 8
            else:
                score -= 25
        else:
            latest_status = "missing"
            score -= 15
            reasons.append("缺少策略验证记录。")
        if regime.risk_level == "risk" and any(item in strategy.category for item in ["趋势", "突破"]):
            score -= 12
            reasons.append("当前风险状态下趋势/突破策略需降权。")
        score = max(0, min(100, score))
        if not strategy.enabled or latest_status == "fail" or score < 35:
            status: Literal["pass", "watch", "fail"] = "fail"
            recommendation = "暂不启用"
        elif latest_status == "pass" and score >= 75:
            status = "pass"
            recommendation = "优先观察"
        else:
            status = "watch"
            recommendation = "先验证"
        items.append(
            StrategyLeaderboardItem(
                rank=0,
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=score,
                latest_validation_status=latest_status,
                average_backtest_return=validation.average_backtest_return if validation else None,
                average_forward_return=validation.average_forward_return if validation else None,
                factor_count=len(strategy.factor_ids),
                filter_count=len(strategy.default_filters),
                recommendation=recommendation,
                reasons=reasons,
            )
        )
    ranked = sorted(items, key=lambda item: (item.score, item.enabled), reverse=True)
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked)]
    return StrategyLeaderboard(
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=f"策略排行榜：{sum(item.status == 'pass' for item in ranked)} 个优先，{sum(item.status == 'watch' for item in ranked)} 个待验证，{sum(item.status == 'fail' for item in ranked)} 个暂停。",
        items=ranked,
    )


def strategy_regime_fit_report(limit: int = 8) -> StrategyRegimeFitReport:
    strategies = list_strategy_templates()
    validations = list_strategy_validations(limit=100)
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in validations:
        if validation.strategy_id not in latest_by_strategy:
            latest_by_strategy[validation.strategy_id] = validation
    regime = market_regime_report()
    symbols = [item.symbol for item in list_supported_market_symbols()]
    reference_symbol = symbols[0] if symbols else "AAPL"
    factor_fit = regime_factor_fit_report(reference_symbol, limit=12)
    factor_fit_by_id = {item.factor_id: item for item in factor_fit.items}
    items: list[StrategyRegimeFitItem] = []
    for strategy in strategies:
        score = 45
        reasons: list[str] = [f"当前市场状态 {regime.label} / {regime.risk_level}。"]
        actions: list[str] = []
        if strategy.enabled:
            score += 8
            reasons.append("策略已启用。")
        else:
            score -= 25
            reasons.append("策略已停用。")
            actions.append("如需纳入模拟，先启用策略。")
        try:
            filter_validation = validate_filters(filter_request_from_dict(strategy.default_filters))
        except HTTPException:
            filter_validation = FilterValidationResult(
                valid=False,
                match_count=0,
                normalized_filters=strategy.default_filters,
                warnings=["默认过滤器参数无效。"],
                suggestions=["修正策略默认过滤器后再验证。"],
                regime=regime,
            )
        if not filter_validation.valid:
            filter_status: Literal["pass", "watch", "fail"] = "fail"
        elif filter_validation.match_count < 2 or filter_validation.warnings:
            filter_status = "watch"
        else:
            filter_status = "pass"
        if filter_status == "pass":
            score += 12
            reasons.append(f"默认过滤器通过，命中 {filter_validation.match_count} 个标的。")
        elif filter_status == "watch":
            score += 2
            reasons.append(f"默认过滤器需观察，命中 {filter_validation.match_count} 个标的。")
            actions.extend(filter_validation.suggestions[:1])
        else:
            score -= 18
            reasons.append("默认过滤器未通过。")
            actions.extend(filter_validation.suggestions[:2])
        strategy_factor_fits: list[StrategyRegimeFitFactor] = []
        for factor_id in strategy.factor_ids:
            fit = factor_fit_by_id.get(factor_id)
            if fit is None:
                strategy_factor_fits.append(
                    StrategyRegimeFitFactor(
                        factor_id=factor_id,
                        factor_name=factor_id,
                        score=0,
                        status="fail",
                        regime_fit="neutral",
                    )
                )
                score -= 10
                reasons.append(f"{factor_id} 缺少当前状态适配结果。")
                actions.append(f"先补齐 {factor_id} 的因子验证和状态归因。")
                continue
            strategy_factor_fits.append(
                StrategyRegimeFitFactor(
                    factor_id=fit.factor_id,
                    factor_name=fit.factor_name,
                    score=fit.score,
                    status=fit.status,
                    regime_fit=fit.regime_fit,
                )
            )
        if strategy_factor_fits:
            average_factor_score = sum(item.score for item in strategy_factor_fits) / len(strategy_factor_fits)
            score += int((average_factor_score - 50) * 0.35)
            aligned_count = sum(item.regime_fit == "aligned" for item in strategy_factor_fits)
            fail_count = sum(item.status == "fail" for item in strategy_factor_fits)
            reasons.append(f"绑定因子平均适配分 {average_factor_score:.0f}/100，{aligned_count} 个状态匹配。")
            if fail_count:
                score -= fail_count * 8
                actions.append("移除或降权当前状态下暂停的因子。")
        else:
            score -= 20
            reasons.append("策略未绑定因子。")
            actions.append("至少绑定一个已启用因子。")
        latest_validation = latest_by_strategy.get(strategy.id)
        if latest_validation is None:
            latest_status: Literal["pass", "watch", "fail", "missing"] = "missing"
            score -= 12
            reasons.append("缺少策略验证记录。")
            actions.append("运行策略验证以补齐回测和前向测试。")
        else:
            latest_status = latest_validation.pass_fail
            reasons.append(f"最近验证 {latest_validation.pass_fail}，回测均值 {latest_validation.average_backtest_return:+.2f}%，前向均值 {latest_validation.average_forward_return:+.2f}%。")
            if latest_validation.pass_fail == "pass":
                score += 18
            elif latest_validation.pass_fail == "watch":
                score += 4
                actions.append("扩大样本或降低仓位后继续前向观察。")
            else:
                score -= 22
                actions.append("最近策略验证失败，先暂停模拟。")
        clipped_score = max(0, min(100, score))
        factor_fail_count = sum(item.status == "fail" for item in strategy_factor_fits)
        if not strategy.enabled or filter_status == "fail" or latest_status == "fail" or factor_fail_count >= max(1, len(strategy_factor_fits)):
            status: Literal["pass", "watch", "fail"] = "fail"
            recommendation = "当前状态暂停"
        elif clipped_score >= 75 and latest_status == "pass" and factor_fail_count == 0:
            status = "pass"
            recommendation = "当前状态优先"
        else:
            status = "watch"
            recommendation = "当前状态观察"
        if not actions:
            actions.append("保留在前向观察池，持续监控状态切换。")
        items.append(
            StrategyRegimeFitItem(
                rank=0,
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=clipped_score,
                current_regime=regime.label,
                filter_status=filter_status,
                latest_validation_status=latest_status,
                recommendation=recommendation,
                factor_fits=strategy_factor_fits,
                reasons=dedupe(reasons),
                actions=dedupe(actions),
            )
        )
    ranked = sorted(items, key=lambda item: (item.score, item.enabled), reverse=True)
    limited = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[: max(1, min(20, limit))])]
    pass_count = sum(item.status == "pass" for item in limited)
    fail_count = sum(item.status == "fail" for item in limited)
    if fail_count >= max(1, len(limited) // 2 + 1):
        status: Literal["pass", "watch", "fail"] = "fail"
    elif pass_count:
        status = "pass"
    else:
        status = "watch"
    return StrategyRegimeFitReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        regime=regime,
        summary=f"当前 {regime.label} 状态下，{pass_count} 个策略优先，{sum(item.status == 'watch' for item in limited)} 个观察，{fail_count} 个暂停。",
        items=limited,
    )


def strategy_regime_matrix_report(current: MarketRegime | None = None) -> StrategyRegimeMatrixReport:
    strategies = list_strategy_templates()
    validations = list_strategy_validations(limit=100)
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in validations:
        if validation.strategy_id not in latest_by_strategy:
            latest_by_strategy[validation.strategy_id] = validation
    regimes: list[Literal["risk_on", "risk_off", "trend", "range", "volatile"]] = ["risk_on", "risk_off", "trend", "range", "volatile"]
    current = current or lightweight_market_regime_report()
    items: list[StrategyRegimeMatrixItem] = []
    for strategy in strategies:
        validation = latest_by_strategy.get(strategy.id)
        latest_status: Literal["pass", "watch", "fail", "missing"] = validation.pass_fail if validation else "missing"
        cells: list[StrategyRegimeMatrixCell] = []
        for regime_label in regimes:
            score = 50
            rationale: list[str] = []
            if strategy.enabled:
                score += 10
                rationale.append("策略已启用。")
            else:
                score -= 35
                rationale.append("策略已停用。")
            if latest_status == "pass":
                score += 18
                rationale.append("最近验证通过。")
            elif latest_status == "watch":
                score += 4
                rationale.append("最近验证观察。")
            elif latest_status == "fail":
                score -= 28
                rationale.append("最近验证失败。")
            else:
                score -= 12
                rationale.append("缺少策略验证。")
            category_text = strategy.category + " " + " ".join(strategy.factor_ids)
            if regime_label in {"risk_on", "trend"} and any(token in category_text for token in ["趋势", "突破", "momentum", "vol-compression"]):
                score += 18
                rationale.append(f"{regime_label} 更适合趋势/突破策略。")
            if regime_label in {"range", "volatile"} and any(token in category_text for token in ["反转", "rsi-reversion"]):
                score += 16
                rationale.append(f"{regime_label} 更适合均值回归观察。")
            if regime_label == "risk_off":
                score -= 18
                rationale.append("risk_off 状态默认压低新增策略预算。")
            if regime_label == "volatile" and any(token in category_text for token in ["新闻", "event", "momentum-news"]):
                score -= 8
                rationale.append("volatile 状态下新闻/动量信号需降低追涨权重。")
            if not strategy.factor_ids:
                score -= 20
                rationale.append("未绑定因子。")
            clipped = max(0, min(100, score))
            if not strategy.enabled or latest_status == "fail" or clipped < 35:
                status: Literal["pass", "watch", "fail"] = "fail"
                decision: Literal["open", "reduced", "paused", "blocked"] = "blocked" if not strategy.enabled or latest_status == "fail" else "paused"
            elif clipped >= 72 and latest_status == "pass" and regime_label != "risk_off":
                status = "pass"
                decision = "open"
            else:
                status = "watch"
                decision = "reduced"
            cells.append(
                StrategyRegimeMatrixCell(
                    regime_label=regime_label,
                    status=status,
                    decision=decision,
                    score=clipped,
                    rationale=rationale,
                )
            )
        current_cell = next(cell for cell in cells if cell.regime_label == current.label)
        fail_count = sum(cell.status == "fail" for cell in cells)
        watch_count = sum(cell.status == "watch" for cell in cells)
        if current_cell.status == "fail" or fail_count >= 3:
            overall_status: Literal["pass", "watch", "fail"] = "fail"
        elif current_cell.status == "pass" and fail_count == 0:
            overall_status = "pass"
        else:
            overall_status = "watch"
        actions: list[str] = []
        if current_cell.decision in {"blocked", "paused"}:
            actions.append(f"当前 {current.label} 状态下暂停或阻断该策略。")
        elif current_cell.decision == "reduced":
            actions.append(f"当前 {current.label} 状态下仅小权重观察。")
        else:
            actions.append(f"当前 {current.label} 状态下可按准入门禁进入模拟。")
        if watch_count:
            actions.append("对 watch 状态补充前向、滚动样本外或压力测试。")
        items.append(
            StrategyRegimeMatrixItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                latest_validation_status=latest_status,
                overall_status=overall_status,
                cells=cells,
                actions=dedupe(actions),
            )
        )
    fail_count = sum(item.overall_status == "fail" for item in items)
    watch_count = sum(item.overall_status == "watch" for item in items)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先处理当前状态下 fail 的策略，避免进入模拟池。")
    if watch_count:
        actions.append("watch 策略只保留小权重观察，并补齐验证。")
    if not actions:
        actions.append("策略状态矩阵整体可用，按当前状态准入执行。")
    return StrategyRegimeMatrixReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        current_regime=current,
        summary=f"策略状态矩阵：当前 {current.label}/{current.risk_level}，{sum(item.overall_status == 'pass' for item in items)} 个可用，{watch_count} 个观察，{fail_count} 个暂停。",
        items=items,
        actions=dedupe(actions),
    )


def regime_scenario_risk_report() -> RegimeScenarioRiskReport:
    matrix = strategy_regime_matrix_report()
    regimes: list[Literal["risk_on", "risk_off", "trend", "range", "volatile"]] = ["risk_on", "risk_off", "trend", "range", "volatile"]
    items: list[RegimeScenarioRiskItem] = []
    for regime_label in regimes:
        cells = [cell for item in matrix.items for cell in item.cells if cell.regime_label == regime_label]
        open_count = sum(cell.decision == "open" for cell in cells)
        reduced_count = sum(cell.decision == "reduced" for cell in cells)
        paused_count = sum(cell.decision == "paused" for cell in cells)
        blocked_count = sum(cell.decision == "blocked" for cell in cells)
        average_score = round(sum(cell.score for cell in cells) / len(cells), 1) if cells else 0.0
        raw_score = average_score + open_count * 8 - reduced_count * 2 - paused_count * 7 - blocked_count * 12
        score = max(0, min(100, int(round(raw_score))))
        if blocked_count >= max(1, len(cells) // 2) or score < 45:
            status: Literal["pass", "watch", "fail"] = "fail"
        elif reduced_count + paused_count + blocked_count or score < 72:
            status = "watch"
        else:
            status = "pass"
        base_budget = {
            "risk_on": 100.0,
            "trend": 90.0,
            "range": 70.0,
            "volatile": 45.0,
            "risk_off": 25.0,
        }[regime_label]
        risk_budget = max(0.0, min(100.0, round(base_budget * (score / 100), 1)))
        actions: list[str] = []
        if open_count:
            actions.append(f"允许 {open_count} 个 open 策略按预算运行。")
        if reduced_count:
            actions.append(f"{reduced_count} 个策略只保留降权观察。")
        if paused_count or blocked_count:
            actions.append(f"{paused_count + blocked_count} 个策略在该状态下暂停或阻断。")
        if regime_label in {"risk_off", "volatile"}:
            actions.append("优先收缩追涨、新闻和突破类策略预算。")
        if not actions:
            actions.append("暂无可运行策略，等待策略验证或状态切换。")
        items.append(
            RegimeScenarioRiskItem(
                regime_label=regime_label,
                status=status,
                score=score,
                open_count=open_count,
                reduced_count=reduced_count,
                paused_count=paused_count,
                blocked_count=blocked_count,
                average_strategy_score=average_score,
                risk_budget=risk_budget,
                summary=f"{regime_label}：open {open_count}，降权 {reduced_count}，暂停 {paused_count}，阻断 {blocked_count}，预算 {risk_budget:.1f}%。",
                actions=dedupe(actions),
            )
        )
    current_item = next((item for item in items if item.regime_label == matrix.current_regime.label), None)
    fail_count = sum(item.status == "fail" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    if current_item and current_item.status == "fail":
        status: Literal["pass", "watch", "fail"] = "fail"
    elif fail_count or (current_item and current_item.status == "watch"):
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if current_item:
        actions.extend(current_item.actions[:2])
    if fail_count:
        actions.append("存在高风险状态场景，预先设置策略暂停和降权规则。")
    if watch_count:
        actions.append("watch 场景需要保持状态迁移监控和预算上限。")
    if not actions:
        actions.append("状态情景风险整体可控，按当前预算运行。")
    return RegimeScenarioRiskReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        current_regime=matrix.current_regime,
        summary=f"状态情景风险：当前 {matrix.current_regime.label}，{sum(item.status == 'pass' for item in items)} 个状态可运行，{watch_count} 个观察，{fail_count} 个高风险。",
        items=items,
        actions=dedupe(actions),
    )


def filter_similarity(left: dict[str, str | float], right: dict[str, str | float]) -> float:
    left_items = set(filter_request_from_dict(left).model_dump(exclude_none=True).items())
    right_items = set(filter_request_from_dict(right).model_dump(exclude_none=True).items())
    if not left_items and not right_items:
        return 1.0
    if not left_items or not right_items:
        return 0.0
    return len(left_items & right_items) / len(left_items | right_items)


def strategy_crowding_report() -> StrategyCrowdingReport:
    strategies = list_strategy_templates(enabled_only=True)
    matrix = strategy_regime_matrix_report()
    current_decision_by_strategy: dict[str, StrategyRegimeMatrixCell] = {}
    for item in matrix.items:
        current = next((cell for cell in item.cells if cell.regime_label == matrix.current_regime.label), None)
        if current:
            current_decision_by_strategy[item.strategy_id] = current
    pairs: list[StrategyCrowdingPair] = []
    for index, left in enumerate(strategies):
        for right in strategies[index + 1 :]:
            left_factors = set(left.factor_ids)
            right_factors = set(right.factor_ids)
            shared = sorted(left_factors & right_factors)
            factor_overlap = len(shared) / max(1, len(left_factors | right_factors))
            filters_overlap = filter_similarity(left.default_filters, right.default_filters)
            left_cell = current_decision_by_strategy.get(left.id)
            right_cell = current_decision_by_strategy.get(right.id)
            decisions = [
                left_cell.decision if left_cell else "blocked",
                right_cell.decision if right_cell else "blocked",
            ]
            concurrent_penalty = 20 if all(decision == "open" for decision in decisions) else 10 if all(decision in {"open", "reduced"} for decision in decisions) else 0
            category_bonus = 10 if left.category == right.category else 0
            overlap_score = int(round(factor_overlap * 55 + filters_overlap * 25 + concurrent_penalty + category_bonus))
            if overlap_score >= 70:
                status: Literal["pass", "watch", "fail"] = "fail"
                action = "避免同时放大，保留评分更高或前向更稳的一个策略。"
            elif overlap_score >= 42:
                status = "watch"
                action = "限制组合权重，并要求两者分开前向观察。"
            else:
                status = "pass"
                action = "拥挤风险可控，可按各自门禁执行。"
            detail = f"共享因子 {len(shared)} 个，过滤器重叠 {filters_overlap * 100:.1f}%，当前决策 {'/'.join(decisions)}。"
            pairs.append(
                StrategyCrowdingPair(
                    strategy_a_id=left.id,
                    strategy_a_name=left.name,
                    strategy_b_id=right.id,
                    strategy_b_name=right.name,
                    status=status,
                    overlap_score=max(0, min(100, overlap_score)),
                    shared_factors=shared,
                    filter_overlap=round(filters_overlap * 100, 1),
                    current_decisions=decisions,
                    detail=detail,
                    action=action,
                )
            )
    pairs.sort(key=lambda item: item.overlap_score, reverse=True)
    fail_count = sum(item.status == "fail" for item in pairs)
    watch_count = sum(item.status == "watch" for item in pairs)
    average_overlap = sum(item.overlap_score for item in pairs) / max(1, len(pairs))
    score = int(max(0, min(100, 100 - average_overlap * 0.6 - fail_count * 18 - watch_count * 8)))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("先拆分 fail 策略对，避免共享因子和过滤器同时放大。")
    if watch_count:
        actions.append("watch 策略对设置组合权重上限和独立前向验证。")
    if not actions:
        actions.append("策略库拥挤风险可控，按策略门禁执行。")
    return StrategyCrowdingReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        current_regime=matrix.current_regime,
        summary=f"策略拥挤度 {score}/100：{sum(item.status == 'pass' for item in pairs)} 对分散，{watch_count} 对观察，{fail_count} 对拥挤。",
        pairs=pairs,
        actions=dedupe(actions),
    )


def strategy_lifecycle_report() -> StrategyLifecycleReport:
    strategies = list_strategy_templates()
    leaderboard = strategy_leaderboard_report()
    gates = strategy_promotion_gates()
    matrix = strategy_regime_matrix_report()
    latest_validations = list_strategy_validations(limit=100)
    validation_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in latest_validations:
        validation_by_strategy.setdefault(validation.strategy_id, validation)
    leaderboard_by_id = {item.strategy_id: item for item in leaderboard.items}
    gate_by_id = {item.strategy_id: item for item in gates}
    matrix_by_id = {item.strategy_id: item for item in matrix.items}
    items: list[StrategyLifecycleItem] = []
    for strategy in strategies:
        leaderboard_item = leaderboard_by_id.get(strategy.id)
        gate = gate_by_id.get(strategy.id)
        matrix_item = matrix_by_id.get(strategy.id)
        latest_validation = validation_by_strategy.get(strategy.id)
        latest_status: Literal["pass", "watch", "fail", "missing"] = latest_validation.pass_fail if latest_validation else "missing"
        leaderboard_score = leaderboard_item.score if leaderboard_item else None
        promotion_score = gate.score if gate else None
        current_cell = None
        if matrix_item:
            current_cell = next((cell for cell in matrix_item.cells if cell.regime_label == matrix.current_regime.label), None)
        regime_score = current_cell.score if current_cell else None
        score_parts = [value for value in [leaderboard_score, promotion_score, regime_score] if value is not None]
        score = int(round(sum(score_parts) / len(score_parts))) if score_parts else 0
        blockers: list[str] = []
        actions: list[str] = []
        if not strategy.enabled:
            stage: Literal["draft", "validation", "candidate", "active", "paused"] = "paused"
            blockers.append("策略已停用")
            actions.append("启用策略后再进入验证或准入。")
        elif not strategy.factor_ids:
            stage = "draft"
            blockers.append("未绑定因子")
            actions.append("绑定至少一个启用因子。")
        elif latest_status == "missing":
            stage = "draft"
            actions.append("运行策略验证，生成回测和前向测试记录。")
        elif latest_status == "fail" or (gate and gate.status == "fail"):
            stage = "validation"
            blockers.extend((gate.blockers[:2] if gate else ["最近验证失败"]))
            actions.extend((gate.actions[:2] if gate else ["复核策略验证失败原因。"]))
        elif matrix_item and matrix_item.overall_status == "fail":
            stage = "validation"
            blockers.append("当前市场状态矩阵为 fail")
            actions.extend(matrix_item.actions[:2])
        elif gate and gate.status == "pass" and leaderboard_item and leaderboard_item.status == "pass" and matrix_item and matrix_item.overall_status == "pass":
            stage = "active"
            actions.append("可进入小规模模拟，并保持复验和市场状态门禁。")
        else:
            stage = "candidate"
            actions.append("保留为候选策略，补齐准入门禁或状态适配观察。")
        if gate and gate.blockers:
            blockers.extend(gate.blockers[:2])
        if leaderboard_item and leaderboard_item.status == "fail":
            blockers.append("策略排行榜失败")
        if stage == "active" and not blockers:
            status: Literal["pass", "watch", "fail"] = "pass"
        elif blockers:
            status = "fail"
        else:
            status = "watch"
        items.append(
            StrategyLifecycleItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                stage=stage,
                status=status,
                score=max(0, min(100, score)),
                leaderboard_score=leaderboard_score,
                promotion_score=promotion_score,
                regime_score=regime_score,
                latest_validation_status=latest_status,
                blockers=dedupe(blockers),
                actions=dedupe(actions),
            )
        )
    fail_count = sum(item.status == "fail" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if any(item.stage == "draft" for item in items):
        actions.append("draft 策略需要补因子绑定或运行策略验证。")
    if any(item.stage == "validation" for item in items):
        actions.append("validation 策略优先处理准入阻断和验证失败。")
    if any(item.stage == "active" for item in items):
        actions.append("active 策略保持复验队列、市场状态矩阵和暴露监控。")
    if not actions:
        actions.append("策略生命周期健康，按准入门禁维护。")
    return StrategyLifecycleReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"策略生命周期：{sum(item.stage == 'active' for item in items)} 个 active，{sum(item.stage == 'candidate' for item in items)} 个 candidate，{sum(item.stage in {'draft', 'validation'} for item in items)} 个待完善，{sum(item.stage == 'paused' for item in items)} 个暂停。",
        items=sorted(items, key=lambda item: (item.stage == "active", item.score), reverse=True),
        actions=dedupe(actions),
    )


def build_strategy_promotion_gate(
    strategy: StrategyTemplate,
    quality: DataQualityReport,
    readiness: ResearchReadiness,
    leaderboard_item: StrategyLeaderboardItem | None,
    latest_validation: StrategyValidationResult | None,
) -> StrategyPromotionGate:
    checks: list[ReadinessCheck] = []
    blockers: list[str] = []
    actions: list[str] = []

    checks.append(ReadinessCheck(name="数据质量", status=quality.status, message=quality.summary))
    if quality.status == "fail":
        blockers.extend(quality.blockers or ["数据质量未通过。"])
    elif quality.status == "watch":
        actions.extend(quality.actions[:2])

    checks.append(ReadinessCheck(name="研究就绪", status=readiness.status, message=readiness.summary))
    if readiness.status == "fail":
        blockers.extend(readiness.blockers or ["研究就绪度未通过。"])
    elif readiness.status == "watch":
        actions.extend(readiness.actions[:2])

    if not strategy.enabled:
        checks.append(ReadinessCheck(name="策略状态", status="fail", message="策略已停用。"))
        blockers.append("启用策略后才能进入模拟或自动化。")
    elif not strategy.factor_ids:
        checks.append(ReadinessCheck(name="策略状态", status="fail", message="策略未绑定因子。"))
        blockers.append("至少绑定一个启用因子。")
    else:
        checks.append(ReadinessCheck(name="策略状态", status="pass", message=f"策略启用，绑定 {len(strategy.factor_ids)} 个因子。"))

    if latest_validation is None:
        checks.append(ReadinessCheck(name="最近验证", status="watch", message="缺少策略验证记录。"))
        actions.append("先运行一次策略验证。")
    else:
        checks.append(ReadinessCheck(name="最近验证", status=latest_validation.pass_fail, message=f"最近验证 {latest_validation.pass_fail}，回测均值 {latest_validation.average_backtest_return:+.2f}%，前向均值 {latest_validation.average_forward_return:+.2f}%。"))
        if latest_validation.pass_fail == "fail":
            blockers.append("最近策略验证失败。")
        elif latest_validation.pass_fail == "watch":
            actions.append("扩大样本或降低仓位后再进入模拟。")

    if leaderboard_item is None:
        checks.append(ReadinessCheck(name="策略排名", status="watch", message="策略未进入排行榜。"))
        actions.append("检查策略模板配置。")
    else:
        checks.append(ReadinessCheck(name="策略排名", status=leaderboard_item.status, message=f"排名 #{leaderboard_item.rank}，分数 {leaderboard_item.score}/100，建议 {leaderboard_item.recommendation}。"))
        if leaderboard_item.status == "fail":
            blockers.append("策略排行榜状态为 fail。")
        elif leaderboard_item.status == "watch":
            actions.append("按策略排行榜建议补齐验证。")

    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(0, 100 - fail_count * 30 - watch_count * 12)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
        recommendation = "禁止进入模拟"
    elif watch_count:
        status = "watch"
        recommendation = "仅观察或小样本模拟"
    else:
        status = "pass"
        recommendation = "可进入小规模模拟"
    if not actions and not blockers:
        actions.append("保持预警和前向测试监控。")
    return StrategyPromotionGate(
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        recommendation=recommendation,
        checks=checks,
        blockers=dedupe(blockers),
        actions=dedupe(actions),
    )


def strategy_promotion_gate_report(strategy_id: str) -> StrategyPromotionGate:
    strategy = get_strategy_template(strategy_id)
    quality = data_quality_report()
    readiness = research_readiness_report()
    leaderboard = strategy_leaderboard_report()
    leaderboard_item = next((item for item in leaderboard.items if item.strategy_id == strategy.id), None)
    latest_validation = next(iter(list_strategy_validations(strategy_id=strategy.id, limit=1)), None)
    return build_strategy_promotion_gate(strategy, quality, readiness, leaderboard_item, latest_validation)


def strategy_promotion_gates() -> list[StrategyPromotionGate]:
    strategies = list_strategy_templates()
    regime = lightweight_market_regime_report()
    quality = lightweight_data_quality_report()
    readiness = lightweight_research_readiness_report(regime=regime, quality=quality)
    leaderboard_by_id = {item.strategy_id: item for item in strategy_leaderboard_report(regime=regime).items}
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in list_strategy_validations(limit=100):
        latest_by_strategy.setdefault(validation.strategy_id, validation)
    return [
        build_strategy_promotion_gate(
            strategy,
            quality,
            readiness,
            leaderboard_by_id.get(strategy.id),
            latest_by_strategy.get(strategy.id),
        )
        for strategy in strategies
    ]


def row_to_strategy_validation(row: sqlite3.Row) -> StrategyValidationResult:
    return StrategyValidationResult(
        id=row["id"],
        strategy_id=row["strategy_id"],
        strategy_name=row["strategy_name"],
        generated_at=row["generated_at"],
        regime=MarketRegime(**json.loads(row["regime"])),
        filter_validation=FilterValidationResult(**json.loads(row["filter_validation"])),
        symbols=json.loads(row["symbols"]),
        backtest_ids=json.loads(row["backtest_ids"]),
        forward_test_ids=json.loads(row["forward_test_ids"]),
        average_backtest_return=row["average_backtest_return"],
        average_forward_return=row["average_forward_return"],
        pass_fail=row["pass_fail"],
        checks=json.loads(row["checks"]),
    )


def strategy_filters_to_request(filters: dict[str, str | float]) -> FilterValidationRequest:
    return FilterValidationRequest(
        min_change=float(filters["min_change"]) if "min_change" in filters else None,
        max_change=float(filters["max_change"]) if "max_change" in filters else None,
        min_rsi=float(filters["min_rsi"]) if "min_rsi" in filters else None,
        max_rsi=float(filters["max_rsi"]) if "max_rsi" in filters else None,
        min_trend=int(float(filters["min_trend"])) if "min_trend" in filters else None,
        signal=str(filters["signal"]) if "signal" in filters else None,
        market=str(filters["market"]) if "market" in filters else None,
    )


def run_strategy_validation(strategy_id: str, payload: StrategyValidationRequest) -> StrategyValidationResult:
    init_db()
    strategy = get_strategy_template(strategy_id)
    if not strategy.enabled:
        raise HTTPException(status_code=400, detail="Strategy template is disabled")
    if not strategy.factor_ids:
        raise HTTPException(status_code=400, detail="Strategy template has no factors")

    filter_request = strategy_filters_to_request(strategy.default_filters)
    filter_result = validate_filters(filter_request)
    screener_result = run_screener(
        min_change=filter_request.min_change,
        max_change=filter_request.max_change,
        min_rsi=filter_request.min_rsi,
        max_rsi=filter_request.max_rsi,
        min_trend=filter_request.min_trend,
        signal=filter_request.signal,
        market=filter_request.market,
        limit=max(1, min(10, payload.symbol_limit)),
    )
    selected_symbols = [item.snapshot.symbol for item in screener_result.items[: max(1, min(10, payload.symbol_limit))]]
    if not selected_symbols:
        selected_symbols = [item.symbol for item in list_watchlist_snapshots()[:1]]

    factor_ids = [factor_id for factor_id in strategy.factor_ids if get_factor_definition(factor_id, require_enabled=True)]
    backtests: list[BacktestRunResult] = []
    forward_tests: list[ForwardTestResult] = []
    for symbol in selected_symbols:
        for factor_id in factor_ids[:2]:
            backtests.append(
                run_backtest(
                    BacktestRunRequest(
                        symbol=symbol,
                        factor_id=factor_id,
                        interval=payload.interval,
                        limit=180,
                        initial_capital=payload.initial_capital,
                        fee_bps=2,
                    )
                )
            )
            forward_tests.append(
                run_forward_test(
                    ForwardTestRequest(
                        symbol=symbol,
                        factor_id=factor_id,
                        interval=payload.interval,
                        train_bars=140,
                        forward_bars=40,
                        initial_capital=payload.initial_capital,
                        fee_bps=2,
                    )
                )
            )

    average_backtest_return = sum(item.total_return for item in backtests) / len(backtests) if backtests else 0.0
    average_forward_return = sum(item.forward_return for item in forward_tests) / len(forward_tests) if forward_tests else 0.0
    forward_failures = sum(1 for item in forward_tests if item.pass_fail == "fail")
    forward_passes = sum(1 for item in forward_tests if item.pass_fail == "pass")
    regime = market_regime_report()
    checks = [
        f"过滤器命中 {filter_result.match_count} 个标的，实际验证 {len(selected_symbols)} 个。",
        f"平均回测收益 {average_backtest_return:+.2f}%，平均前向收益 {average_forward_return:+.2f}%。",
        f"前向测试通过 {forward_passes} 个，失败 {forward_failures} 个。",
        f"市场状态 {regime.label} / {regime.risk_level}。",
    ]
    if not filter_result.valid:
        pass_fail: Literal["pass", "watch", "fail"] = "fail"
        checks.append("过滤器未通过，策略验证失败。")
    elif forward_failures:
        pass_fail = "fail" if forward_failures >= max(1, len(forward_tests) // 2) else "watch"
        checks.append("存在前向测试失败，需重新校准过滤器或风险控制。")
    elif average_forward_return > 0 and regime.risk_level != "risk":
        pass_fail = "pass" if forward_passes else "watch"
        checks.append("策略通过当前样本验证，可进入观察或小规模模拟。")
    else:
        pass_fail = "watch"
        checks.append("策略进入观察状态，需扩大样本或等待更合适市场状态。")

    generated_at = datetime.now(timezone.utc).isoformat()
    validation_id = sha256(f"strategy-validation:{strategy.id}:{generated_at}".encode("utf-8")).hexdigest()[:20]
    result = StrategyValidationResult(
        id=validation_id,
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        generated_at=generated_at,
        regime=regime,
        filter_validation=filter_result,
        symbols=selected_symbols,
        backtest_ids=[item.id for item in backtests],
        forward_test_ids=[item.id for item in forward_tests],
        average_backtest_return=round(average_backtest_return, 2),
        average_forward_return=round(average_forward_return, 2),
        pass_fail=pass_fail,
        checks=checks,
    )
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO strategy_validation_runs (
                id, strategy_id, strategy_name, generated_at, regime, filter_validation,
                symbols, backtest_ids, forward_test_ids, average_backtest_return,
                average_forward_return, pass_fail, checks, request_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.strategy_id,
                result.strategy_name,
                result.generated_at,
                result.regime.model_dump_json(),
                result.filter_validation.model_dump_json(),
                json.dumps(result.symbols),
                json.dumps(result.backtest_ids),
                json.dumps(result.forward_test_ids),
                result.average_backtest_return,
                result.average_forward_return,
                result.pass_fail,
                json.dumps(result.checks, ensure_ascii=False),
                payload.model_dump_json(),
            ),
        )
    add_event_log(
        event_type="strategy_validation",
        severity="info" if pass_fail == "pass" else "watch" if pass_fail == "watch" else "risk",
        title="策略验证完成",
        message=f"{strategy.name}：{pass_fail}，前向均值 {average_forward_return:+.2f}%。",
    )
    return result


def list_strategy_validations(strategy_id: str | None = None, limit: int = 10) -> list[StrategyValidationResult]:
    init_db()
    with db_connect() as connection:
        if strategy_id:
            rows = connection.execute(
                """
                SELECT * FROM strategy_validation_runs
                WHERE strategy_id = ?
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (strategy_id, max(1, min(50, limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM strategy_validation_runs
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (max(1, min(50, limit)),),
            ).fetchall()
    return [row_to_strategy_validation(row) for row in rows]


def strategy_validation_decay_report(limit: int = 12) -> StrategyValidationDecayReport:
    validations = list_strategy_validations(limit=100)
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in validations:
        latest_by_strategy.setdefault(validation.strategy_id, validation)
    items: list[StrategyValidationDecayItem] = []
    for validation in latest_by_strategy.values():
        decay = round(validation.average_backtest_return - validation.average_forward_return, 2)
        decay_ratio = round(decay / abs(validation.average_backtest_return) * 100, 1) if abs(validation.average_backtest_return) >= 0.01 else None
        if validation.average_forward_return < 0 or decay > max(8.0, abs(validation.average_backtest_return) * 0.75):
            status: Literal["pass", "watch", "fail"] = "fail"
            action = "前向相对回测衰减过大，暂停晋级并复核过滤器/参数。"
        elif decay > max(3.0, abs(validation.average_backtest_return) * 0.35) or validation.pass_fail == "watch":
            status = "watch"
            action = "保留观察，扩大样本并降低模拟权重。"
        else:
            status = "pass"
            action = "回测到前向衰减可控，可继续滚动复验。"
        score = int(max(0, min(100, 85 + validation.average_forward_return - max(0, decay) * 3 - (15 if validation.pass_fail == "watch" else 30 if validation.pass_fail == "fail" else 0))))
        items.append(
            StrategyValidationDecayItem(
                strategy_id=validation.strategy_id,
                strategy_name=validation.strategy_name,
                status=status,
                score=score,
                validation_id=validation.id,
                generated_at=validation.generated_at,
                backtest_return=validation.average_backtest_return,
                forward_return=validation.average_forward_return,
                decay=decay,
                decay_ratio=decay_ratio,
                symbol_count=len(validation.symbols),
                backtest_count=len(validation.backtest_ids),
                forward_count=len(validation.forward_test_ids),
                regime_label=validation.regime.label,
                risk_level=validation.regime.risk_level,
                action=action,
            )
        )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score), reverse=True)[: max(1, min(50, limit))]
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    score = int(round(sum(item.score for item in ranked) / max(1, len(ranked)))) if ranked else 0
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count or not ranked:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if not ranked:
        actions.append("先运行策略验证，生成回测和前向样本。")
    if fail_count:
        actions.append("优先处理前向衰减 fail 的策略，暂停进入模拟。")
    if watch_count:
        actions.append("watch 策略扩大样本、降低权重并加入复验队列。")
    if not actions:
        actions.append("策略验证衰减整体可控，继续滚动复验。")
    return StrategyValidationDecayReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"策略验证衰减 {score}/100：{sum(item.status == 'pass' for item in ranked)} 个可控，{watch_count} 个观察，{fail_count} 个失败。",
        items=ranked,
        actions=dedupe(actions),
    )


def strategy_risk_control_report() -> StrategyRiskControlReport:
    strategies = list_strategy_templates()
    gates = {item.strategy_id: item for item in strategy_promotion_gates()}
    lifecycle = {item.strategy_id: item for item in strategy_lifecycle_report().items}
    decay = {item.strategy_id: item for item in strategy_validation_decay_report(limit=50).items}
    crowding = strategy_crowding_report()
    crowding_status: dict[str, Literal["pass", "watch", "fail"]] = {}
    for pair in crowding.pairs:
        for strategy_id in [pair.strategy_a_id, pair.strategy_b_id]:
            current = crowding_status.get(strategy_id)
            if current == "fail" or pair.status == "pass":
                crowding_status.setdefault(strategy_id, pair.status)
            elif pair.status == "fail" or current is None:
                crowding_status[strategy_id] = pair.status
            elif pair.status == "watch":
                crowding_status[strategy_id] = "watch"

    items: list[StrategyRiskControlItem] = []
    for strategy in strategies:
        gate = gates.get(strategy.id)
        lifecycle_item = lifecycle.get(strategy.id)
        decay_item = decay.get(strategy.id)
        blockers: list[str] = []
        actions: list[str] = []
        score = 100
        if not strategy.enabled:
            score -= 25
            blockers.append("策略已停用")
            actions.append("确认是否废弃，或启用后重新进入验证。")
        if not strategy.risk_controls:
            score -= 25
            blockers.append("缺少风险控制")
            actions.append("补充仓位、退出、市场状态和失败处理规则。")
        elif len(strategy.risk_controls) < 3:
            score -= 10
            actions.append("风险控制少于 3 条，补充执行和暂停条件。")
        if not strategy.validation_rules:
            score -= 20
            blockers.append("缺少验证规则")
            actions.append("补充回测、前向、回撤、样本量和状态验证规则。")
        elif len(strategy.validation_rules) < 3:
            score -= 8
            actions.append("验证规则较少，补齐样本和稳健性约束。")
        gate_status: Literal["pass", "watch", "fail", "missing"] = gate.status if gate else "missing"
        if gate_status == "fail":
            score -= 20
            blockers.extend((gate.blockers if gate else [])[:2])
            actions.extend((gate.actions if gate else [])[:2])
        elif gate_status in {"watch", "missing"}:
            score -= 8
            actions.append("复核策略准入门禁并处理 watch/missing 项。")
        lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"] = lifecycle_item.stage if lifecycle_item else "missing"
        if lifecycle_item and lifecycle_item.status == "fail":
            score -= 15
            blockers.extend(lifecycle_item.blockers[:2])
            actions.extend(lifecycle_item.actions[:2])
        elif lifecycle_item and lifecycle_item.status == "watch":
            score -= 6
            actions.extend(lifecycle_item.actions[:1])
        strategy_crowding_status: Literal["pass", "watch", "fail", "missing"] = crowding_status.get(strategy.id, "missing")
        if strategy_crowding_status == "fail":
            score -= 15
            blockers.append("策略拥挤风险 fail")
            actions.append("拆分共享因子/过滤器或设置组合权重上限。")
        elif strategy_crowding_status in {"watch", "missing"}:
            score -= 5
            actions.append("复核策略拥挤度和组合权重上限。")
        decay_status: Literal["pass", "watch", "fail", "missing"] = decay_item.status if decay_item else "missing"
        if decay_status == "fail":
            score -= 20
            blockers.append("策略验证衰减 fail")
            actions.append(decay_item.action if decay_item else "复核回测到前向衰减。")
        elif decay_status in {"watch", "missing"}:
            score -= 8
            actions.append(decay_item.action if decay_item else "运行策略验证，生成衰减报告。")

        clipped_score = max(0, min(100, score))
        if blockers or clipped_score < 55:
            status: Literal["pass", "watch", "fail"] = "fail"
        elif actions or clipped_score < 80:
            status = "watch"
        else:
            status = "pass"
            actions.append("风险控制覆盖完整，按门禁持续复验。")
        items.append(
            StrategyRiskControlItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=clipped_score,
                risk_control_count=len(strategy.risk_controls),
                validation_rule_count=len(strategy.validation_rules),
                gate_status=gate_status,
                lifecycle_stage=lifecycle_stage,
                crowding_status=strategy_crowding_status,
                decay_status=decay_status,
                blockers=dedupe(blockers),
                actions=dedupe(actions),
            )
        )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score), reverse=True)
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
        actions.append("优先修复 fail 策略的风险控制、准入阻断或验证衰减。")
    if watch_count:
        actions.append("watch 策略补齐规则和组合权重控制后再扩大模拟。")
    if not actions:
        actions.append("策略风险控制覆盖完整，按复验周期维护。")
    return StrategyRiskControlReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"策略风险控制 {score}/100：{sum(item.status == 'pass' for item in ranked)} 个通过，{watch_count} 个观察，{fail_count} 个失败。",
        items=ranked,
        actions=dedupe(actions),
    )


def market_state_control_report() -> MarketStateControlReport:
    regime = lightweight_market_regime_report()
    quality = lightweight_data_quality_report()
    readiness = lightweight_research_readiness_report(regime=regime, quality=quality)
    matrix = strategy_regime_matrix_report(current=regime)
    crowding = strategy_crowding_report()
    decay = strategy_validation_decay_report(limit=50)
    risk_controls = strategy_risk_control_report()

    def score_from_status(status: Literal["pass", "watch", "fail"], fallback: int = 65) -> int:
        return {"pass": 90, "watch": 62, "fail": 28}.get(status, fallback)

    items = [
        MarketStateControlItem(
            name="本地数据质量",
            status=quality.status,
            score=quality.score,
            detail=quality.summary,
            action=(quality.actions or quality.blockers or ["保持数据源、新闻源和研究记录维护。"])[0],
        ),
        MarketStateControlItem(
            name="研究就绪度",
            status=readiness.status,
            score=readiness.score,
            detail=readiness.summary,
            action=(readiness.actions or readiness.blockers or ["保持策略验证频率。"])[0],
        ),
        MarketStateControlItem(
            name="市场状态矩阵",
            status=matrix.status,
            score=score_from_status(matrix.status),
            detail=matrix.summary,
            action=(matrix.actions or ["按当前市场状态执行策略准入。"])[0],
        ),
        MarketStateControlItem(
            name="策略拥挤度",
            status=crowding.status,
            score=crowding.score,
            detail=crowding.summary,
            action=(crowding.actions or ["保持组合权重分散。"])[0],
        ),
        MarketStateControlItem(
            name="验证衰减",
            status=decay.status,
            score=decay.score,
            detail=decay.summary,
            action=(decay.actions or ["继续滚动复验回测到前向衰减。"])[0],
        ),
        MarketStateControlItem(
            name="策略风控审计",
            status=risk_controls.status,
            score=risk_controls.score,
            detail=risk_controls.summary,
            action=(risk_controls.actions or ["按策略风控审计维护风险控制和验证规则。"])[0],
        ),
    ]
    fail_count = sum(item.status == "fail" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    average_score = int(round(sum(item.score for item in items) / max(1, len(items))))
    if regime.risk_level == "risk" or fail_count >= 2:
        status: Literal["pass", "watch", "fail"] = "fail"
        decision: Literal["expand", "observe", "reduce", "halt"] = "halt" if fail_count >= 3 else "reduce"
    elif fail_count or watch_count >= 2 or regime.risk_level == "watch":
        status = "watch"
        decision = "observe"
    else:
        status = "pass"
        decision = "expand" if average_score >= 82 and regime.risk_level == "constructive" else "observe"

    actions: list[str] = []
    if decision == "halt":
        actions.append("暂停新增模拟和自动化，先修复 fail 维度。")
    elif decision == "reduce":
        actions.append("降低策略组合预算，只保留已有观察仓位。")
    elif decision == "observe":
        actions.append("维持小规模观察，补齐 watch 维度后再扩大模拟。")
    else:
        actions.append("允许在通过门禁的策略上小幅扩大模拟预算。")
    actions.extend(item.action for item in items if item.status != "pass")
    return MarketStateControlReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=average_score,
        decision=decision,
        current_regime=regime,
        summary=f"市场状态策略总览 {average_score}/100：当前 {regime.label}/{regime.risk_level}，{sum(item.status == 'pass' for item in items)} 项通过，{watch_count} 项观察，{fail_count} 项失败，建议 {decision}。",
        items=items,
        actions=dedupe(actions)[:6],
    )


def strategy_operating_plan_report() -> StrategyOperatingPlanReport:
    market_controls = market_state_control_report()
    strategies = list_strategy_templates()
    gates = {item.strategy_id: item for item in strategy_promotion_gates()}
    lifecycle = {item.strategy_id: item for item in strategy_lifecycle_report().items}
    matrix = strategy_regime_matrix_report(current=market_controls.current_regime)
    matrix_by_id = {item.strategy_id: item for item in matrix.items}
    risk_controls = {item.strategy_id: item for item in strategy_risk_control_report().items}
    backtest_monitor = {item.strategy_id: item for item in strategy_backtest_monitor_report(limit=50).items}
    forward_monitor = {item.strategy_id: item for item in strategy_forward_monitor_report(limit=50).items}
    max_budget = {"expand": 8.0, "observe": 4.0, "reduce": 2.0, "halt": 0.0}[market_controls.decision]

    items: list[StrategyOperatingPlanItem] = []
    for strategy in strategies:
        gate = gates.get(strategy.id)
        lifecycle_item = lifecycle.get(strategy.id)
        matrix_item = matrix_by_id.get(strategy.id)
        risk_item = risk_controls.get(strategy.id)
        backtest_item = backtest_monitor.get(strategy.id)
        forward_item = forward_monitor.get(strategy.id)
        current_cell = None
        if matrix_item:
            current_cell = next((cell for cell in matrix_item.cells if cell.regime_label == market_controls.current_regime.label), None)

        score = 50
        reasons: list[str] = [f"市场总览建议 {market_controls.decision}，状态 {market_controls.current_regime.label}/{market_controls.current_regime.risk_level}。"]
        actions: list[str] = []
        if strategy.enabled:
            score += 8
            reasons.append("策略已启用。")
        else:
            score -= 30
            reasons.append("策略已停用。")
            actions.append("策略停用，不进入模拟计划。")

        gate_status: Literal["pass", "watch", "fail", "missing"] = gate.status if gate else "missing"
        if gate_status == "pass":
            score += 14
            reasons.append("准入门禁通过。")
        elif gate_status == "watch":
            score -= 4
            actions.extend((gate.actions if gate else [])[:1])
        else:
            score -= 24
            actions.extend((gate.blockers if gate else ["缺少准入门禁。"])[:1])

        lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"] = lifecycle_item.stage if lifecycle_item else "missing"
        if lifecycle_stage == "active":
            score += 14
            reasons.append("生命周期为 active。")
        elif lifecycle_stage == "candidate":
            score += 4
        elif lifecycle_stage in {"draft", "validation", "paused", "missing"}:
            score -= 12
            actions.extend((lifecycle_item.actions if lifecycle_item else ["补齐生命周期证据。"])[:1])

        regime_decision: Literal["open", "reduced", "paused", "blocked", "missing"] = current_cell.decision if current_cell else "missing"
        if regime_decision == "open":
            score += 18
            reasons.append("当前状态矩阵允许 open。")
        elif regime_decision == "reduced":
            score += 3
            actions.append("当前状态仅允许降权观察。")
        elif regime_decision in {"paused", "blocked"}:
            score -= 24
            actions.append("当前市场状态下暂停或阻断该策略。")
        else:
            score -= 8
            actions.append("缺少当前状态矩阵结论。")

        risk_control_status: Literal["pass", "watch", "fail", "missing"] = risk_item.status if risk_item else "missing"
        if risk_item:
            score += int((risk_item.score - 60) * 0.25)
            if risk_item.status != "pass":
                actions.extend(risk_item.actions[:1])
        else:
            score -= 10
            actions.append("缺少策略风控审计。")

        backtest_status: Literal["pass", "watch", "fail", "missing"] = backtest_item.status if backtest_item else "missing"
        forward_status: Literal["pass", "watch", "fail", "missing"] = forward_item.status if forward_item else "missing"
        for label, status_value, monitor in [("回测", backtest_status, backtest_item), ("前向", forward_status, forward_item)]:
            if status_value == "pass":
                score += 8
                reasons.append(f"{label}观察通过。")
            elif status_value == "watch":
                score -= 3
                actions.extend((monitor.actions if monitor else [])[:1])
            else:
                score -= 12
                actions.extend((monitor.actions if monitor else [f"补齐{label}观察样本。"])[:1])

        clipped_score = max(0, min(100, score))
        hard_block = (
            not strategy.enabled
            or market_controls.decision == "halt"
            or gate_status == "fail"
            or regime_decision == "blocked"
            or risk_control_status == "fail"
            or forward_status == "fail"
        )
        if hard_block:
            decision: Literal["open", "observe", "reduce", "block"] = "block"
        elif market_controls.decision == "reduce" or regime_decision == "paused":
            decision = "reduce"
        elif clipped_score >= 76 and market_controls.decision == "expand" and gate_status == "pass" and regime_decision == "open":
            decision = "open"
        else:
            decision = "observe"

        weight_multiplier = {"open": 1.0, "observe": 0.45, "reduce": 0.25, "block": 0.0}[decision]
        recommended_weight = round(max_budget * weight_multiplier * clipped_score / 100, 2)
        if decision == "open":
            status: Literal["pass", "watch", "fail"] = "pass"
            actions.append("可进入通过门禁的模拟池，并保持风控审计。")
        elif decision in {"observe", "reduce"}:
            status = "watch"
            actions.append("保持观察预算，不扩大到自动化执行。")
        else:
            status = "fail"
            actions.append("阻断该策略，直到关键证据恢复。")

        items.append(
            StrategyOperatingPlanItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                decision=decision,
                recommended_weight=recommended_weight,
                score=clipped_score,
                gate_status=gate_status,
                lifecycle_stage=lifecycle_stage,
                regime_decision=regime_decision,
                backtest_status=backtest_status,
                forward_status=forward_status,
                risk_control_status=risk_control_status,
                reasons=dedupe(reasons)[:4],
                actions=dedupe(actions)[:4],
            )
        )

    ranked = sorted(items, key=lambda item: (item.decision == "open", item.recommended_weight, item.score), reverse=True)
    open_count = sum(item.decision == "open" for item in ranked)
    block_count = sum(item.decision == "block" for item in ranked)
    watch_count = sum(item.decision in {"observe", "reduce"} for item in ranked)
    score = int(round(sum(item.score for item in ranked) / max(1, len(ranked)))) if ranked else 0
    if block_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif open_count:
        status = "pass"
    else:
        status = "watch"
    actions: list[str] = []
    if open_count:
        actions.append("只扩大 open 策略，且单策略不超过建议权重。")
    if watch_count:
        actions.append("observe/reduce 策略继续补齐验证、前向和市场状态证据。")
    if block_count:
        actions.append("block 策略不得进入模拟或自动化队列。")
    if not actions:
        actions.append("暂无可运行策略，先补齐策略验证和准入门禁。")
    return StrategyOperatingPlanReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        market_decision=market_controls.decision,
        summary=f"策略操作计划 {score}/100：open {open_count}，观察/降权 {watch_count}，阻断 {block_count}，市场建议 {market_controls.decision}。",
        current_regime=market_controls.current_regime,
        items=ranked,
        actions=dedupe(actions),
    )


def strategy_evidence_pack_report() -> StrategyEvidencePackReport:
    strategies = list_strategy_templates()
    operating = {item.strategy_id: item for item in strategy_operating_plan_report().items}
    gates = {item.strategy_id: item for item in strategy_promotion_gates()}
    risk_controls = {item.strategy_id: item for item in strategy_risk_control_report().items}
    decay = {item.strategy_id: item for item in strategy_validation_decay_report(limit=50).items}
    latest_validations: dict[str, StrategyValidationResult] = {}
    for validation in list_strategy_validations(limit=100):
        latest_validations.setdefault(validation.strategy_id, validation)

    items: list[StrategyEvidencePackItem] = []
    for strategy in strategies:
        operation = operating.get(strategy.id)
        gate = gates.get(strategy.id)
        risk_item = risk_controls.get(strategy.id)
        validation = latest_validations.get(strategy.id)
        decay_item = decay.get(strategy.id)
        evidence: list[str] = []
        gaps: list[str] = []
        actions: list[str] = []
        score = 45

        if validation:
            filter_result = validation.filter_validation
        else:
            try:
                static_filter = strategy_filters_to_request(strategy.default_filters)
                normalized_filters = static_filter.model_dump(exclude_none=True)
                filter_warnings: list[str] = []
                filter_suggestions: list[str] = []
                if static_filter.min_change is not None and static_filter.max_change is not None and static_filter.min_change > static_filter.max_change:
                    filter_warnings.append("涨跌幅下限高于上限。")
                if static_filter.min_rsi is not None and static_filter.max_rsi is not None and static_filter.min_rsi > static_filter.max_rsi:
                    filter_warnings.append("RSI 下限高于上限。")
                if not normalized_filters:
                    filter_warnings.append("策略未配置默认过滤器。")
                    filter_suggestions.append("补充默认过滤器后再运行策略验证。")
                filter_result = FilterValidationResult(
                    valid=not filter_warnings,
                    match_count=0,
                    normalized_filters=normalized_filters,
                    warnings=filter_warnings,
                    suggestions=filter_suggestions or ["运行策略验证以获得真实过滤器命中数。"],
                    regime=lightweight_market_regime_report(),
                )
            except (HTTPException, KeyError, ValueError):
                filter_result = FilterValidationResult(
                    valid=False,
                    match_count=0,
                    normalized_filters=strategy.default_filters,
                    warnings=["策略默认过滤器参数无效。"],
                    suggestions=["修正默认过滤器后重新验证。"],
                    regime=lightweight_market_regime_report(),
                )

        if strategy.enabled:
            score += 6
            evidence.append("策略已启用。")
        else:
            score -= 20
            gaps.append("策略已停用。")
            actions.append("确认是否废弃或重新启用。")

        if filter_result.valid and filter_result.match_count >= 2:
            score += 14
            evidence.append(f"默认过滤器通过，命中 {filter_result.match_count} 个标的。")
        elif filter_result.valid:
            score += 4
            gaps.append(f"默认过滤器命中偏少：{filter_result.match_count} 个。")
            actions.extend(filter_result.suggestions[:1])
        else:
            score -= 18
            gaps.append("默认过滤器未通过。")
            actions.extend(filter_result.suggestions[:2])

        latest_status: Literal["pass", "watch", "fail", "missing"] = validation.pass_fail if validation else "missing"
        if validation:
            evidence.append(f"最近验证 {validation.pass_fail}：回测 {validation.average_backtest_return:+.2f}%，前向 {validation.average_forward_return:+.2f}%。")
            if validation.pass_fail == "pass":
                score += 18
            elif validation.pass_fail == "watch":
                score += 4
                gaps.append("最近验证仍处于观察。")
                actions.append("扩大样本或降低权重继续前向观察。")
            else:
                score -= 24
                gaps.append("最近策略验证失败。")
                actions.append("暂停晋级，复核过滤器、因子和风险条件。")
        else:
            score -= 18
            gaps.append("缺少策略验证记录。")
            actions.append("运行策略验证，生成回测和前向证据。")

        if decay_item:
            evidence.append(f"验证衰减 {decay_item.decay:+.2f}pp，状态 {decay_item.status}。")
            if decay_item.status == "pass":
                score += 10
            elif decay_item.status == "watch":
                score -= 4
                actions.append(decay_item.action)
            else:
                score -= 18
                gaps.append("回测到前向衰减失败。")
                actions.append(decay_item.action)
        else:
            score -= 8
            gaps.append("缺少验证衰减记录。")
            actions.append("先运行策略验证以生成衰减证据。")

        gate_status: Literal["pass", "watch", "fail", "missing"] = gate.status if gate else "missing"
        if gate_status == "pass":
            score += 10
            evidence.append("准入门禁通过。")
        elif gate_status == "watch":
            score -= 4
            actions.extend((gate.actions if gate else [])[:1])
        else:
            score -= 16
            gaps.extend((gate.blockers if gate else ["缺少准入门禁。"])[:2])
            actions.extend((gate.actions if gate else ["补齐准入门禁。"])[:1])

        risk_status: Literal["pass", "watch", "fail", "missing"] = risk_item.status if risk_item else "missing"
        if risk_item:
            evidence.append(f"风控审计 {risk_item.status}，风控 {risk_item.risk_control_count} 条，验证规则 {risk_item.validation_rule_count} 条。")
            if risk_item.status == "pass":
                score += 10
            elif risk_item.status == "watch":
                score -= 3
                actions.extend(risk_item.actions[:1])
            else:
                score -= 18
                gaps.extend(risk_item.blockers[:2])
                actions.extend(risk_item.actions[:1])
        else:
            score -= 10
            gaps.append("缺少策略风控审计。")
            actions.append("补齐风险控制和验证规则。")

        operating_decision: Literal["open", "observe", "reduce", "block", "missing"] = operation.decision if operation else "missing"
        if operation:
            evidence.append(f"操作计划 {operation.decision}，建议权重 {operation.recommended_weight:.2f}%。")
            if operation.decision == "open":
                score += 8
            elif operation.decision == "block":
                score -= 16
                gaps.append("当前操作计划阻断。")
            else:
                score += 1
        else:
            score -= 8
            gaps.append("缺少策略操作计划。")

        clipped_score = max(0, min(100, score))
        if clipped_score >= 78 and not gaps:
            status: Literal["pass", "watch", "fail"] = "pass"
        elif clipped_score >= 48 and latest_status != "fail" and risk_status != "fail":
            status = "watch"
        else:
            status = "fail"
        if not actions:
            actions.append("证据链完整，按操作计划和市场状态持续复验。")
        items.append(
            StrategyEvidencePackItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                category=strategy.category,
                enabled=strategy.enabled,
                status=status,
                score=clipped_score,
                operating_decision=operating_decision,
                filter_valid=filter_result.valid,
                filter_match_count=filter_result.match_count,
                latest_validation_status=latest_status,
                validation_id=validation.id if validation else None,
                backtest_return=validation.average_backtest_return if validation else None,
                forward_return=validation.average_forward_return if validation else None,
                decay=decay_item.decay if decay_item else None,
                gate_status=gate_status,
                risk_control_status=risk_status,
                evidence=dedupe(evidence)[:6],
                gaps=dedupe(gaps)[:6],
                actions=dedupe(actions)[:5],
            )
        )

    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score), reverse=True)
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
        actions.append("先修复 fail 策略的过滤器、验证、衰减或风控缺口。")
    if watch_count:
        actions.append("watch 策略保留小权重观察并补齐证据链。")
    if not actions:
        actions.append("策略证据链完整，按操作计划持续复验。")
    return StrategyEvidencePackReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"策略验证证据包 {score}/100：{sum(item.status == 'pass' for item in ranked)} 个完整，{watch_count} 个观察，{fail_count} 个失败。",
        items=ranked,
        actions=dedupe(actions),
    )


def research_readiness_report() -> ResearchReadiness:
    generated_at = datetime.now(timezone.utc).isoformat()
    regime = market_regime_report()
    health = get_system_health()
    catalog = data_catalog_items()
    strategies = list_strategy_templates(enabled_only=True)
    validations = list_strategy_validations(limit=20)
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in validations:
        latest_by_strategy.setdefault(validation.strategy_id, validation)
    latest_validations = list(latest_by_strategy.values())[:8]

    checks: list[ReadinessCheck] = []
    blockers: list[str] = []
    actions: list[str] = []
    score = 100

    health_errors = [item for item in health.components if item.status == "error"]
    health_warnings = [item for item in health.components if item.status == "warn"]
    if health_errors:
        checks.append(ReadinessCheck(name="系统健康", status="fail", message=f"{len(health_errors)} 个组件异常。"))
        blockers.extend(item.message for item in health_errors[:3])
        score -= 35
    elif health_warnings:
        checks.append(ReadinessCheck(name="系统健康", status="watch", message=f"{len(health_warnings)} 个组件需要关注。"))
        actions.extend(item.message for item in health_warnings[:2])
        score -= 12
    else:
        checks.append(ReadinessCheck(name="系统健康", status="pass", message="核心组件可用。"))

    degraded_data = [item for item in catalog if item.status != "ready"]
    if degraded_data:
        checks.append(ReadinessCheck(name="数据目录", status="watch", message=f"{len(degraded_data)} 个数据集未完全就绪。"))
        actions.extend(f"补齐 {item.name}：{item.notes}" for item in degraded_data[:2])
        score -= 10
    else:
        checks.append(ReadinessCheck(name="数据目录", status="pass", message=f"{len(catalog)} 个数据集可用。"))

    if regime.risk_level == "risk":
        checks.append(ReadinessCheck(name="市场状态", status="fail", message=regime.summary))
        blockers.append("市场状态为 risk，策略应暂停自动化或仅保留观察。")
        score -= 30
    elif regime.risk_level == "watch":
        checks.append(ReadinessCheck(name="市场状态", status="watch", message=regime.summary))
        actions.extend(regime.actions[:2])
        score -= 12
    else:
        checks.append(ReadinessCheck(name="市场状态", status="pass", message=regime.summary))

    watchlist_items = list_watchlist_snapshots()
    if watchlist_items:
        drift = strategy_drift_report(watchlist_items[0].symbol)
        if drift.status == "fail":
            checks.append(ReadinessCheck(name="漂移监控", status="fail", message=drift.summary))
            blockers.append(f"{drift.symbol} 策略漂移失败，需复核前向、滚动样本外和压力测试。")
            score -= 22
        elif drift.status == "watch":
            checks.append(ReadinessCheck(name="漂移监控", status="watch", message=drift.summary))
            actions.append(f"复核 {drift.symbol} 漂移报告中的观察因子。")
            score -= 10
        else:
            checks.append(ReadinessCheck(name="漂移监控", status="pass", message=drift.summary))
    else:
        checks.append(ReadinessCheck(name="漂移监控", status="watch", message="缺少自选标的，无法生成漂移哨兵。"))
        actions.append("先添加自选标的，再查看策略漂移报告。")
        score -= 8

    if not strategies:
        checks.append(ReadinessCheck(name="策略覆盖", status="fail", message="没有启用的策略模板。"))
        blockers.append("至少启用一个策略模板并完成验证。")
        score -= 25
    else:
        coverage = len([item for item in strategies if item.id in latest_by_strategy]) / len(strategies)
        if coverage < 0.5:
            checks.append(ReadinessCheck(name="策略覆盖", status="watch", message=f"已验证 {coverage * 100:.0f}% 的启用策略。"))
            actions.append("运行策略验证，提高启用策略的验证覆盖率。")
            score -= 12
        else:
            checks.append(ReadinessCheck(name="策略覆盖", status="pass", message=f"已验证 {coverage * 100:.0f}% 的启用策略。"))

    failed_validations = [item for item in latest_validations if item.pass_fail == "fail"]
    watch_validations = [item for item in latest_validations if item.pass_fail == "watch"]
    if failed_validations:
        checks.append(ReadinessCheck(name="验证结果", status="fail", message=f"{len(failed_validations)} 个策略最近验证失败。"))
        blockers.extend(f"{item.strategy_name} 最近验证失败" for item in failed_validations[:3])
        score -= 25
    elif watch_validations:
        checks.append(ReadinessCheck(name="验证结果", status="watch", message=f"{len(watch_validations)} 个策略处于观察状态。"))
        actions.extend(f"复核 {item.strategy_name} 的前向测试和过滤器" for item in watch_validations[:2])
        score -= 8
    elif latest_validations:
        checks.append(ReadinessCheck(name="验证结果", status="pass", message=f"{len(latest_validations)} 个策略最近验证通过。"))
    else:
        checks.append(ReadinessCheck(name="验证结果", status="watch", message="暂无策略验证记录。"))
        actions.append("先运行至少一次策略验证。")
        score -= 15

    score = max(0, min(100, score))
    if blockers:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif score >= 80:
        status = "pass"
    else:
        status = "watch"
    if status == "pass":
        summary = "研究环境可进入观察或小规模模拟。"
    elif status == "watch":
        summary = "研究环境可继续分析，但需要补齐验证或降低风险。"
    else:
        summary = "研究环境存在阻断项，暂不适合自动化执行。"
    if not actions and status == "pass":
        actions = ["保持策略验证频率", "继续监控市场状态变化", "新增策略前先运行前向测试"]
    return ResearchReadiness(
        generated_at=generated_at,
        status=status,
        score=score,
        summary=summary,
        regime=regime,
        checks=checks,
        blockers=blockers,
        actions=actions[:6],
        latest_validations=latest_validations,
    )


def lightweight_research_readiness_report(
    regime: MarketRegime | None = None,
    quality: DataQualityReport | None = None,
) -> ResearchReadiness:
    init_db()
    generated_at = datetime.now(timezone.utc).isoformat()
    current_regime = regime or lightweight_market_regime_report()
    data_quality = quality or lightweight_data_quality_report()
    strategies = list_strategy_templates(enabled_only=True)
    validations = list_strategy_validations(limit=50)
    latest_by_strategy: dict[str, StrategyValidationResult] = {}
    for validation in validations:
        latest_by_strategy.setdefault(validation.strategy_id, validation)
    latest_validations = list(latest_by_strategy.values())[:8]

    checks: list[ReadinessCheck] = []
    blockers: list[str] = []
    actions: list[str] = []
    score = 100

    checks.append(ReadinessCheck(name="本地数据质量", status=data_quality.status, message=data_quality.summary))
    if data_quality.status == "fail":
        blockers.extend(data_quality.blockers[:3])
        score -= 32
    elif data_quality.status == "watch":
        actions.extend(data_quality.actions[:2])
        score -= 10

    if current_regime.risk_level == "risk":
        checks.append(ReadinessCheck(name="市场状态快照", status="fail", message=current_regime.summary))
        blockers.append("市场状态快照为 risk，批量策略只允许观察。")
        score -= 26
    elif current_regime.risk_level == "watch":
        checks.append(ReadinessCheck(name="市场状态快照", status="watch", message=current_regime.summary))
        actions.extend(current_regime.actions[:2])
        score -= 10
    else:
        checks.append(ReadinessCheck(name="市场状态快照", status="pass", message=current_regime.summary))

    if not strategies:
        checks.append(ReadinessCheck(name="策略覆盖", status="fail", message="没有启用策略模板。"))
        blockers.append("至少启用一个策略模板。")
        score -= 28
    else:
        coverage = len([item for item in strategies if item.id in latest_by_strategy]) / len(strategies)
        if coverage < 0.5:
            checks.append(ReadinessCheck(name="策略覆盖", status="watch", message=f"已验证 {coverage * 100:.0f}% 的启用策略。"))
            actions.append("补齐启用策略的策略验证记录。")
            score -= 12
        else:
            checks.append(ReadinessCheck(name="策略覆盖", status="pass", message=f"已验证 {coverage * 100:.0f}% 的启用策略。"))

    failed_validations = [item for item in latest_validations if item.pass_fail == "fail"]
    watch_validations = [item for item in latest_validations if item.pass_fail == "watch"]
    if failed_validations:
        checks.append(ReadinessCheck(name="验证结果", status="fail", message=f"{len(failed_validations)} 个策略最近验证失败。"))
        blockers.extend(f"{item.strategy_name} 最近验证失败" for item in failed_validations[:3])
        score -= 24
    elif watch_validations:
        checks.append(ReadinessCheck(name="验证结果", status="watch", message=f"{len(watch_validations)} 个策略处于观察状态。"))
        actions.extend(f"复核 {item.strategy_name} 的前向测试和过滤器" for item in watch_validations[:2])
        score -= 8
    elif latest_validations:
        checks.append(ReadinessCheck(name="验证结果", status="pass", message=f"{len(latest_validations)} 个策略最近验证通过。"))
    else:
        checks.append(ReadinessCheck(name="验证结果", status="watch", message="暂无策略验证记录。"))
        actions.append("先运行至少一次策略验证。")
        score -= 15

    score = max(0, min(100, score))
    if blockers:
        status: Literal["pass", "watch", "fail"] = "fail"
        summary = "本地研究证据存在阻断项，暂不适合扩大模拟。"
    elif score >= 80:
        status = "pass"
        summary = "本地研究证据可支持观察或小规模模拟。"
    else:
        status = "watch"
        summary = "本地研究证据可继续分析，但需要补齐验证或降低风险。"
    if not actions and status == "pass":
        actions = ["保持策略验证频率", "继续监控市场状态变化", "新增策略前先运行前向测试"]
    return ResearchReadiness(
        generated_at=generated_at,
        status=status,
        score=score,
        summary=summary,
        regime=current_regime,
        checks=checks,
        blockers=dedupe(blockers),
        actions=dedupe(actions)[:6],
        latest_validations=latest_validations,
    )
