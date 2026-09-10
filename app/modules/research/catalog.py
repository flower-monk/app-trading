"""Research domain: catalog implementation."""

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
    DataCatalogItem,
    DataFreshnessItem,
    DataFreshnessReport,
    DataLineageNode,
    DataLineageReport,
    DataQualityReport,
    FactorAdmissionQueueItem,
    FactorCoverageBackfillRequest,
    FactorCoverageBackfillResult,
    FactorCoverageItem,
    FactorCoverageReport,
    FactorLifecycleItem,
    FactorLifecycleReport,
    FilterAdmissionCheck,
    FilterAdmissionReport,
    FilterContributionItem,
    FilterContributionReport,
    FilterStressScenario,
    FilterStressTestReport,
    FilterValidationRequest,
    FilterValidationResult,
    ForwardTestRequest,
    Literal,
    MarketRegime,
    MarketRegimeTransition,
    ReadinessCheck,
    RegimeFilterTemplate,
    RegimePlaybook,
    RegimePlaybookStep,
    ResearchActionItem,
    ResearchActionPlan,
    ResearchCoverageBackfillRequest,
    ResearchCoverageBackfillResult,
    ResearchCoverageItem,
    ResearchCoverageReport,
    ResearchDashboardMetric,
    ResearchDashboardSummary,
    ResearchHealthCheck,
    ResearchHealthReport,
    ResearchRunAuditItem,
    ResearchRunAuditReport,
    StrategyAllocationReport,
    StrategyLineageItem,
    StressTestRequest,
    WalkForwardRequest,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
backtest_summaries = service_call('app.modules.research.backtesting', 'backtest_summaries')
candles = service_call('app.modules.market.service', 'get_market_candles')
list_news_sources = service_call('app.modules.news.service', 'list_news_sources')
market_provider_status = service_call('app.modules.market.service', 'get_market_provider_status')
from app.modules.market.service import MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
dedupe = service_call('app.modules.market.service', 'dedupe')
filter_library_score_report = service_call('app.modules.market.service', 'filter_library_score_report')
filter_strategy_coverage_report = service_call('app.modules.market.service', 'filter_strategy_coverage_report')
get_active_market_provider_name = service_call('app.modules.market.service', 'get_active_market_provider_name')
get_market_breadth = service_call('app.modules.market.service', 'get_market_breadth')
get_portfolio_risk_report = service_call('app.modules.trading.service', 'get_portfolio_risk_report')
get_portfolio_summary = service_call('app.modules.trading.service', 'get_portfolio_summary')
get_system_health = service_call('app.modules.operations.service', 'get_system_health')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_strategy_templates = service_call('app.modules.market.service', 'list_strategy_templates')
list_watchlist_snapshots = service_call('app.modules.market.service', 'list_watchlist_snapshots')
parse_volume_value = service_call('app.modules.market.service', 'parse_volume_value')
run_screener = service_call('app.modules.market.service', 'run_screener')

# Deferred internal references keep research submodules independently importable.
factor_admission_queue_report = service_call('app.modules.research.strategy', 'factor_admission_queue_report')
factor_leaderboard_report = service_call('app.modules.research.strategy', 'factor_leaderboard_report')
get_factor_definition = service_call('app.modules.research.factors', 'get_factor_definition')
latest_backtest_for_factor = service_call('app.modules.research.experiments', 'latest_backtest_for_factor')
latest_forward_for_factor = service_call('app.modules.research.experiments', 'latest_forward_for_factor')
latest_stress_for_factor = service_call('app.modules.research.experiments', 'latest_stress_for_factor')
latest_walk_forward_for_factor = service_call('app.modules.research.experiments', 'latest_walk_forward_for_factor')
list_factor_definitions = service_call('app.modules.research.factors', 'list_factor_definitions')
market_state_control_report = service_call('app.modules.research.strategy', 'market_state_control_report')
regime_exposure_report = service_call('app.modules.research.strategy', 'regime_exposure_report')
regime_scenario_risk_report = service_call('app.modules.research.strategy', 'regime_scenario_risk_report')
research_readiness_report = service_call('app.modules.research.strategy', 'research_readiness_report')
run_backtest = service_call('app.modules.research.backtesting', 'run_backtest')
run_forward_test = service_call('app.modules.research.experiments', 'run_forward_test')
run_stress_test = service_call('app.modules.research.experiments', 'run_stress_test')
run_walk_forward_test = service_call('app.modules.research.experiments', 'run_walk_forward_test')
strategy_allocation_report = service_call('app.modules.research.strategy', 'strategy_allocation_report')
strategy_backtest_monitor_report = service_call('app.modules.research.experiments', 'strategy_backtest_monitor_report')
strategy_crowding_report = service_call('app.modules.research.strategy', 'strategy_crowding_report')
strategy_evidence_pack_report = service_call('app.modules.research.strategy', 'strategy_evidence_pack_report')
strategy_forward_monitor_report = service_call('app.modules.research.experiments', 'strategy_forward_monitor_report')
strategy_leaderboard_report = service_call('app.modules.research.strategy', 'strategy_leaderboard_report')
strategy_lifecycle_report = service_call('app.modules.research.strategy', 'strategy_lifecycle_report')
strategy_operating_plan_report = service_call('app.modules.research.strategy', 'strategy_operating_plan_report')
strategy_promotion_gates = service_call('app.modules.research.strategy', 'strategy_promotion_gates')
strategy_regime_matrix_report = service_call('app.modules.research.strategy', 'strategy_regime_matrix_report')
strategy_revalidation_plan_report = service_call('app.modules.research.experiments', 'strategy_revalidation_plan_report')
strategy_risk_control_report = service_call('app.modules.research.strategy', 'strategy_risk_control_report')
strategy_sensitivity_report = service_call('app.modules.research.experiments', 'strategy_sensitivity_report')
strategy_validation_coverage_report = service_call('app.modules.research.experiments', 'strategy_validation_coverage_report')
strategy_validation_decay_report = service_call('app.modules.research.strategy', 'strategy_validation_decay_report')


def data_catalog_items() -> list[DataCatalogItem]:
    provider = market_provider_status()
    watchlist_items = list_watchlist_snapshots()
    factor_defs = list_factor_definitions()
    strategies = list_strategy_templates()
    news_source_count = len(list_news_sources())
    article_count = 0
    forward_count = 0
    walk_forward_count = 0
    stress_count = 0
    backtest_count = 0
    init_db()
    with db_connect() as connection:
        article_count = int(connection.execute("SELECT COUNT(*) AS total FROM news_articles").fetchone()["total"])
        backtest_count = int(connection.execute("SELECT COUNT(*) AS total FROM backtest_runs").fetchone()["total"])
        forward_count = int(connection.execute("SELECT COUNT(*) AS total FROM forward_test_runs").fetchone()["total"])
        walk_forward_count = int(connection.execute("SELECT COUNT(*) AS total FROM walk_forward_runs").fetchone()["total"])
        stress_count = int(connection.execute("SELECT COUNT(*) AS total FROM stress_test_runs").fetchone()["total"])
    return [
        DataCatalogItem(
            id="active-watchlist",
            name="当前自选行情快照",
            type="market",
            provider=provider.display_name,
            coverage=[item.symbol for item in watchlist_items],
            update_mode="WebSocket / REST refresh",
            status="ready" if watchlist_items else "degraded",
            rows=len(watchlist_items),
            fields=["price", "change_percent", "volume", "rsi", "macd", "trend_score", "signal"],
            notes=f"当前行情源 {provider.name}，用于盯盘、筛选、预警和回测入参。",
        ),
        DataCatalogItem(
            id="news-events",
            name="新闻与事件库",
            type="news",
            provider="Managed RSS and news APIs",
            coverage=[source.name for source in list_news_sources()],
            update_mode="Manual refresh / automation",
            status="ready" if news_source_count or article_count else "degraded",
            rows=article_count,
            fields=["title", "summary", "symbols", "sentiment", "impact_score", "event_type"],
            notes="用于 K 线新闻标记、AI 简报和事件驱动因子。",
        ),
        DataCatalogItem(
            id="factor-library",
            name="因子库",
            type="factor",
            provider="SQLite factor_definitions",
            coverage=sorted({item.category for item in factor_defs}),
            update_mode="User managed",
            status="ready" if factor_defs else "degraded",
            rows=len(factor_defs),
            fields=["name", "category", "signal", "horizon", "enabled"],
            notes="用于因子推荐、回测和前向测试。",
        ),
        DataCatalogItem(
            id="strategy-library",
            name="策略模板库",
            type="strategy",
            provider="SQLite strategy_templates",
            coverage=sorted({item.category for item in strategies}),
            update_mode="User managed",
            status="ready" if strategies else "degraded",
            rows=len(strategies),
            fields=["factor_ids", "default_filters", "risk_controls", "validation_rules"],
            notes="沉淀可复用的策略配置、过滤器和验证规则。",
        ),
        DataCatalogItem(
            id="research-runs",
            name="研究运行记录",
            type="strategy",
            provider="SQLite backtest_runs / forward_test_runs / walk_forward_runs",
            coverage=["backtest", "forward-test", "walk-forward", "stress-test"],
            update_mode="On demand",
            status="ready" if backtest_count or forward_count or walk_forward_count or stress_count else "degraded",
            rows=backtest_count + forward_count + walk_forward_count + stress_count,
            fields=["return", "drawdown", "sharpe", "exposure", "pass_fail"],
            notes="用于比较回测、前向和滚动样本外稳定性。",
        ),
    ]


def data_quality_report() -> DataQualityReport:
    init_db()
    generated_at = datetime.now(timezone.utc).isoformat()
    checks: list[ReadinessCheck] = []
    blockers: list[str] = []
    actions: list[str] = []
    health = get_system_health()
    provider = market_provider_status()
    watchlist_items = list_watchlist_snapshots()

    if health.status == "error":
        checks.append(ReadinessCheck(name="系统健康", status="fail", message="存在系统级错误，数据质量不可用。"))
        blockers.append("先修复系统健康错误。")
    elif health.status == "warn":
        checks.append(ReadinessCheck(name="系统健康", status="watch", message="系统存在告警，建议检查数据源或自动化。"))
        actions.append("查看右侧系统状态中的 warn 组件。")
    else:
        checks.append(ReadinessCheck(name="系统健康", status="pass", message="核心系统组件可用。"))

    if provider.status == "online":
        checks.append(ReadinessCheck(name="行情源", status="pass", message=f"{provider.display_name} 在线。"))
    else:
        checks.append(ReadinessCheck(name="行情源", status="fail", message=f"{provider.display_name} 当前 {provider.status}。"))
        blockers.append("切换到可用行情源或等待行情源恢复。")

    if len(watchlist_items) >= 3:
        checks.append(ReadinessCheck(name="自选覆盖", status="pass", message=f"当前覆盖 {len(watchlist_items)} 个标的。"))
    elif watchlist_items:
        checks.append(ReadinessCheck(name="自选覆盖", status="watch", message=f"仅覆盖 {len(watchlist_items)} 个标的，样本偏少。"))
        actions.append("增加自选标的以提升筛选和研究覆盖。")
    else:
        checks.append(ReadinessCheck(name="自选覆盖", status="fail", message="没有可用自选标的。"))
        blockers.append("先添加自选标的。")

    candle_failures: list[str] = []
    for snapshot in watchlist_items[: min(5, len(watchlist_items))]:
        try:
            rows = candles(snapshot.symbol, interval="1h", limit=80)
            if len(rows) < 60:
                candle_failures.append(f"{snapshot.symbol} 仅 {len(rows)} 根")
        except HTTPException:
            candle_failures.append(f"{snapshot.symbol} K线不可用")
    if not watchlist_items:
        checks.append(ReadinessCheck(name="K线完整性", status="fail", message="缺少标的，无法检查 K 线。"))
    elif candle_failures:
        checks.append(ReadinessCheck(name="K线完整性", status="watch", message="；".join(candle_failures[:3])))
        actions.append("切换行情源或减少依赖缺失 K 线的标的。")
    else:
        checks.append(ReadinessCheck(name="K线完整性", status="pass", message="抽样标的 K 线数量满足回测要求。"))

    news_sources = [source for source in list_news_sources() if source.enabled]
    news_count = 0
    backtest_count = 0
    forward_count = 0
    walk_forward_count = 0
    stress_count = 0
    strategy_count = 0
    with db_connect() as connection:
        news_count = int(connection.execute("SELECT COUNT(*) AS total FROM news_articles").fetchone()["total"])
        backtest_count = int(connection.execute("SELECT COUNT(*) AS total FROM backtest_runs").fetchone()["total"])
        forward_count = int(connection.execute("SELECT COUNT(*) AS total FROM forward_test_runs").fetchone()["total"])
        walk_forward_count = int(connection.execute("SELECT COUNT(*) AS total FROM walk_forward_runs").fetchone()["total"])
        stress_count = int(connection.execute("SELECT COUNT(*) AS total FROM stress_test_runs").fetchone()["total"])
        strategy_count = int(connection.execute("SELECT COUNT(*) AS total FROM strategy_templates WHERE enabled = 1").fetchone()["total"])

    if news_sources or news_count:
        checks.append(ReadinessCheck(name="新闻覆盖", status="pass", message=f"已启用信息源 {len(news_sources)} 个，已入库新闻 {news_count} 条。"))
    else:
        checks.append(ReadinessCheck(name="新闻覆盖", status="watch", message="暂无启用的信息源或新闻入库，事件因子证据不足。"))
        actions.append("启用信息源或运行信息源刷新。")

    enabled_factors = [item for item in list_factor_definitions() if item.enabled]
    if len(enabled_factors) >= 3:
        checks.append(ReadinessCheck(name="因子库", status="pass", message=f"启用因子 {len(enabled_factors)} 个。"))
    elif enabled_factors:
        checks.append(ReadinessCheck(name="因子库", status="watch", message=f"仅启用 {len(enabled_factors)} 个因子。"))
        actions.append("补充或启用更多因子以提高研究覆盖。")
    else:
        checks.append(ReadinessCheck(name="因子库", status="fail", message="没有启用因子。"))
        blockers.append("至少启用一个因子。")

    if strategy_count >= 2:
        checks.append(ReadinessCheck(name="策略库", status="pass", message=f"启用策略模板 {strategy_count} 个。"))
    elif strategy_count:
        checks.append(ReadinessCheck(name="策略库", status="watch", message=f"仅启用 {strategy_count} 个策略模板。"))
        actions.append("沉淀更多可复用策略模板。")
    else:
        checks.append(ReadinessCheck(name="策略库", status="fail", message="没有启用策略模板。"))
        blockers.append("先创建或启用策略模板。")

    research_runs = backtest_count + forward_count + walk_forward_count + stress_count
    if backtest_count and forward_count and walk_forward_count and stress_count:
        checks.append(ReadinessCheck(name="研究记录", status="pass", message=f"回测 {backtest_count}，前向 {forward_count}，滚动 {walk_forward_count}，压力 {stress_count}。"))
    elif research_runs:
        checks.append(ReadinessCheck(name="研究记录", status="watch", message=f"研究记录不均衡：回测 {backtest_count}，前向 {forward_count}，滚动 {walk_forward_count}，压力 {stress_count}。"))
        actions.append("对核心因子补齐前向、滚动样本外和压力测试。")
    else:
        checks.append(ReadinessCheck(name="研究记录", status="watch", message="暂无回测、前向、滚动样本外或压力测试记录。"))
        actions.append("先对一个核心因子运行一键验证。")

    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(0, 100 - fail_count * 28 - watch_count * 10)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    if not actions and not blockers:
        actions.append("数据质量满足当前研究流程，可继续验证策略。")
    return DataQualityReport(
        generated_at=generated_at,
        status=status,
        score=score,
        summary=f"数据质量 {score}/100：{sum(item.status == 'pass' for item in checks)} 项通过，{watch_count} 项观察，{fail_count} 项失败。",
        checks=checks,
        blockers=blockers,
        actions=actions,
    )


def lightweight_data_quality_report() -> DataQualityReport:
    init_db()
    generated_at = datetime.now(timezone.utc).isoformat()
    checks: list[ReadinessCheck] = []
    blockers: list[str] = []
    actions: list[str] = []
    provider_name = get_active_market_provider_name()
    provider = MARKET_PROVIDERS[provider_name].status(is_active=True)
    with db_connect() as connection:
        watchlist_count = int(connection.execute("SELECT COUNT(*) AS total FROM watchlist_items WHERE provider = ?", (provider_name,)).fetchone()["total"])
        news_count = int(connection.execute("SELECT COUNT(*) AS total FROM news_articles").fetchone()["total"])
        factor_count = int(connection.execute("SELECT COUNT(*) AS total FROM factor_definitions WHERE enabled = 1").fetchone()["total"])
        strategy_count = int(connection.execute("SELECT COUNT(*) AS total FROM strategy_templates WHERE enabled = 1").fetchone()["total"])
        validation_count = int(connection.execute("SELECT COUNT(*) AS total FROM strategy_validation_runs").fetchone()["total"])
        research_count = sum(
            int(connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"])
            for table in ["backtest_runs", "forward_test_runs", "walk_forward_runs", "stress_test_runs"]
        )

    checks.append(ReadinessCheck(name="行情源配置", status="pass" if provider.status == "online" else "watch", message=f"{provider.display_name}：{provider.status}。"))
    if watchlist_count:
        checks.append(ReadinessCheck(name="自选池配置", status="pass", message=f"已保存 {watchlist_count} 个自选标的。"))
    else:
        checks.append(ReadinessCheck(name="自选池配置", status="watch", message="尚未保存自选标的，将使用行情源默认标的。"))
        actions.append("保存核心自选池，减少研究样本漂移。")
    if news_count:
        checks.append(ReadinessCheck(name="新闻库", status="pass", message=f"已入库新闻 {news_count} 条。"))
    else:
        checks.append(ReadinessCheck(name="新闻库", status="watch", message="新闻库暂无入库记录。"))
        actions.append("启用或刷新信息源，补齐事件因子证据。")
    if factor_count:
        checks.append(ReadinessCheck(name="因子库", status="pass" if factor_count >= 3 else "watch", message=f"启用因子 {factor_count} 个。"))
        if factor_count < 3:
            actions.append("补充更多可验证因子，避免策略过度集中。")
    else:
        checks.append(ReadinessCheck(name="因子库", status="fail", message="没有启用因子。"))
        blockers.append("至少启用一个因子。")
    if strategy_count:
        checks.append(ReadinessCheck(name="策略库", status="pass" if strategy_count >= 2 else "watch", message=f"启用策略 {strategy_count} 个。"))
    else:
        checks.append(ReadinessCheck(name="策略库", status="fail", message="没有启用策略模板。"))
        blockers.append("先创建或启用策略模板。")
    if validation_count and research_count:
        checks.append(ReadinessCheck(name="验证记录", status="pass", message=f"策略验证 {validation_count} 条，研究运行 {research_count} 条。"))
    else:
        checks.append(ReadinessCheck(name="验证记录", status="watch", message=f"策略验证 {validation_count} 条，研究运行 {research_count} 条。"))
        actions.append("运行策略验证和前向测试，补齐准入证据。")

    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(0, 100 - fail_count * 28 - watch_count * 10)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    if not actions and not blockers:
        actions.append("本地研究数据满足批量策略审计。")
    return DataQualityReport(
        generated_at=generated_at,
        status=status,
        score=score,
        summary=f"本地数据质量 {score}/100：{sum(item.status == 'pass' for item in checks)} 项通过，{watch_count} 项观察，{fail_count} 项失败。",
        checks=checks,
        blockers=blockers,
        actions=actions,
    )


def parse_iso_datetime_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def data_freshness_report() -> DataFreshnessReport:
    init_db()
    now = datetime.now(timezone.utc)
    datasets: list[tuple[str, str, str, int, int, str]] = []
    with db_connect() as connection:
        table_specs = [
            ("news-articles", "新闻事件", "news_articles", "published_at", 24, "RSS / API 信息源"),
            ("backtest-runs", "回测记录", "backtest_runs", "created_at", 168, "SQLite backtest_runs"),
            ("forward-test-runs", "前向测试", "forward_test_runs", "generated_at", 168, "SQLite forward_test_runs"),
            ("walk-forward-runs", "滚动前向", "walk_forward_runs", "generated_at", 336, "SQLite walk_forward_runs"),
            ("stress-test-runs", "压力测试", "stress_test_runs", "generated_at", 336, "SQLite stress_test_runs"),
            ("strategy-validations", "策略验证", "strategy_validation_runs", "generated_at", 168, "SQLite strategy_validation_runs"),
            ("market-regime-snapshots", "市场状态快照", "market_regime_snapshots", "generated_at", 4, "SQLite market_regime_snapshots"),
            ("factor-definitions", "因子定义", "factor_definitions", "created_at", 720, "SQLite factor_definitions"),
            ("strategy-templates", "策略模板", "strategy_templates", "created_at", 720, "SQLite strategy_templates"),
        ]
        for dataset_id, name, table, column, sla_hours, source in table_specs:
            row = connection.execute(f"SELECT COUNT(*) AS total, MAX({column}) AS latest FROM {table}").fetchone()
            datasets.append((dataset_id, name, row["latest"], int(row["total"]), sla_hours, source))

    watchlist = list_watchlist_snapshots()
    candle_latest: str | None = None
    candle_rows = 0
    for snapshot in watchlist[:5]:
        try:
            history = candles(snapshot.symbol, interval="1h", limit=80)
        except HTTPException:
            history = []
        candle_rows += len(history)
        if history:
            candidate = history[-1].time
            if candle_latest is None or candidate > candle_latest:
                candle_latest = candidate
    datasets.insert(0, ("market-candles", "行情 K 线", candle_latest, candle_rows, 2, market_provider_status().display_name))

    items: list[DataFreshnessItem] = []
    for dataset_id, name, latest_at, rows, sla_hours, source in datasets:
        parsed = parse_iso_datetime_or_none(latest_at)
        age_hours = round(max(0.0, (now - parsed).total_seconds() / 3600), 1) if parsed else None
        if rows == 0 or parsed is None:
            status: Literal["pass", "watch", "fail"] = "fail"
            lag_score = 0
            action = "缺少可用记录，先运行采集、刷新或研究任务。"
        elif age_hours is not None and age_hours > sla_hours * 2:
            status = "fail"
            lag_score = max(0, int(100 - (age_hours / max(1, sla_hours)) * 45))
            action = f"超过 SLA {sla_hours}h 两倍以上，优先刷新该数据集。"
        elif age_hours is not None and age_hours > sla_hours:
            status = "watch"
            lag_score = max(0, int(100 - (age_hours / max(1, sla_hours)) * 35))
            action = f"超过 SLA {sla_hours}h，加入刷新或复验队列。"
        else:
            status = "pass"
            lag_score = 100 if age_hours is None else max(70, int(100 - (age_hours / max(1, sla_hours)) * 20))
            action = "新鲜度满足当前研究和盯盘要求。"
        items.append(
            DataFreshnessItem(
                dataset_id=dataset_id,
                name=name,
                status=status,
                rows=rows,
                latest_at=latest_at,
                age_hours=age_hours,
                sla_hours=sla_hours,
                lag_score=max(0, min(100, lag_score)),
                source=source,
                action=action,
            )
        )

    fail_count = sum(item.status == "fail" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    score = int(round(sum(item.lag_score for item in items) / max(1, len(items))))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先处理 fail 数据集，避免用过期证据做策略决策。")
    if watch_count:
        actions.append("watch 数据集加入刷新、回测或复验队列。")
    if not actions:
        actions.append("数据新鲜度满足当前工作台要求。")
    return DataFreshnessReport(
        generated_at=now.isoformat(),
        status=status,
        score=score,
        summary=f"数据新鲜度 {score}/100：{sum(item.status == 'pass' for item in items)} 项通过，{watch_count} 项观察，{fail_count} 项失败。",
        items=items,
        actions=dedupe(actions),
    )


def research_coverage_report() -> ResearchCoverageReport:
    init_db()
    watchlist_items = list_watchlist_snapshots()
    items: list[ResearchCoverageItem] = []
    with db_connect() as connection:
        for snapshot in watchlist_items:
            backtest_row = connection.execute(
                "SELECT COUNT(*) AS total, MAX(created_at) AS latest FROM backtest_runs WHERE symbol = ?",
                (snapshot.symbol,),
            ).fetchone()
            forward_row = connection.execute(
                "SELECT COUNT(*) AS total, MAX(generated_at) AS latest FROM forward_test_runs WHERE symbol = ?",
                (snapshot.symbol,),
            ).fetchone()
            walk_forward_row = connection.execute(
                "SELECT COUNT(*) AS total, MAX(generated_at) AS latest FROM walk_forward_runs WHERE symbol = ?",
                (snapshot.symbol,),
            ).fetchone()
            stress_row = connection.execute(
                "SELECT COUNT(*) AS total, MAX(generated_at) AS latest FROM stress_test_runs WHERE symbol = ?",
                (snapshot.symbol,),
            ).fetchone()
            backtest_count = int(backtest_row["total"])
            forward_count = int(forward_row["total"])
            walk_forward_count = int(walk_forward_row["total"])
            stress_count = int(stress_row["total"])
            latest_candidates = [item for item in [backtest_row["latest"], forward_row["latest"], walk_forward_row["latest"], stress_row["latest"]] if item]
            latest_run_at = max(latest_candidates) if latest_candidates else None
            coverage_parts = sum([backtest_count > 0, forward_count > 0, walk_forward_count > 0, stress_count > 0])
            score = coverage_parts * 23 + min(8, backtest_count + forward_count + walk_forward_count + stress_count)
            score = min(100, score)
            notes: list[str] = [
                f"回测 {backtest_count}，前向 {forward_count}，滚动 {walk_forward_count}，压力 {stress_count}。",
            ]
            actions: list[str] = []
            if coverage_parts == 4:
                status: Literal["pass", "watch", "fail"] = "pass"
                actions.append("研究覆盖完整，可进入策略横向比较。")
            elif coverage_parts:
                status = "watch"
                if not backtest_count:
                    actions.append("补一次基础回测。")
                if not forward_count:
                    actions.append("补一次前向测试。")
                if not walk_forward_count:
                    actions.append("补一次滚动样本外验证。")
                if not stress_count:
                    actions.append("补一次压力测试。")
            else:
                status = "fail"
                actions.append("先运行一键验证或至少运行基础回测。")
            items.append(
                ResearchCoverageItem(
                    symbol=snapshot.symbol,
                    name=snapshot.name,
                    status=status,
                    score=score,
                    backtest_count=backtest_count,
                    forward_count=forward_count,
                    walk_forward_count=walk_forward_count,
                    stress_count=stress_count,
                    latest_run_at=latest_run_at,
                    notes=notes,
                    actions=actions,
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
    return ResearchCoverageReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"研究覆盖：{sum(item.status == 'pass' for item in items)} 个完整，{watch_count} 个待补齐，{fail_count} 个未覆盖。",
        items=items,
    )


def factor_coverage_report() -> FactorCoverageReport:
    init_db()
    factors = list_factor_definitions(enabled_only=True)
    watchlist_items = list_watchlist_snapshots()
    symbol_count = len(watchlist_items)
    items: list[FactorCoverageItem] = []
    with db_connect() as connection:
        for factor in factors:
            backtest_symbols = 0
            forward_symbols = 0
            walk_forward_symbols = 0
            stress_symbols = 0
            latest_candidates: list[str] = []
            missing_symbols: list[str] = []
            for snapshot in watchlist_items:
                backtest_row = connection.execute(
                    "SELECT COUNT(*) AS total, MAX(created_at) AS latest FROM backtest_runs WHERE symbol = ? AND factor_id = ?",
                    (snapshot.symbol, factor.id),
                ).fetchone()
                forward_row = connection.execute(
                    "SELECT COUNT(*) AS total, MAX(generated_at) AS latest FROM forward_test_runs WHERE symbol = ? AND factor_id = ?",
                    (snapshot.symbol, factor.id),
                ).fetchone()
                walk_row = connection.execute(
                    "SELECT COUNT(*) AS total, MAX(generated_at) AS latest FROM walk_forward_runs WHERE symbol = ? AND factor_id = ?",
                    (snapshot.symbol, factor.id),
                ).fetchone()
                stress_row = connection.execute(
                    "SELECT COUNT(*) AS total, MAX(generated_at) AS latest FROM stress_test_runs WHERE symbol = ? AND factor_id = ?",
                    (snapshot.symbol, factor.id),
                ).fetchone()
                has_backtest = int(backtest_row["total"]) > 0
                has_forward = int(forward_row["total"]) > 0
                has_walk = int(walk_row["total"]) > 0
                has_stress = int(stress_row["total"]) > 0
                backtest_symbols += int(has_backtest)
                forward_symbols += int(has_forward)
                walk_forward_symbols += int(has_walk)
                stress_symbols += int(has_stress)
                latest_candidates.extend(
                    item for item in [backtest_row["latest"], forward_row["latest"], walk_row["latest"], stress_row["latest"]] if item
                )
                if not all([has_backtest, has_forward, has_walk, has_stress]):
                    missing_symbols.append(snapshot.symbol)
            denominator = max(1, symbol_count * 4)
            covered_parts = backtest_symbols + forward_symbols + walk_forward_symbols + stress_symbols
            score = int(round(covered_parts / denominator * 100))
            if score >= 85:
                status: Literal["pass", "watch", "fail"] = "pass"
            elif score >= 35:
                status = "watch"
            else:
                status = "fail"
            actions: list[str] = []
            if backtest_symbols < symbol_count:
                actions.append("补齐缺失标的的基础回测。")
            if forward_symbols < symbol_count:
                actions.append("补齐前向测试用于确认样本外效果。")
            if walk_forward_symbols < symbol_count:
                actions.append("补齐滚动样本外验证。")
            if stress_symbols < symbol_count:
                actions.append("补齐压力测试，检查极端场景韧性。")
            if not actions:
                actions.append("覆盖完整，可进入跨标的因子比较。")
            items.append(
                FactorCoverageItem(
                    factor_id=factor.id,
                    factor_name=factor.name,
                    category=factor.category,
                    status=status,
                    score=score,
                    symbol_count=symbol_count,
                    backtest_symbols=backtest_symbols,
                    forward_symbols=forward_symbols,
                    walk_forward_symbols=walk_forward_symbols,
                    stress_symbols=stress_symbols,
                    latest_run_at=max(latest_candidates) if latest_candidates else None,
                    missing_symbols=missing_symbols[:8],
                    actions=dedupe(actions)[:4],
                )
            )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score), reverse=True)
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    return FactorCoverageReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"因子覆盖：{sum(item.status == 'pass' for item in ranked)} 个完整，{watch_count} 个待补，{fail_count} 个不足。",
        items=ranked,
    )


def factor_lifecycle_report(symbol: str | None = None) -> FactorLifecycleReport:
    factors = list_factor_definitions()
    coverage = factor_coverage_report()
    coverage_by_factor = {item.factor_id: item for item in coverage.items}
    reference_symbol = (symbol or (list_watchlist_snapshots()[0].symbol if list_watchlist_snapshots() else "AAPL")).upper()
    try:
        leaderboard = factor_leaderboard_report(reference_symbol)
        leaderboard_by_factor = {item.factor_id: item for item in leaderboard.items}
    except HTTPException:
        leaderboard_by_factor = {}
    try:
        queue = factor_admission_queue_report(limit=10, interval="1h")
        best_admission_by_factor: dict[str, FactorAdmissionQueueItem] = {}
        for item in queue.items:
            current = best_admission_by_factor.get(item.factor_id)
            if current is None or item.score > current.score:
                best_admission_by_factor[item.factor_id] = item
    except HTTPException:
        best_admission_by_factor = {}
    items: list[FactorLifecycleItem] = []
    for factor in factors:
        factor_coverage = coverage_by_factor.get(factor.id)
        leaderboard_item = leaderboard_by_factor.get(factor.id)
        admission_item = best_admission_by_factor.get(factor.id)
        coverage_score = factor_coverage.score if factor_coverage else None
        leaderboard_score = leaderboard_item.composite_score if leaderboard_item else None
        admission_score = admission_item.score if admission_item else None
        score_parts = [
            value
            for value in [coverage_score, leaderboard_score, admission_score]
            if value is not None
        ]
        score = int(round(sum(score_parts) / len(score_parts))) if score_parts else 0
        blockers: list[str] = []
        actions: list[str] = []
        if not factor.enabled:
            stage: Literal["idea", "research", "validation", "candidate", "active", "paused"] = "paused"
            blockers.append("因子已停用")
            actions.append("如需继续研究，先启用因子并补齐覆盖。")
        elif coverage_score is None or coverage_score < 25:
            stage = "idea"
            actions.append("先补齐基础回测和前向测试。")
        elif coverage_score < 60:
            stage = "research"
            actions.extend((factor_coverage.actions[:2] if factor_coverage else ["补齐研究覆盖。"]))
        elif leaderboard_item is None or leaderboard_item.status == "fail":
            stage = "validation"
            blockers.append("当前榜单或稳健性不足")
            actions.append("运行回测、walk-forward、stress 后再进入候选。")
        elif admission_item and admission_item.status == "pass" and score >= 75:
            stage = "active"
            actions.append("可进入小规模模拟，并保持复验队列监控。")
        elif admission_item and admission_item.status in {"pass", "watch"}:
            stage = "candidate"
            actions.append("生成准入包并观察前向表现。")
        else:
            stage = "validation"
            actions.append("补齐因子准入队列和配置建议。")
        if factor_coverage and factor_coverage.status == "fail":
            blockers.append("覆盖不足")
        if leaderboard_item and leaderboard_item.status == "fail":
            blockers.append("排行榜失败")
        if admission_item and admission_item.blockers:
            blockers.extend(admission_item.blockers[:2])
        if stage in {"active", "candidate"} and not blockers:
            status: Literal["pass", "watch", "fail"] = "pass" if stage == "active" else "watch"
        elif blockers:
            status = "fail"
        else:
            status = "watch"
        items.append(
            FactorLifecycleItem(
                factor_id=factor.id,
                factor_name=factor.name,
                category=factor.category,
                enabled=factor.enabled,
                stage=stage,
                status=status,
                score=max(0, min(100, score)),
                coverage_score=coverage_score,
                leaderboard_score=leaderboard_score,
                admission_score=admission_score,
                latest_run_at=factor_coverage.latest_run_at if factor_coverage else None,
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
    if any(item.stage == "idea" for item in items):
        actions.append("优先把 idea 阶段因子补齐基础研究覆盖。")
    if any(item.stage == "validation" for item in items):
        actions.append("validation 阶段因子需要补齐稳健性、准入或配置验证。")
    if any(item.stage == "active" for item in items):
        actions.append("active 因子保持复验队列和市场状态门禁。")
    if not actions:
        actions.append("因子生命周期健康，继续按研究运行审计维护。")
    return FactorLifecycleReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"因子生命周期：{sum(item.stage == 'active' for item in items)} 个 active，{sum(item.stage == 'candidate' for item in items)} 个 candidate，{sum(item.stage in {'idea', 'research', 'validation'} for item in items)} 个研究/验证中，{sum(item.stage == 'paused' for item in items)} 个暂停。",
        items=sorted(items, key=lambda item: (item.stage == "active", item.score), reverse=True),
        actions=dedupe(actions),
    )


def data_lineage_report() -> DataLineageReport:
    catalog = data_catalog_items()
    factor_coverage = factor_coverage_report()
    coverage_by_factor = {item.factor_id: item for item in factor_coverage.items}
    strategies = list_strategy_templates()
    nodes: list[DataLineageNode] = []
    for item in catalog:
        if item.status == "ready":
            status: Literal["pass", "watch", "fail"] = "pass"
        elif item.status == "degraded":
            status = "watch"
        else:
            status = "fail"
        nodes.append(
            DataLineageNode(
                id=item.id,
                name=item.name,
                type="dataset",
                status=status,
                detail=f"{item.type} · {item.provider} · {item.rows} rows",
            )
        )
    strategy_items: list[StrategyLineageItem] = []
    for strategy in strategies:
        missing: list[str] = []
        actions: list[str] = []
        validation_totals = {"backtest": 0, "forward": 0, "walk_forward": 0, "stress": 0}
        datasets = ["active-watchlist", "factor-library", "strategy-library"]
        if any(factor_id in {"momentum-news", "event-drift"} for factor_id in strategy.factor_ids):
            datasets.append("news-events")
        factor_scores: list[int] = []
        for factor_id in strategy.factor_ids:
            coverage = coverage_by_factor.get(factor_id)
            if coverage is None:
                missing.append(f"{factor_id}: 因子未启用或无覆盖记录")
                actions.append(f"启用或补齐 {factor_id} 的因子覆盖。")
                continue
            factor_scores.append(coverage.score)
            validation_totals["backtest"] += coverage.backtest_symbols
            validation_totals["forward"] += coverage.forward_symbols
            validation_totals["walk_forward"] += coverage.walk_forward_symbols
            validation_totals["stress"] += coverage.stress_symbols
            if coverage.status != "pass":
                missing.append(f"{coverage.factor_name}: 覆盖 {coverage.score}/100")
                actions.extend(coverage.actions[:1])
        if not strategy.factor_ids:
            missing.append("策略未绑定因子")
            actions.append("至少绑定一个启用因子后再纳入验证。")
        if not strategy.enabled:
            missing.append("策略已停用")
            actions.append("启用策略后才能进入研究血缘闭环。")
        average_score = int(round(sum(factor_scores) / len(factor_scores))) if factor_scores else 0
        if not strategy.enabled or not strategy.factor_ids:
            status: Literal["pass", "watch", "fail"] = "fail"
        elif average_score >= 85 and not missing:
            status = "pass"
        elif average_score >= 35:
            status = "watch"
        else:
            status = "fail"
        if not actions:
            actions.append("数据、因子和验证链路完整，保持周期性复验。")
        strategy_items.append(
            StrategyLineageItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                status=status,
                score=average_score,
                factor_ids=strategy.factor_ids,
                datasets=dedupe(datasets),
                validation_coverage=validation_totals,
                missing=dedupe(missing),
                actions=dedupe(actions),
            )
        )
        nodes.append(
            DataLineageNode(
                id=f"strategy:{strategy.id}",
                name=strategy.name,
                type="strategy",
                status=status,
                detail=f"{len(strategy.factor_ids)} factors · {len(strategy.default_filters)} filters · score {average_score}/100",
            )
        )
    for factor in factor_coverage.items:
        nodes.append(
            DataLineageNode(
                id=f"factor:{factor.factor_id}",
                name=factor.factor_name,
                type="factor",
                status=factor.status,
                detail=f"{factor.category} · coverage {factor.score}/100 · symbols {factor.symbol_count}",
            )
        )
    fail_count = sum(item.status == "fail" for item in strategy_items)
    watch_count = sum(item.status == "watch" for item in strategy_items)
    pass_count = sum(item.status == "pass" for item in strategy_items)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    score = int(round(sum(item.score for item in strategy_items) / len(strategy_items))) if strategy_items else 0
    actions: list[str] = []
    if fail_count or watch_count:
        actions.append("优先补齐策略依赖因子的 backtest、forward、walk-forward 和 stress 覆盖。")
    if any("news-events" in item.datasets for item in strategy_items):
        actions.append("新闻事件驱动策略需要保持 RSS/新闻源刷新。")
    if not actions:
        actions.append("数据血缘完整，可进入策略排行榜和准入门禁比较。")
    return DataLineageReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"数据血缘：{pass_count} 个策略完整，{watch_count} 个待补齐，{fail_count} 个断链或不足。",
        nodes=nodes,
        strategies=strategy_items,
        actions=dedupe(actions),
    )


def research_run_audit_report(
    limit: int = 12,
    symbol: str | None = None,
    factor_id: str | None = None,
) -> ResearchRunAuditReport:
    init_db()
    max_limit = max(1, min(50, limit))
    base_symbol = symbol.upper() if symbol else None
    filters: list[str] = []
    params: list[str] = []
    if base_symbol:
        filters.append("symbol = ?")
        params.append(base_symbol)
    if factor_id:
        filters.append("factor_id = ?")
        params.append(factor_id)
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    query = f"""
        SELECT id, 'backtest' AS run_type, symbol, factor_id, factor_name, created_at AS generated_at,
               CASE
                 WHEN total_return > 0 AND max_drawdown <= 15 AND sharpe >= 0.5 AND trades >= 1 THEN 'pass'
                 WHEN total_return > -5 AND max_drawdown <= 24 AND trades >= 1 THEN 'watch'
                 ELSE 'fail'
               END AS status,
               total_return AS primary_metric, max_drawdown AS drawdown,
               '收益 ' || ROUND(total_return, 2) || '%，回撤 ' || ROUND(max_drawdown, 2) || '%，Sharpe ' || ROUND(sharpe, 2) AS summary
        FROM backtest_runs
        {where_clause}
        UNION ALL
        SELECT id, 'forward' AS run_type, symbol, factor_id, factor_name, generated_at, pass_fail AS status,
               forward_return AS primary_metric, max_drawdown AS drawdown,
               '前向 ' || ROUND(forward_return, 2) || '%，衰减 ' || ROUND(degradation, 2) || 'pp' AS summary
        FROM forward_test_runs
        {where_clause}
        UNION ALL
        SELECT id, 'walk_forward' AS run_type, symbol, factor_id, factor_name, generated_at, pass_fail AS status,
               average_forward_return AS primary_metric, max_drawdown AS drawdown,
               '通过率 ' || ROUND(pass_rate, 1) || '%，稳定性 ' || stability_score || '/100' AS summary
        FROM walk_forward_runs
        {where_clause}
        UNION ALL
        SELECT id, 'stress' AS run_type, symbol, factor_id, factor_name, generated_at, pass_fail AS status,
               worst_return AS primary_metric, worst_drawdown AS drawdown,
               '最差 ' || ROUND(worst_return, 2) || '%，压力回撤 ' || ROUND(worst_drawdown, 2) || '%' AS summary
        FROM stress_test_runs
        {where_clause}
        ORDER BY generated_at DESC
        LIMIT ?
    """
    all_params = params + params + params + params + [str(max_limit)]
    with db_connect() as connection:
        rows = connection.execute(query, all_params).fetchall()
    items: list[ResearchRunAuditItem] = []
    for row in rows:
        status = row["status"]
        if status == "pass":
            action = "保留在候选池，按复验周期继续跟踪。"
        elif status == "watch":
            action = "维持观察，补充样本或降低模拟权重。"
        else:
            action = "暂停推广，优先复核参数、数据和市场状态。"
        items.append(
            ResearchRunAuditItem(
                id=row["id"],
                run_type=row["run_type"],
                symbol=row["symbol"],
                factor_id=row["factor_id"],
                factor_name=row["factor_name"],
                generated_at=row["generated_at"],
                status=status,
                primary_metric=round(float(row["primary_metric"]), 2),
                drawdown=round(float(row["drawdown"]), 2),
                summary=row["summary"],
                action=action,
            )
        )
    pass_count = sum(item.status == "pass" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    fail_count = sum(item.status == "fail" for item in items)
    total_runs = len(items)
    score = int(max(0, min(100, pass_count * 100 / max(1, total_runs) + watch_count * 45 / max(1, total_runs) - fail_count * 12)))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if fail_count:
        actions.append("优先处理最近失败的研究运行，再进入策略准入。")
    if watch_count:
        actions.append("观察状态的运行需要补样本或复验。")
    if not items:
        status = "fail"
        actions.append("暂无研究运行记录，先运行回测和前向测试。")
    if not actions:
        actions.append("最近研究运行状态良好，继续按复验队列维护。")
    return ResearchRunAuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"研究运行审计：最近 {total_runs} 条，pass {pass_count}，watch {watch_count}，fail {fail_count}。",
        total_runs=total_runs,
        pass_count=pass_count,
        watch_count=watch_count,
        fail_count=fail_count,
        items=items,
        actions=dedupe(actions),
    )


def backfill_factor_coverage(payload: FactorCoverageBackfillRequest) -> FactorCoverageBackfillResult:
    init_db()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    target_symbol = payload.symbol.upper() if payload.symbol else None
    watchlist_items = list_watchlist_snapshots()
    targets = [item for item in watchlist_items if target_symbol is None or item.symbol == target_symbol]
    if target_symbol and not targets:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    max_tasks = max(1, min(16, payload.max_tasks))
    executed: list[str] = []
    skipped: list[str] = []

    for snapshot in targets:
        if len(executed) >= max_tasks:
            skipped.append(f"{snapshot.symbol}：达到任务上限")
            continue
        latest_backtest = latest_backtest_for_factor(snapshot.symbol, factor.id)
        latest_forward = latest_forward_for_factor(snapshot.symbol, factor.id)
        latest_walk = latest_walk_forward_for_factor(snapshot.symbol, factor.id)
        latest_stress = latest_stress_for_factor(snapshot.symbol, factor.id)
        if payload.missing_only:
            task_plan: list[str] = []
            if latest_backtest is None:
                task_plan.append("backtest")
            if latest_forward is None:
                task_plan.append("forward")
            if latest_walk is None:
                task_plan.append("walk-forward")
            if latest_stress is None:
                task_plan.append("stress")
        else:
            task_plan = ["backtest", "forward", "walk-forward", "stress"]
        if not task_plan:
            skipped.append(f"{snapshot.symbol}：{factor.name} 覆盖已满足 missing_only 条件")
            continue
        for task in task_plan:
            if len(executed) >= max_tasks:
                skipped.append(f"{snapshot.symbol}：剩余任务达到上限")
                break
            if task == "backtest":
                run_backtest(BacktestRunRequest(symbol=snapshot.symbol, factor_id=factor.id, interval="1h", limit=180))
            elif task == "forward":
                run_forward_test(ForwardTestRequest(symbol=snapshot.symbol, factor_id=factor.id, interval="1h"))
            elif task == "walk-forward":
                run_walk_forward_test(WalkForwardRequest(symbol=snapshot.symbol, factor_id=factor.id, interval="1h"))
            else:
                run_stress_test(StressTestRequest(symbol=snapshot.symbol, factor_id=factor.id, interval="1h"))
            executed.append(f"{snapshot.symbol}:{factor.id}:{task}")

    generated_at = datetime.now(timezone.utc).isoformat()
    result_id = sha256(f"factor-coverage-backfill:{factor.id}:{generated_at}:{','.join(executed)}".encode("utf-8")).hexdigest()[:20]
    updated_coverage = factor_coverage_report()
    add_event_log(
        event_type="research_backfill",
        severity="info" if executed else "watch",
        title="因子覆盖补齐完成",
        message=f"{factor.name} 执行 {len(executed)} 个任务，跳过 {len(skipped)} 项。",
        created_at=generated_at,
    )
    return FactorCoverageBackfillResult(
        id=result_id,
        generated_at=generated_at,
        factor_id=factor.id,
        factor_name=factor.name,
        requested_symbols=[item.symbol for item in targets],
        executed_tasks=executed,
        skipped=skipped,
        coverage=updated_coverage,
    )


def backfill_research_coverage(payload: ResearchCoverageBackfillRequest) -> ResearchCoverageBackfillResult:
    init_db()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    coverage = research_coverage_report()
    target_symbol = payload.symbol.upper() if payload.symbol else None
    targets = [item for item in coverage.items if target_symbol is None or item.symbol == target_symbol]
    if target_symbol and not targets:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    max_tasks = max(1, min(12, payload.max_tasks))
    executed: list[str] = []
    skipped: list[str] = []

    for item in targets:
        if len(executed) >= max_tasks:
            skipped.append(f"{item.symbol}：达到任务上限")
            continue
        task_plan: list[str] = []
        if payload.missing_only:
            if item.backtest_count == 0:
                task_plan.append("backtest")
            if item.forward_count == 0:
                task_plan.append("forward")
            if item.walk_forward_count == 0:
                task_plan.append("walk-forward")
            if item.stress_count == 0:
                task_plan.append("stress")
        else:
            task_plan = ["backtest", "forward", "walk-forward", "stress"]
        if not task_plan:
            skipped.append(f"{item.symbol}：研究覆盖已满足 missing_only 条件")
            continue
        for task in task_plan:
            if len(executed) >= max_tasks:
                skipped.append(f"{item.symbol}：剩余任务达到上限")
                break
            if task == "backtest":
                run_backtest(BacktestRunRequest(symbol=item.symbol, factor_id=payload.factor_id, interval="1h", limit=180))
            elif task == "forward":
                run_forward_test(ForwardTestRequest(symbol=item.symbol, factor_id=payload.factor_id, interval="1h"))
            elif task == "walk-forward":
                run_walk_forward_test(WalkForwardRequest(symbol=item.symbol, factor_id=payload.factor_id, interval="1h"))
            else:
                run_stress_test(StressTestRequest(symbol=item.symbol, factor_id=payload.factor_id, interval="1h"))
            executed.append(f"{item.symbol}:{task}")

    generated_at = datetime.now(timezone.utc).isoformat()
    result_id = sha256(f"coverage-backfill:{payload.factor_id}:{generated_at}:{','.join(executed)}".encode("utf-8")).hexdigest()[:20]
    updated_coverage = research_coverage_report()
    add_event_log(
        event_type="research_backfill",
        severity="info" if executed else "watch",
        title="研究覆盖补齐完成",
        message=f"执行 {len(executed)} 个任务，跳过 {len(skipped)} 项。",
        created_at=generated_at,
    )
    return ResearchCoverageBackfillResult(
        id=result_id,
        generated_at=generated_at,
        factor_id=payload.factor_id,
        factor_name=factor.name,
        requested_symbols=[item.symbol for item in targets],
        executed_tasks=executed,
        skipped=skipped,
        coverage=updated_coverage,
    )


def research_action_plan() -> ResearchActionPlan:
    quality = data_quality_report()
    readiness = research_readiness_report()
    coverage = research_coverage_report()
    gates = strategy_promotion_gates()
    items: list[ResearchActionItem] = []

    def add_item(source: str, severity: Literal["pass", "watch", "fail"], title: str, detail: str, action: str, base_priority: int) -> None:
        items.append(
            ResearchActionItem(
                priority=base_priority + (30 if severity == "fail" else 10 if severity == "watch" else 0),
                source=source,
                severity=severity,
                title=title,
                detail=detail,
                action=action,
            )
        )

    for blocker in quality.blockers:
        add_item("数据质量", "fail", "修复数据质量阻断", quality.summary, blocker, 70)
    for action in quality.actions[:3]:
        add_item("数据质量", quality.status, "改善数据质量", quality.summary, action, 45)

    for blocker in readiness.blockers:
        add_item("研究就绪", "fail", "修复研究就绪阻断", readiness.summary, blocker, 68)
    for action in readiness.actions[:3]:
        add_item("研究就绪", readiness.status, "提升研究就绪度", readiness.summary, action, 42)

    for coverage_item in coverage.items:
        if coverage_item.status != "pass":
            for action in coverage_item.actions[:2]:
                add_item("研究覆盖", coverage_item.status, f"补齐 {coverage_item.symbol} 研究覆盖", coverage_item.notes[0], action, 55)

    for gate in gates:
        if gate.status != "pass":
            for blocker in gate.blockers[:2]:
                add_item("策略准入", "fail", f"{gate.strategy_name} 准入阻断", gate.recommendation, blocker, 65)
            for action in gate.actions[:2]:
                add_item("策略准入", gate.status, f"{gate.strategy_name} 准入动作", gate.recommendation, action, 40)

    if not items:
        add_item("研究流程", "pass", "保持当前研究节奏", "当前没有阻断项。", "定期复跑一键验证和市场状态检查。", 20)

    ranked = sorted(items, key=lambda item: item.priority, reverse=True)
    fail_count = sum(item.severity == "fail" for item in ranked)
    watch_count = sum(item.severity == "watch" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    return ResearchActionPlan(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"行动计划：{fail_count} 个阻断，{watch_count} 个观察动作，优先处理前 {min(5, len(ranked))} 项。",
        items=ranked[:20],
    )


def research_dashboard_summary() -> ResearchDashboardSummary:
    quality = data_quality_report()
    readiness = research_readiness_report()
    coverage = research_coverage_report()
    action_plan = research_action_plan()
    gates = strategy_promotion_gates()
    regime = market_regime_report()
    complete_coverage = sum(item.status == "pass" for item in coverage.items)
    pass_gates = sum(item.status == "pass" for item in gates)
    fail_count = sum(item.status == "fail" for item in [quality, readiness, coverage, action_plan])
    watch_count = sum(item.status == "watch" for item in [quality, readiness, coverage, action_plan])
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    coverage_score = int(sum(item.score for item in coverage.items) / len(coverage.items)) if coverage.items else 0
    score = int((quality.score + readiness.score + coverage_score + (pass_gates / max(1, len(gates)) * 100)) / 4)
    metrics = [
        ResearchDashboardMetric(name="数据质量", status=quality.status, value=f"{quality.score}/100", detail=quality.summary),
        ResearchDashboardMetric(name="研究就绪", status=readiness.status, value=f"{readiness.score}/100", detail=readiness.summary),
        ResearchDashboardMetric(name="覆盖完整", status=coverage.status, value=f"{complete_coverage}/{len(coverage.items)}", detail=coverage.summary),
        ResearchDashboardMetric(name="策略准入", status="pass" if pass_gates else "watch" if gates else "fail", value=f"{pass_gates}/{len(gates)}", detail="通过准入闸门的策略数量。"),
        ResearchDashboardMetric(name="市场状态", status="fail" if regime.risk_level == "risk" else "watch" if regime.risk_level == "watch" else "pass", value=regime.label, detail=regime.summary),
    ]
    return ResearchDashboardSummary(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"研究工作台 {score}/100：数据、覆盖、策略准入和市场状态综合为 {status}。",
        metrics=metrics,
    )


def research_health_report() -> ResearchHealthReport:
    quality = data_quality_report()
    coverage = research_coverage_report()
    gates = strategy_promotion_gates()
    regime = market_regime_report()
    readiness = research_readiness_report()
    lifecycle = factor_lifecycle_report()
    run_audit = research_run_audit_report(limit=12)
    exposure = regime_exposure_report(interval="1h")
    filter_pack = filter_admission_report(FilterValidationRequest(min_rsi=30, min_trend=40))
    coverage_score = int(sum(item.score for item in coverage.items) / max(1, len(coverage.items)))
    gate_score = int(sum(gate.score for gate in gates) / max(1, len(gates))) if gates else 0
    regime_score = 100 if regime.risk_level == "constructive" else 78 if regime.risk_level == "neutral" else 55 if regime.risk_level == "watch" else 20
    dashboard_score = int(round((quality.score + coverage_score + gate_score + regime_score) / 4))
    dashboard_fail_count = sum(item.status == "fail" for item in [quality, coverage]) + sum(gate.status == "fail" for gate in gates)
    dashboard_watch_count = sum(item.status == "watch" for item in [quality, coverage]) + sum(gate.status == "watch" for gate in gates)
    if dashboard_fail_count:
        dashboard_status: Literal["pass", "watch", "fail"] = "fail"
    elif dashboard_watch_count or regime.risk_level == "watch":
        dashboard_status = "watch"
    else:
        dashboard_status = "pass"
    dashboard_summary = f"研究工作台 {dashboard_score}/100：数据、覆盖、策略准入和市场状态综合为 {dashboard_status}。"
    checks = [
        ResearchHealthCheck(name="研究工作台", status=dashboard_status, score=dashboard_score, detail=dashboard_summary),
        ResearchHealthCheck(name="研究就绪度", status=readiness.status, score=readiness.score, detail=readiness.summary),
        ResearchHealthCheck(name="因子生命周期", status=lifecycle.status, score=int(sum(item.score for item in lifecycle.items) / max(1, len(lifecycle.items))), detail=lifecycle.summary),
        ResearchHealthCheck(name="运行审计", status=run_audit.status, score=run_audit.score, detail=run_audit.summary),
        ResearchHealthCheck(name="市场暴露", status=exposure.status, score=exposure.score, detail=exposure.summary),
        ResearchHealthCheck(name="过滤器准入", status=filter_pack.status, score=filter_pack.score, detail=filter_pack.summary),
    ]
    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = int(round(sum(item.score for item in checks) / max(1, len(checks))))
    blockers: list[str] = []
    actions: list[str] = []
    if readiness.blockers:
        blockers.extend(readiness.blockers[:3])
    if lifecycle.status == "fail":
        blockers.extend(item.blockers[0] for item in lifecycle.items if item.blockers)
    if run_audit.fail_count:
        blockers.append(f"最近研究运行存在 {run_audit.fail_count} 条失败。")
    if exposure.status == "fail":
        blockers.append("市场状态暴露报告为 fail。")
    if filter_pack.decision == "reject":
        blockers.append("默认过滤器准入被拒绝。")
    actions.extend(readiness.actions[:2])
    actions.extend(lifecycle.actions[:2])
    actions.extend(run_audit.actions[:2])
    actions.extend(exposure.actions[:2])
    actions.extend(filter_pack.actions[:2])
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
        decision: Literal["go", "caution", "stop"] = "stop"
    elif watch_count or score < 78:
        status = "watch"
        decision = "caution"
    else:
        status = "pass"
        decision = "go"
    if not actions:
        actions.append("研究健康良好，可按策略准入和市场状态门禁执行。")
    return ResearchHealthReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        decision=decision,
        score=score,
        summary=f"研究健康总览 {decision}：得分 {score}/100，fail {fail_count}，watch {watch_count}。",
        checks=checks,
        blockers=dedupe(blockers)[:6],
        actions=dedupe(actions)[:8],
    )


def market_regime_report() -> MarketRegime:
    breadth = get_market_breadth()
    snapshots = list_watchlist_snapshots()
    volatility_values: list[float] = []
    for snapshot in snapshots[: min(6, len(snapshots))]:
        history = candles(snapshot.symbol, interval="1h", limit=50)
        latest = history[-1]
        recent = history[-20:]
        volatility_values.append((max(item.high for item in recent) - min(item.low for item in recent)) / latest.close * 100)
    volatility_score = sum(volatility_values) / len(volatility_values) if volatility_values else 0.0
    breadth_score = (breadth.advancers - breadth.decliners) / max(1, breadth.total) * 100
    trend_score = breadth.average_trend_score
    liquidity_score = min(100.0, sum(parse_volume_value(item.volume) for item in snapshots) / max(1, len(snapshots)) / 15)
    if volatility_score >= 7.5 and breadth.average_change < 0:
        label: Literal["risk_on", "risk_off", "trend", "range", "volatile"] = "volatile"
    elif breadth.risk_level == "risk":
        label = "risk_off"
    elif trend_score >= 65 and breadth_score > 10:
        label = "trend"
    elif breadth.risk_level == "constructive":
        label = "risk_on"
    else:
        label = "range"
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    if label == "risk_off" or (label == "volatile" and breadth.average_change < -0.5):
        risk_level = "risk"
    elif label == "volatile" or breadth.risk_level == "watch":
        risk_level = "watch"
    elif label in {"risk_on", "trend"}:
        risk_level = "constructive"
    else:
        risk_level = "neutral"
    actions = {
        "risk_on": ["允许趋势策略正常运行", "优先观察强趋势和新闻确认因子", "保留预警但不扩大阈值"],
        "trend": ["趋势策略优先", "回测需检查暴露和追高风险", "突破类策略需要成交量确认"],
        "range": ["降低追涨策略权重", "优先验证均值回归和事件驱动", "筛选器避免过窄导致样本不足"],
        "volatile": ["缩小仓位和前向测试窗口", "提高预警频率", "回测通过也需要人工确认"],
        "risk_off": ["暂停新增高 beta 策略", "只保留防守型观察和风险预警", "检查新闻风险和持仓集中度"],
    }[label]
    return MarketRegime(
        generated_at=datetime.now(timezone.utc).isoformat(),
        label=label,
        risk_level=risk_level,
        breadth_score=round(breadth_score, 1),
        volatility_score=round(volatility_score, 2),
        trend_score=round(trend_score, 1),
        liquidity_score=round(liquidity_score, 1),
        summary=f"{label}：广度 {breadth_score:+.1f}，均趋势 {trend_score:.1f}，估算波动 {volatility_score:.2f}%。",
        evidence=[
            f"上涨/下跌 {breadth.advancers}/{breadth.decliners}",
            f"平均涨跌 {breadth.average_change:+.2f}%",
            f"平均 RSI {breadth.average_rsi:.1f}",
            f"强买/买入 {breadth.strong_buy_count + breadth.buy_count} 个",
        ],
        actions=actions,
    )


def lightweight_market_regime_report() -> MarketRegime:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM market_regime_snapshots ORDER BY generated_at DESC LIMIT 1").fetchone()
        validation_row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN pass_fail = 'fail' THEN 1 ELSE 0 END), 0) AS fail_count,
                COALESCE(SUM(CASE WHEN pass_fail = 'watch' THEN 1 ELSE 0 END), 0) AS watch_count
            FROM strategy_validation_runs
            """
        ).fetchone()
    if row:
        latest = row_to_market_regime(row)
        parsed = parse_iso_datetime_or_none(latest.generated_at)
        age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600 if parsed else None
        if age_hours is not None and age_hours <= 24:
            return latest

    total = int(validation_row["total"]) if validation_row else 0
    fail_count = int(validation_row["fail_count"]) if validation_row else 0
    watch_count = int(validation_row["watch_count"]) if validation_row else 0
    if fail_count:
        label: Literal["risk_on", "risk_off", "trend", "range", "volatile"] = "risk_off"
        risk_level: Literal["constructive", "neutral", "watch", "risk"] = "risk"
    elif watch_count:
        label = "range"
        risk_level = "watch"
    elif total:
        label = "risk_on"
        risk_level = "constructive"
    else:
        label = "range"
        risk_level = "neutral"
    return MarketRegime(
        generated_at=datetime.now(timezone.utc).isoformat(),
        label=label,
        risk_level=risk_level,
        breadth_score=0.0,
        volatility_score=0.0,
        trend_score=50.0,
        liquidity_score=50.0,
        summary=f"{label}：使用本地策略验证快照估算，未同步触发实时行情请求。",
        evidence=[
            f"策略验证记录 {total} 条",
            f"失败 {fail_count} 条，观察 {watch_count} 条",
            f"活跃行情源 {get_active_market_provider_name()}",
        ],
        actions=["需要实时市场状态时打开市场状态模块刷新快照。"],
    )


def row_to_market_regime(row: sqlite3.Row) -> MarketRegime:
    return MarketRegime(
        generated_at=row["generated_at"],
        label=row["label"],
        risk_level=row["risk_level"],
        breadth_score=row["breadth_score"],
        volatility_score=row["volatility_score"],
        trend_score=row["trend_score"],
        liquidity_score=row["liquidity_score"],
        summary=row["summary"],
        evidence=json.loads(row["evidence"]),
        actions=json.loads(row["actions"]),
    )


def record_market_regime_snapshot(regime: MarketRegime) -> MarketRegime:
    init_db()
    snapshot_id = sha256(f"regime:{regime.generated_at}:{regime.label}:{regime.risk_level}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO market_regime_snapshots (
                id, generated_at, label, risk_level, breadth_score, volatility_score,
                trend_score, liquidity_score, summary, evidence, actions
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                regime.generated_at,
                regime.label,
                regime.risk_level,
                regime.breadth_score,
                regime.volatility_score,
                regime.trend_score,
                regime.liquidity_score,
                regime.summary,
                json.dumps(regime.evidence, ensure_ascii=False),
                json.dumps(regime.actions, ensure_ascii=False),
            ),
        )
    return regime


def list_market_regime_history(limit: int = 12) -> list[MarketRegime]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM market_regime_snapshots
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (max(1, min(100, limit)),),
        ).fetchall()
    return [row_to_market_regime(row) for row in rows]


def regime_risk_rank(level: Literal["constructive", "neutral", "watch", "risk"]) -> int:
    return {"constructive": 0, "neutral": 1, "watch": 2, "risk": 3}[level]


def market_regime_transition_report() -> MarketRegimeTransition:
    current = record_market_regime_snapshot(market_regime_report())
    history = list_market_regime_history(limit=8)
    previous = next((item for item in history if item.generated_at != current.generated_at), None)
    if previous is None:
        return MarketRegimeTransition(
            generated_at=datetime.now(timezone.utc).isoformat(),
            status="watch",
            transition_score=35,
            current=current,
            previous=None,
            label_changed=False,
            risk_level_changed=False,
            breadth_delta=0.0,
            volatility_delta=0.0,
            trend_delta=0.0,
            liquidity_delta=0.0,
            summary=f"市场状态迁移监控：当前 {current.label} / {current.risk_level}，历史样本不足。",
            evidence=["尚无可比较的前一条市场状态快照。"],
            actions=["保留当前状态快照，等待下一次刷新后评估迁移。"],
        )

    breadth_delta = round(current.breadth_score - previous.breadth_score, 1)
    volatility_delta = round(current.volatility_score - previous.volatility_score, 2)
    trend_delta = round(current.trend_score - previous.trend_score, 1)
    liquidity_delta = round(current.liquidity_score - previous.liquidity_score, 1)
    label_changed = current.label != previous.label
    risk_level_changed = current.risk_level != previous.risk_level
    risk_delta = regime_risk_rank(current.risk_level) - regime_risk_rank(previous.risk_level)
    score = 0
    evidence: list[str] = []
    actions: list[str] = []
    if label_changed:
        score += 24
        evidence.append(f"状态标签从 {previous.label} 切换为 {current.label}。")
    if risk_delta > 0:
        score += 26 * risk_delta
        evidence.append(f"风险等级从 {previous.risk_level} 上升到 {current.risk_level}。")
    elif risk_delta < 0:
        evidence.append(f"风险等级从 {previous.risk_level} 回落到 {current.risk_level}。")
    if breadth_delta <= -25:
        score += 18
        actions.append("市场广度快速走弱，降低趋势追涨权重。")
    elif breadth_delta <= -12:
        score += 9
    if volatility_delta >= 2.5:
        score += 18
        actions.append("波动明显抬升，缩小前向测试窗口并提高预警频率。")
    elif volatility_delta >= 1.0:
        score += 8
    if trend_delta <= -18:
        score += 16
        actions.append("趋势分下滑较快，暂停扩大趋势策略模拟权重。")
    elif trend_delta <= -8:
        score += 7
    if liquidity_delta <= -20:
        score += 10
        actions.append("流动性评分下降，控制滑点和成交假设。")
    score = max(0, min(100, score))
    evidence.extend(
        [
            f"广度变化 {breadth_delta:+.1f}。",
            f"波动变化 {volatility_delta:+.2f}%。",
            f"趋势变化 {trend_delta:+.1f}。",
            f"流动性变化 {liquidity_delta:+.1f}。",
        ]
    )
    if score >= 55 or current.risk_level == "risk":
        status: Literal["pass", "watch", "fail"] = "fail"
        actions.append("迁移风险偏高：新策略准入改为人工复核，复验队列优先处理。")
    elif score >= 24 or current.risk_level == "watch":
        status = "watch"
        actions.append("迁移风险观察：新开仓降权，等待下一次状态确认。")
    else:
        status = "pass"
        actions.append("迁移风险可控：按现有交易门禁执行。")
    summary = f"市场状态迁移 {status}：{previous.label}/{previous.risk_level} -> {current.label}/{current.risk_level}，迁移分 {score}/100。"
    return MarketRegimeTransition(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        transition_score=score,
        current=current,
        previous=previous,
        label_changed=label_changed,
        risk_level_changed=risk_level_changed,
        breadth_delta=breadth_delta,
        volatility_delta=volatility_delta,
        trend_delta=trend_delta,
        liquidity_delta=liquidity_delta,
        summary=summary,
        evidence=dedupe(evidence),
        actions=dedupe(actions),
    )


def validate_filters(payload: FilterValidationRequest) -> FilterValidationResult:
    warnings: list[str] = []
    suggestions: list[str] = []
    normalized: dict[str, str | float] = {}
    if payload.min_change is not None:
        normalized["min_change"] = payload.min_change
    if payload.max_change is not None:
        normalized["max_change"] = payload.max_change
    if payload.min_rsi is not None:
        normalized["min_rsi"] = max(0, min(100, payload.min_rsi))
    if payload.max_rsi is not None:
        normalized["max_rsi"] = max(0, min(100, payload.max_rsi))
    if payload.min_trend is not None:
        normalized["min_trend"] = float(max(0, min(100, payload.min_trend)))
    if payload.signal:
        normalized["signal"] = payload.signal
    if payload.market:
        normalized["market"] = payload.market.strip().lower()
    if payload.min_change is not None and payload.max_change is not None and payload.min_change > payload.max_change:
        warnings.append("涨跌幅下限高于上限。")
    if payload.min_rsi is not None and payload.max_rsi is not None and payload.min_rsi > payload.max_rsi:
        warnings.append("RSI 下限高于上限。")
    result = run_screener(
        min_change=payload.min_change,
        max_change=payload.max_change,
        min_rsi=payload.min_rsi,
        max_rsi=payload.max_rsi,
        min_trend=payload.min_trend,
        signal=payload.signal,
        market=payload.market,
        limit=100,
    )
    regime = market_regime_report()
    if result.total == 0:
        warnings.append("当前过滤器没有命中标的。")
        suggestions.append("放宽 RSI、趋势分或信号条件，先保证样本量。")
    elif result.total < 2:
        warnings.append("过滤器命中样本偏少，回测和前向测试稳定性较弱。")
        suggestions.append("至少保留 2-3 个候选标的再做策略验证。")
    if regime.risk_level == "risk" and payload.signal in {"strong_buy", "buy"}:
        warnings.append("当前市场状态偏风险，强趋势过滤器需要降低仓位或提高确认条件。")
        suggestions.append("增加新闻情绪、成交量或前向测试通过条件。")
    if not warnings:
        suggestions.append("过滤器结构可用，可进入回测和前向测试。")
    return FilterValidationResult(
        valid=not any("高于上限" in item or "没有命中" in item for item in warnings),
        normalized_filters=normalized,
        match_count=result.total,
        warnings=warnings,
        suggestions=suggestions,
        regime=regime,
    )


def clamp_optional_number(value: float | None, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    return max(minimum, min(maximum, value))


def clone_filter_request(payload: FilterValidationRequest, **updates: float | str | None) -> FilterValidationRequest:
    data = payload.model_dump()
    data.update(updates)
    return FilterValidationRequest(**data)


def filter_contribution_report(payload: FilterValidationRequest) -> FilterContributionReport:
    baseline = validate_filters(payload)
    universe_count = len(list_watchlist_snapshots())
    baseline_count = baseline.match_count
    active_filters = baseline.normalized_filters
    filter_labels = {
        "min_change": "最小涨跌",
        "max_change": "最大涨跌",
        "min_rsi": "最小 RSI",
        "max_rsi": "最大 RSI",
        "min_trend": "最小趋势",
        "signal": "信号",
        "market": "市场",
    }
    items: list[FilterContributionItem] = []
    for filter_name, value in active_filters.items():
        relaxed = payload.model_copy(update={filter_name: None})
        relaxed_count = run_screener(
            min_change=relaxed.min_change,
            max_change=relaxed.max_change,
            min_rsi=relaxed.min_rsi,
            max_rsi=relaxed.max_rsi,
            min_trend=relaxed.min_trend,
            signal=relaxed.signal,
            market=relaxed.market,
            limit=100,
        ).total
        removed = max(0, relaxed_count - baseline_count)
        if baseline_count == 0 and removed > 0:
            severity: Literal["pass", "watch", "fail"] = "fail"
            insight = f"该条件移除后可恢复 {removed} 个样本，是当前无命中的主要约束。"
            action = f"优先放宽或移除 {filter_labels.get(filter_name, filter_name)}。"
        elif universe_count and removed / universe_count >= 0.5:
            severity = "watch"
            insight = f"该条件过滤掉 {removed} 个潜在样本，约占观察池 {removed / universe_count * 100:.1f}%。"
            action = f"若回测样本不足，先下调 {filter_labels.get(filter_name, filter_name)} 强度。"
        else:
            severity = "pass"
            insight = f"该条件贡献可控，移除后新增 {removed} 个样本。"
            action = "保留该条件，并继续观察样本稳定性。"
        items.append(
            FilterContributionItem(
                filter_name=filter_name,
                label=filter_labels.get(filter_name, filter_name),
                current_value=value,
                baseline_match_count=baseline_count,
                relaxed_match_count=relaxed_count,
                removed_by_filter=removed,
                severity=severity,
                insight=insight,
                action=action,
            )
        )
    items.sort(key=lambda item: item.removed_by_filter, reverse=True)
    actions = list(baseline.suggestions)
    if not items:
        actions.append("当前未设置过滤器，先选择 RSI、趋势或信号条件再诊断贡献。")
    elif baseline_count == 0:
        actions.append("先放宽贡献最高的条件，保证至少 2-3 个候选标的。")
    elif baseline_count < 2:
        actions.append("样本偏少，优先检查贡献最高的过滤器。")
    else:
        actions.append("过滤器样本量可用，可进入回测、前向和压力验证。")
    watch_count = sum(item.severity == "watch" for item in items)
    fail_count = sum(item.severity == "fail" for item in items)
    concentration_score = 100
    if universe_count:
        concentration_score = max(0, min(100, int(100 - (max((item.removed_by_filter for item in items), default=0) / universe_count * 100))))
    if fail_count or baseline_count == 0:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count or baseline_count < 2:
        status = "watch"
    else:
        status = "pass"
    return FilterContributionReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        baseline_match_count=baseline_count,
        total_universe=universe_count,
        concentration_score=concentration_score,
        summary=f"过滤器贡献诊断 {status}：当前命中 {baseline_count}/{universe_count}，最高单项约束移除 {max((item.removed_by_filter for item in items), default=0)} 个样本。",
        filters=active_filters,
        items=items,
        actions=dedupe(actions),
    )


def filter_stress_test_report(payload: FilterValidationRequest) -> FilterStressTestReport:
    baseline = validate_filters(payload)
    base_min_rsi = payload.min_rsi
    base_min_trend = payload.min_trend
    scenarios_config: list[tuple[str, FilterValidationRequest]] = [
        ("基准", payload),
        (
            "放宽",
            clone_filter_request(
                payload,
                min_rsi=clamp_optional_number((base_min_rsi if base_min_rsi is not None else 30) - 10, 0, 100),
                min_trend=clamp_optional_number((base_min_trend if base_min_trend is not None else 40) - 10, 0, 100),
            ),
        ),
        (
            "收紧",
            clone_filter_request(
                payload,
                min_rsi=clamp_optional_number((base_min_rsi if base_min_rsi is not None else 40) + 10, 0, 100),
                min_trend=clamp_optional_number((base_min_trend if base_min_trend is not None else 50) + 10, 0, 100),
            ),
        ),
        (
            "RSI 更严",
            clone_filter_request(
                payload,
                min_rsi=clamp_optional_number((base_min_rsi if base_min_rsi is not None else 45) + 15, 0, 100),
            ),
        ),
        (
            "趋势更严",
            clone_filter_request(
                payload,
                min_trend=clamp_optional_number((base_min_trend if base_min_trend is not None else 55) + 15, 0, 100),
            ),
        ),
    ]
    scenarios: list[FilterStressScenario] = []
    for name, filters in scenarios_config:
        validation = validate_filters(filters)
        if not validation.valid or validation.match_count == 0:
            status: Literal["pass", "watch", "fail"] = "fail"
        elif validation.match_count < 2 or validation.warnings:
            status = "watch"
        else:
            status = "pass"
        scenarios.append(
            FilterStressScenario(
                name=name,
                status=status,
                match_count=validation.match_count,
                filters=filters,
                warnings=validation.warnings,
                suggestions=validation.suggestions,
            )
        )
    counts = [item.match_count for item in scenarios]
    min_count = min(counts) if counts else 0
    max_count = max(counts) if counts else 0
    spread = max_count - min_count
    fail_count = sum(item.status == "fail" for item in scenarios)
    watch_count = sum(item.status == "watch" for item in scenarios)
    score = max(0, 100 - fail_count * 24 - watch_count * 9 - min(35, spread * 4))
    actions: list[str] = []
    if baseline.match_count < 2:
        actions.append("基准过滤器命中过窄，先放宽 RSI 或趋势阈值。")
    if fail_count:
        actions.append("存在压力场景无效，保存模板前先调整阈值。")
    if spread >= 5:
        actions.append("不同场景命中波动较大，避免把该过滤器作为唯一入场条件。")
    if not actions:
        actions.append("过滤器压力测试稳定，可进入策略候选池和前向验证。")
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count or score < 75:
        status = "watch"
    else:
        status = "pass"
    return FilterStressTestReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        stability_score=score,
        baseline_match_count=baseline.match_count,
        min_match_count=min_count,
        max_match_count=max_count,
        summary=f"过滤器压力测试 {status}：稳定分 {score}/100，基准命中 {baseline.match_count}，范围 {min_count}-{max_count}。",
        scenarios=scenarios,
        actions=dedupe(actions),
    )


def filter_admission_report(payload: FilterValidationRequest) -> FilterAdmissionReport:
    validation = validate_filters(payload)
    contribution = filter_contribution_report(payload)
    stress = filter_stress_test_report(payload)
    checks: list[FilterAdmissionCheck] = []
    actions: list[str] = []

    validation_status: Literal["pass", "watch", "fail"]
    if not validation.valid:
        validation_status = "fail"
    elif validation.match_count < 2 or validation.warnings:
        validation_status = "watch"
    else:
        validation_status = "pass"
    checks.append(
        FilterAdmissionCheck(
            name="基础验证",
            status=validation_status,
            message=f"命中 {validation.match_count} 个标的，警告 {len(validation.warnings)} 条。",
        )
    )
    checks.append(
        FilterAdmissionCheck(
            name="贡献集中度",
            status=contribution.status,
            message=f"集中度 {contribution.concentration_score}/100，最高约束新增 {max((item.removed_by_filter for item in contribution.items), default=0)} 个样本。",
        )
    )
    checks.append(
        FilterAdmissionCheck(
            name="压力稳定性",
            status=stress.status,
            message=f"稳定分 {stress.stability_score}/100，命中范围 {stress.min_match_count}-{stress.max_match_count}。",
        )
    )
    regime_status: Literal["pass", "watch", "fail"] = "pass"
    if validation.regime.risk_level == "risk":
        regime_status = "fail"
    elif validation.regime.risk_level == "watch":
        regime_status = "watch"
    checks.append(
        FilterAdmissionCheck(
            name="市场状态",
            status=regime_status,
            message=f"{validation.regime.label} / {validation.regime.risk_level}。",
        )
    )
    fail_count = sum(item.status == "fail" for item in checks)
    watch_count = sum(item.status == "watch" for item in checks)
    score = max(
        0,
        min(
            100,
            100
            - fail_count * 30
            - watch_count * 10
            - max(0, 2 - validation.match_count) * 12
            - max(0, 70 - stress.stability_score) * 0.4
            - max(0, 60 - contribution.concentration_score) * 0.25,
        ),
    )
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
        decision: Literal["accept", "revise", "reject"] = "reject"
    elif watch_count or score < 75:
        status = "watch"
        decision = "revise"
    else:
        status = "pass"
        decision = "accept"
    if decision == "reject":
        actions.append("过滤器暂不进入策略库，先处理 fail 检查项。")
    elif decision == "revise":
        actions.append("保存前建议放宽过紧条件或补充确认条件。")
    else:
        actions.append("过滤器可保存为策略模板，并进入回测/前向测试。")
    actions.extend(validation.suggestions[:2])
    actions.extend(contribution.actions[:1])
    actions.extend(stress.actions[:1])
    return FilterAdmissionReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        decision=decision,
        score=int(round(score)),
        summary=f"过滤器准入 {decision}：得分 {score:.0f}/100，fail {fail_count}，watch {watch_count}。",
        filters=validation.normalized_filters,
        validation=validation,
        contribution=contribution,
        stress_test=stress,
        checks=checks,
        actions=dedupe(actions),
    )


def regime_filter_templates() -> list[RegimeFilterTemplate]:
    regime = market_regime_report()
    base_templates: list[tuple[str, str, FilterValidationRequest, list[str]]] = [
        (
            "trend-confirmation",
            "趋势确认",
            FilterValidationRequest(min_change=0, min_rsi=50, min_trend=62, signal="buy"),
            ["适合趋势或风险偏好较强环境。", "要求价格和技术状态同步确认。"],
        ),
        (
            "event-observation",
            "事件观察",
            FilterValidationRequest(min_change=-1, min_rsi=40, min_trend=45),
            ["保留更宽候选池，适合新闻驱动和事件漂移观察。", "用于后续叠加新闻影响分和 AI 摘要。"],
        ),
        (
            "defensive-watch",
            "防守观察",
            FilterValidationRequest(min_change=-2, min_rsi=35, min_trend=35, signal="neutral"),
            ["适合风险偏高或波动放大环境。", "避免强追涨，优先观察修复和止跌。"],
        ),
    ]
    if regime.label in {"risk_on", "trend"}:
        ordered = [base_templates[0], base_templates[1], base_templates[2]]
    elif regime.label == "risk_off":
        ordered = [base_templates[2], base_templates[1], base_templates[0]]
    elif regime.label == "volatile":
        ordered = [
            (
                "volatility-repair",
                "波动修复",
                FilterValidationRequest(min_change=-1.5, min_rsi=38, min_trend=42),
                ["适合波动放大后等待修复。", "不强制买入信号，先扩大观察样本。"],
            ),
            base_templates[2],
            base_templates[1],
        ]
    else:
        ordered = [
            (
                "range-reversion",
                "震荡均衡",
                FilterValidationRequest(min_change=-0.5, min_rsi=38, max_rsi=68, min_trend=38),
                ["适合区间震荡环境。", "避免过度追高，保留均值回归候选。"],
            ),
            base_templates[1],
            base_templates[0],
        ]
    templates: list[RegimeFilterTemplate] = []
    for template_id, name, filters, rationale in ordered:
        validation = validate_filters(filters)
        templates.append(
            RegimeFilterTemplate(
                id=template_id,
                name=name,
                regime_label=regime.label,
                risk_level=regime.risk_level,
                filters=filters,
                match_count=validation.match_count,
                valid=validation.valid,
                rationale=rationale,
                warnings=validation.warnings,
                suggestions=validation.suggestions,
            )
        )
    return templates


def regime_playbook_report(symbol: str | None = None) -> RegimePlaybook:
    regime = market_regime_report()
    templates = regime_filter_templates()
    watchlist_items = list_watchlist_snapshots()
    target_symbol = (symbol or (watchlist_items[0].symbol if watchlist_items else "AAPL")).upper()
    allocation: StrategyAllocationReport | None = None
    try:
        allocation = strategy_allocation_report(target_symbol)
    except HTTPException:
        allocation = None
    preferred_by_regime = {
        "risk_on": ["momentum-news", "event-drift", "vol-compression"],
        "trend": ["momentum-news", "vol-compression"],
        "range": ["rsi-reversion", "event-drift"],
        "volatile": ["event-drift", "rsi-reversion"],
        "risk_off": ["rsi-reversion"],
    }
    preferred_factors = preferred_by_regime[regime.label]
    steps: list[RegimePlaybookStep] = []
    valid_templates = [item for item in templates if item.valid and item.match_count > 0]
    steps.append(
        RegimePlaybookStep(
            name="筛选模板",
            status="pass" if valid_templates else "watch",
            detail=f"{len(valid_templates)}/{len(templates)} 个模板当前可用。",
            actions=[f"优先使用 {item.name}，命中 {item.match_count} 个标的。" for item in valid_templates[:2]] or ["放宽过滤器，先保证样本数量。"],
        )
    )
    if allocation:
        positive_items = [item for item in allocation.items if item.target_weight > 0]
        steps.append(
            RegimePlaybookStep(
                name="策略预算",
                status=allocation.status,
                detail=f"{allocation.symbol} 预算 {allocation.risk_budget:.1f}%，目标权重 {allocation.total_target_weight:.1f}%。",
                actions=[f"{item.factor_name}：{item.status}，目标 {item.target_weight:.1f}%。" for item in positive_items[:3]] or allocation.actions[:2],
            )
        )
    else:
        steps.append(
            RegimePlaybookStep(
                name="策略预算",
                status="watch",
                detail="无法生成配置建议。",
                actions=["先选择有效标的并补齐因子验证记录。"],
            )
        )
    steps.append(
        RegimePlaybookStep(
            name="市场状态",
            status="fail" if regime.risk_level == "risk" else "watch" if regime.risk_level == "watch" else "pass",
            detail=regime.summary,
            actions=regime.actions,
        )
    )
    if regime.risk_level == "risk":
        risk_controls = ["暂停新增高 beta 策略。", "只允许观察仓或对冲仓。", "预警阈值收紧并复核新闻风险。"]
    elif regime.risk_level == "watch":
        risk_controls = ["单因子目标权重减半。", "前向或滚动验证 fail 时立即降至 0。", "高影响新闻后再确认入场。"]
    elif regime.label == "range":
        risk_controls = ["降低趋势追涨权重。", "优先均值回归或事件验证。", "过滤器保留足够样本。"]
    else:
        risk_controls = ["允许小规模模拟。", "保持压力测试和漂移监控。", "单因子触达上限前必须通过滚动样本外。"]
    fail_count = sum(step.status == "fail" for step in steps)
    watch_count = sum(step.status == "watch" for step in steps)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count:
        status = "watch"
    else:
        status = "pass"
    next_actions = dedupe([action for step in steps for action in step.actions[:2]] + risk_controls[:2])
    return RegimePlaybook(
        generated_at=datetime.now(timezone.utc).isoformat(),
        regime=regime,
        status=status,
        summary=f"{regime.label} playbook：偏好 {', '.join(preferred_factors)}，状态 {status}。",
        preferred_factors=preferred_factors,
        filter_templates=templates,
        steps=steps,
        risk_controls=risk_controls,
        next_actions=next_actions[:8],
    )


def csv_response(filename: str, headers: list[str], rows: list[list[str | int | float]]) -> Response:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def export_dataset_csv(dataset: str, symbol: str | None = None) -> Response:
    generated = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    if dataset == "watchlist":
        rows = [
            [item.symbol, item.name, item.market, item.price, item.change_percent, item.volume, item.signal, item.rsi, item.macd, item.trend_score]
            for item in list_watchlist_snapshots()
        ]
        return csv_response(
            f"watchlist-{generated}.csv",
            ["symbol", "name", "market", "price", "change_percent", "volume", "signal", "rsi", "macd", "trend_score"],
            rows,
        )
    if dataset == "screener":
        result = run_screener()
        rows = [
            [
                item.snapshot.symbol,
                item.snapshot.name,
                item.snapshot.market,
                item.snapshot.price,
                item.snapshot.change_percent,
                item.snapshot.signal,
                item.snapshot.rsi,
                item.snapshot.trend_score,
                " | ".join(item.reasons),
            ]
            for item in result.items
        ]
        return csv_response(
            f"screener-{generated}.csv",
            ["symbol", "name", "market", "price", "change_percent", "signal", "rsi", "trend_score", "reasons"],
            rows,
        )
    if dataset == "portfolio":
        summary = get_portfolio_summary()
        rows = [
            [
                item.symbol,
                item.name,
                item.side,
                item.quantity,
                item.average_price,
                item.current_price if item.current_price is not None else "",
                item.market_value,
                item.cost_basis,
                item.unrealized_pnl,
                item.unrealized_pnl_percent,
                item.stale_price,
            ]
            for item in summary.positions
        ]
        return csv_response(
            f"portfolio-{generated}.csv",
            ["symbol", "name", "side", "quantity", "average_price", "current_price", "market_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_percent", "stale_price"],
            rows,
        )
    if dataset == "portfolio-risk":
        report = get_portfolio_risk_report()
        rows = [
            [item.symbol, item.side, item.weight_percent, item.market_value, item.unrealized_pnl, item.risk_flag]
            for item in report.contributions
        ]
        return csv_response(
            f"portfolio-risk-{generated}.csv",
            ["symbol", "side", "weight_percent", "market_value", "unrealized_pnl", "risk_flag"],
            rows,
        )
    if dataset == "backtests":
        export_symbol = (symbol or "AAPL").upper()
        rows = [
            [item.id, item.symbol, item.factor, item.total_return, item.max_drawdown, item.sharpe, item.win_rate, item.trades, item.status]
            for item in backtest_summaries(export_symbol)
        ]
        return csv_response(
            f"backtests-{export_symbol.lower()}-{generated}.csv",
            ["id", "symbol", "factor", "total_return", "max_drawdown", "sharpe", "win_rate", "trades", "status"],
            rows,
        )
    if dataset == "data-quality":
        report = data_quality_report()
        rows = [
            [report.generated_at, report.status, report.score, item.name, item.status, item.message]
            for item in report.checks
        ]
        return csv_response(
            f"data-quality-{generated}.csv",
            ["generated_at", "report_status", "score", "check_name", "check_status", "message"],
            rows,
        )
    if dataset == "data-freshness":
        report = data_freshness_report()
        rows = [
            [item.dataset_id, item.name, item.status, item.rows, item.latest_at or "", item.age_hours if item.age_hours is not None else "", item.sla_hours, item.lag_score, item.source, item.action]
            for item in report.items
        ]
        return csv_response(
            f"data-freshness-{generated}.csv",
            ["dataset_id", "name", "status", "rows", "latest_at", "age_hours", "sla_hours", "lag_score", "source", "action"],
            rows,
        )
    if dataset == "research-coverage":
        report = research_coverage_report()
        rows = [
            [item.symbol, item.name, item.status, item.score, item.backtest_count, item.forward_count, item.walk_forward_count, item.stress_count, item.latest_run_at or "", " | ".join(item.actions)]
            for item in report.items
        ]
        return csv_response(
            f"research-coverage-{generated}.csv",
            ["symbol", "name", "status", "score", "backtest_count", "forward_count", "walk_forward_count", "stress_count", "latest_run_at", "actions"],
            rows,
        )
    if dataset == "research-action-plan":
        plan = research_action_plan()
        rows = [
            [item.priority, item.source, item.severity, item.title, item.detail, item.action]
            for item in plan.items
        ]
        return csv_response(
            f"research-action-plan-{generated}.csv",
            ["priority", "source", "severity", "title", "detail", "action"],
            rows,
        )
    if dataset == "strategy-leaderboard":
        leaderboard = strategy_leaderboard_report()
        rows = [
            [item.rank, item.strategy_id, item.strategy_name, item.category, item.enabled, item.status, item.score, item.latest_validation_status, item.average_backtest_return if item.average_backtest_return is not None else "", item.average_forward_return if item.average_forward_return is not None else "", item.recommendation]
            for item in leaderboard.items
        ]
        return csv_response(
            f"strategy-leaderboard-{generated}.csv",
            ["rank", "strategy_id", "strategy_name", "category", "enabled", "status", "score", "latest_validation_status", "average_backtest_return", "average_forward_return", "recommendation"],
            rows,
        )
    if dataset == "strategy-promotion-gates":
        gates = strategy_promotion_gates()
        rows = [
            [item.strategy_id, item.strategy_name, item.status, item.score, item.recommendation, " | ".join(item.blockers), " | ".join(item.actions)]
            for item in gates
        ]
        return csv_response(
            f"strategy-promotion-gates-{generated}.csv",
            ["strategy_id", "strategy_name", "status", "score", "recommendation", "blockers", "actions"],
            rows,
        )
    if dataset == "research-health":
        report = research_health_report()
        rows = [
            [report.generated_at, report.decision, report.status, report.score, item.name, item.status, item.score, item.detail]
            for item in report.checks
        ]
        return csv_response(
            f"research-health-{generated}.csv",
            ["generated_at", "decision", "report_status", "report_score", "check_name", "check_status", "check_score", "detail"],
            rows,
        )
    if dataset == "factor-lifecycle":
        report = factor_lifecycle_report(symbol=symbol)
        rows = [
            [
                item.factor_id,
                item.factor_name,
                item.category,
                item.enabled,
                item.stage,
                item.status,
                item.score,
                item.coverage_score if item.coverage_score is not None else "",
                item.leaderboard_score if item.leaderboard_score is not None else "",
                item.admission_score if item.admission_score is not None else "",
                item.latest_run_at or "",
                " | ".join(item.blockers),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"factor-lifecycle-{generated}.csv",
            ["factor_id", "factor_name", "category", "enabled", "stage", "status", "score", "coverage_score", "leaderboard_score", "admission_score", "latest_run_at", "blockers", "actions"],
            rows,
        )
    if dataset == "research-run-audit":
        report = research_run_audit_report(limit=50, symbol=symbol)
        rows = [
            [item.id, item.run_type, item.symbol, item.factor_id, item.factor_name, item.generated_at, item.status, item.primary_metric, item.drawdown, item.summary, item.action]
            for item in report.items
        ]
        return csv_response(
            f"research-run-audit-{generated}.csv",
            ["id", "run_type", "symbol", "factor_id", "factor_name", "generated_at", "status", "primary_metric", "drawdown", "summary", "action"],
            rows,
        )
    if dataset == "data-lineage":
        report = data_lineage_report()
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.status,
                item.score,
                " | ".join(item.factor_ids),
                " | ".join(item.datasets),
                item.validation_coverage.get("backtest", 0),
                item.validation_coverage.get("forward", 0),
                item.validation_coverage.get("walk_forward", 0),
                item.validation_coverage.get("stress", 0),
                " | ".join(item.missing),
                " | ".join(item.actions),
            ]
            for item in report.strategies
        ]
        return csv_response(
            f"data-lineage-{generated}.csv",
            ["strategy_id", "strategy_name", "status", "score", "factor_ids", "datasets", "backtest", "forward", "walk_forward", "stress", "missing", "actions"],
            rows,
        )
    if dataset == "regime-exposure":
        report = regime_exposure_report()
        rows = [
            [item.symbol, item.status, item.allocation_status, item.correlation_status, item.risk_budget, item.target_weight, item.crowding_score, item.dominant_factor or "", " | ".join(item.notes)]
            for item in report.items
        ]
        return csv_response(
            f"regime-exposure-{generated}.csv",
            ["symbol", "status", "allocation_status", "correlation_status", "risk_budget", "target_weight", "crowding_score", "dominant_factor", "notes"],
            rows,
        )
    if dataset == "regime-scenario-risk":
        report = regime_scenario_risk_report()
        rows = [
            [
                item.regime_label,
                item.status,
                item.score,
                item.open_count,
                item.reduced_count,
                item.paused_count,
                item.blocked_count,
                item.average_strategy_score,
                item.risk_budget,
                item.summary,
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"regime-scenario-risk-{generated}.csv",
            ["regime_label", "status", "score", "open_count", "reduced_count", "paused_count", "blocked_count", "average_strategy_score", "risk_budget", "summary", "actions"],
            rows,
        )
    if dataset == "strategy-regime-matrix":
        report = strategy_regime_matrix_report()
        rows = [
            [item.strategy_id, item.strategy_name, item.category, item.enabled, item.latest_validation_status, item.overall_status, cell.regime_label, cell.status, cell.decision, cell.score, " | ".join(cell.rationale)]
            for item in report.items
            for cell in item.cells
        ]
        return csv_response(
            f"strategy-regime-matrix-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "latest_validation_status", "overall_status", "regime_label", "cell_status", "decision", "score", "rationale"],
            rows,
        )
    if dataset == "strategy-crowding":
        report = strategy_crowding_report()
        rows = [
            [
                item.strategy_a_id,
                item.strategy_a_name,
                item.strategy_b_id,
                item.strategy_b_name,
                item.status,
                item.overlap_score,
                " | ".join(item.shared_factors),
                item.filter_overlap,
                " | ".join(item.current_decisions),
                item.detail,
                item.action,
            ]
            for item in report.pairs
        ]
        return csv_response(
            f"strategy-crowding-{generated}.csv",
            ["strategy_a_id", "strategy_a_name", "strategy_b_id", "strategy_b_name", "status", "overlap_score", "shared_factors", "filter_overlap", "current_decisions", "detail", "action"],
            rows,
        )
    if dataset == "strategy-validation-decay":
        report = strategy_validation_decay_report(limit=50)
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.status,
                item.score,
                item.validation_id,
                item.generated_at,
                item.backtest_return,
                item.forward_return,
                item.decay,
                item.decay_ratio if item.decay_ratio is not None else "",
                item.symbol_count,
                item.backtest_count,
                item.forward_count,
                item.regime_label,
                item.risk_level,
                item.action,
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-validation-decay-{generated}.csv",
            ["strategy_id", "strategy_name", "status", "score", "validation_id", "generated_at", "backtest_return", "forward_return", "decay", "decay_ratio", "symbol_count", "backtest_count", "forward_count", "regime_label", "risk_level", "action"],
            rows,
        )
    if dataset == "strategy-risk-controls":
        report = strategy_risk_control_report()
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.score,
                item.risk_control_count,
                item.validation_rule_count,
                item.gate_status,
                item.lifecycle_stage,
                item.crowding_status,
                item.decay_status,
                " | ".join(item.blockers),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-risk-controls-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "score", "risk_control_count", "validation_rule_count", "gate_status", "lifecycle_stage", "crowding_status", "decay_status", "blockers", "actions"],
            rows,
        )
    if dataset == "market-state-controls":
        report = market_state_control_report()
        rows = [
            [
                report.generated_at,
                report.status,
                report.score,
                report.decision,
                report.current_regime.label,
                report.current_regime.risk_level,
                item.name,
                item.status,
                item.score,
                item.detail,
                item.action,
            ]
            for item in report.items
        ]
        return csv_response(
            f"market-state-controls-{generated}.csv",
            ["generated_at", "report_status", "report_score", "decision", "regime_label", "risk_level", "item_name", "item_status", "item_score", "detail", "action"],
            rows,
        )
    if dataset == "strategy-operating-plan":
        report = strategy_operating_plan_report()
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.decision,
                item.recommended_weight,
                item.score,
                item.gate_status,
                item.lifecycle_stage,
                item.regime_decision,
                item.backtest_status,
                item.forward_status,
                item.risk_control_status,
                " | ".join(item.reasons),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-operating-plan-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "decision", "recommended_weight", "score", "gate_status", "lifecycle_stage", "regime_decision", "backtest_status", "forward_status", "risk_control_status", "reasons", "actions"],
            rows,
        )
    if dataset == "strategy-evidence-packs":
        report = strategy_evidence_pack_report()
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.score,
                item.operating_decision,
                item.filter_valid,
                item.filter_match_count,
                item.latest_validation_status,
                item.validation_id or "",
                item.backtest_return if item.backtest_return is not None else "",
                item.forward_return if item.forward_return is not None else "",
                item.decay if item.decay is not None else "",
                item.gate_status,
                item.risk_control_status,
                " | ".join(item.evidence),
                " | ".join(item.gaps),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-evidence-packs-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "score", "operating_decision", "filter_valid", "filter_match_count", "latest_validation_status", "validation_id", "backtest_return", "forward_return", "decay", "gate_status", "risk_control_status", "evidence", "gaps", "actions"],
            rows,
        )
    if dataset == "strategy-lifecycle":
        report = strategy_lifecycle_report()
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.stage,
                item.status,
                item.score,
                item.leaderboard_score if item.leaderboard_score is not None else "",
                item.promotion_score if item.promotion_score is not None else "",
                item.regime_score if item.regime_score is not None else "",
                item.latest_validation_status,
                " | ".join(item.blockers),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-lifecycle-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "stage", "status", "score", "leaderboard_score", "promotion_score", "regime_score", "latest_validation_status", "blockers", "actions"],
            rows,
        )
    if dataset == "strategy-forward-monitor":
        report = strategy_forward_monitor_report(limit=50)
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.score,
                item.factor_count,
                item.tested_factor_count,
                item.symbol_count,
                item.pass_count,
                item.watch_count,
                item.fail_count,
                item.average_forward_return if item.average_forward_return is not None else "",
                item.average_degradation if item.average_degradation is not None else "",
                item.max_drawdown if item.max_drawdown is not None else "",
                item.latest_run_at or "",
                item.freshness_days if item.freshness_days is not None else "",
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-forward-monitor-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "score", "factor_count", "tested_factor_count", "symbol_count", "pass_count", "watch_count", "fail_count", "average_forward_return", "average_degradation", "max_drawdown", "latest_run_at", "freshness_days", "actions"],
            rows,
        )
    if dataset == "strategy-sensitivity":
        report = strategy_sensitivity_report(limit=50)
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.score,
                item.factor_count,
                item.tested_factor_count,
                item.symbol_count,
                item.interval_count,
                item.backtest_count,
                item.forward_count,
                item.positive_backtest_rate,
                item.forward_pass_rate,
                item.return_dispersion if item.return_dispersion is not None else "",
                item.degradation_dispersion if item.degradation_dispersion is not None else "",
                item.worst_drawdown if item.worst_drawdown is not None else "",
                item.window_variants,
                " | ".join(item.evidence),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-sensitivity-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "score", "factor_count", "tested_factor_count", "symbol_count", "interval_count", "backtest_count", "forward_count", "positive_backtest_rate", "forward_pass_rate", "return_dispersion", "degradation_dispersion", "worst_drawdown", "window_variants", "evidence", "actions"],
            rows,
        )
    if dataset == "strategy-revalidation-plan":
        report = strategy_revalidation_plan_report(limit=50)
        rows = [
            [
                item.rank,
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.status,
                item.priority,
                item.current_regime,
                item.regime_decision,
                item.sensitivity_status,
                item.evidence_status,
                item.lifecycle_stage,
                " | ".join(item.due_tasks),
                " | ".join(item.reasons),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-revalidation-plan-{generated}.csv",
            ["rank", "strategy_id", "strategy_name", "category", "status", "priority", "current_regime", "regime_decision", "sensitivity_status", "evidence_status", "lifecycle_stage", "due_tasks", "reasons", "actions"],
            rows,
        )
    if dataset == "strategy-validation-coverage":
        report = strategy_validation_coverage_report(limit=50)
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.score,
                item.factor_count,
                item.symbol_count,
                item.backtest_factor_count,
                item.forward_factor_count,
                item.walk_forward_factor_count,
                item.stress_factor_count,
                item.fully_validated_factor_count,
                item.backtest_run_count,
                item.forward_run_count,
                item.walk_forward_run_count,
                item.stress_run_count,
                " | ".join(item.missing_tasks),
                " | ".join(item.evidence),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-validation-coverage-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "score", "factor_count", "symbol_count", "backtest_factor_count", "forward_factor_count", "walk_forward_factor_count", "stress_factor_count", "fully_validated_factor_count", "backtest_run_count", "forward_run_count", "walk_forward_run_count", "stress_run_count", "missing_tasks", "evidence", "actions"],
            rows,
        )
    if dataset == "strategy-backtest-monitor":
        report = strategy_backtest_monitor_report(limit=50)
        rows = [
            [
                item.strategy_id,
                item.strategy_name,
                item.category,
                item.enabled,
                item.status,
                item.score,
                item.factor_count,
                item.tested_factor_count,
                item.symbol_count,
                item.run_count,
                item.positive_count,
                item.average_return if item.average_return is not None else "",
                item.average_sharpe if item.average_sharpe is not None else "",
                item.average_win_rate if item.average_win_rate is not None else "",
                item.max_drawdown if item.max_drawdown is not None else "",
                item.latest_run_at or "",
                item.freshness_days if item.freshness_days is not None else "",
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"strategy-backtest-monitor-{generated}.csv",
            ["strategy_id", "strategy_name", "category", "enabled", "status", "score", "factor_count", "tested_factor_count", "symbol_count", "run_count", "positive_count", "average_return", "average_sharpe", "average_win_rate", "max_drawdown", "latest_run_at", "freshness_days", "actions"],
            rows,
        )
    if dataset == "filter-library":
        report = filter_library_score_report()
        rows = [
            [
                item.preset_id,
                item.name,
                item.status,
                item.decision,
                item.score,
                item.match_count,
                item.filter_count,
                item.concentration_score,
                item.stability_score,
                item.regime_label,
                item.risk_level,
                " | ".join(f"{key}:{value}" for key, value in item.filters.items()),
                " | ".join(item.warnings),
                " | ".join(item.actions),
            ]
            for item in report.items
        ]
        return csv_response(
            f"filter-library-{generated}.csv",
            ["preset_id", "name", "status", "decision", "score", "match_count", "filter_count", "concentration_score", "stability_score", "regime_label", "risk_level", "filters", "warnings", "actions"],
            rows,
        )
    if dataset == "filter-strategy-coverage":
        report = filter_strategy_coverage_report()
        rows = [
            [
                item.preset_id or "",
                item.preset_name or "",
                item.strategy_id or "",
                item.strategy_name or "",
                item.status,
                item.score,
                item.match_type,
                item.filter_count,
                item.linked_strategy_count,
                " | ".join(f"{key}:{value}" for key, value in item.filters.items()),
                item.detail,
                item.action,
            ]
            for item in report.items
        ]
        return csv_response(
            f"filter-strategy-coverage-{generated}.csv",
            ["preset_id", "preset_name", "strategy_id", "strategy_name", "status", "score", "match_type", "filter_count", "linked_strategy_count", "filters", "detail", "action"],
            rows,
        )
    raise HTTPException(status_code=404, detail="Unsupported export dataset")
