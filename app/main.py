from __future__ import annotations

import asyncio
import csv
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import pbkdf2_hmac, sha256
from html import unescape
from math import sqrt, sin
from random import Random
import re
import sqlite3
import json
from pathlib import Path
from io import StringIO
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    init_db()
    app_instance.state.automation_task = asyncio.create_task(automation_loop())
    app_instance.state.agent_task = asyncio.create_task(agent_worker_loop())
    try:
        yield
    finally:
        task = getattr(app_instance.state, "automation_task", None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        agent_task = getattr(app_instance.state, "agent_task", None)
        if agent_task:
            agent_task.cancel()
            with suppress(asyncio.CancelledError):
                await agent_task


app = FastAPI(title="App Trading", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

DB_PATH = Path(os.getenv("APP_DB_PATH", "app_data.db"))
SESSION_COOKIE = "app_trading_session"
SESSION_HOURS = 8
PASSWORD_ITERATIONS = 600_000


class AuthUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["admin", "viewer"]
    enabled: bool
    created_at: str
    updated_at: str


class SetupStatus(BaseModel):
    required: bool


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthSessionInfo(BaseModel):
    user: AuthUser
    csrf_token: str
    expires_at: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["admin", "viewer"] = "viewer"
    enabled: bool = True


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: Literal["admin", "viewer"] | None = None
    enabled: bool | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=10, max_length=200)


class AdminResourceDescriptor(BaseModel):
    id: str
    name: str
    category: str
    mutable: bool
    status_field: str | None = None
    total: int
    active: int | None = None
    actions: list[str]


class AdminResourcePage(BaseModel):
    resource: AdminResourceDescriptor
    page: int
    page_size: int
    total: int
    items: list[dict[str, object]]


class ResourceStatusUpdate(BaseModel):
    enabled: bool


class BulkResourceStatusUpdate(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    enabled: bool


class AdminAuditItem(BaseModel):
    id: str
    user_id: str
    username: str
    action: str
    resource_type: str
    resource_id: str | None
    detail: str
    before_state: dict[str, object] | None = None
    after_state: dict[str, object] | None = None
    created_at: str


class SymbolSnapshot(BaseModel):
    symbol: str
    name: str
    market: str
    price: float
    change_percent: float
    volume: str
    signal: Literal["strong_buy", "buy", "neutral", "sell", "strong_sell"]
    rsi: float
    macd: float
    trend_score: int


class Candle(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    ma20: float
    ma60: float
    rsi: float


class IndicatorSummary(BaseModel):
    label: str
    value: str
    state: Literal["up", "down", "flat"]
    detail: str


class MarketSymbol(BaseModel):
    symbol: str
    name: str
    asset_class: Literal["stock", "etf", "crypto"]
    market: str
    currency: str
    active: bool


class MarketProviderStatus(BaseModel):
    name: str
    display_name: str
    is_active: bool
    is_realtime: bool
    is_delayed: bool
    status: Literal["online", "degraded", "offline"]
    coverage: list[str]
    capabilities: list[str]
    message: str
    updated_at: str


class MarketProviderConfig(BaseModel):
    provider: Literal["mock", "binance", "coinbase"] = "mock"


class WatchlistItemCreate(BaseModel):
    symbol: str


class MarketBreadthGroup(BaseModel):
    name: str
    count: int
    advancers: int
    decliners: int
    unchanged: int
    average_change: float
    average_rsi: float
    average_trend_score: float
    bullish_signals: int
    bearish_signals: int
    risk_level: Literal["risk", "watch", "neutral", "constructive"]


class MarketBreadth(BaseModel):
    generated_at: str
    total: int
    advancers: int
    decliners: int
    unchanged: int
    advance_decline_ratio: float
    average_change: float
    average_rsi: float
    average_trend_score: float
    strong_buy_count: int
    buy_count: int
    neutral_count: int
    sell_count: int
    strong_sell_count: int
    risk_level: Literal["risk", "watch", "neutral", "constructive"]
    groups: list[MarketBreadthGroup]


class ScreenerMatch(BaseModel):
    snapshot: SymbolSnapshot
    reasons: list[str]


class ScreenerResult(BaseModel):
    generated_at: str
    total: int
    filters: dict[str, str | float]
    items: list[ScreenerMatch]


class ScreenerPreset(BaseModel):
    id: str
    name: str
    filters: dict[str, str | float]
    created_at: str


class ScreenerPresetCreate(BaseModel):
    name: str
    filters: dict[str, str | float] = {}


class ScreenerPresetUpdate(BaseModel):
    name: str | None = None
    filters: dict[str, str | float] | None = None
    enabled: bool | None = None


class ScreenerPresetValidation(BaseModel):
    id: str
    name: str
    status: Literal["pass", "watch", "fail"]
    match_count: int
    filters: dict[str, str | float]
    warnings: list[str]
    suggestions: list[str]


class FilterLibraryScoreItem(BaseModel):
    preset_id: str
    name: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["accept", "revise", "reject"]
    score: int
    match_count: int
    filter_count: int
    concentration_score: int
    stability_score: int
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    filters: dict[str, str | float]
    warnings: list[str]
    actions: list[str]


class FilterLibraryScoreReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FilterLibraryScoreItem]
    actions: list[str]


class FilterStrategyCoverageItem(BaseModel):
    preset_id: str | None = None
    preset_name: str | None = None
    strategy_id: str | None = None
    strategy_name: str | None = None
    status: Literal["pass", "watch", "fail"]
    score: int
    match_type: Literal["preset_reused", "orphan_preset", "unregistered_strategy_filter"]
    filter_count: int
    linked_strategy_count: int
    filters: dict[str, str | float]
    detail: str
    action: str


class FilterStrategyCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[FilterStrategyCoverageItem]
    actions: list[str]


class StrategyCandidateItem(BaseModel):
    rank: int
    symbol: str
    name: str
    market: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["go", "size_down", "hold", "block"]
    score: int
    recommended_weight: float
    match_reasons: list[str]
    blockers: list[str]
    warnings: list[str]
    actions: list[str]


class StrategyCandidateReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    filters: dict[str, str | float]
    total: int
    items: list[StrategyCandidateItem]


class HealthComponent(BaseModel):
    name: str
    status: Literal["ok", "warn", "error"]
    message: str
    count: int | None = None


class SystemHealth(BaseModel):
    status: Literal["ok", "warn", "error"]
    generated_at: str
    components: list[HealthComponent]


class MarketProvider(Protocol):
    name: str
    display_name: str

    def list_symbols(self) -> list[MarketSymbol]: ...

    def watchlist(self) -> list[SymbolSnapshot]: ...

    def candles(self, symbol: str, interval: Literal["15m", "1h", "4h", "1d"], limit: int) -> list[Candle]: ...

    def indicators(self, symbol: str) -> list[IndicatorSummary]: ...

    def status(self, is_active: bool) -> MarketProviderStatus: ...


class NewsEvent(BaseModel):
    id: str
    symbol: str
    title: str
    summary: str
    source: str
    url: str
    published_at: str
    event_type: Literal["earnings", "macro", "regulatory", "product", "analyst", "crypto", "flow"]
    sentiment: Literal["bullish", "bearish", "neutral", "mixed"]
    impact_score: int
    price_reaction: str


class NewsDetail(NewsEvent):
    related_symbols: list[str]
    ai_summary: str
    key_points: list[str]
    detail: str


class NewsSource(BaseModel):
    id: int
    name: str
    type: Literal["rss"]
    url: str
    enabled: bool
    symbols: list[str]
    poll_interval_seconds: int
    last_fetched_at: str | None
    last_error: str | None
    created_at: str


class NewsSourceCreate(BaseModel):
    name: str
    url: HttpUrl
    symbols: list[str] = []
    enabled: bool = True
    poll_interval_seconds: int = 900


class NewsSourceUpdate(BaseModel):
    name: str | None = None
    url: HttpUrl | None = None
    enabled: bool | None = None
    symbols: list[str] | None = None
    poll_interval_seconds: int | None = None


class NewsRefreshResult(BaseModel):
    source_id: int
    fetched: int
    inserted: int
    error: str | None = None


class AiInsight(BaseModel):
    id: str
    agent: str
    symbol: str
    title: str
    summary: str
    severity: Literal["info", "watch", "risk", "opportunity"]
    confidence: int
    evidence: list[str]
    created_at: str


class AiBriefing(BaseModel):
    id: str
    title: str
    summary: str
    risk_level: Literal["info", "watch", "risk", "opportunity"]
    generated_at: str
    opportunities: list[str]
    risks: list[str]
    actions: list[str]
    evidence: list[str]


AgentTemplate = Literal["market_monitor", "news_analyst", "risk_sentinel", "factor_research", "briefing"]
AgentRuntimeMode = Literal["rule", "llm", "hybrid"]


class AgentProviderProfile(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    enabled: bool
    secret_configured: bool = False
    created_at: str
    updated_at: str


class AgentProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_key_env: str = Field(min_length=2, max_length=120)
    timeout_seconds: int = Field(default=30, ge=3, le=180)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1200, ge=100, le=8000)
    enabled: bool = True


class AgentProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    api_key_env: str | None = Field(default=None, min_length=2, max_length=120)
    timeout_seconds: int | None = Field(default=None, ge=3, le=180)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=100, le=8000)
    enabled: bool | None = None


class AgentConfig(BaseModel):
    id: str
    template_type: AgentTemplate
    builtin: bool
    name: str
    description: str
    enabled: bool
    runtime_mode: AgentRuntimeMode
    provider_id: str | None = None
    symbol_scope: Literal["watchlist", "all", "custom"]
    symbols: list[str]
    schedule_enabled: bool
    interval_seconds: int
    triggers: list[str]
    system_prompt: str
    task_prompt: str
    settings: dict[str, object]
    notification_channel_ids: list[str]
    fallback_to_rules: bool
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str
    updated_at: str


class AgentCreate(BaseModel):
    template_type: AgentTemplate
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    runtime_mode: AgentRuntimeMode = "rule"
    provider_id: str | None = None
    symbol_scope: Literal["watchlist", "all", "custom"] = "watchlist"
    symbols: list[str] = Field(default_factory=list)
    schedule_enabled: bool = False
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    triggers: list[str] = Field(default_factory=lambda: ["manual"])
    system_prompt: str = Field(default="", max_length=12000)
    task_prompt: str = Field(default="", max_length=12000)
    settings: dict[str, object] = Field(default_factory=dict)
    notification_channel_ids: list[str] = Field(default_factory=list)
    fallback_to_rules: bool = True
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    runtime_mode: AgentRuntimeMode | None = None
    provider_id: str | None = None
    symbol_scope: Literal["watchlist", "all", "custom"] | None = None
    symbols: list[str] | None = None
    schedule_enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)
    triggers: list[str] | None = None
    system_prompt: str | None = Field(default=None, max_length=12000)
    task_prompt: str | None = Field(default=None, max_length=12000)
    settings: dict[str, object] | None = None
    notification_channel_ids: list[str] | None = None
    fallback_to_rules: bool | None = None
    enabled: bool | None = None


class AgentRunRequest(BaseModel):
    symbol: str | None = None
    context: dict[str, object] = Field(default_factory=dict)
    test: bool = False


class AgentRun(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    trigger: str
    symbol: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    runtime_mode: AgentRuntimeMode
    output: dict[str, object] | None = None
    error: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    test: bool = False
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class AgentProposal(BaseModel):
    id: str
    run_id: str
    agent_id: str
    resource_type: str
    action: str
    title: str
    payload: dict[str, object]
    status: Literal["pending", "approved", "rejected", "executed", "failed"]
    reviewed_by: str | None = None
    review_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


class AgentProposalReview(BaseModel):
    note: str = Field(default="", max_length=500)


class FactorIdea(BaseModel):
    id: str
    name: str
    category: str
    description: str
    signal: Literal["long", "short", "neutral"]
    score: int
    horizon: str


class FactorDefinition(BaseModel):
    id: str
    name: str
    category: str
    description: str
    signal: Literal["long", "short", "neutral"]
    horizon: str
    enabled: bool
    created_at: str


class FactorDefinitionCreate(BaseModel):
    name: str
    category: str
    description: str
    signal: Literal["long", "short", "neutral"] = "long"
    horizon: str = "3-10 days"
    enabled: bool = True


class FactorDefinitionUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    signal: Literal["long", "short", "neutral"] | None = None
    horizon: str | None = None
    enabled: bool | None = None


class BacktestSummary(BaseModel):
    id: str
    name: str
    symbol: str
    factor: str
    total_return: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    trades: int
    status: Literal["completed", "running"]
    equity_curve: list[float]


class BacktestRunRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    limit: int = 180
    initial_capital: float = 10000
    fee_bps: float = 2.0


class BacktestRunResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    interval: str
    initial_capital: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    trades: int
    exposure: float
    created_at: str
    equity_curve: list[float]
    signal_points: list[dict[str, str | float]]


class BacktestBenchmarkReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    interval: str
    strategy_return: float
    benchmark_return: float
    alpha: float
    strategy_drawdown: float
    benchmark_drawdown: float
    drawdown_delta: float
    strategy_sharpe: float
    benchmark_sharpe: float
    sharpe_delta: float
    trades: int
    exposure: float
    summary: str
    checks: list[str]
    actions: list[str]


class StrategyTemplate(BaseModel):
    id: str
    name: str
    category: str
    description: str
    factor_ids: list[str]
    default_filters: dict[str, str | float]
    risk_controls: list[str]
    validation_rules: list[str]
    enabled: bool
    created_at: str


class StrategyTemplateCreate(BaseModel):
    name: str
    category: str
    description: str
    factor_ids: list[str] = []
    default_filters: dict[str, str | float] = {}
    risk_controls: list[str] = []
    validation_rules: list[str] = []
    enabled: bool = True


class StrategyTemplateUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    factor_ids: list[str] | None = None
    default_filters: dict[str, str | float] | None = None
    risk_controls: list[str] | None = None
    validation_rules: list[str] | None = None
    enabled: bool | None = None


class DataCatalogItem(BaseModel):
    id: str
    name: str
    type: Literal["market", "news", "factor", "strategy", "portfolio"]
    provider: str
    coverage: list[str]
    update_mode: str
    status: Literal["ready", "degraded", "offline"]
    rows: int
    fields: list[str]
    notes: str


class MarketRegime(BaseModel):
    generated_at: str
    label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    breadth_score: float
    volatility_score: float
    trend_score: float
    liquidity_score: float
    summary: str
    evidence: list[str]
    actions: list[str]


class MarketRegimeTransition(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    transition_score: int
    current: MarketRegime
    previous: MarketRegime | None = None
    label_changed: bool
    risk_level_changed: bool
    breadth_delta: float
    volatility_delta: float
    trend_delta: float
    liquidity_delta: float
    summary: str
    evidence: list[str]
    actions: list[str]


class FilterValidationRequest(BaseModel):
    min_change: float | None = None
    max_change: float | None = None
    min_rsi: float | None = None
    max_rsi: float | None = None
    min_trend: int | None = None
    signal: str | None = None
    market: str | None = None


class FilterValidationResult(BaseModel):
    valid: bool
    normalized_filters: dict[str, str | float]
    match_count: int
    warnings: list[str]
    suggestions: list[str]
    regime: MarketRegime


class FilterContributionItem(BaseModel):
    filter_name: str
    label: str
    current_value: str | float
    baseline_match_count: int
    relaxed_match_count: int
    removed_by_filter: int
    severity: Literal["pass", "watch", "fail"]
    insight: str
    action: str


class FilterContributionReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    baseline_match_count: int
    total_universe: int
    concentration_score: int
    summary: str
    filters: dict[str, str | float]
    items: list[FilterContributionItem]
    actions: list[str]


class FilterStressScenario(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    match_count: int
    filters: FilterValidationRequest
    warnings: list[str]
    suggestions: list[str]


class FilterStressTestReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    stability_score: int
    baseline_match_count: int
    min_match_count: int
    max_match_count: int
    summary: str
    scenarios: list[FilterStressScenario]
    actions: list[str]


class FilterAdmissionCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    message: str


class FilterAdmissionReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["accept", "revise", "reject"]
    score: int
    summary: str
    filters: dict[str, str | float]
    validation: FilterValidationResult
    contribution: FilterContributionReport
    stress_test: FilterStressTestReport
    checks: list[FilterAdmissionCheck]
    actions: list[str]


class RegimeFilterTemplate(BaseModel):
    id: str
    name: str
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    filters: FilterValidationRequest
    match_count: int
    valid: bool
    rationale: list[str]
    warnings: list[str]
    suggestions: list[str]


class ForwardTestRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    train_bars: int = 140
    forward_bars: int = 40
    initial_capital: float = 10000
    fee_bps: float = 2.0


class ForwardTestResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    interval: str
    train_bars: int
    forward_bars: int
    train_return: float
    forward_return: float
    degradation: float
    max_drawdown: float
    pass_fail: Literal["pass", "watch", "fail"]
    generated_at: str
    equity_curve: list[float]
    checks: list[str]


class ForwardLeaderboardItem(BaseModel):
    rank: int
    symbol: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    train_return: float
    forward_return: float
    degradation: float
    max_drawdown: float
    generated_at: str
    recommendation: str
    actions: list[str]


class ForwardLeaderboard(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[ForwardLeaderboardItem]


class StrategyBacktestMonitorItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    tested_factor_count: int
    symbol_count: int
    run_count: int
    positive_count: int
    average_return: float | None = None
    average_sharpe: float | None = None
    average_win_rate: float | None = None
    max_drawdown: float | None = None
    latest_run_at: str | None = None
    freshness_days: int | None = None
    actions: list[str]


class StrategyBacktestMonitorReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StrategyBacktestMonitorItem]
    actions: list[str]


class StrategyForwardMonitorItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    tested_factor_count: int
    symbol_count: int
    pass_count: int
    watch_count: int
    fail_count: int
    average_forward_return: float | None = None
    average_degradation: float | None = None
    max_drawdown: float | None = None
    latest_run_at: str | None = None
    freshness_days: int | None = None
    actions: list[str]


class StrategyForwardMonitorReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StrategyForwardMonitorItem]
    actions: list[str]


class StrategySensitivityItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    tested_factor_count: int
    symbol_count: int
    interval_count: int
    backtest_count: int
    forward_count: int
    positive_backtest_rate: float
    forward_pass_rate: float
    return_dispersion: float | None = None
    degradation_dispersion: float | None = None
    worst_drawdown: float | None = None
    window_variants: int
    evidence: list[str]
    actions: list[str]


class StrategySensitivityReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategySensitivityItem]
    actions: list[str]


class StrategyRevalidationPlanItem(BaseModel):
    rank: int
    strategy_id: str
    strategy_name: str
    category: str
    status: Literal["pass", "watch", "fail"]
    priority: int
    current_regime: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    regime_decision: Literal["open", "reduced", "paused", "blocked", "missing"]
    sensitivity_status: Literal["pass", "watch", "fail", "missing"]
    evidence_status: Literal["pass", "watch", "fail", "missing"]
    lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"]
    due_tasks: list[str]
    reasons: list[str]
    actions: list[str]


class StrategyRevalidationPlanReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    current_regime: MarketRegime
    summary: str
    items: list[StrategyRevalidationPlanItem]
    actions: list[str]


class StrategyValidationCoverageItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    symbol_count: int
    backtest_factor_count: int
    forward_factor_count: int
    walk_forward_factor_count: int
    stress_factor_count: int
    fully_validated_factor_count: int
    backtest_run_count: int
    forward_run_count: int
    walk_forward_run_count: int
    stress_run_count: int
    missing_tasks: list[str]
    evidence: list[str]
    actions: list[str]


class StrategyValidationCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyValidationCoverageItem]
    actions: list[str]


class WalkForwardRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    train_bars: int = 90
    forward_bars: int = 30
    windows: int = 4
    initial_capital: float = 10000
    fee_bps: float = 2.0


class WalkForwardWindowResult(BaseModel):
    window: int
    train_start: str
    train_end: str
    forward_start: str
    forward_end: str
    train_return: float
    forward_return: float
    degradation: float
    max_drawdown: float
    trades: int
    pass_fail: Literal["pass", "watch", "fail"]


class WalkForwardResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    interval: str
    train_bars: int
    forward_bars: int
    window_count: int
    pass_rate: float
    average_forward_return: float
    worst_forward_return: float
    max_drawdown: float
    stability_score: int
    pass_fail: Literal["pass", "watch", "fail"]
    generated_at: str
    windows: list[WalkForwardWindowResult]
    checks: list[str]
    actions: list[str]


class WalkForwardLeaderboardItem(BaseModel):
    rank: int
    symbol: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    pass_rate: float
    average_forward_return: float
    worst_forward_return: float
    max_drawdown: float
    stability_score: int
    window_count: int
    generated_at: str
    recommendation: str
    actions: list[str]


class WalkForwardLeaderboard(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[WalkForwardLeaderboardItem]


class StressTestRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_capital: float = 10000


class StressScenarioResult(BaseModel):
    name: str
    shock: str
    stressed_return: float
    stressed_drawdown: float
    pass_fail: Literal["pass", "watch", "fail"]
    notes: list[str]


class StressTestResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    base_return: float
    base_drawdown: float
    worst_return: float
    worst_drawdown: float
    pass_fail: Literal["pass", "watch", "fail"]
    scenarios: list[StressScenarioResult]
    actions: list[str]


class StressLeaderboardItem(BaseModel):
    rank: int
    symbol: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    base_return: float
    worst_return: float
    worst_drawdown: float
    scenario_count: int
    pass_scenarios: int
    generated_at: str
    recommendation: str
    actions: list[str]


class StressLeaderboard(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StressLeaderboardItem]


class ConsistencyCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail", "missing"]
    message: str


class BacktestForwardConsistencyReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    recommendation: str
    backtest_return: float | None = None
    forward_return: float | None = None
    walk_forward_pass_rate: float | None = None
    stress_worst_return: float | None = None
    degradation: float | None = None
    summary: str
    checks: list[ConsistencyCheck]
    actions: list[str]


class RobustnessFactorReport(BaseModel):
    factor_id: str
    factor_name: str
    signal: Literal["long", "short", "neutral"]
    status: Literal["pass", "watch", "fail"]
    score: int
    backtest_status: Literal["pass", "watch", "fail", "missing"]
    forward_status: Literal["pass", "watch", "fail", "missing"]
    walk_forward_status: Literal["pass", "watch", "fail", "missing"]
    stress_status: Literal["pass", "watch", "fail", "missing"]
    regime_status: Literal["pass", "watch", "fail"]
    latest_backtest_return: float | None = None
    latest_backtest_drawdown: float | None = None
    latest_forward_return: float | None = None
    latest_walk_forward_pass_rate: float | None = None
    latest_stress_worst_return: float | None = None
    notes: list[str]
    actions: list[str]


class RobustnessReport(BaseModel):
    symbol: str
    generated_at: str
    regime: MarketRegime
    status: Literal["pass", "watch", "fail"]
    summary: str
    factors: list[RobustnessFactorReport]


class StrategyDriftFactor(BaseModel):
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    drift_score: int
    baseline_return: float | None = None
    forward_return: float | None = None
    return_degradation: float | None = None
    walk_forward_pass_rate: float | None = None
    stress_worst_return: float | None = None
    regime_alignment: Literal["pass", "watch", "fail"]
    evidence: list[str]
    actions: list[str]


class StrategyDriftReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    regime: MarketRegime
    factors: list[StrategyDriftFactor]


class StrategyAllocationItem(BaseModel):
    factor_id: str
    factor_name: str
    status: Literal["increase", "hold", "reduce", "avoid"]
    target_weight: float
    max_weight: float
    confidence: int
    rationale: list[str]
    controls: list[str]


class StrategyAllocationReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    total_target_weight: float
    risk_budget: float
    summary: str
    regime: MarketRegime
    items: list[StrategyAllocationItem]
    portfolio_notes: list[str]
    actions: list[str]


class TradeReadinessCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    action: str


class TradeReadinessReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["go", "size_down", "hold", "block"]
    score: int
    recommended_weight: float
    max_weight: float
    summary: str
    checks: list[TradeReadinessCheck]
    blockers: list[str]
    warnings: list[str]
    actions: list[str]


class RegimeRiskGateCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    action: str


class RegimeRiskGate(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["open", "reduced", "paused", "blocked"]
    score: int
    allowed_weight: float
    regime: MarketRegime
    transition: MarketRegimeTransition
    summary: str
    checks: list[RegimeRiskGateCheck]
    blockers: list[str]
    actions: list[str]


class RegimePlaybookStep(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    actions: list[str]


class RegimePlaybook(BaseModel):
    generated_at: str
    regime: MarketRegime
    status: Literal["pass", "watch", "fail"]
    summary: str
    preferred_factors: list[str]
    filter_templates: list[RegimeFilterTemplate]
    steps: list[RegimePlaybookStep]
    risk_controls: list[str]
    next_actions: list[str]


class ResearchPipelineRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_capital: float = 10000
    filters: FilterValidationRequest = Field(default_factory=FilterValidationRequest)


class ResearchPipelineResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    pass_fail: Literal["pass", "watch", "fail"]
    summary: str
    filter_validation: FilterValidationResult
    backtest: BacktestRunResult
    forward_test: ForwardTestResult
    stress_test: StressTestResult
    robustness: RobustnessFactorReport
    actions: list[str]


class FactorAdmissionRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    filters: FilterValidationRequest = Field(default_factory=FilterValidationRequest)


class FactorAdmissionCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    action: str


class FactorAdmissionReport(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    checks: list[FactorAdmissionCheck]
    blockers: list[str]
    actions: list[str]


class FactorAdmissionQueueItem(BaseModel):
    rank: int
    symbol: str
    name: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    target_weight: float
    recommendation: str
    blockers: list[str]
    actions: list[str]


class FactorAdmissionQueueReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FactorAdmissionQueueItem]


class SensitivityScenarioResult(BaseModel):
    name: str
    interval: Literal["15m", "1h", "4h", "1d"]
    fee_bps: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trades: int
    pass_fail: Literal["pass", "watch", "fail"]
    notes: list[str]


class SensitivityTestRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    initial_capital: float = 10000


class SensitivityTestResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    pass_fail: Literal["pass", "watch", "fail"]
    return_spread: float
    drawdown_spread: float
    scenarios: list[SensitivityScenarioResult]
    actions: list[str]


class FactorLeaderboardItem(BaseModel):
    rank: int
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    composite_score: int
    robustness_score: int
    backtest_return: float | None = None
    backtest_drawdown: float | None = None
    forward_return: float | None = None
    stress_worst_return: float | None = None
    recommendation: str
    reasons: list[str]


class FactorLeaderboard(BaseModel):
    symbol: str
    generated_at: str
    summary: str
    items: list[FactorLeaderboardItem]


class FactorExposureItem(BaseModel):
    factor_id: str
    factor_name: str
    exposure_rate: float
    active_signals: int
    signal_changes: int


class FactorCorrelationPair(BaseModel):
    factor_a_id: str
    factor_a_name: str
    factor_b_id: str
    factor_b_name: str
    correlation: float
    overlap_rate: float
    status: Literal["pass", "watch", "fail"]


class FactorCorrelationReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    crowding_score: int
    max_correlation: float
    summary: str
    regime: MarketRegime
    exposures: list[FactorExposureItem]
    pairs: list[FactorCorrelationPair]
    actions: list[str]


class RegimeExposureItem(BaseModel):
    symbol: str
    allocation_status: Literal["pass", "watch", "fail"]
    correlation_status: Literal["pass", "watch", "fail"]
    risk_budget: float
    target_weight: float
    crowding_score: int
    dominant_factor: str | None = None
    status: Literal["pass", "watch", "fail"]
    notes: list[str]


class RegimeExposureReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    regime: MarketRegime
    total_target_weight: float
    average_risk_budget: float
    average_crowding_score: int
    summary: str
    items: list[RegimeExposureItem]
    actions: list[str]


class RegimeAttributionSlice(BaseModel):
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    bars: int
    total_return: float
    max_drawdown: float
    trades: int
    win_rate: float
    exposure_rate: float
    status: Literal["pass", "watch", "fail"]
    notes: list[str]


class RegimeAttributionReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    interval: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    best_regime: str | None = None
    worst_regime: str | None = None
    slices: list[RegimeAttributionSlice]
    actions: list[str]


class RegimeFactorFitItem(BaseModel):
    rank: int
    factor_id: str
    factor_name: str
    current_regime: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    status: Literal["pass", "watch", "fail"]
    score: int
    regime_fit: Literal["aligned", "neutral", "misaligned"]
    leaderboard_score: int
    robustness_score: int
    best_regime: str | None = None
    worst_regime: str | None = None
    recommendation: str
    reasons: list[str]
    actions: list[str]


class RegimeFactorFitReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    regime: MarketRegime
    summary: str
    items: list[RegimeFactorFitItem]


class ParameterOptimizationRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    limit: int = 220
    initial_capital: float = 10000
    fee_bps: float = 2.0


class ParameterOptimizationResult(BaseModel):
    rank: int
    min_rsi: float
    min_trend: int
    total_return: float
    max_drawdown: float
    trades: int
    exposure_rate: float
    score: int
    status: Literal["pass", "watch", "fail"]


class ParameterOptimizationReport(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    interval: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    best: ParameterOptimizationResult | None = None
    results: list[ParameterOptimizationResult]
    actions: list[str]


class StrategyLeaderboardItem(BaseModel):
    rank: int
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    average_backtest_return: float | None = None
    average_forward_return: float | None = None
    factor_count: int
    filter_count: int
    recommendation: str
    reasons: list[str]


class StrategyLeaderboard(BaseModel):
    generated_at: str
    summary: str
    items: list[StrategyLeaderboardItem]


class StrategyRegimeFitFactor(BaseModel):
    factor_id: str
    factor_name: str
    score: int
    status: Literal["pass", "watch", "fail"]
    regime_fit: Literal["aligned", "neutral", "misaligned"]


class StrategyRegimeFitItem(BaseModel):
    rank: int
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    current_regime: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    filter_status: Literal["pass", "watch", "fail"]
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    recommendation: str
    factor_fits: list[StrategyRegimeFitFactor]
    reasons: list[str]
    actions: list[str]


class StrategyRegimeFitReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    regime: MarketRegime
    summary: str
    items: list[StrategyRegimeFitItem]


class StrategyRegimeMatrixCell(BaseModel):
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    status: Literal["pass", "watch", "fail"]
    decision: Literal["open", "reduced", "paused", "blocked"]
    score: int
    rationale: list[str]


class StrategyRegimeMatrixItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    overall_status: Literal["pass", "watch", "fail"]
    cells: list[StrategyRegimeMatrixCell]
    actions: list[str]


class StrategyRegimeMatrixReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    current_regime: MarketRegime
    summary: str
    items: list[StrategyRegimeMatrixItem]
    actions: list[str]


class StrategyCrowdingPair(BaseModel):
    strategy_a_id: str
    strategy_a_name: str
    strategy_b_id: str
    strategy_b_name: str
    status: Literal["pass", "watch", "fail"]
    overlap_score: int
    shared_factors: list[str]
    filter_overlap: float
    current_decisions: list[Literal["open", "reduced", "paused", "blocked"]]
    detail: str
    action: str


class StrategyCrowdingReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    current_regime: MarketRegime
    summary: str
    pairs: list[StrategyCrowdingPair]
    actions: list[str]


class RegimeScenarioRiskItem(BaseModel):
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    status: Literal["pass", "watch", "fail"]
    score: int
    open_count: int
    reduced_count: int
    paused_count: int
    blocked_count: int
    average_strategy_score: float
    risk_budget: float
    summary: str
    actions: list[str]


class RegimeScenarioRiskReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    current_regime: MarketRegime
    summary: str
    items: list[RegimeScenarioRiskItem]
    actions: list[str]


class StrategyLifecycleItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    stage: Literal["draft", "validation", "candidate", "active", "paused"]
    status: Literal["pass", "watch", "fail"]
    score: int
    leaderboard_score: int | None = None
    promotion_score: int | None = None
    regime_score: int | None = None
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    blockers: list[str]
    actions: list[str]


class StrategyLifecycleReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StrategyLifecycleItem]
    actions: list[str]


class StrategyPromotionGate(BaseModel):
    strategy_id: str
    strategy_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    recommendation: str
    checks: list[ReadinessCheck]
    blockers: list[str]
    actions: list[str]


class StrategyValidationRequest(BaseModel):
    symbol_limit: int = 3
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_capital: float = 10000


class StrategyValidationResult(BaseModel):
    id: str
    strategy_id: str
    strategy_name: str
    generated_at: str
    regime: MarketRegime
    filter_validation: FilterValidationResult
    symbols: list[str]
    backtest_ids: list[str]
    forward_test_ids: list[str]
    average_backtest_return: float
    average_forward_return: float
    pass_fail: Literal["pass", "watch", "fail"]
    checks: list[str]


class StrategyValidationDecayItem(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    validation_id: str
    generated_at: str
    backtest_return: float
    forward_return: float
    decay: float
    decay_ratio: float | None = None
    symbol_count: int
    backtest_count: int
    forward_count: int
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    action: str


class StrategyValidationDecayReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyValidationDecayItem]
    actions: list[str]


class StrategyRiskControlItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    risk_control_count: int
    validation_rule_count: int
    gate_status: Literal["pass", "watch", "fail", "missing"]
    lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"]
    crowding_status: Literal["pass", "watch", "fail", "missing"]
    decay_status: Literal["pass", "watch", "fail", "missing"]
    blockers: list[str]
    actions: list[str]


class StrategyRiskControlReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyRiskControlItem]
    actions: list[str]


class MarketStateControlItem(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    detail: str
    action: str


class MarketStateControlReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    decision: Literal["expand", "observe", "reduce", "halt"]
    current_regime: MarketRegime
    summary: str
    items: list[MarketStateControlItem]
    actions: list[str]


class StrategyOperatingPlanItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    decision: Literal["open", "observe", "reduce", "block"]
    recommended_weight: float
    score: int
    gate_status: Literal["pass", "watch", "fail", "missing"]
    lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"]
    regime_decision: Literal["open", "reduced", "paused", "blocked", "missing"]
    backtest_status: Literal["pass", "watch", "fail", "missing"]
    forward_status: Literal["pass", "watch", "fail", "missing"]
    risk_control_status: Literal["pass", "watch", "fail", "missing"]
    reasons: list[str]
    actions: list[str]


class StrategyOperatingPlanReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    market_decision: Literal["expand", "observe", "reduce", "halt"]
    summary: str
    current_regime: MarketRegime
    items: list[StrategyOperatingPlanItem]
    actions: list[str]


class StrategyEvidencePackItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    operating_decision: Literal["open", "observe", "reduce", "block", "missing"]
    filter_valid: bool
    filter_match_count: int
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    validation_id: str | None = None
    backtest_return: float | None = None
    forward_return: float | None = None
    decay: float | None = None
    gate_status: Literal["pass", "watch", "fail", "missing"]
    risk_control_status: Literal["pass", "watch", "fail", "missing"]
    evidence: list[str]
    gaps: list[str]
    actions: list[str]


class StrategyEvidencePackReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyEvidencePackItem]
    actions: list[str]


class ReadinessCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    message: str


class ResearchReadiness(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    regime: MarketRegime
    checks: list[ReadinessCheck]
    blockers: list[str]
    actions: list[str]
    latest_validations: list[StrategyValidationResult]


class DataQualityReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    checks: list[ReadinessCheck]
    blockers: list[str]
    actions: list[str]


class DataFreshnessItem(BaseModel):
    dataset_id: str
    name: str
    status: Literal["pass", "watch", "fail"]
    rows: int
    latest_at: str | None = None
    age_hours: float | None = None
    sla_hours: int
    lag_score: int
    source: str
    action: str


class DataFreshnessReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[DataFreshnessItem]
    actions: list[str]


class ResearchCoverageItem(BaseModel):
    symbol: str
    name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    backtest_count: int
    forward_count: int
    walk_forward_count: int
    stress_count: int
    latest_run_at: str | None = None
    notes: list[str]
    actions: list[str]


class ResearchCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[ResearchCoverageItem]


class FactorCoverageItem(BaseModel):
    factor_id: str
    factor_name: str
    category: str
    status: Literal["pass", "watch", "fail"]
    score: int
    symbol_count: int
    backtest_symbols: int
    forward_symbols: int
    walk_forward_symbols: int
    stress_symbols: int
    latest_run_at: str | None = None
    missing_symbols: list[str]
    actions: list[str]


class FactorCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FactorCoverageItem]


class FactorLifecycleItem(BaseModel):
    factor_id: str
    factor_name: str
    category: str
    enabled: bool
    stage: Literal["idea", "research", "validation", "candidate", "active", "paused"]
    status: Literal["pass", "watch", "fail"]
    score: int
    coverage_score: int | None = None
    leaderboard_score: int | None = None
    admission_score: int | None = None
    latest_run_at: str | None = None
    blockers: list[str]
    actions: list[str]


class FactorLifecycleReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FactorLifecycleItem]
    actions: list[str]


class DataLineageNode(BaseModel):
    id: str
    name: str
    type: Literal["dataset", "factor", "strategy", "validation"]
    status: Literal["pass", "watch", "fail"]
    detail: str


class StrategyLineageItem(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_ids: list[str]
    datasets: list[str]
    validation_coverage: dict[str, int]
    missing: list[str]
    actions: list[str]


class DataLineageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    nodes: list[DataLineageNode]
    strategies: list[StrategyLineageItem]
    actions: list[str]


class ResearchRunAuditItem(BaseModel):
    id: str
    run_type: Literal["backtest", "forward", "walk_forward", "stress"]
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    primary_metric: float
    drawdown: float
    summary: str
    action: str


class ResearchRunAuditReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    total_runs: int
    pass_count: int
    watch_count: int
    fail_count: int
    items: list[ResearchRunAuditItem]
    actions: list[str]


class FactorCoverageBackfillRequest(BaseModel):
    factor_id: str = "momentum-news"
    symbol: str | None = None
    missing_only: bool = True
    max_tasks: int = 6


class FactorCoverageBackfillResult(BaseModel):
    id: str
    generated_at: str
    factor_id: str
    factor_name: str
    requested_symbols: list[str]
    executed_tasks: list[str]
    skipped: list[str]
    coverage: FactorCoverageReport


class RevalidationQueueItem(BaseModel):
    rank: int
    symbol: str
    name: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    priority: int
    due_tasks: list[str]
    latest_run_at: str | None = None
    reason: str
    actions: list[str]


class RevalidationQueueReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    stale_days: int
    items: list[RevalidationQueueItem]


class ResearchCoverageBackfillRequest(BaseModel):
    symbol: str | None = None
    factor_id: str = "momentum-news"
    missing_only: bool = True
    max_tasks: int = 4


class ResearchCoverageBackfillResult(BaseModel):
    id: str
    generated_at: str
    factor_id: str
    factor_name: str
    requested_symbols: list[str]
    executed_tasks: list[str]
    skipped: list[str]
    coverage: ResearchCoverageReport


class ResearchActionItem(BaseModel):
    priority: int
    source: str
    severity: Literal["pass", "watch", "fail"]
    title: str
    detail: str
    action: str


class ResearchActionPlan(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[ResearchActionItem]


class ResearchDashboardMetric(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    value: str
    detail: str


class ResearchDashboardSummary(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    metrics: list[ResearchDashboardMetric]


class ResearchHealthCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    detail: str


class ResearchHealthReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["go", "caution", "stop"]
    score: int
    summary: str
    checks: list[ResearchHealthCheck]
    blockers: list[str]
    actions: list[str]


class AlertRule(BaseModel):
    id: str
    symbol: str
    name: str
    metric: Literal["price", "rsi", "news_impact"]
    operator: Literal[">=", "<="]
    threshold: float
    enabled: bool
    created_at: str
    last_triggered_at: str | None = None


class AlertRuleCreate(BaseModel):
    symbol: str
    name: str
    metric: Literal["price", "rsi", "news_impact"]
    operator: Literal[">=", "<="]
    threshold: float
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    operator: Literal[">=", "<="] | None = None
    threshold: float | None = None
    enabled: bool | None = None


class AlertEvaluation(BaseModel):
    rule: AlertRule
    current_value: float
    triggered: bool
    message: str
    evaluated_at: str


class EventLog(BaseModel):
    id: str
    event_type: Literal["alert_triggered", "news_refresh", "automation_run", "forward_test", "walk_forward_test", "strategy_validation", "stress_test", "sensitivity_test", "research_pipeline", "research_backfill", "system"]
    severity: Literal["info", "watch", "risk", "error"]
    symbol: str | None
    title: str
    message: str
    created_at: str


class AutomationRunResult(BaseModel):
    id: str
    started_at: str
    finished_at: str
    news_results: list[NewsRefreshResult]
    alert_results: list[AlertEvaluation]
    triggered_alerts: int
    inserted_news: int


class AutomationRunRecord(BaseModel):
    id: str
    started_at: str
    finished_at: str
    status: Literal["completed", "warning", "failed"]
    news_sources: int
    inserted_news: int
    triggered_alerts: int
    summary: str


class AutomationSettings(BaseModel):
    enabled: bool
    interval_seconds: int
    next_run_hint: str | None = None


class FilterPreferences(BaseModel):
    signal_filter: Literal["all", "strong_buy", "buy", "neutral", "sell", "strong_sell"] = "all"
    rsi_min: int = 0
    positive_only: bool = False


class AppSettings(BaseModel):
    active_market_provider: str
    automation: AutomationSettings
    filters: FilterPreferences
    news_sources: int
    notification_channels: int
    watchlist_symbols: int


class AppSettingsUpdate(BaseModel):
    active_market_provider: Literal["mock", "binance", "coinbase"] | None = None
    automation_enabled: bool | None = None
    automation_interval_seconds: int | None = None
    signal_filter: Literal["all", "strong_buy", "buy", "neutral", "sell", "strong_sell"] | None = None
    rsi_min: int | None = None
    positive_only: bool | None = None


class NotificationChannel(BaseModel):
    id: str
    name: str
    type: Literal["webhook"]
    target: str
    enabled: bool
    created_at: str
    last_delivery_at: str | None = None
    last_error: str | None = None


class NotificationChannelCreate(BaseModel):
    name: str
    target: HttpUrl
    enabled: bool = True


class NotificationChannelUpdate(BaseModel):
    name: str | None = None
    target: HttpUrl | None = None
    enabled: bool | None = None


class NotificationDelivery(BaseModel):
    id: str
    channel_id: str
    event_id: str
    success: bool
    status_code: int | None
    error: str | None
    delivered_at: str


class PortfolioPositionCreate(BaseModel):
    symbol: str
    quantity: float
    average_price: float
    side: Literal["long", "short"] = "long"
    note: str = ""


class PortfolioPositionUpdate(BaseModel):
    symbol: str | None = None
    quantity: float | None = None
    average_price: float | None = None
    side: Literal["long", "short"] | None = None
    note: str | None = None


class PortfolioPosition(BaseModel):
    id: str
    symbol: str
    name: str
    side: Literal["long", "short"]
    quantity: float
    average_price: float
    current_price: float | None
    market_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_percent: float
    stale_price: bool
    note: str
    created_at: str


class PortfolioSummary(BaseModel):
    total_market_value: float
    total_cost_basis: float
    total_unrealized_pnl: float
    total_unrealized_pnl_percent: float
    long_exposure: float
    short_exposure: float
    positions: list[PortfolioPosition]


class PortfolioRiskContribution(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    weight_percent: float
    market_value: float
    unrealized_pnl: float
    risk_flag: Literal["ok", "watch", "risk"]


class PortfolioRiskReport(BaseModel):
    generated_at: str
    risk_level: Literal["ok", "watch", "risk"]
    gross_exposure: float
    net_exposure: float
    net_exposure_percent: float
    largest_position_symbol: str | None
    largest_position_weight: float
    concentration_score: float
    stale_price_count: int
    warnings: list[str]
    actions: list[str]
    contributions: list[PortfolioRiskContribution]


SYMBOLS = [
    ("AAPL", "Apple Inc.", "NASDAQ", 212.44, 1.28, "68.2M"),
    ("NVDA", "NVIDIA Corp.", "NASDAQ", 154.31, -0.74, "91.4M"),
    ("TSLA", "Tesla Inc.", "NASDAQ", 328.52, 2.46, "104.8M"),
    ("MSFT", "Microsoft Corp.", "NASDAQ", 498.18, 0.38, "29.1M"),
    ("BTCUSD", "Bitcoin / USD", "Crypto", 108420.0, 1.82, "41.6B"),
    ("ETHUSD", "Ether / USD", "Crypto", 3890.2, -1.16, "19.3B"),
    ("SPY", "S&P 500 ETF", "NYSE Arca", 612.84, 0.21, "52.7M"),
    ("QQQ", "Nasdaq 100 ETF", "NASDAQ", 541.63, 0.58, "44.5M"),
]

EVENT_TEMPLATES = [
    ("earnings", "bullish", "盘前财报预期上修", "机构上调收入预期，期权隐含波动率升高。", "MarketWire", 84),
    ("analyst", "bullish", "分析师上调目标价", "最新研报强调利润率改善和订单能见度。", "Street Brief", 68),
    ("macro", "mixed", "宏观数据影响风险偏好", "利率预期变化带动成长股和加密资产同步波动。", "Global Macro RSS", 61),
    ("regulatory", "bearish", "监管消息引发短线避险", "市场关注政策不确定性，成交量明显放大。", "Policy Feed", 78),
    ("product", "bullish", "新品发布形成催化", "社交热度和媒体报道同步升温。", "Tech Daily", 58),
    ("flow", "neutral", "ETF 资金流变化", "资金流入放缓，但中期趋势仍未破坏。", "Fund Flow Monitor", 52),
    ("crypto", "bearish", "链上风险提示", "大额转账和交易所净流入上升，短线波动风险扩大。", "Crypto Pulse", 74),
]


class MockMarketProvider:
    name = "mock"
    display_name = "Mock deterministic market data"

    def list_symbols(self) -> list[MarketSymbol]:
        symbols: list[MarketSymbol] = []
        for symbol, name, market, *_ in SYMBOLS:
            if market == "Crypto":
                asset_class: Literal["stock", "etf", "crypto"] = "crypto"
                currency = "USD"
            elif symbol in {"SPY", "QQQ"}:
                asset_class = "etf"
                currency = "USD"
            else:
                asset_class = "stock"
                currency = "USD"
            symbols.append(
                MarketSymbol(
                    symbol=symbol,
                    name=name,
                    asset_class=asset_class,
                    market=market,
                    currency=currency,
                    active=True,
                )
            )
        return symbols

    def watchlist(self) -> list[SymbolSnapshot]:
        snapshots: list[SymbolSnapshot] = []
        for idx, (symbol, name, market, price, change, volume) in enumerate(SYMBOLS):
            rsi = 48 + sin(idx * 1.7) * 20 + change * 1.7
            macd = round(sin(idx * 0.9) * 1.6 + change / 3, 2)
            score = max(8, min(98, int(54 + change * 10 + macd * 8)))
            signal: Literal["strong_buy", "buy", "neutral", "sell", "strong_sell"]
            if score >= 78:
                signal = "strong_buy"
            elif score >= 60:
                signal = "buy"
            elif score <= 24:
                signal = "strong_sell"
            elif score <= 42:
                signal = "sell"
            else:
                signal = "neutral"
            snapshots.append(
                SymbolSnapshot(
                    symbol=symbol,
                    name=name,
                    market=market,
                    price=price,
                    change_percent=change,
                    volume=volume,
                    signal=signal,
                    rsi=round(rsi, 1),
                    macd=macd,
                    trend_score=score,
                )
            )
        return snapshots

    def candles(self, symbol: str, interval: Literal["15m", "1h", "4h", "1d"], limit: int) -> list[Candle]:
        base_symbol = symbol.upper()
        base_price = next((item[3] for item in SYMBOLS if item[0] == base_symbol), 180.0)
        seed = sum(ord(char) for char in f"{base_symbol}:{interval}")
        random = Random(seed)
        step_minutes = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}[interval]
        start = datetime.now(timezone.utc) - timedelta(minutes=step_minutes * limit)

        closes: list[float] = []
        rows: list[Candle] = []
        previous = base_price * (0.92 + random.random() * 0.12)
        for index in range(limit):
            wave = sin(index / 8) * base_price * 0.006
            drift = (index - limit / 2) * base_price * 0.00009
            open_price = previous
            close = max(0.01, previous + wave + drift + random.uniform(-1, 1) * base_price * 0.006)
            high = max(open_price, close) + random.random() * base_price * 0.008
            low = min(open_price, close) - random.random() * base_price * 0.008
            closes.append(close)
            ma20 = sum(closes[-20:]) / min(len(closes), 20)
            ma60 = sum(closes[-60:]) / min(len(closes), 60)
            rsi = 50 + sin(index / 6) * 18 + random.uniform(-4, 4)
            rows.append(
                Candle(
                    time=(start + timedelta(minutes=step_minutes * index)).isoformat(),
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(close, 2),
                    volume=int(random.uniform(700_000, 8_500_000)),
                    ma20=round(ma20, 2),
                    ma60=round(ma60, 2),
                    rsi=round(max(5, min(95, rsi)), 1),
                )
            )
            previous = close
        return rows

    def indicators(self, symbol: str) -> list[IndicatorSummary]:
        seed = sum(ord(char) for char in symbol.upper())
        random = Random(seed)
        rsi = random.randint(42, 76)
        trend = random.randint(52, 91)
        volatility = random.uniform(1.2, 5.8)
        money_flow = random.randint(-18, 38)
        return [
            IndicatorSummary(label="RSI 14", value=str(rsi), state="up" if rsi > 55 else "flat", detail="动能偏强" if rsi > 55 else "中性震荡"),
            IndicatorSummary(label="MACD", value="金叉" if trend > 64 else "贴近零轴", state="up" if trend > 64 else "flat", detail="快线维持在慢线上方"),
            IndicatorSummary(label="波动率", value=f"{volatility:.1f}%", state="down" if volatility < 2.4 else "up", detail="按近 20 根K线估算"),
            IndicatorSummary(label="资金流", value=f"{money_flow:+d}", state="up" if money_flow > 0 else "down", detail="大单净流入评分"),
        ]

    def status(self, is_active: bool) -> MarketProviderStatus:
        return MarketProviderStatus(
            name=self.name,
            display_name=self.display_name,
            is_active=is_active,
            is_realtime=False,
            is_delayed=True,
            status="online",
            coverage=["US stocks", "ETFs", "Crypto"],
            capabilities=["symbols", "snapshots", "candles", "indicators", "backtest-fixtures"],
            message="Deterministic local data for development, demos, and offline testing.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


def fetch_json_url(url: str, timeout: int = 10) -> object:
    request = UrlRequest(url, headers={"User-Agent": "AppTrading/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


class BinanceMarketProvider:
    name = "binance"
    display_name = "Binance public crypto REST"
    base_url = "https://api.binance.com"
    symbol_map = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
    display_names = {"BTCUSD": "Bitcoin / USD", "ETHUSD": "Ether / USD"}

    def list_symbols(self) -> list[MarketSymbol]:
        return [
            MarketSymbol(symbol=symbol, name=self.display_names[symbol], asset_class="crypto", market="Binance", currency="USD", active=True)
            for symbol in self.symbol_map
        ]

    def _binance_symbol(self, symbol: str) -> str:
        base_symbol = symbol.upper()
        if base_symbol not in self.symbol_map:
            raise HTTPException(status_code=404, detail="Symbol is not supported by Binance provider")
        return self.symbol_map[base_symbol]

    def _ticker(self, symbol: str) -> dict[str, object]:
        binance_symbol = self._binance_symbol(symbol)
        try:
            data = fetch_json_url(f"{self.base_url}/api/v3/ticker/24hr?symbol={binance_symbol}")
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Binance ticker request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail="Unexpected Binance ticker response")
        return data

    def watchlist(self) -> list[SymbolSnapshot]:
        snapshots: list[SymbolSnapshot] = []
        for symbol, binance_symbol in self.symbol_map.items():
            data = self._ticker(symbol)
            price = float(data.get("lastPrice", 0) or 0)
            change = float(data.get("priceChangePercent", 0) or 0)
            quote_volume = float(data.get("quoteVolume", 0) or 0)
            volume = f"{quote_volume / 1_000_000_000:.1f}B" if quote_volume >= 1_000_000_000 else f"{quote_volume / 1_000_000:.1f}M"
            trend_score = max(5, min(95, int(52 + change * 5)))
            signal: Literal["strong_buy", "buy", "neutral", "sell", "strong_sell"]
            if trend_score >= 75:
                signal = "strong_buy"
            elif trend_score >= 58:
                signal = "buy"
            elif trend_score <= 25:
                signal = "strong_sell"
            elif trend_score <= 42:
                signal = "sell"
            else:
                signal = "neutral"
            snapshots.append(
                SymbolSnapshot(
                    symbol=symbol,
                    name=self.display_names[symbol],
                    market="Binance",
                    price=round(price, 2),
                    change_percent=round(change, 2),
                    volume=volume,
                    signal=signal,
                    rsi=round(max(5, min(95, 50 + change * 2)), 1),
                    macd=round(change / 3, 2),
                    trend_score=trend_score,
                )
            )
        return snapshots

    def candles(self, symbol: str, interval: Literal["15m", "1h", "4h", "1d"], limit: int) -> list[Candle]:
        binance_symbol = self._binance_symbol(symbol)
        interval_map = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        try:
            data = fetch_json_url(
                f"{self.base_url}/api/v3/klines?symbol={binance_symbol}&interval={interval_map[interval]}&limit={max(40, min(240, limit))}"
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Binance kline request failed: {exc}") from exc
        if not isinstance(data, list):
            raise HTTPException(status_code=502, detail="Unexpected Binance kline response")

        closes: list[float] = []
        rows: list[Candle] = []
        for item in data:
            if not isinstance(item, list) or len(item) < 6:
                continue
            close = float(item[4])
            closes.append(close)
            ma20 = sum(closes[-20:]) / min(len(closes), 20)
            ma60 = sum(closes[-60:]) / min(len(closes), 60)
            if len(closes) >= 15:
                gains = [max(0, closes[idx] - closes[idx - 1]) for idx in range(len(closes) - 14, len(closes))]
                losses = [max(0, closes[idx - 1] - closes[idx]) for idx in range(len(closes) - 14, len(closes))]
                avg_gain = sum(gains) / 14
                avg_loss = sum(losses) / 14
                rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
            else:
                rsi = 50
            rows.append(
                Candle(
                    time=datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc).isoformat(),
                    open=round(float(item[1]), 2),
                    high=round(float(item[2]), 2),
                    low=round(float(item[3]), 2),
                    close=round(close, 2),
                    volume=int(float(item[5])),
                    ma20=round(ma20, 2),
                    ma60=round(ma60, 2),
                    rsi=round(max(5, min(95, rsi)), 1),
                )
            )
        return rows

    def indicators(self, symbol: str) -> list[IndicatorSummary]:
        rows = self.candles(symbol, interval="1h", limit=80)
        latest = rows[-1]
        previous = rows[-2]
        trend_up = latest.close >= latest.ma20 >= latest.ma60
        volatility = (max(item.high for item in rows[-20:]) - min(item.low for item in rows[-20:])) / latest.close * 100
        macd_proxy = (latest.ma20 - latest.ma60) / latest.close * 100
        return [
            IndicatorSummary(label="RSI 14", value=str(latest.rsi), state="up" if latest.rsi > 55 else "down" if latest.rsi < 45 else "flat", detail="按 Binance 1h K线估算"),
            IndicatorSummary(label="趋势结构", value="多头排列" if trend_up else "震荡/转弱", state="up" if trend_up else "flat", detail="MA20 与 MA60 对比"),
            IndicatorSummary(label="波动率", value=f"{volatility:.1f}%", state="up" if volatility > 5 else "flat", detail="近 20 根 1h K线区间"),
            IndicatorSummary(label="MACD Proxy", value=f"{macd_proxy:+.2f}%", state="up" if macd_proxy > 0 else "down", detail=f"上一收盘 {previous.close:.2f}"),
        ]

    def status(self, is_active: bool) -> MarketProviderStatus:
        return MarketProviderStatus(
            name=self.name,
            display_name=self.display_name,
            is_active=is_active,
            is_realtime=False,
            is_delayed=False,
            status="online",
            coverage=["Crypto spot: BTCUSDT, ETHUSDT"],
            capabilities=["symbols", "24h ticker", "candles", "indicator-estimates", "backtest-data"],
            message="Uses Binance public REST endpoints. No API key required; availability depends on network and regional access.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


class CoinbaseMarketProvider:
    name = "coinbase"
    display_name = "Coinbase public crypto REST"
    base_url = "https://api.exchange.coinbase.com"
    product_map = {"BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}
    display_names = {"BTCUSD": "Bitcoin / USD", "ETHUSD": "Ether / USD"}

    def list_symbols(self) -> list[MarketSymbol]:
        return [
            MarketSymbol(symbol=symbol, name=self.display_names[symbol], asset_class="crypto", market="Coinbase", currency="USD", active=True)
            for symbol in self.product_map
        ]

    def _product(self, symbol: str) -> str:
        base_symbol = symbol.upper()
        if base_symbol not in self.product_map:
            raise HTTPException(status_code=404, detail="Symbol is not supported by Coinbase provider")
        return self.product_map[base_symbol]

    def _get(self, path: str) -> object:
        try:
            return fetch_json_url(f"{self.base_url}{path}")
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Coinbase request failed: {exc}") from exc

    def watchlist(self) -> list[SymbolSnapshot]:
        snapshots: list[SymbolSnapshot] = []
        for symbol, product in self.product_map.items():
            ticker = self._get(f"/products/{product}/ticker")
            stats = self._get(f"/products/{product}/stats")
            if not isinstance(ticker, dict) or not isinstance(stats, dict):
                raise HTTPException(status_code=502, detail="Unexpected Coinbase response")
            price = float(ticker.get("price", 0) or 0)
            open_price = float(stats.get("open", price) or price)
            volume = float(stats.get("volume", 0) or 0)
            change = (price / open_price - 1) * 100 if open_price else 0
            notional = volume * price
            volume_label = f"{notional / 1_000_000_000:.1f}B" if notional >= 1_000_000_000 else f"{notional / 1_000_000:.1f}M"
            trend_score = max(5, min(95, int(52 + change * 5)))
            signal: Literal["strong_buy", "buy", "neutral", "sell", "strong_sell"]
            if trend_score >= 75:
                signal = "strong_buy"
            elif trend_score >= 58:
                signal = "buy"
            elif trend_score <= 25:
                signal = "strong_sell"
            elif trend_score <= 42:
                signal = "sell"
            else:
                signal = "neutral"
            snapshots.append(
                SymbolSnapshot(
                    symbol=symbol,
                    name=self.display_names[symbol],
                    market="Coinbase",
                    price=round(price, 2),
                    change_percent=round(change, 2),
                    volume=volume_label,
                    signal=signal,
                    rsi=round(max(5, min(95, 50 + change * 2)), 1),
                    macd=round(change / 3, 2),
                    trend_score=trend_score,
                )
            )
        return snapshots

    def candles(self, symbol: str, interval: Literal["15m", "1h", "4h", "1d"], limit: int) -> list[Candle]:
        product = self._product(symbol)
        granularity = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[interval]
        data = self._get(f"/products/{product}/candles?granularity={granularity}")
        if not isinstance(data, list):
            raise HTTPException(status_code=502, detail="Unexpected Coinbase candle response")
        rows_raw = sorted([item for item in data if isinstance(item, list) and len(item) >= 6], key=lambda item: item[0])[-max(40, min(240, limit)) :]
        closes: list[float] = []
        rows: list[Candle] = []
        for item in rows_raw:
            close = float(item[4])
            closes.append(close)
            ma20 = sum(closes[-20:]) / min(len(closes), 20)
            ma60 = sum(closes[-60:]) / min(len(closes), 60)
            if len(closes) >= 15:
                gains = [max(0, closes[idx] - closes[idx - 1]) for idx in range(len(closes) - 14, len(closes))]
                losses = [max(0, closes[idx - 1] - closes[idx]) for idx in range(len(closes) - 14, len(closes))]
                avg_gain = sum(gains) / 14
                avg_loss = sum(losses) / 14
                rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
            else:
                rsi = 50
            rows.append(
                Candle(
                    time=datetime.fromtimestamp(int(item[0]), tz=timezone.utc).isoformat(),
                    low=round(float(item[1]), 2),
                    high=round(float(item[2]), 2),
                    open=round(float(item[3]), 2),
                    close=round(close, 2),
                    volume=int(float(item[5])),
                    ma20=round(ma20, 2),
                    ma60=round(ma60, 2),
                    rsi=round(max(5, min(95, rsi)), 1),
                )
            )
        return rows

    def indicators(self, symbol: str) -> list[IndicatorSummary]:
        rows = self.candles(symbol, interval="1h", limit=80)
        latest = rows[-1]
        previous = rows[-2]
        trend_up = latest.close >= latest.ma20 >= latest.ma60
        volatility = (max(item.high for item in rows[-20:]) - min(item.low for item in rows[-20:])) / latest.close * 100
        macd_proxy = (latest.ma20 - latest.ma60) / latest.close * 100
        return [
            IndicatorSummary(label="RSI 14", value=str(latest.rsi), state="up" if latest.rsi > 55 else "down" if latest.rsi < 45 else "flat", detail="按 Coinbase 1h K线估算"),
            IndicatorSummary(label="趋势结构", value="多头排列" if trend_up else "震荡/转弱", state="up" if trend_up else "flat", detail="MA20 与 MA60 对比"),
            IndicatorSummary(label="波动率", value=f"{volatility:.1f}%", state="up" if volatility > 5 else "flat", detail="近 20 根 1h K线区间"),
            IndicatorSummary(label="MACD Proxy", value=f"{macd_proxy:+.2f}%", state="up" if macd_proxy > 0 else "down", detail=f"上一收盘 {previous.close:.2f}"),
        ]

    def status(self, is_active: bool) -> MarketProviderStatus:
        return MarketProviderStatus(
            name=self.name,
            display_name=self.display_name,
            is_active=is_active,
            is_realtime=False,
            is_delayed=False,
            status="online",
            coverage=["Crypto spot: BTC-USD, ETH-USD"],
            capabilities=["symbols", "ticker", "stats", "candles", "indicator-estimates", "backtest-data"],
            message="Uses Coinbase Exchange public REST endpoints. No API key required.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


MARKET_PROVIDERS: dict[str, MarketProvider] = {
    "mock": MockMarketProvider(),
    "binance": BinanceMarketProvider(),
    "coinbase": CoinbaseMarketProvider(),
}

DEFAULT_FACTORS = [
    ("momentum-news", "新闻确认动量", "动量", "20周期动量为正，且近 24 小时新闻情绪不为负面。", "long", "3-10 days"),
    ("vol-compression", "波动率压缩突破", "波动率", "ATR 收窄后放量突破前高，适合观察趋势延续。", "long", "1-5 days"),
    ("rsi-reversion", "RSI 均值回归", "反转", "RSI 高于 70 后若新闻影响走弱，测试短线回撤概率。", "short", "1-3 days"),
    ("event-drift", "事件后漂移", "事件驱动", "高影响新闻后一小时保持强势，测试后续漂移。", "long", "1-7 days"),
]

DEFAULT_STRATEGIES = [
    (
        "trend-news-confirmation",
        "趋势新闻确认",
        "趋势跟随",
        "用动量和正向新闻过滤趋势延续机会，适合风险偏建设性或趋势市。",
        ["momentum-news", "event-drift"],
        {"min_change": 0.0, "min_rsi": 52, "min_trend": 55, "signal": "buy"},
        ["单标的最大风险 1%", "若市场状态为 risk_off 则降仓", "连续两次前向测试失败后停用"],
        ["前向收益不低于回测收益 35%", "最大回撤低于 18%", "样本命中不少于 2 个标的"],
    ),
    (
        "volatility-breakout",
        "波动压缩突破",
        "波动率",
        "观察波动率压缩后的放量突破，强调成交活跃和趋势确认。",
        ["vol-compression"],
        {"min_change": -1.0, "min_rsi": 45, "min_trend": 50},
        ["突破失败后一根 K 线退出", "高波动市场缩小仓位", "回测暴露高于 80% 时重新校准"],
        ["前向最大回撤低于 12%", "交易次数不少于 3", "市场状态非 risk_off"],
    ),
    (
        "mean-reversion-watch",
        "超跌反转观察",
        "反转",
        "在震荡或风险缓和时测试 RSI 回归，主要用于观察，不默认追涨。",
        ["rsi-reversion"],
        {"max_rsi": 45, "signal": "neutral"},
        ["只在 range 市场启用", "跌破最近低点退出", "新闻风险为 bearish 时跳过"],
        ["前向收益为正", "最大回撤低于 10%", "过滤器不能与趋势追踪模板混用"],
    ),
]

AGENT_TEMPLATES: dict[str, dict[str, object]] = {
    "market_monitor": {
        "name": "Market Monitor",
        "description": "监控价格、趋势、成交和异常波动，并生成可核验的盯盘结论。",
        "interval": 60,
        "triggers": ["manual", "schedule", "alert_triggered"],
        "settings": {"min_trend_score": 65, "change_threshold": 1.5, "min_confidence": 60},
    },
    "news_analyst": {
        "name": "News Analyst",
        "description": "分析新闻影响、情绪、关联标的和潜在价格催化。",
        "interval": 300,
        "triggers": ["manual", "schedule", "high_impact_news"],
        "settings": {"min_impact_score": 60, "news_limit": 8, "min_confidence": 60},
    },
    "risk_sentinel": {
        "name": "Risk Sentinel",
        "description": "监控组合敞口、回撤、波动、指标破位和负面新闻风险。",
        "interval": 120,
        "triggers": ["manual", "schedule", "alert_triggered", "high_impact_news"],
        "settings": {"max_drawdown_percent": 5, "rsi_high": 75, "rsi_low": 25, "min_confidence": 65},
    },
    "factor_research": {
        "name": "Factor Research",
        "description": "结合市场状态、新闻和研究结果提出可审批的因子测试建议。",
        "interval": 1800,
        "triggers": ["manual", "schedule", "backtest_completed"],
        "settings": {"min_factor_score": 65, "backtest_interval": "1h", "min_confidence": 60},
    },
    "briefing": {
        "name": "Briefing",
        "description": "汇总市场广度、异动、新闻、组合风险和下一步动作。",
        "interval": 900,
        "triggers": ["manual", "schedule"],
        "settings": {"news_limit": 6, "mover_limit": 5, "min_confidence": 60},
    },
}


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with db_connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'rss',
                url TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                symbols TEXT NOT NULL DEFAULT '',
                poll_interval_seconds INTEGER NOT NULL DEFAULT 900,
                last_fetched_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_articles (
                id TEXT PRIMARY KEY,
                source_id INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                symbols TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                impact_score INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES news_sources(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_articles_symbol_time ON news_articles(symbols, published_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_articles_source ON news_articles(source_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                factor_id TEXT NOT NULL,
                factor_name TEXT NOT NULL,
                interval TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                final_equity REAL NOT NULL,
                total_return REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                sharpe REAL NOT NULL,
                win_rate REAL NOT NULL,
                trades INTEGER NOT NULL,
                exposure REAL NOT NULL,
                created_at TEXT NOT NULL,
                equity_curve TEXT NOT NULL,
                signal_points TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol_time ON backtest_runs(symbol, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_definitions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                signal TEXT NOT NULL,
                horizon TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        created_at = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            """
            INSERT OR IGNORE INTO factor_definitions (
                id, name, category, description, signal, horizon, enabled, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            [(factor_id, name, category, description, signal, horizon, created_at) for factor_id, name, category, description, signal, horizon in DEFAULT_FACTORS],
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO app_config (key, value, updated_at)
            VALUES ('active_market_provider', 'mock', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO app_config (key, value, updated_at)
            VALUES ('automation_enabled', 'false', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO app_config (key, value, updated_at)
            VALUES ('automation_interval_seconds', '300', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_rules (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                metric TEXT NOT NULL,
                operator TEXT NOT NULL,
                threshold REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_triggered_at TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_alert_rules_symbol ON alert_rules(symbol, enabled)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_logs (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                symbol TEXT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_event_logs_created_at ON event_logs(created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                news_sources INTEGER NOT NULL,
                inserted_news INTEGER NOT NULL,
                triggered_alerts INTEGER NOT NULL,
                summary TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_automation_runs_finished_at ON automation_runs(finished_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_channels (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                target TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_delivery_at TEXT,
                last_error TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                success INTEGER NOT NULL,
                status_code INTEGER,
                error TEXT,
                delivered_at TEXT NOT NULL,
                FOREIGN KEY(channel_id) REFERENCES notification_channels(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_notification_deliveries_channel ON notification_deliveries(channel_id, delivered_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                average_price REAL NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_positions_symbol ON portfolio_positions(symbol)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist_items (
                provider TEXT NOT NULL,
                symbol TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(provider, symbol)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_provider ON watchlist_items(provider, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS screener_presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                filters TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_screener_presets_created_at ON screener_presets(created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                factor_ids TEXT NOT NULL,
                default_filters TEXT NOT NULL,
                risk_controls TEXT NOT NULL,
                validation_rules TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO strategy_templates (
                id, name, category, description, factor_ids, default_filters,
                risk_controls, validation_rules, enabled, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            [
                (
                    strategy_id,
                    name,
                    category,
                    description,
                    json.dumps(factor_ids, ensure_ascii=False),
                    json.dumps(default_filters, ensure_ascii=False),
                    json.dumps(risk_controls, ensure_ascii=False),
                    json.dumps(validation_rules, ensure_ascii=False),
                    created_at,
                )
                for strategy_id, name, category, description, factor_ids, default_filters, risk_controls, validation_rules in DEFAULT_STRATEGIES
            ],
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_strategy_templates_enabled ON strategy_templates(enabled, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forward_test_runs (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                factor_id TEXT NOT NULL,
                factor_name TEXT NOT NULL,
                interval TEXT NOT NULL,
                train_bars INTEGER NOT NULL,
                forward_bars INTEGER NOT NULL,
                train_return REAL NOT NULL,
                forward_return REAL NOT NULL,
                degradation REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                pass_fail TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                equity_curve TEXT NOT NULL,
                checks TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forward_test_runs_symbol_time ON forward_test_runs(symbol, generated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS walk_forward_runs (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                factor_id TEXT NOT NULL,
                factor_name TEXT NOT NULL,
                interval TEXT NOT NULL,
                train_bars INTEGER NOT NULL,
                forward_bars INTEGER NOT NULL,
                window_count INTEGER NOT NULL,
                pass_rate REAL NOT NULL,
                average_forward_return REAL NOT NULL,
                worst_forward_return REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                stability_score INTEGER NOT NULL,
                pass_fail TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                windows TEXT NOT NULL,
                checks TEXT NOT NULL,
                actions TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_walk_forward_runs_symbol_time ON walk_forward_runs(symbol, generated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_validation_runs (
                id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                regime TEXT NOT NULL,
                filter_validation TEXT NOT NULL,
                symbols TEXT NOT NULL,
                backtest_ids TEXT NOT NULL,
                forward_test_ids TEXT NOT NULL,
                average_backtest_return REAL NOT NULL,
                average_forward_return REAL NOT NULL,
                pass_fail TEXT NOT NULL,
                checks TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_strategy_validation_runs_strategy_time ON strategy_validation_runs(strategy_id, generated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stress_test_runs (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                factor_id TEXT NOT NULL,
                factor_name TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                base_return REAL NOT NULL,
                base_drawdown REAL NOT NULL,
                worst_return REAL NOT NULL,
                worst_drawdown REAL NOT NULL,
                pass_fail TEXT NOT NULL,
                scenarios TEXT NOT NULL,
                actions TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_stress_test_runs_symbol_time ON stress_test_runs(symbol, generated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_regime_snapshots (
                id TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL,
                label TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                breadth_score REAL NOT NULL,
                volatility_score REAL NOT NULL,
                trend_score REAL NOT NULL,
                liquidity_score REAL NOT NULL,
                summary TEXT NOT NULL,
                evidence TEXT NOT NULL,
                actions TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_market_regime_snapshots_time ON market_regime_snapshots(generated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_iterations INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_role_enabled ON users(role, enabled)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                csrf_token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, expires_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                detail TEXT NOT NULL,
                before_state TEXT,
                after_state TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_logs(created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_provider_profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL, model TEXT NOT NULL,
                api_key_env TEXT NOT NULL, timeout_seconds INTEGER NOT NULL, temperature REAL NOT NULL,
                max_tokens INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_configs (
                id TEXT PRIMARY KEY, template_type TEXT NOT NULL, builtin INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL, description TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                runtime_mode TEXT NOT NULL, provider_id TEXT, symbol_scope TEXT NOT NULL, symbols TEXT NOT NULL,
                schedule_enabled INTEGER NOT NULL DEFAULT 0, interval_seconds INTEGER NOT NULL,
                triggers TEXT NOT NULL, system_prompt TEXT NOT NULL, task_prompt TEXT NOT NULL,
                settings TEXT NOT NULL, notification_channel_ids TEXT NOT NULL, fallback_to_rules INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT, next_run_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(provider_id) REFERENCES agent_provider_profiles(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_configs_schedule ON agent_configs(enabled, schedule_enabled, next_run_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, agent_name TEXT NOT NULL, trigger TEXT NOT NULL,
                symbol TEXT, status TEXT NOT NULL, runtime_mode TEXT NOT NULL, input_payload TEXT NOT NULL,
                output TEXT, error TEXT, model TEXT, prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0,
                test INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                FOREIGN KEY(agent_id) REFERENCES agent_configs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_time ON agent_runs(agent_id, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_queue ON agent_runs(status, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_proposals (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, agent_id TEXT NOT NULL, resource_type TEXT NOT NULL,
                action TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                reviewed_by TEXT, review_note TEXT, created_at TEXT NOT NULL, reviewed_at TEXT,
                FOREIGN KEY(run_id) REFERENCES agent_runs(id), FOREIGN KEY(agent_id) REFERENCES agent_configs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_proposals_status ON agent_proposals(status, created_at)")
        agent_created_at = datetime.now(timezone.utc).isoformat()
        for template_type, template in AGENT_TEMPLATES.items():
            interval = int(template["interval"])
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_configs (
                    id, template_type, builtin, name, description, enabled, runtime_mode, provider_id,
                    symbol_scope, symbols, schedule_enabled, interval_seconds, triggers, system_prompt,
                    task_prompt, settings, notification_channel_ids, fallback_to_rules, last_run_at,
                    next_run_at, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, 1, 'rule', NULL, 'watchlist', '[]', 0, ?, ?, ?, ?, ?, '[]', 1, NULL, NULL, ?, ?)
                """,
                (
                    template_type, template_type, template["name"], template["description"], interval,
                    json.dumps(template["triggers"], ensure_ascii=False),
                    "你是量化研究平台中的专业分析 Agent。只能依据提供的数据输出结论，并明确证据不足。",
                    "分析当前上下文，输出摘要、风险级别、置信度、证据和建议动作。",
                    json.dumps(template["settings"], ensure_ascii=False), agent_created_at, agent_created_at,
                ),
            )
        ensure_column(connection, "admin_audit_logs", "before_state", "TEXT")
        ensure_column(connection, "admin_audit_logs", "after_state", "TEXT")
        ensure_column(connection, "watchlist_items", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "screener_presets", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "portfolio_positions", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "news_articles", "enabled", "INTEGER NOT NULL DEFAULT 1")
        for table in ("backtest_runs", "forward_test_runs", "walk_forward_runs", "strategy_validation_runs", "stress_test_runs"):
            ensure_column(connection, table, "request_payload", "TEXT")


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
    return token, AuthSessionInfo(user=user, csrf_token=csrf_token, expires_at=expires.isoformat())


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
    "news-sources": {"name": "新闻源", "category": "交易配置", "table": "news_sources", "id": "id", "search": ["name", "url", "symbols"], "order": "created_at", "actions": ["view", "edit", "enable", "refresh", "bulk-status"], "status": "enabled"},
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
    )


def list_admin_resources() -> list[AdminResourceDescriptor]:
    init_db()
    return [admin_resource_descriptor(resource_type) for resource_type in ADMIN_RESOURCE_CONFIG]


def query_admin_resource(
    resource_type: str,
    page: int = 1,
    page_size: int = 25,
    query: str = "",
    enabled: bool | None = None,
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


async def automation_loop() -> None:
    while True:
        settings = get_automation_settings()
        if not settings.enabled:
            await asyncio.sleep(5)
            continue
        try:
            await asyncio.to_thread(run_automation_cycle)
        except Exception as exc:  # noqa: BLE001 - keep scheduler alive and record failures
            add_event_log(
                event_type="system",
                severity="error",
                title="自动化盯盘失败",
                message=str(exc)[:240],
            )
        await asyncio.sleep(settings.interval_seconds)


def get_active_market_provider_name() -> str:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT value FROM app_config WHERE key = 'active_market_provider'").fetchone()
    provider = row["value"] if row else "mock"
    return provider if provider in MARKET_PROVIDERS else "mock"


def get_market_provider() -> MarketProvider:
    return MARKET_PROVIDERS[get_active_market_provider_name()]


def set_active_market_provider(provider: str) -> MarketProviderStatus:
    if provider not in MARKET_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported market provider")
    init_db()
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO app_config (key, value, updated_at)
            VALUES ('active_market_provider', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (provider, datetime.now(timezone.utc).isoformat()),
        )
    return MARKET_PROVIDERS[provider].status(is_active=True)


def persisted_watchlist_symbols(provider_name: str) -> list[str]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT symbol
            FROM watchlist_items
            WHERE provider = ? AND enabled = 1
            ORDER BY created_at, symbol
            """,
            (provider_name,),
        ).fetchall()
    return [row["symbol"] for row in rows]


def ensure_watchlist_initialized(provider_name: str) -> list[str]:
    symbols = persisted_watchlist_symbols(provider_name)
    with db_connect() as connection:
        initialized = connection.execute("SELECT 1 FROM watchlist_items WHERE provider = ? LIMIT 1", (provider_name,)).fetchone()
    if initialized:
        return symbols

    provider = MARKET_PROVIDERS[provider_name]
    created_at = datetime.now(timezone.utc).isoformat()
    default_symbols = [item.symbol for item in provider.list_symbols()]
    with db_connect() as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO watchlist_items (provider, symbol, created_at)
            VALUES (?, ?, ?)
            """,
            [(provider_name, symbol, created_at) for symbol in default_symbols],
        )
    return default_symbols


def list_watchlist_snapshots() -> list[SymbolSnapshot]:
    provider_name = get_active_market_provider_name()
    provider = MARKET_PROVIDERS[provider_name]
    symbols = ensure_watchlist_initialized(provider_name)
    order = {symbol: index for index, symbol in enumerate(symbols)}
    try:
        snapshots = [item for item in provider.watchlist() if item.symbol in order]
    except HTTPException:
        if provider_name == "mock":
            raise
        fallback = MARKET_PROVIDERS["mock"]
        fallback_symbols = ensure_watchlist_initialized("mock")
        fallback_order = {symbol: index for index, symbol in enumerate(fallback_symbols)}
        snapshots = [item for item in fallback.watchlist() if item.symbol in fallback_order]
        return sorted(snapshots, key=lambda item: fallback_order[item.symbol])
    return sorted(snapshots, key=lambda item: order[item.symbol])


def add_watchlist_symbol(payload: WatchlistItemCreate) -> SymbolSnapshot:
    provider_name = get_active_market_provider_name()
    provider = MARKET_PROVIDERS[provider_name]
    valid_symbols = {item.symbol for item in provider.list_symbols()}
    symbol = payload.symbol.strip().upper()
    if symbol not in valid_symbols:
        raise HTTPException(status_code=400, detail="Symbol is not supported by active market provider")

    ensure_watchlist_initialized(provider_name)
    with db_connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO watchlist_items (provider, symbol, created_at)
            VALUES (?, ?, ?)
            """,
            (provider_name, symbol, datetime.now(timezone.utc).isoformat()),
        )
        connection.execute("UPDATE watchlist_items SET enabled = 1 WHERE provider = ? AND symbol = ?", (provider_name, symbol))

    snapshot = next((item for item in provider.watchlist() if item.symbol == symbol), None)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Symbol snapshot is unavailable")
    add_event_log(
        event_type="system",
        severity="info",
        symbol=symbol,
        title="自选标的已添加",
        message=f"{symbol} 已加入 {provider.display_name} 自选观察。",
    )
    return snapshot


def delete_watchlist_symbol(symbol: str) -> dict[str, bool]:
    provider_name = get_active_market_provider_name()
    normalized = symbol.strip().upper()
    valid_symbols = {item.symbol for item in MARKET_PROVIDERS[provider_name].list_symbols()}
    if normalized not in valid_symbols:
        raise HTTPException(status_code=404, detail="Symbol is not supported by active market provider")

    ensure_watchlist_initialized(provider_name)
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE watchlist_items SET enabled = 0 WHERE provider = ? AND symbol = ?",
            (provider_name, normalized),
        )
    add_event_log(
        event_type="system",
        severity="info",
        symbol=normalized,
        title="自选标的已停用",
        message=f"{normalized} 已从自选观察停用，可在管理中心恢复。",
    )
    return {"deleted": False, "disabled": cursor.rowcount > 0}


def classify_breadth_risk(average_change: float, advance_decline_ratio: float, average_rsi: float) -> Literal["risk", "watch", "neutral", "constructive"]:
    if average_change <= -1.25 or advance_decline_ratio < 0.65 or average_rsi < 35:
        return "risk"
    if average_change < -0.25 or advance_decline_ratio < 0.9 or average_rsi < 42:
        return "watch"
    if average_change >= 0.5 and advance_decline_ratio >= 1.25 and average_rsi >= 48:
        return "constructive"
    return "neutral"


def build_breadth_group(name: str, snapshots: list[SymbolSnapshot]) -> MarketBreadthGroup:
    count = len(snapshots)
    advancers = sum(1 for item in snapshots if item.change_percent > 0)
    decliners = sum(1 for item in snapshots if item.change_percent < 0)
    unchanged = count - advancers - decliners
    average_change = sum(item.change_percent for item in snapshots) / count if count else 0
    average_rsi = sum(item.rsi for item in snapshots) / count if count else 0
    average_trend = sum(item.trend_score for item in snapshots) / count if count else 0
    bullish_signals = sum(1 for item in snapshots if item.signal in {"strong_buy", "buy"})
    bearish_signals = sum(1 for item in snapshots if item.signal in {"sell", "strong_sell"})
    ratio = advancers / max(1, decliners)
    return MarketBreadthGroup(
        name=name,
        count=count,
        advancers=advancers,
        decliners=decliners,
        unchanged=unchanged,
        average_change=round(average_change, 2),
        average_rsi=round(average_rsi, 1),
        average_trend_score=round(average_trend, 1),
        bullish_signals=bullish_signals,
        bearish_signals=bearish_signals,
        risk_level=classify_breadth_risk(average_change, ratio, average_rsi),
    )


def get_market_breadth() -> MarketBreadth:
    snapshots = list_watchlist_snapshots()
    total = len(snapshots)
    advancers = sum(1 for item in snapshots if item.change_percent > 0)
    decliners = sum(1 for item in snapshots if item.change_percent < 0)
    unchanged = total - advancers - decliners
    average_change = sum(item.change_percent for item in snapshots) / total if total else 0
    average_rsi = sum(item.rsi for item in snapshots) / total if total else 0
    average_trend = sum(item.trend_score for item in snapshots) / total if total else 0
    ratio = advancers / max(1, decliners)
    signal_counts = {
        "strong_buy": sum(1 for item in snapshots if item.signal == "strong_buy"),
        "buy": sum(1 for item in snapshots if item.signal == "buy"),
        "neutral": sum(1 for item in snapshots if item.signal == "neutral"),
        "sell": sum(1 for item in snapshots if item.signal == "sell"),
        "strong_sell": sum(1 for item in snapshots if item.signal == "strong_sell"),
    }
    grouped: dict[str, list[SymbolSnapshot]] = {}
    for item in snapshots:
        grouped.setdefault(item.market, []).append(item)
    groups = [build_breadth_group(name, items) for name, items in sorted(grouped.items())]
    return MarketBreadth(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total=total,
        advancers=advancers,
        decliners=decliners,
        unchanged=unchanged,
        advance_decline_ratio=round(ratio, 2),
        average_change=round(average_change, 2),
        average_rsi=round(average_rsi, 1),
        average_trend_score=round(average_trend, 1),
        strong_buy_count=signal_counts["strong_buy"],
        buy_count=signal_counts["buy"],
        neutral_count=signal_counts["neutral"],
        sell_count=signal_counts["sell"],
        strong_sell_count=signal_counts["strong_sell"],
        risk_level=classify_breadth_risk(average_change, ratio, average_rsi),
        groups=groups,
    )


def run_screener(
    min_change: float | None = None,
    max_change: float | None = None,
    min_rsi: float | None = None,
    max_rsi: float | None = None,
    min_trend: int | None = None,
    signal: str | None = None,
    market: str | None = None,
    limit: int = 20,
) -> ScreenerResult:
    valid_signals = {"strong_buy", "buy", "neutral", "sell", "strong_sell"}
    selected_signal = signal if signal in valid_signals else None
    selected_market = market.strip().lower() if market else None
    matches: list[ScreenerMatch] = []
    for snapshot in list_watchlist_snapshots():
        if min_change is not None and snapshot.change_percent < min_change:
            continue
        if max_change is not None and snapshot.change_percent > max_change:
            continue
        if min_rsi is not None and snapshot.rsi < min_rsi:
            continue
        if max_rsi is not None and snapshot.rsi > max_rsi:
            continue
        if min_trend is not None and snapshot.trend_score < min_trend:
            continue
        if selected_signal and snapshot.signal != selected_signal:
            continue
        if selected_market and selected_market not in snapshot.market.lower():
            continue

        reasons: list[str] = []
        if snapshot.change_percent > 0:
            reasons.append(f"上涨 {snapshot.change_percent:+.2f}%")
        else:
            reasons.append(f"回落 {snapshot.change_percent:+.2f}%")
        reasons.append(f"RSI {snapshot.rsi:.1f}")
        reasons.append(f"趋势 {snapshot.trend_score}")
        reasons.append(f"信号 {snapshot.signal}")
        matches.append(ScreenerMatch(snapshot=snapshot, reasons=reasons))

    matches.sort(key=lambda item: (item.snapshot.trend_score, item.snapshot.change_percent), reverse=True)
    active_filters: dict[str, str | float] = {}
    if min_change is not None:
        active_filters["min_change"] = min_change
    if max_change is not None:
        active_filters["max_change"] = max_change
    if min_rsi is not None:
        active_filters["min_rsi"] = min_rsi
    if max_rsi is not None:
        active_filters["max_rsi"] = max_rsi
    if min_trend is not None:
        active_filters["min_trend"] = float(min_trend)
    if selected_signal:
        active_filters["signal"] = selected_signal
    if selected_market:
        active_filters["market"] = selected_market
    return ScreenerResult(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total=len(matches),
        filters=active_filters,
        items=matches[: max(1, min(100, limit))],
    )


def strategy_candidate_report(
    min_change: float | None = None,
    max_change: float | None = None,
    min_rsi: float | None = None,
    max_rsi: float | None = None,
    min_trend: int | None = None,
    signal: str | None = None,
    market: str | None = None,
    factor_id: str | None = None,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 6,
) -> StrategyCandidateReport:
    max_items = max(1, min(12, limit))
    screener_result = run_screener(
        min_change=min_change,
        max_change=max_change,
        min_rsi=min_rsi,
        max_rsi=max_rsi,
        min_trend=min_trend,
        signal=signal,
        market=market,
        limit=max(3, max_items),
    )
    factors = list_factor_definitions(enabled_only=True)
    if factor_id:
        factors = [get_factor_definition(factor_id, require_enabled=True)]
    candidates: list[StrategyCandidateItem] = []
    for match in screener_result.items:
        for factor in factors:
            readiness = trade_readiness_report(
                symbol=match.snapshot.symbol,
                factor_id=factor.id,
                interval=interval,
                min_rsi=min_rsi,
                min_trend=min_trend,
                signal=signal,
            )
            score = readiness.score
            if readiness.decision == "go":
                score += 8
            elif readiness.decision == "size_down":
                score += 2
            elif readiness.decision == "block":
                score -= 18
            score += min(6, max(-6, int(match.snapshot.change_percent)))
            candidates.append(
                StrategyCandidateItem(
                    rank=0,
                    symbol=match.snapshot.symbol,
                    name=match.snapshot.name,
                    market=match.snapshot.market,
                    factor_id=factor.id,
                    factor_name=factor.name,
                    status=readiness.status,
                    decision=readiness.decision,
                    score=max(0, min(100, score)),
                    recommended_weight=readiness.recommended_weight,
                    match_reasons=match.reasons,
                    blockers=readiness.blockers[:3],
                    warnings=readiness.warnings[:3],
                    actions=readiness.actions[:3],
                )
            )
    ranked = sorted(
        candidates,
        key=lambda item: (
            item.decision == "go",
            item.decision == "size_down",
            item.score,
            item.recommended_weight,
        ),
        reverse=True,
    )
    ranked = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked[:max_items])]
    pass_count = sum(item.status == "pass" for item in ranked)
    block_count = sum(item.decision == "block" for item in ranked)
    if ranked and pass_count:
        status: Literal["pass", "watch", "fail"] = "pass"
    elif ranked and block_count < len(ranked):
        status = "watch"
    else:
        status = "fail"
    summary = f"策略候选池：{len(ranked)} 个候选，{sum(item.decision == 'go' for item in ranked)} 个可小规模模拟，{block_count} 个阻断。"
    if not ranked:
        summary = "策略候选池为空：当前筛选条件没有命中可评估标的。"
    return StrategyCandidateReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=summary,
        filters=screener_result.filters,
        total=len(ranked),
        items=ranked,
    )


def row_to_screener_preset(row: sqlite3.Row) -> ScreenerPreset:
    return ScreenerPreset(
        id=row["id"],
        name=row["name"],
        filters=json.loads(row["filters"]),
        created_at=row["created_at"],
    )


def list_screener_presets() -> list[ScreenerPreset]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM screener_presets WHERE enabled = 1 ORDER BY created_at DESC").fetchall()
    return [row_to_screener_preset(row) for row in rows]


def create_screener_preset(payload: ScreenerPresetCreate) -> ScreenerPreset:
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    preset_id = sha256(f"screener:{payload.name}:{created_at}".encode("utf-8")).hexdigest()[:20]
    allowed = {"min_change", "max_change", "min_rsi", "max_rsi", "min_trend", "signal", "market"}
    filters = {key: value for key, value in payload.filters.items() if key in allowed and value not in ("", None)}
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO screener_presets (id, name, filters, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (preset_id, payload.name.strip() or "未命名筛选", json.dumps(filters, ensure_ascii=False), created_at),
        )
        row = connection.execute("SELECT * FROM screener_presets WHERE id = ?", (preset_id,)).fetchone()
    add_event_log(
        event_type="system",
        severity="info",
        title="筛选模板已保存",
        message=f"{row['name']} 已加入条件选股模板。",
    )
    return row_to_screener_preset(row)


def update_screener_preset(preset_id: str, payload: ScreenerPresetUpdate) -> ScreenerPreset:
    init_db()
    allowed = {"min_change", "max_change", "min_rsi", "max_rsi", "min_trend", "signal", "market"}
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM screener_presets WHERE id = ?", (preset_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Screener preset not found")
        filters = json.loads(current["filters"])
        if payload.filters is not None:
            filters = {key: value for key, value in payload.filters.items() if key in allowed and value not in ("", None)}
        connection.execute(
            "UPDATE screener_presets SET name = ?, filters = ?, enabled = ? WHERE id = ?",
            (
                current["name"] if payload.name is None else payload.name.strip() or current["name"],
                json.dumps(filters, ensure_ascii=False),
                current["enabled"] if payload.enabled is None else int(payload.enabled),
                preset_id,
            ),
        )
        row = connection.execute("SELECT * FROM screener_presets WHERE id = ?", (preset_id,)).fetchone()
    return row_to_screener_preset(row)


def delete_screener_preset(preset_id: str) -> dict[str, bool]:
    init_db()
    with db_connect() as connection:
        cursor = connection.execute("UPDATE screener_presets SET enabled = 0 WHERE id = ?", (preset_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Screener preset not found")
    return {"deleted": False, "disabled": True}


def filter_request_from_dict(filters: dict[str, str | float]) -> FilterValidationRequest:
    return FilterValidationRequest(
        min_change=float(filters["min_change"]) if "min_change" in filters else None,
        max_change=float(filters["max_change"]) if "max_change" in filters else None,
        min_rsi=float(filters["min_rsi"]) if "min_rsi" in filters else None,
        max_rsi=float(filters["max_rsi"]) if "max_rsi" in filters else None,
        min_trend=int(float(filters["min_trend"])) if "min_trend" in filters else None,
        signal=str(filters["signal"]) if "signal" in filters else None,
        market=str(filters["market"]) if "market" in filters else None,
    )


def validate_screener_presets() -> list[ScreenerPresetValidation]:
    results: list[ScreenerPresetValidation] = []
    for preset in list_screener_presets():
        validation = validate_filters(filter_request_from_dict(preset.filters))
        if not validation.valid:
            status: Literal["pass", "watch", "fail"] = "fail"
        elif validation.warnings:
            status = "watch"
        else:
            status = "pass"
        results.append(
            ScreenerPresetValidation(
                id=preset.id,
                name=preset.name,
                status=status,
                match_count=validation.match_count,
                filters=preset.filters,
                warnings=validation.warnings,
                suggestions=validation.suggestions,
            )
        )
    return results


def filter_library_score_report() -> FilterLibraryScoreReport:
    presets = list_screener_presets()
    items: list[FilterLibraryScoreItem] = []
    for preset in presets:
        admission = filter_admission_report(filter_request_from_dict(preset.filters))
        validation = admission.validation
        warnings = dedupe(validation.warnings + admission.stress_test.actions[:1])
        actions = list(admission.actions)
        if admission.decision == "accept":
            actions.append("可作为策略模板默认过滤器或当前状态候选模板。")
        elif admission.decision == "revise":
            actions.append("保留在过滤器库中观察，先按动作建议微调。")
        else:
            actions.append("暂不用于策略创建，先修复样本量或阈值冲突。")
        items.append(
            FilterLibraryScoreItem(
                preset_id=preset.id,
                name=preset.name,
                status=admission.status,
                decision=admission.decision,
                score=admission.score,
                match_count=validation.match_count,
                filter_count=len(preset.filters),
                concentration_score=admission.contribution.concentration_score,
                stability_score=admission.stress_test.stability_score,
                regime_label=validation.regime.label,
                risk_level=validation.regime.risk_level,
                filters=admission.filters,
                warnings=warnings,
                actions=dedupe(actions),
            )
        )
    ranked = sorted(items, key=lambda item: (item.status == "pass", item.score, item.match_count), reverse=True)
    fail_count = sum(item.status == "fail" for item in ranked)
    watch_count = sum(item.status == "watch" for item in ranked)
    pass_count = sum(item.status == "pass" for item in ranked)
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count or not ranked:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if not ranked:
        actions.append("先保存常用筛选模板，再进入过滤器库评分。")
    if fail_count:
        actions.append("优先修复 reject 模板，避免被策略复用。")
    if watch_count:
        actions.append("revise 模板需要补样本、降集中度或改善压力稳定性。")
    if pass_count:
        actions.append("accept 模板可绑定策略并进入回测/前向验证。")
    return FilterLibraryScoreReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=f"过滤器库评分：{pass_count} 个可接受，{watch_count} 个需调整，{fail_count} 个拒绝。",
        items=ranked,
        actions=dedupe(actions),
    )


def canonical_filter_key(filters: dict[str, str | float]) -> str:
    allowed = {"min_change", "max_change", "min_rsi", "max_rsi", "min_trend", "signal", "market"}
    normalized: dict[str, str | float] = {}
    for key, value in filters.items():
        if key not in allowed or value in ("", None):
            continue
        if key in {"min_trend"}:
            normalized[key] = int(float(value))
        elif key in {"min_change", "max_change", "min_rsi", "max_rsi"}:
            numeric = float(value)
            normalized[key] = int(numeric) if numeric.is_integer() else round(numeric, 4)
        elif key == "market":
            normalized[key] = str(value).strip().lower()
        else:
            normalized[key] = str(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def filter_strategy_coverage_report() -> FilterStrategyCoverageReport:
    presets = list_screener_presets()
    strategies = list_strategy_templates()
    score_by_preset = {item.preset_id: item for item in filter_library_score_report().items}
    strategy_by_filter: dict[str, list[StrategyTemplate]] = {}
    for strategy in strategies:
        strategy_by_filter.setdefault(canonical_filter_key(strategy.default_filters), []).append(strategy)
    preset_keys = {preset.id: canonical_filter_key(preset.filters) for preset in presets}
    preset_key_set = set(preset_keys.values())
    items: list[FilterStrategyCoverageItem] = []

    for preset in presets:
        key = preset_keys[preset.id]
        linked = strategy_by_filter.get(key, [])
        score_item = score_by_preset.get(preset.id)
        filter_score = score_item.score if score_item else 50
        if linked:
            status: Literal["pass", "watch", "fail"] = "pass" if score_item is None or score_item.status == "pass" else "watch"
            detail = f"被 {len(linked)} 个策略复用：{', '.join(item.name for item in linked[:3])}。"
            action = "作为策略默认过滤器维护，并随策略复验刷新。"
            match_type: Literal["preset_reused", "orphan_preset", "unregistered_strategy_filter"] = "preset_reused"
            score = min(100, filter_score + min(15, len(linked) * 5))
        else:
            status = "watch" if score_item is None or score_item.status != "fail" else "fail"
            detail = "保存模板尚未被任何策略默认过滤器复用。"
            action = "若评分可接受，将其沉淀为策略模板；否则删除或调整。"
            match_type = "orphan_preset"
            score = max(0, filter_score - 20)
        items.append(
            FilterStrategyCoverageItem(
                preset_id=preset.id,
                preset_name=preset.name,
                status=status,
                score=score,
                match_type=match_type,
                filter_count=len(preset.filters),
                linked_strategy_count=len(linked),
                filters=filter_request_from_dict(preset.filters).model_dump(exclude_none=True),
                detail=detail,
                action=action,
            )
        )

    for strategy in strategies:
        key = canonical_filter_key(strategy.default_filters)
        if key in preset_key_set:
            continue
        status: Literal["pass", "watch", "fail"] = "watch" if strategy.default_filters else "fail"
        action = "将该策略默认过滤器保存为筛选模板，进入过滤器库评分。" if strategy.default_filters else "先为策略补默认过滤器。"
        score = 55 if strategy.default_filters else 20
        items.append(
            FilterStrategyCoverageItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                status=status,
                score=score,
                match_type="unregistered_strategy_filter",
                filter_count=len(strategy.default_filters),
                linked_strategy_count=1,
                filters=filter_request_from_dict(strategy.default_filters).model_dump(exclude_none=True),
                detail=f"{strategy.name} 的默认过滤器未沉淀为筛选模板。",
                action=action,
            )
        )

    pass_count = sum(item.status == "pass" for item in items)
    watch_count = sum(item.status == "watch" for item in items)
    fail_count = sum(item.status == "fail" for item in items)
    score = int(round(sum(item.score for item in items) / max(1, len(items))))
    if fail_count:
        status: Literal["pass", "watch", "fail"] = "fail"
    elif watch_count or not presets:
        status = "watch"
    else:
        status = "pass"
    actions: list[str] = []
    if any(item.match_type == "orphan_preset" for item in items):
        actions.append("孤立筛选模板需要决定转为策略、继续观察或删除。")
    if any(item.match_type == "unregistered_strategy_filter" for item in items):
        actions.append("策略默认过滤器应回写为筛选模板，便于统一评分和复用。")
    if pass_count:
        actions.append("已复用模板保持随策略验证一起复验。")
    if not actions:
        actions.append("过滤器与策略库覆盖关系健康。")
    return FilterStrategyCoverageReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        score=score,
        summary=f"过滤器策略覆盖 {score}/100：{pass_count} 个复用健康，{watch_count} 个待处理，{fail_count} 个失败。",
        items=sorted(items, key=lambda item: (item.status == "pass", item.score, item.linked_strategy_count), reverse=True),
        actions=dedupe(actions),
    )


def row_to_strategy_template(row: sqlite3.Row) -> StrategyTemplate:
    return StrategyTemplate(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        description=row["description"],
        factor_ids=json.loads(row["factor_ids"]),
        default_filters=json.loads(row["default_filters"]),
        risk_controls=json.loads(row["risk_controls"]),
        validation_rules=json.loads(row["validation_rules"]),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
    )


def list_strategy_templates(enabled_only: bool = False) -> list[StrategyTemplate]:
    init_db()
    with db_connect() as connection:
        if enabled_only:
            rows = connection.execute("SELECT * FROM strategy_templates WHERE enabled = 1 ORDER BY created_at, name").fetchall()
        else:
            rows = connection.execute("SELECT * FROM strategy_templates ORDER BY created_at, name").fetchall()
    return [row_to_strategy_template(row) for row in rows]


def get_strategy_template(strategy_id: str) -> StrategyTemplate:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM strategy_templates WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Strategy template not found")
    return row_to_strategy_template(row)


def create_strategy_template(payload: StrategyTemplateCreate) -> StrategyTemplate:
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    base_id = slugify_factor_id(payload.name)
    strategy_id = base_id
    with db_connect() as connection:
        suffix = 2
        while connection.execute("SELECT 1 FROM strategy_templates WHERE id = ?", (strategy_id,)).fetchone():
            strategy_id = f"{base_id}-{suffix}"
            suffix += 1
        valid_factor_ids = {item.id for item in list_factor_definitions()}
        factor_ids = [item for item in payload.factor_ids if item in valid_factor_ids]
        connection.execute(
            """
            INSERT INTO strategy_templates (
                id, name, category, description, factor_ids, default_filters,
                risk_controls, validation_rules, enabled, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                payload.name.strip(),
                payload.category.strip(),
                payload.description.strip(),
                json.dumps(factor_ids, ensure_ascii=False),
                json.dumps(payload.default_filters, ensure_ascii=False),
                json.dumps(payload.risk_controls, ensure_ascii=False),
                json.dumps(payload.validation_rules, ensure_ascii=False),
                int(payload.enabled),
                created_at,
            ),
        )
        row = connection.execute("SELECT * FROM strategy_templates WHERE id = ?", (strategy_id,)).fetchone()
    add_event_log(
        event_type="system",
        severity="info",
        title="策略模板已创建",
        message=f"{payload.name.strip()} 已加入策略库。",
    )
    return row_to_strategy_template(row)


def update_strategy_template(strategy_id: str, payload: StrategyTemplateUpdate) -> StrategyTemplate:
    current = get_strategy_template(strategy_id)
    valid_factor_ids = {item.id for item in list_factor_definitions()}
    updated_factor_ids = current.factor_ids if payload.factor_ids is None else [item for item in payload.factor_ids if item in valid_factor_ids]
    values = {
        "name": current.name if payload.name is None else payload.name.strip(),
        "category": current.category if payload.category is None else payload.category.strip(),
        "description": current.description if payload.description is None else payload.description.strip(),
        "factor_ids": updated_factor_ids,
        "default_filters": current.default_filters if payload.default_filters is None else payload.default_filters,
        "risk_controls": current.risk_controls if payload.risk_controls is None else payload.risk_controls,
        "validation_rules": current.validation_rules if payload.validation_rules is None else payload.validation_rules,
        "enabled": current.enabled if payload.enabled is None else payload.enabled,
    }
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE strategy_templates
            SET name = ?, category = ?, description = ?, factor_ids = ?, default_filters = ?,
                risk_controls = ?, validation_rules = ?, enabled = ?
            WHERE id = ?
            """,
            (
                values["name"],
                values["category"],
                values["description"],
                json.dumps(values["factor_ids"], ensure_ascii=False),
                json.dumps(values["default_filters"], ensure_ascii=False),
                json.dumps(values["risk_controls"], ensure_ascii=False),
                json.dumps(values["validation_rules"], ensure_ascii=False),
                int(bool(values["enabled"])),
                strategy_id,
            ),
        )
        row = connection.execute("SELECT * FROM strategy_templates WHERE id = ?", (strategy_id,)).fetchone()
    add_event_log(
        event_type="system",
        severity="info",
        title="策略模板已更新",
        message=f"{values['name']} 已更新。",
    )
    return row_to_strategy_template(row)


def delete_strategy_template(strategy_id: str) -> dict[str, bool]:
    init_db()
    with db_connect() as connection:
        cursor = connection.execute("UPDATE strategy_templates SET enabled = 0 WHERE id = ?", (strategy_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Strategy template not found")
    add_event_log(
        event_type="system",
        severity="info",
        title="策略模板已停用",
        message=f"{strategy_id} 已停用，可在管理中心恢复。",
    )
    return {"deleted": False, "disabled": True}


def parse_volume_value(value: str) -> float:
    cleaned = value.strip().upper().replace(",", "")
    multiplier = 1.0
    if cleaned.endswith("K"):
        multiplier = 1_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("B"):
        multiplier = 1_000_000_000
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return 0.0


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


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
            provider="RSS + built-in examples",
            coverage=[source.name for source in list_news_sources()] or ["内置示例新闻"],
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

    news_sources = list_news_sources()
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
        checks.append(ReadinessCheck(name="新闻覆盖", status="pass", message=f"新闻源 {len(news_sources)} 个，已入库新闻 {news_count} 条。"))
    else:
        checks.append(ReadinessCheck(name="新闻覆盖", status="watch", message="暂无 RSS 源或新闻入库，事件因子证据不足。"))
        actions.append("添加 RSS 新闻源或运行新闻刷新。")

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
        actions.append("添加 RSS 或刷新新闻源，补齐事件因子证据。")
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
            ("news-articles", "新闻事件", "news_articles", "published_at", 24, "RSS / 内置新闻"),
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


def normalize_symbols(symbols: list[str] | str | None) -> list[str]:
    if symbols is None:
        return []
    if isinstance(symbols, str):
        raw = re.split(r"[,，\s]+", symbols)
    else:
        raw = symbols
    valid = {item[0] for item in SYMBOLS}
    return sorted({item.strip().upper() for item in raw if item.strip().upper() in valid})


def row_to_source(row: sqlite3.Row) -> NewsSource:
    return NewsSource(
        id=row["id"],
        name=row["name"],
        type="rss",
        url=row["url"],
        enabled=bool(row["enabled"]),
        symbols=normalize_symbols(row["symbols"]),
        poll_interval_seconds=row["poll_interval_seconds"],
        last_fetched_at=row["last_fetched_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
    )


def strip_markup(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_datetime(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
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
    if source_symbols:
        return source_symbols
    text = f"{title} {summary}".upper()

    def contains_token(token: str) -> bool:
        return re.search(rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])", text) is not None

    matched = [
        symbol
        for symbol, name, *_ in SYMBOLS
        if contains_token(symbol) or contains_token(name.upper().split()[0])
    ]
    if contains_token("BITCOIN") or contains_token("BTC"):
        matched.append("BTCUSD")
    if contains_token("ETHEREUM") or contains_token("ETHER") or contains_token("ETH"):
        matched.append("ETHUSD")
    return sorted(set(matched))


def source_articles_to_events(symbol: str, limit: int) -> list[NewsEvent]:
    init_db()
    base_symbol = symbol.upper()
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM news_articles
            WHERE enabled = 1 AND ',' || symbols || ',' LIKE ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (f"%,{base_symbol},%", limit),
        ).fetchall()
    return [
        NewsEvent(
            id=row["id"],
            symbol=base_symbol,
            title=row["title"],
            summary=row["summary"],
            source=row["source_name"],
            url=row["url"],
            published_at=row["published_at"],
            event_type=row["event_type"],
            sentiment=row["sentiment"],
            impact_score=row["impact_score"],
            price_reaction="待观察",
        )
        for row in rows
    ]


def event_to_detail(event: NewsEvent, related_symbols: list[str] | None = None) -> NewsDetail:
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
    ai_summary = (
        f"{event.symbol} 这条新闻被归类为{direction}，影响分 {event.impact_score}。"
        "建议结合 K 线事件标记位置、成交量变化和后续新闻确认，不单独作为交易指令。"
    )
    event_payload = event.model_dump() if hasattr(event, "model_dump") else event.dict()
    return NewsDetail(
        **event_payload,
        related_symbols=related_symbols or [event.symbol],
        ai_summary=ai_summary,
        key_points=key_points,
        detail=event.summary,
    )


def get_news_detail(news_id: str) -> NewsDetail:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_articles WHERE id = ? AND enabled = 1", (news_id,)).fetchone()
    if row:
        symbols = normalize_symbols(row["symbols"])
        primary_symbol = symbols[0] if symbols else ""
        event = NewsEvent(
            id=row["id"],
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
        )
        return event_to_detail(event, related_symbols=symbols)

    for snapshot in get_market_provider().watchlist():
        for event in news_events(snapshot.symbol, limit=20):
            if event.id == news_id:
                return event_to_detail(event)
    raise HTTPException(status_code=404, detail="News event not found")


def fetch_rss_items(source: NewsSource) -> list[dict[str, str]]:
    request = UrlRequest(source.url, headers={"User-Agent": "AppTrading/0.1 RSS Reader"})
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read()
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc

    root = ET.fromstring(body)
    if root.tag.rsplit("}", 1)[-1].lower() == "rss":
        items = root.findall("./channel/item")
    else:
        items = [child for child in root if child.tag.rsplit("}", 1)[-1].lower() == "entry"]

    parsed_items: list[dict[str, str]] = []
    for item in items[:50]:
        title = strip_markup(xml_text(item, ["title"]))
        summary = strip_markup(xml_text(item, ["description", "summary", "content"]))
        link = xml_text(item, ["link"])
        if not link:
            for child in item:
                if child.tag.rsplit("}", 1)[-1].lower() == "link":
                    link = child.attrib.get("href", "")
                    break
        published_at = parse_datetime(xml_text(item, ["pubDate", "published", "updated"]))
        if title and link:
            parsed_items.append({"title": title, "summary": summary or title, "url": link, "published_at": published_at})
    return parsed_items


def refresh_source(source_id: int) -> NewsRefreshResult:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="News source not found")

    source = row_to_source(row)
    if not source.enabled:
        return NewsRefreshResult(source_id=source_id, fetched=0, inserted=0, error="Source is disabled")

    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        items = fetch_rss_items(source)
        inserted = 0
        with db_connect() as connection:
            for item in items:
                symbols = extract_symbols(item["title"], item["summary"], source.symbols)
                if not symbols:
                    continue
                sentiment, event_type, impact = classify_article(item["title"], item["summary"])
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
            connection.execute(
                "UPDATE news_sources SET last_fetched_at = ?, last_error = NULL WHERE id = ?",
                (fetched_at, source.id),
            )
        return NewsRefreshResult(source_id=source_id, fetched=len(items), inserted=inserted)
    except (RuntimeError, ET.ParseError) as exc:
        message = str(exc)[:240]
        with db_connect() as connection:
            connection.execute(
                "UPDATE news_sources SET last_fetched_at = ?, last_error = ? WHERE id = ?",
                (fetched_at, message, source.id),
            )
        return NewsRefreshResult(source_id=source_id, fetched=0, inserted=0, error=message)


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


def create_portfolio_position(payload: PortfolioPositionCreate) -> PortfolioPosition:
    init_db()
    symbol = payload.symbol.upper()
    if payload.quantity <= 0 or payload.average_price <= 0:
        raise HTTPException(status_code=400, detail="Quantity and average price must be positive")
    created_at = datetime.now(timezone.utc).isoformat()
    position_id = sha256(f"position:{symbol}:{payload.side}:{payload.quantity}:{payload.average_price}:{created_at}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO portfolio_positions (
                id, symbol, side, quantity, average_price, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                symbol,
                payload.side,
                payload.quantity,
                payload.average_price,
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
        symbol = current["symbol"] if payload.symbol is None else payload.symbol.strip().upper()
        quantity = current["quantity"] if payload.quantity is None else payload.quantity
        average_price = current["average_price"] if payload.average_price is None else payload.average_price
        if quantity <= 0 or average_price <= 0:
            raise HTTPException(status_code=400, detail="Quantity and average price must be positive")
        connection.execute(
            "UPDATE portfolio_positions SET symbol = ?, side = ?, quantity = ?, average_price = ?, note = ? WHERE id = ?",
            (
                symbol,
                current["side"] if payload.side is None else payload.side,
                quantity,
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
    symbol_meta = {item[0]: item[1] for item in SYMBOLS}
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM portfolio_positions WHERE enabled = 1 ORDER BY created_at DESC").fetchall()

    positions: list[PortfolioPosition] = []
    for row in rows:
        symbol = row["symbol"]
        snapshot = snapshots.get(symbol)
        current_price = snapshot.price if snapshot else None
        stale_price = current_price is None
        mark_price = current_price if current_price is not None else row["average_price"]
        signed_qty = row["quantity"] if row["side"] == "long" else -row["quantity"]
        market_value = signed_qty * mark_price
        cost_basis = signed_qty * row["average_price"]
        unrealized_pnl = market_value - cost_basis
        pnl_pct = unrealized_pnl / abs(cost_basis) * 100 if cost_basis else 0.0
        positions.append(
            PortfolioPosition(
                id=row["id"],
                symbol=symbol,
                name=symbol_meta.get(symbol, symbol),
                side=row["side"],
                quantity=row["quantity"],
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


def update_alert_rule(alert_id: str, payload: AlertRuleUpdate) -> AlertRule:
    init_db()
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        connection.execute(
            """
            UPDATE alert_rules
            SET name = ?, operator = ?, threshold = ?, enabled = ?
            WHERE id = ?
            """,
            (
                payload.name.strip() if payload.name is not None else current["name"],
                payload.operator if payload.operator is not None else current["operator"],
                payload.threshold if payload.threshold is not None else current["threshold"],
                int(payload.enabled) if payload.enabled is not None else current["enabled"],
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
    if rule.metric == "price":
        snapshot = next((item for item in get_market_provider().watchlist() if item.symbol == rule.symbol), None)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"No market snapshot for {rule.symbol}")
        return snapshot.price
    if rule.metric == "rsi":
        rsi_card = next((item for item in indicators(rule.symbol) if item.label.startswith("RSI")), None)
        if rsi_card is None:
            raise HTTPException(status_code=404, detail=f"No RSI indicator for {rule.symbol}")
        return float(rsi_card.value)
    latest_news = news_events(rule.symbol, limit=10)
    return max((item.impact_score for item in latest_news), default=0)


def evaluate_alert_rule(rule: AlertRule) -> AlertEvaluation:
    value = alert_current_value(rule)
    triggered = value >= rule.threshold if rule.operator == ">=" else value <= rule.threshold
    evaluated_at = datetime.now(timezone.utc).isoformat()
    if triggered and rule.enabled:
        with db_connect() as connection:
            connection.execute("UPDATE alert_rules SET last_triggered_at = ? WHERE id = ?", (evaluated_at, rule.id))
        rule.last_triggered_at = evaluated_at
    metric_label = {"price": "价格", "rsi": "RSI", "news_impact": "新闻影响分"}[rule.metric]
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


def parameter_optimization_report(payload: ParameterOptimizationRequest) -> ParameterOptimizationReport:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    valid_symbols = {item[0] for item in SYMBOLS}
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
    symbols = [item[0] for item in SYMBOLS]
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


def run_backtest(payload: BacktestRunRequest) -> BacktestRunResult:
    init_db()
    symbol = payload.symbol.upper()
    factor = get_factor_definition(payload.factor_id, require_enabled=True)
    valid_symbols = {item[0] for item in SYMBOLS}
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


def run_automation_cycle() -> AutomationRunResult:
    init_db()
    started_at = datetime.now(timezone.utc).isoformat()
    news_results = refresh_all_news_sources()
    alert_results = evaluate_alerts()
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
    provider = market_provider_status()
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
        news_candidates.extend(news_events(snapshot.symbol, limit=2))
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
        title="AI 全局盯盘简报",
        summary=summary,
        risk_level=risk_level,
        generated_at=generated_at,
        opportunities=opportunities[:4],
        risks=risks[:4],
        actions=actions,
        evidence=evidence[:8],
    )


AGENT_TRIGGER_TYPES = {"manual", "schedule", "high_impact_news", "alert_triggered", "backtest_completed"}


def json_value(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def row_to_agent_provider(row: sqlite3.Row) -> AgentProviderProfile:
    return AgentProviderProfile(
        id=row["id"], name=row["name"], base_url=row["base_url"], model=row["model"],
        api_key_env=row["api_key_env"], timeout_seconds=row["timeout_seconds"],
        temperature=row["temperature"], max_tokens=row["max_tokens"], enabled=bool(row["enabled"]),
        secret_configured=bool(os.getenv(row["api_key_env"])), created_at=row["created_at"], updated_at=row["updated_at"],
    )


def row_to_agent_config(row: sqlite3.Row) -> AgentConfig:
    return AgentConfig(
        id=row["id"], template_type=row["template_type"], builtin=bool(row["builtin"]), name=row["name"],
        description=row["description"], enabled=bool(row["enabled"]), runtime_mode=row["runtime_mode"],
        provider_id=row["provider_id"], symbol_scope=row["symbol_scope"], symbols=json_value(row["symbols"], []),
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
    return row_to_agent_provider(row)


def update_agent_provider(provider_id: str, payload: AgentProviderUpdate) -> AgentProviderProfile:
    current = get_agent_provider(provider_id)
    values = current.model_dump(exclude={"secret_configured", "created_at", "updated_at"})
    values.update(payload.model_dump(exclude_none=True))
    base_url, env_name = validate_agent_provider_input(str(values["base_url"]), str(values["api_key_env"]))
    updated_at = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """UPDATE agent_provider_profiles SET name=?, base_url=?, model=?, api_key_env=?, timeout_seconds=?,
               temperature=?, max_tokens=?, enabled=?, updated_at=? WHERE id=?""",
            (values["name"], base_url, values["model"], env_name, values["timeout_seconds"], values["temperature"],
             values["max_tokens"], int(values["enabled"]), updated_at, provider_id),
        )
    return get_agent_provider(provider_id)


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
    if payload.runtime_mode in {"llm", "hybrid"} and not payload.provider_id:
        raise HTTPException(status_code=422, detail="LLM and hybrid Agents require a provider")
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
                notification_channel_ids, fallback_to_rules, last_run_at, next_run_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            (resolved_id, payload.template_type, int(builtin), payload.name.strip(), payload.description.strip(), int(payload.enabled),
             payload.runtime_mode, payload.provider_id, payload.symbol_scope, json.dumps(symbols), int(payload.schedule_enabled),
             payload.interval_seconds, json.dumps(triggers), payload.system_prompt.strip(), payload.task_prompt.strip(),
             json.dumps(payload.settings, ensure_ascii=False), json.dumps(payload.notification_channel_ids), int(payload.fallback_to_rules),
             next_run, now, now),
        )
    return get_agent(resolved_id)


def update_agent(agent_id: str, payload: AgentUpdate) -> AgentConfig:
    current = get_agent(agent_id)
    changes = payload.model_dump(exclude_unset=True)
    values = current.model_dump(exclude={"id", "template_type", "builtin", "last_run_at", "next_run_at", "created_at", "updated_at"})
    values.update(changes)
    provider_id = values.get("provider_id")
    if provider_id:
        get_agent_provider(str(provider_id))
    if values["runtime_mode"] in {"llm", "hybrid"} and not provider_id:
        raise HTTPException(status_code=422, detail="LLM and hybrid Agents require a provider")
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
               notification_channel_ids=?, fallback_to_rules=?, next_run_at=?, updated_at=? WHERE id=?""",
            (values["name"], values["description"], int(values["enabled"]), values["runtime_mode"], provider_id,
             values["symbol_scope"], json.dumps(symbols), int(values["schedule_enabled"]), values["interval_seconds"],
             json.dumps(triggers), values["system_prompt"], values["task_prompt"], json.dumps(values["settings"], ensure_ascii=False),
             json.dumps(values["notification_channel_ids"]), int(values["fallback_to_rules"]), next_run, now.isoformat(), agent_id),
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


def agent_rule_output(agent: AgentConfig, symbol: str | None, context: dict[str, object]) -> dict[str, object]:
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


def call_agent_llm(provider: AgentProviderProfile, agent: AgentConfig, rule_output: dict[str, object], context: dict[str, object]) -> tuple[dict[str, object], dict[str, int]]:
    api_key = os.getenv(provider.api_key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {provider.api_key_env} is not configured")
    payload = {
        "model": provider.model, "temperature": provider.temperature, "max_tokens": provider.max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": agent.system_prompt or "Return a JSON object grounded only in supplied evidence."},
            {"role": "user", "content": f"{agent.task_prompt}\nRequired keys: summary,risk_level,confidence,symbols,evidence,actions,proposals.\nRule baseline:\n{json.dumps(rule_output, ensure_ascii=False)}\nAdditional context:\n{json.dumps(context, ensure_ascii=False)}"},
        ],
    }
    body = agent_http_post(f"{provider.base_url.rstrip('/')}/chat/completions", payload, api_key, provider.timeout_seconds)
    content = body["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    output = json.loads(content)
    required = {"summary", "risk_level", "confidence", "evidence", "actions"}
    if not isinstance(output, dict) or not required.issubset(output):
        raise RuntimeError("Model response does not match the Agent output contract")
    if output["risk_level"] not in {"info", "watch", "risk", "opportunity"}:
        raise RuntimeError("Model returned an unsupported risk level")
    output["confidence"] = max(0, min(100, int(output["confidence"])))
    output.setdefault("symbols", rule_output.get("symbols", []))
    output.setdefault("proposals", [])
    output["created_at"] = datetime.now(timezone.utc).isoformat()
    usage = body.get("usage") or {}
    return output, {"prompt_tokens": int(usage.get("prompt_tokens", 0)), "completion_tokens": int(usage.get("completion_tokens", 0))}


def enqueue_agent_run(agent_id: str, trigger: str = "manual", symbol: str | None = None, context: dict[str, object] | None = None, test: bool = False) -> AgentRun:
    agent = get_agent(agent_id, require_enabled=not test)
    if trigger not in AGENT_TRIGGER_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported Agent trigger")
    now = datetime.now(timezone.utc).isoformat()
    run_id = f"agent-run-{sha256(f'{agent_id}:{now}:{secrets.token_hex(4)}'.encode()).hexdigest()[:16]}"
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO agent_runs (id, agent_id, agent_name, trigger, symbol, status, runtime_mode, input_payload,
               output, error, model, prompt_tokens, completion_tokens, latency_ms, test, created_at, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, NULL, NULL, NULL, 0, 0, 0, ?, ?, NULL, NULL)""",
            (run_id, agent.id, agent.name, trigger, symbol.upper() if symbol else None, agent.runtime_mode,
             json.dumps(context or {}, ensure_ascii=False), int(test), now),
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


def persist_agent_proposals(run: AgentRun, output: dict[str, object]) -> None:
    if run.test:
        return
    proposals = output.get("proposals", [])
    if not isinstance(proposals, list):
        return
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        for item in proposals[:10]:
            if not isinstance(item, dict) or item.get("resource_type") not in {"factors", "alerts", "watchlist"} or item.get("action") != "create":
                continue
            proposal_id = f"proposal-{sha256(f'{run.id}:{json.dumps(item, sort_keys=True)}'.encode()).hexdigest()[:14]}"
            connection.execute(
                "INSERT OR IGNORE INTO agent_proposals VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, NULL)",
                (proposal_id, run.id, run.agent_id, item["resource_type"], item["action"], str(item.get("title", "Agent proposal"))[:200],
                 json.dumps(item.get("payload", {}), ensure_ascii=False), now),
            )


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
    try:
        rule_output = agent_rule_output(agent, run.symbol, context)
        output = rule_output
        if agent.runtime_mode in {"llm", "hybrid"}:
            try:
                provider = get_agent_provider(agent.provider_id or "", require_enabled=True)
                model = provider.model
                output, usage = call_agent_llm(provider, agent, rule_output, context)
            except Exception as exc:
                if not agent.fallback_to_rules:
                    raise
                error = f"LLM fallback: {str(exc)[:500]}"
                output = rule_output
        finished = datetime.now(timezone.utc)
        status = "succeeded"
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        status = "failed"
        error = str(exc)[:1000]
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
        except Exception as exc:
            add_event_log(event_type="system", severity="error", title="Agent worker error", message=str(exc)[:500])
        await asyncio.sleep(2)


def list_agent_proposals(status: str | None = None, limit: int = 100) -> list[AgentProposal]:
    init_db()
    with db_connect() as connection:
        if status:
            rows = connection.execute("SELECT * FROM agent_proposals WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = connection.execute("SELECT * FROM agent_proposals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [row_to_agent_proposal(row) for row in rows]


def agent_output_for_api(agent: AgentConfig, symbol: str | None = None) -> dict[str, object]:
    if agent.runtime_mode in {"llm", "hybrid"}:
        with db_connect() as connection:
            if symbol:
                row = connection.execute(
                    """SELECT output FROM agent_runs WHERE agent_id=? AND status='succeeded' AND test=0
                       AND (symbol=? OR symbol IS NULL) AND output IS NOT NULL ORDER BY finished_at DESC LIMIT 1""",
                    (agent.id, symbol.upper()),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT output FROM agent_runs WHERE agent_id=? AND status='succeeded' AND test=0 AND output IS NOT NULL ORDER BY finished_at DESC LIMIT 1",
                    (agent.id,),
                ).fetchone()
        if row:
            cached = json_value(row["output"], None)
            if isinstance(cached, dict):
                return cached
    return agent_rule_output(agent, symbol, {})


def review_agent_proposal(proposal_id: str, approve: bool, actor: AuthUser, note: str = "") -> AgentProposal:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent proposal not found")
    proposal = row_to_agent_proposal(row)
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Agent proposal has already been reviewed")
    now = datetime.now(timezone.utc).isoformat()
    status = "rejected"
    if approve:
        try:
            if proposal.resource_type == "factors":
                create_factor_definition(FactorDefinitionCreate(**proposal.payload))
            elif proposal.resource_type == "alerts":
                create_alert(AlertRuleCreate(**proposal.payload))
            elif proposal.resource_type == "watchlist":
                add_watchlist_symbol(WatchlistItemCreate(**proposal.payload))
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


def get_system_health() -> SystemHealth:
    init_db()
    generated_at = datetime.now(timezone.utc).isoformat()
    components: list[HealthComponent] = []
    provider_name = get_active_market_provider_name()

    try:
        with db_connect() as connection:
            connection.execute("SELECT 1").fetchone()
            source_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled,
                    COALESCE(SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END), 0) AS errors
                FROM news_sources
                """
            ).fetchone()
            alert_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled
                FROM alert_rules
                """
            ).fetchone()
            factor_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled
                FROM factor_definitions
                """
            ).fetchone()
            notification_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled,
                    COALESCE(SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END), 0) AS errors
                FROM notification_channels
                """
            ).fetchone()
            failed_delivery_row = connection.execute(
                "SELECT COUNT(*) AS failed FROM notification_deliveries WHERE success = 0"
            ).fetchone()
            portfolio_row = connection.execute("SELECT COUNT(*) AS total FROM portfolio_positions").fetchone()
            watchlist_row = connection.execute(
                "SELECT COUNT(*) AS total FROM watchlist_items WHERE provider = ?",
                (provider_name,),
            ).fetchone()
            event_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END), 0) AS errors,
                    COALESCE(SUM(CASE WHEN severity = 'risk' THEN 1 ELSE 0 END), 0) AS risks
                FROM event_logs
                WHERE created_at >= ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
            ).fetchone()
        components.append(HealthComponent(name="数据库", status="ok", message="SQLite 可读写，核心表已初始化。"))
    except sqlite3.Error as exc:
        return SystemHealth(
            status="error",
            generated_at=generated_at,
            components=[
                HealthComponent(
                    name="数据库",
                    status="error",
                    message=f"SQLite 检查失败：{str(exc)[:120]}",
                )
            ],
        )

    provider = market_provider_status()
    provider_status: Literal["ok", "warn", "error"] = "ok"
    if provider.status == "degraded":
        provider_status = "warn"
    if provider.status == "offline":
        provider_status = "error"
    components.append(
        HealthComponent(
            name="行情源",
            status=provider_status,
            message=f"{provider.display_name}：{provider.message}",
            count=len(provider.coverage),
        )
    )

    watchlist_total = int(watchlist_row["total"])
    provider_symbol_total = len(MARKET_PROVIDERS[provider_name].list_symbols())
    components.append(
        HealthComponent(
            name="自选观察",
            status="ok" if watchlist_total else "warn",
            message=(
                f"当前自选 {watchlist_total} 个标的。"
                if watchlist_total
                else f"未定制自选列表，默认展示 {provider_symbol_total} 个可用标的。"
            ),
            count=watchlist_total or provider_symbol_total,
        )
    )

    settings = get_automation_settings()
    components.append(
        HealthComponent(
            name="自动化盯盘",
            status="ok" if settings.enabled else "warn",
            message=f"{'已开启' if settings.enabled else '未开启'}，间隔 {settings.interval_seconds} 秒。",
        )
    )

    source_total = int(source_row["total"])
    source_enabled = int(source_row["enabled"])
    source_errors = int(source_row["errors"])
    source_status: Literal["ok", "warn", "error"] = "ok"
    source_message = f"{source_enabled}/{source_total} 个新闻源启用。"
    if source_total == 0 or source_enabled == 0:
        source_status = "warn"
        source_message = "尚未启用新闻源，当前依赖内置示例新闻。"
    if source_errors:
        source_status = "error"
        source_message = f"{source_errors} 个新闻源最近拉取失败。"
    components.append(HealthComponent(name="新闻源", status=source_status, message=source_message, count=source_total))

    alert_total = int(alert_row["total"])
    alert_enabled = int(alert_row["enabled"])
    components.append(
        HealthComponent(
            name="预警规则",
            status="ok" if alert_enabled else "warn",
            message=f"{alert_enabled}/{alert_total} 条规则启用。",
            count=alert_total,
        )
    )

    factor_total = int(factor_row["total"])
    factor_enabled = int(factor_row["enabled"])
    components.append(
        HealthComponent(
            name="因子库",
            status="ok" if factor_enabled else "warn",
            message=f"{factor_enabled}/{factor_total} 个因子启用。",
            count=factor_total,
        )
    )

    channel_total = int(notification_row["total"])
    channel_enabled = int(notification_row["enabled"])
    channel_errors = int(notification_row["errors"])
    failed_deliveries = int(failed_delivery_row["failed"])
    notification_status: Literal["ok", "warn", "error"] = "ok"
    notification_message = f"{channel_enabled}/{channel_total} 个通知通道启用。"
    if channel_total == 0 or channel_enabled == 0:
        notification_status = "warn"
        notification_message = "尚未配置通知通道，预警只会写入事件日志。"
    if channel_errors or failed_deliveries:
        notification_status = "warn"
        notification_message = f"{channel_errors} 个通道异常，累计 {failed_deliveries} 次投递失败。"
    components.append(
        HealthComponent(
            name="通知投递",
            status=notification_status,
            message=notification_message,
            count=channel_total,
        )
    )

    position_total = int(portfolio_row["total"])
    components.append(
        HealthComponent(
            name="模拟组合",
            status="ok" if position_total else "warn",
            message=f"当前跟踪 {position_total} 个持仓。",
            count=position_total,
        )
    )

    event_total = int(event_row["total"])
    event_errors = int(event_row["errors"])
    event_risks = int(event_row["risks"])
    event_status: Literal["ok", "warn", "error"] = "ok"
    event_message = f"近 24 小时 {event_total} 条事件。"
    if event_risks:
        event_status = "warn"
        event_message = f"近 24 小时 {event_risks} 条风险事件。"
    if event_errors:
        event_status = "error"
        event_message = f"近 24 小时 {event_errors} 条错误事件。"
    components.append(HealthComponent(name="事件日志", status=event_status, message=event_message, count=event_total))

    overall: Literal["ok", "warn", "error"] = "ok"
    if any(item.status == "error" for item in components):
        overall = "error"
    elif any(item.status == "warn" for item in components):
        overall = "warn"

    return SystemHealth(status=overall, generated_at=generated_at, components=components)


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    public_paths = {"/login", "/setup", "/api/auth/setup-status", "/api/auth/setup", "/api/auth/login", "/favicon.ico"}
    if path.startswith("/static/") or path in public_paths:
        return await call_next(request)

    if auth_setup_required():
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Initial administrator setup required"}, status_code=503)
        return RedirectResponse("/setup", status_code=303)

    session = lookup_auth_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    user, csrf_token, expires_at = session
    request.state.user = user
    request.state.csrf_token = csrf_token
    request.state.session_expires_at = expires_at
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if path != "/api/auth/logout" and user.role != "admin":
            return JSONResponse({"detail": "Administrator role required"}, status_code=403)
        supplied_csrf = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied_csrf, csrf_token):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
    response = await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400 and not path.startswith("/api/admin/") and not path.startswith("/api/auth/"):
        segments = [segment for segment in path.split("/") if segment]
        resource_type = segments[1] if len(segments) > 1 else "system"
        resource_id = segments[-1] if len(segments) > 2 else None
        add_admin_audit(user, request.method.lower(), resource_type, resource_id, f"{request.method} {path}")
    return response


@app.get("/setup", include_in_schema=False)
def setup_page() -> Response:
    if not auth_setup_required():
        return RedirectResponse("/login", status_code=303)
    return FileResponse("app/static/auth.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse("app/static/auth.html")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse("app/static/admin.html")


@app.get("/agents", include_in_schema=False)
def agents_page() -> FileResponse:
    return FileResponse("app/static/agents.html")


@app.get("/api/auth/setup-status", response_model=SetupStatus)
def auth_setup_status() -> SetupStatus:
    return SetupStatus(required=auth_setup_required())


@app.post("/api/auth/setup", response_model=AuthSessionInfo)
def auth_setup(payload: SetupRequest, response: Response) -> AuthSessionInfo:
    user = create_initial_admin(payload)
    token, session = create_auth_session(user)
    set_session_cookie(response, token)
    add_admin_audit(user, "setup", "users", user.id, "Initial administrator created")
    return session


@app.post("/api/auth/login", response_model=AuthSessionInfo)
def auth_login(payload: LoginRequest, response: Response) -> AuthSessionInfo:
    user = authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token, session = create_auth_session(user)
    set_session_cookie(response, token)
    add_admin_audit(user, "login", "auth_sessions", None, "User signed in")
    return session


@app.get("/api/auth/me", response_model=AuthSessionInfo)
def auth_me(request: Request) -> AuthSessionInfo:
    return AuthSessionInfo(
        user=current_request_user(request),
        csrf_token=request.state.csrf_token,
        expires_at=request.state.session_expires_at,
    )


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    user = current_request_user(request)
    revoke_auth_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    add_admin_audit(user, "logout", "auth_sessions", None, "User signed out")
    return {"logged_out": True}


@app.get("/api/agent-providers", response_model=list[AgentProviderProfile])
def agent_providers() -> list[AgentProviderProfile]:
    return list_agent_providers()


@app.post("/api/agent-providers", response_model=AgentProviderProfile)
def add_agent_provider(payload: AgentProviderCreate, request: Request) -> AgentProviderProfile:
    actor = current_request_user(request)
    provider = create_agent_provider(payload)
    add_admin_audit(actor, "create", "agent-providers", provider.id, "Agent model provider created", after_state=provider.model_dump(mode="json"))
    return provider


@app.patch("/api/agent-providers/{provider_id}", response_model=AgentProviderProfile)
def patch_agent_provider(provider_id: str, payload: AgentProviderUpdate, request: Request) -> AgentProviderProfile:
    actor = current_request_user(request)
    before = get_agent_provider(provider_id).model_dump(mode="json")
    provider = update_agent_provider(provider_id, payload)
    add_admin_audit(actor, "edit", "agent-providers", provider_id, "Agent model provider updated", before_state=before, after_state=provider.model_dump(mode="json"))
    return provider


@app.post("/api/agent-providers/{provider_id}/test")
def test_agent_provider_connection(provider_id: str, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    provider = get_agent_provider(provider_id, require_enabled=True)
    api_key = os.getenv(provider.api_key_env)
    if not api_key:
        raise HTTPException(status_code=409, detail=f"Environment variable {provider.api_key_env} is not configured")
    started = datetime.now(timezone.utc)
    try:
        agent_http_post(
            f"{provider.base_url.rstrip('/')}/chat/completions",
            {"model": provider.model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 10, "temperature": 0},
            api_key,
            provider.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Provider connection failed: {str(exc)[:300]}") from exc
    latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    add_admin_audit(actor, "test", "agent-providers", provider_id, f"Provider connection succeeded in {latency}ms")
    return {"ok": True, "latency_ms": latency, "model": provider.model}


@app.get("/api/agents", response_model=list[AgentConfig])
def agents(enabled_only: bool = False) -> list[AgentConfig]:
    return list_agents(enabled_only=enabled_only)


@app.post("/api/agents", response_model=AgentConfig)
def add_agent(payload: AgentCreate, request: Request) -> AgentConfig:
    actor = current_request_user(request)
    agent = create_agent(payload)
    add_admin_audit(actor, "create", "agents", agent.id, "Agent created", after_state=agent.model_dump(mode="json"))
    return agent


@app.get("/api/agents/runs", response_model=list[AgentRun])
def all_agent_runs(limit: int = Query(default=50, ge=1, le=200)) -> list[AgentRun]:
    return list_agent_runs(limit=limit)


@app.get("/api/agents/{agent_id}", response_model=AgentConfig)
def agent_detail(agent_id: str) -> AgentConfig:
    return get_agent(agent_id)


@app.patch("/api/agents/{agent_id}", response_model=AgentConfig)
def patch_agent(agent_id: str, payload: AgentUpdate, request: Request) -> AgentConfig:
    actor = current_request_user(request)
    before = get_agent(agent_id).model_dump(mode="json")
    agent = update_agent(agent_id, payload)
    add_admin_audit(actor, "edit", "agents", agent_id, "Agent configuration updated", before_state=before, after_state=agent.model_dump(mode="json"))
    return agent


@app.post("/api/agents/{agent_id}/clone", response_model=AgentConfig)
def clone_agent_api(agent_id: str, request: Request) -> AgentConfig:
    actor = current_request_user(request)
    agent = clone_agent(agent_id)
    add_admin_audit(actor, "clone", "agents", agent.id, f"Cloned from {agent_id}", after_state=agent.model_dump(mode="json"))
    return agent


@app.post("/api/agents/{agent_id}/run", response_model=AgentRun)
def run_agent_api(agent_id: str, payload: AgentRunRequest, request: Request) -> AgentRun:
    actor = current_request_user(request)
    run = enqueue_agent_run(agent_id, trigger="manual", symbol=payload.symbol, context=payload.context, test=False)
    add_admin_audit(actor, "run", "agents", agent_id, f"Queued Agent run {run.id}")
    return run


@app.post("/api/agents/{agent_id}/test", response_model=AgentRun)
def test_agent_api(agent_id: str, payload: AgentRunRequest, request: Request) -> AgentRun:
    actor = current_request_user(request)
    run = enqueue_agent_run(agent_id, trigger="manual", symbol=payload.symbol, context=payload.context, test=True)
    result = execute_agent_run(run.id)
    add_admin_audit(actor, "test", "agents", agent_id, f"Test Agent run {result.id}: {result.status}")
    return result


@app.get("/api/agents/{agent_id}/runs", response_model=list[AgentRun])
def agent_run_history(agent_id: str, limit: int = Query(default=50, ge=1, le=200)) -> list[AgentRun]:
    get_agent(agent_id)
    return list_agent_runs(agent_id=agent_id, limit=limit)


@app.get("/api/agent-proposals", response_model=list[AgentProposal])
def agent_proposals(status: str | None = None, limit: int = Query(default=100, ge=1, le=200)) -> list[AgentProposal]:
    return list_agent_proposals(status=status, limit=limit)


@app.post("/api/agent-proposals/{proposal_id}/approve", response_model=AgentProposal)
def approve_agent_proposal(proposal_id: str, payload: AgentProposalReview, request: Request) -> AgentProposal:
    actor = current_request_user(request)
    proposal = review_agent_proposal(proposal_id, True, actor, payload.note)
    add_admin_audit(actor, "approve", "agent-proposals", proposal_id, proposal.title, after_state=proposal.model_dump(mode="json"))
    return proposal


@app.post("/api/agent-proposals/{proposal_id}/reject", response_model=AgentProposal)
def reject_agent_proposal(proposal_id: str, payload: AgentProposalReview, request: Request) -> AgentProposal:
    actor = current_request_user(request)
    proposal = review_agent_proposal(proposal_id, False, actor, payload.note)
    add_admin_audit(actor, "reject", "agent-proposals", proposal_id, proposal.title, after_state=proposal.model_dump(mode="json"))
    return proposal


@app.get("/api/admin/users", response_model=list[AuthUser])
def admin_users(request: Request) -> list[AuthUser]:
    current_request_user(request)
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return [row_to_auth_user(row) for row in rows]


@app.post("/api/admin/users", response_model=AuthUser)
def admin_create_user(payload: UserCreate, request: Request) -> AuthUser:
    actor = current_request_user(request)
    with db_connect() as connection:
        user = insert_user(connection, payload)
    add_admin_audit(actor, "create", "users", user.id, f"Created {user.username} as {user.role}", after_state=user.model_dump(mode="json"))
    return user


@app.patch("/api/admin/users/{user_id}", response_model=AuthUser)
def admin_update_user(user_id: str, payload: UserUpdate, request: Request) -> AuthUser:
    actor = current_request_user(request)
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="User not found")
        before_user = row_to_auth_user(current).model_dump(mode="json")
        next_enabled = bool(current["enabled"]) if payload.enabled is None else payload.enabled
        next_role = current["role"] if payload.role is None else payload.role
        if user_id == actor.id and (not next_enabled or next_role != "admin"):
            raise HTTPException(status_code=409, detail="You cannot disable or demote your own administrator account")
        if current["role"] == "admin" and (not next_enabled or next_role != "admin"):
            other_admins = connection.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND enabled = 1 AND id != ?",
                (user_id,),
            ).fetchone()["total"]
            if not other_admins:
                raise HTTPException(status_code=409, detail="At least one active administrator is required")
        connection.execute(
            "UPDATE users SET display_name = ?, role = ?, enabled = ?, updated_at = ? WHERE id = ?",
            (
                current["display_name"] if payload.display_name is None else payload.display_name.strip(),
                next_role,
                int(next_enabled),
                datetime.now(timezone.utc).isoformat(),
                user_id,
            ),
        )
        if not next_enabled:
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    user = row_to_auth_user(row)
    add_admin_audit(
        actor,
        "update",
        "users",
        user.id,
        f"Updated {user.username}: role={user.role}, enabled={user.enabled}",
        before_state=before_user,
        after_state=user.model_dump(mode="json"),
    )
    return user


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_user_password(user_id: str, payload: PasswordResetRequest, request: Request) -> dict[str, bool]:
    actor = current_request_user(request)
    salt = secrets.token_hex(16)
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE users SET password_salt = ?, password_hash = ?, password_iterations = ?, updated_at = ? WHERE id = ?",
            (salt, password_digest(payload.password, salt), PASSWORD_ITERATIONS, datetime.now(timezone.utc).isoformat(), user_id),
        )
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    add_admin_audit(actor, "reset_password", "users", user_id, "Password reset and sessions revoked")
    return {"reset": True}


@app.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke_user_sessions(user_id: str, request: Request) -> dict[str, bool]:
    actor = current_request_user(request)
    with db_connect() as connection:
        if connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    add_admin_audit(actor, "revoke_sessions", "users", user_id, "All user sessions revoked")
    return {"revoked": True}


@app.get("/api/admin/resources", response_model=list[AdminResourceDescriptor])
def admin_resources(request: Request) -> list[AdminResourceDescriptor]:
    current_request_user(request)
    return list_admin_resources()


@app.post("/api/admin/resources/{resource_type}")
def admin_resource_create(resource_type: str, payload: dict[str, object], request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    if resource_type == "users":
        with db_connect() as connection:
            result: BaseModel = insert_user(connection, UserCreate.model_validate(payload))
    elif resource_type == "watchlist":
        result = add_watchlist_symbol(WatchlistItemCreate.model_validate(payload))
    elif resource_type == "screener-presets":
        result = create_screener_preset(ScreenerPresetCreate.model_validate(payload))
    elif resource_type == "news-sources":
        result = add_news_source(NewsSourceCreate.model_validate(payload))
    elif resource_type == "factors":
        result = create_factor_definition(FactorDefinitionCreate.model_validate(payload))
    elif resource_type == "strategies":
        result = create_strategy_template(StrategyTemplateCreate.model_validate(payload))
    elif resource_type == "alerts":
        result = create_alert(AlertRuleCreate.model_validate(payload))
    elif resource_type == "notification-channels":
        result = create_notification_channel(NotificationChannelCreate.model_validate(payload))
    elif resource_type == "portfolio-positions":
        result = create_portfolio_position(PortfolioPositionCreate.model_validate(payload))
    else:
        raise HTTPException(status_code=409, detail="This resource cannot be created manually")
    result_data = result.model_dump(mode="json")
    resource_id = str(result_data.get("id", result_data.get("symbol", "")))
    add_admin_audit(actor, "create", resource_type, resource_id, "Resource created from management center", after_state=result_data)
    return result_data


@app.get("/api/admin/resources/{resource_type}", response_model=AdminResourcePage)
def admin_resource_list(
    resource_type: str,
    request: Request,
    page: int = 1,
    page_size: int = 25,
    query: str = "",
    enabled: bool | None = None,
    sort: str = "",
    direction: Literal["asc", "desc"] = "desc",
) -> AdminResourcePage:
    current_request_user(request)
    return query_admin_resource(resource_type, page=page, page_size=page_size, query=query, enabled=enabled, sort=sort, direction=direction)


@app.get("/api/admin/resources/{resource_type}/export", response_class=Response)
def admin_resource_export(
    resource_type: str,
    request: Request,
    query: str = "",
    enabled: bool | None = None,
    sort: str = "",
    direction: Literal["asc", "desc"] = "desc",
) -> Response:
    current_request_user(request)
    page = query_admin_resource(resource_type, page=1, page_size=100, query=query, enabled=enabled, sort=sort, direction=direction)
    items = list(page.items)
    page_number = 2
    while len(items) < page.total:
        next_page = query_admin_resource(resource_type, page=page_number, page_size=100, query=query, enabled=enabled, sort=sort, direction=direction)
        if not next_page.items:
            break
        items.extend(next_page.items)
        page_number += 1
    output = StringIO()
    if items:
        fields = list(items[0].keys())
        writer = csv.writer(output)
        writer.writerow(fields)
        for item in items:
            writer.writerow([json.dumps(item.get(field), ensure_ascii=False) if isinstance(item.get(field), (dict, list)) else item.get(field, "") for field in fields])
    else:
        output.write("resource_id\n")
    generated = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resource_type}-{generated}.csv"'},
    )


@app.get("/api/admin/resources/{resource_type}/{resource_id}")
def admin_resource_detail(resource_type: str, resource_id: str, request: Request) -> dict[str, object]:
    current_request_user(request)
    return get_admin_resource_item(resource_type, resource_id)


@app.patch("/api/admin/resources/{resource_type}/{resource_id}")
def admin_resource_edit(resource_type: str, resource_id: str, payload: dict[str, object], request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    before = get_admin_resource_item(resource_type, resource_id)
    if resource_type == "screener-presets":
        result: BaseModel = update_screener_preset(resource_id, ScreenerPresetUpdate.model_validate(payload))
    elif resource_type == "news-sources":
        result = update_news_source(int(resource_id), NewsSourceUpdate.model_validate(payload))
    elif resource_type == "factors":
        result = update_factor_definition(resource_id, FactorDefinitionUpdate.model_validate(payload))
    elif resource_type == "strategies":
        result = update_strategy_template(resource_id, StrategyTemplateUpdate.model_validate(payload))
    elif resource_type == "alerts":
        result = update_alert_rule(resource_id, AlertRuleUpdate.model_validate(payload))
    elif resource_type == "notification-channels":
        result = update_notification_channel(resource_id, NotificationChannelUpdate.model_validate(payload))
    elif resource_type == "portfolio-positions":
        result = update_portfolio_position(resource_id, PortfolioPositionUpdate.model_validate(payload))
    elif resource_type == "settings":
        allowed_settings = {
            "active_market_provider",
            "automation_enabled",
            "automation_interval_seconds",
            "default_signal_filter",
            "default_rsi_min",
            "default_positive_only",
        }
        if resource_id not in allowed_settings or "value" not in payload:
            raise HTTPException(status_code=422, detail="Unsupported setting or missing value")
        value = str(payload["value"]).strip()
        if resource_id == "active_market_provider" and value not in MARKET_PROVIDERS:
            raise HTTPException(status_code=422, detail="Unsupported market provider")
        if resource_id in {"automation_enabled", "default_positive_only"} and value.lower() not in {"true", "false"}:
            raise HTTPException(status_code=422, detail="Boolean setting must be true or false")
        if resource_id in {"automation_interval_seconds", "default_rsi_min"}:
            try:
                int(value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Numeric setting requires an integer") from exc
        set_config_value(resource_id, value)
        result_data = {"key": resource_id, "value": value}
        add_admin_audit(actor, "edit", resource_type, resource_id, "System setting updated from management center", before_state=before, after_state=result_data)
        return result_data
    else:
        raise HTTPException(status_code=409, detail="This resource does not support field editing")
    result_data = result.model_dump(mode="json")
    add_admin_audit(actor, "edit", resource_type, resource_id, "Resource fields updated from management center", before_state=before, after_state=result_data)
    return result_data


@app.patch("/api/admin/resources/{resource_type}/{resource_id}/status")
def admin_resource_status(resource_type: str, resource_id: str, payload: ResourceStatusUpdate, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    config = admin_resource_config(resource_type)
    status_field = config.get("status")
    if not status_field or resource_type == "users":
        raise HTTPException(status_code=409, detail="This resource does not support generic status changes")
    before = get_admin_resource_item(resource_type, resource_id)
    with db_connect() as connection:
        cursor = connection.execute(
            f"UPDATE {config['table']} SET {status_field} = ? WHERE {config['id']} = ?",
            (int(payload.enabled), resource_id),
        )
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="Resource not found")
    after = get_admin_resource_item(resource_type, resource_id)
    add_admin_audit(
        actor,
        "enable" if payload.enabled else "disable",
        resource_type,
        resource_id,
        f"enabled={payload.enabled}",
        before_state=before,
        after_state=after,
    )
    return after


@app.post("/api/admin/resources/{resource_type}/bulk-status")
def admin_resource_bulk_status(resource_type: str, payload: BulkResourceStatusUpdate, request: Request) -> dict[str, int | bool]:
    actor = current_request_user(request)
    config = admin_resource_config(resource_type)
    status_field = config.get("status")
    if not status_field or resource_type == "users":
        raise HTTPException(status_code=409, detail="This resource does not support bulk status changes")
    updated = 0
    with db_connect() as connection:
        for resource_id in dict.fromkeys(payload.ids):
            updated += connection.execute(
                f"UPDATE {config['table']} SET {status_field} = ? WHERE {config['id']} = ?",
                (int(payload.enabled), resource_id),
            ).rowcount
    add_admin_audit(actor, "bulk_enable" if payload.enabled else "bulk_disable", resource_type, None, f"Updated {updated} resources")
    return {"updated": updated, "enabled": payload.enabled}


@app.post("/api/admin/resources/{resource_type}/{resource_id}/rerun")
def admin_resource_rerun(resource_type: str, resource_id: str, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    item = get_admin_resource_item(resource_type, resource_id)
    payload = item.get("request_payload") if isinstance(item.get("request_payload"), dict) else {}
    if resource_type == "backtests":
        result = run_backtest(BacktestRunRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": item["interval"], "initial_capital": item["initial_capital"]})))
    elif resource_type == "forward-tests":
        result = run_forward_test(ForwardTestRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": item["interval"], "train_bars": item["train_bars"], "forward_bars": item["forward_bars"]})))
    elif resource_type == "walk-forward-tests":
        result = run_walk_forward_test(WalkForwardRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": item["interval"], "train_bars": item["train_bars"], "forward_bars": item["forward_bars"], "windows": item["window_count"]})))
    elif resource_type == "stress-tests":
        result = run_stress_test(StressTestRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": "1h"})))
    elif resource_type == "strategy-validations":
        result = validate_strategy_template(
            str(item["strategy_id"]),
            StrategyValidationRequest(**(payload or {"symbol_limit": max(1, len(item.get("symbols", []))), "interval": "1h"})),
        )
    elif resource_type == "automation-runs":
        result = run_automation_cycle()
    else:
        raise HTTPException(status_code=409, detail="This resource cannot be rerun")
    result_data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    result_id = str(result_data.get("id", "")) if isinstance(result_data, dict) else ""
    add_admin_audit(actor, "rerun", resource_type, resource_id, f"Created run {result_id}")
    return {"source_id": resource_id, "new_id": result_id, "result": result_data}


@app.post("/api/admin/resources/{resource_type}/{resource_id}/action/{action}")
def admin_resource_action(resource_type: str, resource_id: str, action: str, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    if resource_type == "news-sources" and action == "refresh":
        result: BaseModel = refresh_source(int(resource_id))
    elif resource_type == "strategies" and action == "validate":
        result = validate_strategy_template(resource_id, StrategyValidationRequest())
    elif resource_type == "alerts" and action == "evaluate":
        rule = next((item for item in list_alert_rules() if item.id == resource_id), None)
        if rule is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        result = evaluate_alert_rule(rule)
    elif resource_type == "notification-channels" and action == "test":
        result = test_notification_channel(resource_id)
    elif resource_type == "auth-sessions" and action == "revoke":
        with db_connect() as connection:
            cursor = connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (resource_id,))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Session not found")
        result = {"revoked": True}
    else:
        raise HTTPException(status_code=409, detail="Unsupported resource action")
    result_data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    add_admin_audit(actor, action, resource_type, resource_id, "Resource action executed")
    return result_data


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.websocket("/api/stream/market")
async def market_stream(websocket: WebSocket, interval: int = 5) -> None:
    session = lookup_auth_session(websocket.cookies.get(SESSION_COOKIE))
    if session is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    delay = max(2, min(60, interval))
    try:
        while True:
            await websocket.send_json(market_stream_payload())
            await asyncio.sleep(delay)
    except WebSocketDisconnect:
        return


@app.get("/api/health", response_model=SystemHealth)
def system_health() -> SystemHealth:
    return get_system_health()


@app.get("/api/settings", response_model=AppSettings)
def app_settings() -> AppSettings:
    return get_app_settings()


@app.patch("/api/settings", response_model=AppSettings)
def patch_app_settings(payload: AppSettingsUpdate) -> AppSettings:
    return update_app_settings(payload)


@app.get("/api/export/{dataset}", response_class=Response)
def export_dataset(dataset: str, symbol: str | None = None) -> Response:
    return export_dataset_csv(dataset, symbol=symbol)


@app.get("/api/market-provider", response_model=MarketProviderStatus)
def market_provider_status() -> MarketProviderStatus:
    active_name = get_active_market_provider_name()
    return MARKET_PROVIDERS[active_name].status(is_active=True)


@app.get("/api/market-provider/options", response_model=list[MarketProviderStatus])
def market_provider_options() -> list[MarketProviderStatus]:
    active_name = get_active_market_provider_name()
    return [provider.status(is_active=name == active_name) for name, provider in MARKET_PROVIDERS.items()]


@app.post("/api/market-provider", response_model=MarketProviderStatus)
def update_market_provider(payload: MarketProviderConfig) -> MarketProviderStatus:
    return set_active_market_provider(payload.provider)


@app.get("/api/symbols", response_model=list[MarketSymbol])
def symbols() -> list[MarketSymbol]:
    return get_market_provider().list_symbols()


@app.get("/api/watchlist", response_model=list[SymbolSnapshot])
def watchlist() -> list[SymbolSnapshot]:
    return list_watchlist_snapshots()


@app.post("/api/watchlist", response_model=SymbolSnapshot)
def add_watchlist_item(payload: WatchlistItemCreate) -> SymbolSnapshot:
    return add_watchlist_symbol(payload)


@app.delete("/api/watchlist/{symbol}", deprecated=True)
def remove_watchlist_item(symbol: str) -> dict[str, bool]:
    return delete_watchlist_symbol(symbol)


@app.get("/api/market-breadth", response_model=MarketBreadth)
def market_breadth() -> MarketBreadth:
    return get_market_breadth()


@app.get("/api/screener", response_model=ScreenerResult)
def screener(
    min_change: float | None = None,
    max_change: float | None = None,
    min_rsi: float | None = Query(default=None, ge=0, le=100),
    max_rsi: float | None = Query(default=None, ge=0, le=100),
    min_trend: int | None = Query(default=None, ge=0, le=100),
    signal: str | None = None,
    market: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> ScreenerResult:
    return run_screener(
        min_change=min_change,
        max_change=max_change,
        min_rsi=min_rsi,
        max_rsi=max_rsi,
        min_trend=min_trend,
        signal=signal,
        market=market,
        limit=limit,
    )


@app.get("/api/strategy-candidates", response_model=StrategyCandidateReport)
def strategy_candidates(
    min_change: float | None = None,
    max_change: float | None = None,
    min_rsi: float | None = None,
    max_rsi: float | None = None,
    min_trend: int | None = None,
    signal: str | None = None,
    market: str | None = None,
    factor_id: str | None = None,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = Query(default=6, ge=1, le=12),
) -> StrategyCandidateReport:
    return strategy_candidate_report(
        min_change=min_change,
        max_change=max_change,
        min_rsi=min_rsi,
        max_rsi=max_rsi,
        min_trend=min_trend,
        signal=signal,
        market=market,
        factor_id=factor_id,
        interval=interval,
        limit=limit,
    )


@app.get("/api/screener/presets", response_model=list[ScreenerPreset])
def screener_presets() -> list[ScreenerPreset]:
    return list_screener_presets()


@app.get("/api/screener/preset-validations", response_model=list[ScreenerPresetValidation])
def screener_preset_validations() -> list[ScreenerPresetValidation]:
    return validate_screener_presets()


@app.get("/api/filters/library-score", response_model=FilterLibraryScoreReport)
def filter_library_score() -> FilterLibraryScoreReport:
    return filter_library_score_report()


@app.get("/api/filters/strategy-coverage", response_model=FilterStrategyCoverageReport)
def filter_strategy_coverage() -> FilterStrategyCoverageReport:
    return filter_strategy_coverage_report()


@app.post("/api/screener/presets", response_model=ScreenerPreset)
def add_screener_preset(payload: ScreenerPresetCreate) -> ScreenerPreset:
    return create_screener_preset(payload)


@app.patch("/api/screener/presets/{preset_id}", response_model=ScreenerPreset)
def patch_screener_preset(preset_id: str, payload: ScreenerPresetUpdate) -> ScreenerPreset:
    return update_screener_preset(preset_id, payload)


@app.delete("/api/screener/presets/{preset_id}", deprecated=True)
def remove_screener_preset(preset_id: str) -> dict[str, bool]:
    return delete_screener_preset(preset_id)


@app.get("/api/candles/{symbol}", response_model=list[Candle])
def candles(
    symbol: str,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = Query(default=120, ge=40, le=240),
) -> list[Candle]:
    provider_name = get_active_market_provider_name()
    try:
        return MARKET_PROVIDERS[provider_name].candles(symbol=symbol, interval=interval, limit=limit)
    except HTTPException:
        if provider_name == "mock":
            raise
        mock_symbols = {item.symbol for item in MARKET_PROVIDERS["mock"].list_symbols()}
        if symbol.upper() not in mock_symbols:
            raise
        return MARKET_PROVIDERS["mock"].candles(symbol=symbol, interval=interval, limit=limit)


@app.get("/api/indicators/{symbol}", response_model=list[IndicatorSummary])
def indicators(symbol: str) -> list[IndicatorSummary]:
    provider_name = get_active_market_provider_name()
    try:
        return MARKET_PROVIDERS[provider_name].indicators(symbol)
    except HTTPException:
        if provider_name == "mock":
            raise
        mock_symbols = {item.symbol for item in MARKET_PROVIDERS["mock"].list_symbols()}
        if symbol.upper() not in mock_symbols:
            raise
        return MARKET_PROVIDERS["mock"].indicators(symbol)


@app.get("/api/news-sources", response_model=list[NewsSource])
def list_news_sources() -> list[NewsSource]:
    init_db()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM news_sources ORDER BY created_at DESC").fetchall()
    return [row_to_source(row) for row in rows]


@app.post("/api/news-sources", response_model=NewsSource)
def create_news_source(payload: NewsSourceCreate) -> NewsSource:
    init_db()
    symbols = ",".join(normalize_symbols(payload.symbols))
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with db_connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO news_sources (
                    name, type, url, enabled, symbols, poll_interval_seconds, created_at
                )
                VALUES (?, 'rss', ?, ?, ?, ?, ?)
                """,
                (
                    payload.name.strip(),
                    str(payload.url),
                    int(payload.enabled),
                    symbols,
                    max(60, payload.poll_interval_seconds),
                    created_at,
                ),
            )
            row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="News source URL already exists") from exc
    return row_to_source(row)


@app.patch("/api/news-sources/{source_id}", response_model=NewsSource)
def update_news_source(source_id: int, payload: NewsSourceUpdate) -> NewsSource:
    init_db()
    current_sources = {source.id: source for source in list_news_sources()}
    if source_id not in current_sources:
        raise HTTPException(status_code=404, detail="News source not found")

    current = current_sources[source_id]
    name = payload.name.strip() if payload.name is not None else current.name
    url = str(payload.url) if payload.url is not None else current.url
    enabled = int(payload.enabled) if payload.enabled is not None else int(current.enabled)
    symbols = ",".join(normalize_symbols(payload.symbols)) if payload.symbols is not None else ",".join(current.symbols)
    poll_interval = max(60, payload.poll_interval_seconds) if payload.poll_interval_seconds is not None else current.poll_interval_seconds

    with db_connect() as connection:
        connection.execute(
            """
            UPDATE news_sources
            SET name = ?, url = ?, enabled = ?, symbols = ?, poll_interval_seconds = ?
            WHERE id = ?
            """,
            (name, url, enabled, symbols, poll_interval, source_id),
        )
        row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (source_id,)).fetchone()
    return row_to_source(row)


@app.post("/api/news-sources/refresh", response_model=list[NewsRefreshResult])
def refresh_all_news_sources() -> list[NewsRefreshResult]:
    init_db()
    sources = [source for source in list_news_sources() if source.enabled]
    return [refresh_source(source.id) for source in sources]


@app.post("/api/news-sources/{source_id}/refresh", response_model=NewsRefreshResult)
def refresh_news_source(source_id: int) -> NewsRefreshResult:
    init_db()
    return refresh_source(source_id)


@app.get("/api/news/{symbol}", response_model=list[NewsEvent])
def news_events(symbol: str, limit: int = 7) -> list[NewsEvent]:
    limit = max(1, min(20, limit))
    base_symbol = symbol.upper()
    persisted_events = source_articles_to_events(base_symbol, limit)
    if persisted_events:
        return sorted(persisted_events, key=lambda item: item.published_at)

    seed = sum(ord(char) for char in f"news:{base_symbol}")
    random = Random(seed)
    now = datetime.now(timezone.utc)
    events: list[NewsEvent] = []
    for index in range(limit):
        event_type, sentiment, title, summary, source, base_impact = EVENT_TEMPLATES[(index + seed) % len(EVENT_TEMPLATES)]
        hours_ago = (limit - index) * random.choice([3, 5, 8, 13])
        impact = max(30, min(98, base_impact + random.randint(-10, 12)))
        reaction_prefix = "+" if sentiment == "bullish" else "-" if sentiment == "bearish" else ""
        reaction_value = random.uniform(0.2, 3.8)
        events.append(
            NewsEvent(
                id=f"{base_symbol.lower()}-news-{index + 1}",
                symbol=base_symbol,
                title=f"{base_symbol} {title}",
                summary=summary,
                source=source,
                url=f"https://example.com/{base_symbol.lower()}/news/{index + 1}",
                published_at=(now - timedelta(hours=hours_ago)).isoformat(),
                event_type=event_type,  # type: ignore[arg-type]
                sentiment=sentiment,  # type: ignore[arg-type]
                impact_score=impact,
                price_reaction=f"{reaction_prefix}{reaction_value:.1f}% / 1h" if reaction_prefix else f"{reaction_value:.1f}% range / 1h",
            )
        )
    return sorted(events, key=lambda item: item.published_at)


@app.get("/api/news-events/{news_id}", response_model=NewsDetail)
def news_detail(news_id: str) -> NewsDetail:
    return get_news_detail(news_id)


@app.get("/api/alerts", response_model=list[AlertRule])
def alerts(symbol: str | None = None) -> list[AlertRule]:
    return list_alert_rules(symbol)


@app.post("/api/alerts", response_model=AlertRule)
def create_alert(payload: AlertRuleCreate) -> AlertRule:
    init_db()
    valid_symbols = {item.symbol for item in get_market_provider().list_symbols()}
    symbol = payload.symbol.upper()
    if symbol not in valid_symbols:
        raise HTTPException(status_code=400, detail="Symbol is not supported by active market provider")
    created_at = datetime.now(timezone.utc).isoformat()
    alert_id = sha256(f"{symbol}:{payload.metric}:{payload.operator}:{payload.threshold}:{created_at}".encode("utf-8")).hexdigest()[:20]
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO alert_rules (
                id, symbol, name, metric, operator, threshold, enabled, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                symbol,
                payload.name.strip() or f"{symbol} {payload.metric}",
                payload.metric,
                payload.operator,
                payload.threshold,
                int(payload.enabled),
                created_at,
            ),
        )
        row = connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()
    return row_to_alert(row)


@app.patch("/api/alerts/{alert_id}", response_model=AlertRule)
def update_alert(alert_id: str, payload: AlertRuleUpdate) -> AlertRule:
    return update_alert_rule(alert_id, payload)


@app.delete("/api/alerts/{alert_id}", deprecated=True)
def remove_alert(alert_id: str) -> dict[str, bool]:
    return delete_alert_rule(alert_id)


@app.post("/api/alerts/evaluate", response_model=list[AlertEvaluation])
def evaluate_alerts(symbol: str | None = None) -> list[AlertEvaluation]:
    rules = [rule for rule in list_alert_rules(symbol) if rule.enabled]
    return [evaluate_alert_rule(rule) for rule in rules]


@app.get("/api/events", response_model=list[EventLog])
def events(symbol: str | None = None, limit: int = 50) -> list[EventLog]:
    return list_event_logs(symbol=symbol, limit=limit)


@app.get("/api/automation/settings", response_model=AutomationSettings)
def automation_settings() -> AutomationSettings:
    return get_automation_settings()


@app.post("/api/automation/settings", response_model=AutomationSettings)
def update_automation_settings(settings: AutomationSettings) -> AutomationSettings:
    return set_automation_settings(settings)


@app.post("/api/automation/run", response_model=AutomationRunResult)
def automation_run() -> AutomationRunResult:
    return run_automation_cycle()


@app.get("/api/automation/runs", response_model=list[AutomationRunRecord])
def automation_runs(limit: int = 20) -> list[AutomationRunRecord]:
    return list_automation_runs(limit=limit)


@app.get("/api/notifications/channels", response_model=list[NotificationChannel])
def notification_channels() -> list[NotificationChannel]:
    return list_notification_channels()


@app.post("/api/notifications/channels", response_model=NotificationChannel)
def add_notification_channel(payload: NotificationChannelCreate) -> NotificationChannel:
    return create_notification_channel(payload)


@app.patch("/api/notifications/channels/{channel_id}", response_model=NotificationChannel)
def patch_notification_channel(channel_id: str, payload: NotificationChannelUpdate) -> NotificationChannel:
    return update_notification_channel(channel_id, payload)


@app.delete("/api/notifications/channels/{channel_id}", deprecated=True)
def remove_notification_channel(channel_id: str) -> dict[str, bool]:
    return delete_notification_channel(channel_id)


@app.get("/api/notifications/deliveries", response_model=list[NotificationDelivery])
def notification_deliveries(channel_id: str | None = None, limit: int = 50) -> list[NotificationDelivery]:
    return list_notification_deliveries(channel_id=channel_id, limit=limit)


@app.get("/api/portfolio", response_model=PortfolioSummary)
def portfolio() -> PortfolioSummary:
    return get_portfolio_summary()


@app.get("/api/portfolio/risk", response_model=PortfolioRiskReport)
def portfolio_risk() -> PortfolioRiskReport:
    return get_portfolio_risk_report()


@app.post("/api/portfolio/positions", response_model=PortfolioPosition)
def add_portfolio_position(payload: PortfolioPositionCreate) -> PortfolioPosition:
    return create_portfolio_position(payload)


@app.patch("/api/portfolio/positions/{position_id}", response_model=PortfolioPosition)
def patch_portfolio_position(position_id: str, payload: PortfolioPositionUpdate) -> PortfolioPosition:
    return update_portfolio_position(position_id, payload)


@app.delete("/api/portfolio/positions/{position_id}", deprecated=True)
def remove_portfolio_position(position_id: str) -> dict[str, bool]:
    return delete_portfolio_position(position_id)


@app.get("/api/chart-events/{symbol}", response_model=list[NewsEvent])
def chart_events(symbol: str) -> list[NewsEvent]:
    return [event for event in news_events(symbol, limit=10) if event.impact_score >= 50]


@app.get("/api/ai/briefing", response_model=AiBriefing)
def ai_briefing() -> AiBriefing:
    agent = next((item for item in list_agents(enabled_only=True) if item.template_type == "briefing" and item.builtin), None)
    if agent is None:
        now = datetime.now(timezone.utc).isoformat()
        return AiBriefing(id="briefing-disabled", title="AI 全局盯盘简报已停用", summary="请在 Agent 管理中心启用 Briefing Agent。",
                          risk_level="watch", generated_at=now, opportunities=[], risks=["简报 Agent 当前停用。"], actions=["检查 Agent 配置。"], evidence=[])
    output = agent_output_for_api(agent)
    generated_at = str(output.get("created_at") or datetime.now(timezone.utc).isoformat())
    return AiBriefing(
        id=sha256(f"{agent.id}:{generated_at}".encode()).hexdigest()[:20], title="AI 全局盯盘简报",
        summary=str(output.get("summary", "暂无简报输出。")), risk_level=output.get("risk_level", "watch"), generated_at=generated_at,
        opportunities=[str(item) for item in output.get("opportunities", [])][:4],
        risks=[str(item) for item in output.get("risks", [])][:4], actions=[str(item) for item in output.get("actions", [])][:5],
        evidence=[str(item) for item in output.get("evidence", [])][:8],
    )


@app.get("/api/ai/insights/{symbol}", response_model=list[AiInsight])
def ai_insights(symbol: str) -> list[AiInsight]:
    base_symbol = symbol.upper()
    titles = {"market_monitor": "实时盯盘结论", "news_analyst": "新闻催化解读", "risk_sentinel": "风险提示"}
    insights: list[AiInsight] = []
    for agent in list_agents(enabled_only=True):
        if agent.template_type not in titles:
            continue
        if agent.symbol_scope == "custom" and base_symbol not in agent.symbols:
            continue
        output = agent_output_for_api(agent, base_symbol)
        insights.append(AiInsight(
            id=f"{base_symbol.lower()}-{agent.id}", agent=agent.name, symbol=base_symbol, title=titles[agent.template_type],
            summary=str(output.get("summary", "暂无分析输出。")), severity=output.get("risk_level", "watch"),
            confidence=max(0, min(100, int(output.get("confidence", 50)))),
            evidence=[str(item) for item in output.get("evidence", [])][:8],
            created_at=str(output.get("created_at") or datetime.now(timezone.utc).isoformat()),
        ))
    return insights


@app.get("/api/factors/{symbol}", response_model=list[FactorIdea])
def factor_ideas(symbol: str) -> list[FactorIdea]:
    base_symbol = symbol.upper()
    seed = sum(ord(char) for char in f"factor:{base_symbol}")
    random = Random(seed)
    return [
        FactorIdea(
            id=f"{base_symbol.lower()}-{factor.id}",
            name=factor.name,
            category=factor.category,
            description=factor.description,
            signal=factor.signal,
            score=random.randint(54, 91),
            horizon=factor.horizon,
        )
        for factor in list_factor_definitions(enabled_only=True)
    ]


@app.get("/api/factor-definitions", response_model=list[FactorDefinition])
def factor_definitions(enabled_only: bool = False) -> list[FactorDefinition]:
    return list_factor_definitions(enabled_only=enabled_only)


@app.get("/api/data-catalog", response_model=list[DataCatalogItem])
def data_catalog() -> list[DataCatalogItem]:
    return data_catalog_items()


@app.get("/api/data-quality", response_model=DataQualityReport)
def data_quality() -> DataQualityReport:
    return data_quality_report()


@app.get("/api/data-freshness", response_model=DataFreshnessReport)
def data_freshness() -> DataFreshnessReport:
    return data_freshness_report()


@app.get("/api/market-regime", response_model=MarketRegime)
def market_regime() -> MarketRegime:
    return record_market_regime_snapshot(market_regime_report())


@app.get("/api/market-regime/history", response_model=list[MarketRegime])
def market_regime_history(limit: int = 12) -> list[MarketRegime]:
    return list_market_regime_history(limit=limit)


@app.get("/api/market-regime/transition", response_model=MarketRegimeTransition)
def market_regime_transition() -> MarketRegimeTransition:
    return market_regime_transition_report()


@app.get("/api/regime-scenario-risk", response_model=RegimeScenarioRiskReport)
def regime_scenario_risk() -> RegimeScenarioRiskReport:
    return regime_scenario_risk_report()


@app.get("/api/research-readiness", response_model=ResearchReadiness)
def research_readiness() -> ResearchReadiness:
    return research_readiness_report()


@app.get("/api/research-coverage", response_model=ResearchCoverageReport)
def research_coverage() -> ResearchCoverageReport:
    return research_coverage_report()


@app.get("/api/factor-coverage", response_model=FactorCoverageReport)
def factor_coverage() -> FactorCoverageReport:
    return factor_coverage_report()


@app.get("/api/factor-lifecycle", response_model=FactorLifecycleReport)
def factor_lifecycle(symbol: str | None = None) -> FactorLifecycleReport:
    return factor_lifecycle_report(symbol=symbol)


@app.get("/api/data-lineage", response_model=DataLineageReport)
def data_lineage() -> DataLineageReport:
    return data_lineage_report()


@app.get("/api/research-run-audit", response_model=ResearchRunAuditReport)
def research_run_audit(
    limit: int = 12,
    symbol: str | None = None,
    factor_id: str | None = None,
) -> ResearchRunAuditReport:
    return research_run_audit_report(limit=limit, symbol=symbol, factor_id=factor_id)


@app.post("/api/factor-coverage/backfill", response_model=FactorCoverageBackfillResult)
def factor_coverage_backfill(payload: FactorCoverageBackfillRequest) -> FactorCoverageBackfillResult:
    return backfill_factor_coverage(payload)


@app.post("/api/research-coverage/backfill", response_model=ResearchCoverageBackfillResult)
def research_coverage_backfill(payload: ResearchCoverageBackfillRequest) -> ResearchCoverageBackfillResult:
    return backfill_research_coverage(payload)


@app.get("/api/research-action-plan", response_model=ResearchActionPlan)
def research_actions() -> ResearchActionPlan:
    return research_action_plan()


@app.get("/api/research-dashboard", response_model=ResearchDashboardSummary)
def research_dashboard() -> ResearchDashboardSummary:
    return research_dashboard_summary()


@app.get("/api/research-health", response_model=ResearchHealthReport)
def research_health() -> ResearchHealthReport:
    return research_health_report()


@app.post("/api/filters/validate", response_model=FilterValidationResult)
def filter_validation(payload: FilterValidationRequest) -> FilterValidationResult:
    return validate_filters(payload)


@app.post("/api/filters/contribution", response_model=FilterContributionReport)
def filter_contribution(payload: FilterValidationRequest) -> FilterContributionReport:
    return filter_contribution_report(payload)


@app.post("/api/filters/admission", response_model=FilterAdmissionReport)
def filter_admission(payload: FilterValidationRequest) -> FilterAdmissionReport:
    return filter_admission_report(payload)


@app.post("/api/filters/stress-test", response_model=FilterStressTestReport)
def filter_stress_test(payload: FilterValidationRequest) -> FilterStressTestReport:
    return filter_stress_test_report(payload)


@app.get("/api/filters/regime-templates", response_model=list[RegimeFilterTemplate])
def filter_regime_templates() -> list[RegimeFilterTemplate]:
    return regime_filter_templates()


@app.get("/api/regime-playbook", response_model=RegimePlaybook)
def regime_playbook(symbol: str | None = None) -> RegimePlaybook:
    return regime_playbook_report(symbol=symbol)


@app.get("/api/strategy-templates", response_model=list[StrategyTemplate])
def strategy_templates(enabled_only: bool = False) -> list[StrategyTemplate]:
    return list_strategy_templates(enabled_only=enabled_only)


@app.post("/api/strategy-templates", response_model=StrategyTemplate)
def add_strategy_template(payload: StrategyTemplateCreate) -> StrategyTemplate:
    return create_strategy_template(payload)


@app.patch("/api/strategy-templates/{strategy_id}", response_model=StrategyTemplate)
def patch_strategy_template(strategy_id: str, payload: StrategyTemplateUpdate) -> StrategyTemplate:
    return update_strategy_template(strategy_id, payload)


@app.delete("/api/strategy-templates/{strategy_id}", deprecated=True)
def remove_strategy_template(strategy_id: str) -> dict[str, bool]:
    return delete_strategy_template(strategy_id)


@app.post("/api/strategy-templates/{strategy_id}/validate", response_model=StrategyValidationResult)
def validate_strategy_template(strategy_id: str, payload: StrategyValidationRequest) -> StrategyValidationResult:
    return run_strategy_validation(strategy_id, payload)


@app.get("/api/strategy-promotion-gates", response_model=list[StrategyPromotionGate])
def strategy_promotion_gate_list() -> list[StrategyPromotionGate]:
    return strategy_promotion_gates()


@app.get("/api/strategy-promotion-gates/{strategy_id}", response_model=StrategyPromotionGate)
def strategy_promotion_gate(strategy_id: str) -> StrategyPromotionGate:
    return strategy_promotion_gate_report(strategy_id)


@app.get("/api/strategy-validations", response_model=list[StrategyValidationResult])
def strategy_validations(strategy_id: str | None = None, limit: int = 10) -> list[StrategyValidationResult]:
    return list_strategy_validations(strategy_id=strategy_id, limit=limit)


@app.get("/api/strategy-validation-decay", response_model=StrategyValidationDecayReport)
def strategy_validation_decay(limit: int = 12) -> StrategyValidationDecayReport:
    return strategy_validation_decay_report(limit=limit)


@app.get("/api/strategy-risk-controls", response_model=StrategyRiskControlReport)
def strategy_risk_controls() -> StrategyRiskControlReport:
    return strategy_risk_control_report()


@app.get("/api/market-state-controls", response_model=MarketStateControlReport)
def market_state_controls() -> MarketStateControlReport:
    return market_state_control_report()


@app.get("/api/strategy-operating-plan", response_model=StrategyOperatingPlanReport)
def strategy_operating_plan() -> StrategyOperatingPlanReport:
    return strategy_operating_plan_report()


@app.get("/api/strategy-evidence-packs", response_model=StrategyEvidencePackReport)
def strategy_evidence_packs() -> StrategyEvidencePackReport:
    return strategy_evidence_pack_report()


@app.post("/api/factor-definitions", response_model=FactorDefinition)
def add_factor_definition(payload: FactorDefinitionCreate) -> FactorDefinition:
    return create_factor_definition(payload)


@app.patch("/api/factor-definitions/{factor_id}", response_model=FactorDefinition)
def patch_factor_definition(factor_id: str, payload: FactorDefinitionUpdate) -> FactorDefinition:
    return update_factor_definition(factor_id, payload)


@app.post("/api/backtest-runs", response_model=BacktestRunResult)
def create_backtest_run(payload: BacktestRunRequest) -> BacktestRunResult:
    result = run_backtest(payload)
    enqueue_agent_event("backtest_completed", symbol=payload.symbol, context={"backtest_id": result.id, "factor_id": payload.factor_id})
    return result


@app.get("/api/backtest-benchmark/{symbol}", response_model=BacktestBenchmarkReport)
def backtest_benchmark(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 180,
) -> BacktestBenchmarkReport:
    return backtest_benchmark_report(symbol=symbol, factor_id=factor_id, interval=interval, limit=limit)


@app.get("/api/backtest-forward-consistency/{symbol}", response_model=BacktestForwardConsistencyReport)
def backtest_forward_consistency(symbol: str, factor_id: str = "momentum-news") -> BacktestForwardConsistencyReport:
    return backtest_forward_consistency_report(symbol=symbol, factor_id=factor_id)


@app.post("/api/forward-tests", response_model=ForwardTestResult)
def create_forward_test(payload: ForwardTestRequest) -> ForwardTestResult:
    return run_forward_test(payload)


@app.get("/api/forward-tests/{symbol}", response_model=list[ForwardTestResult])
def forward_tests(symbol: str, limit: int = 10) -> list[ForwardTestResult]:
    return list_forward_tests(symbol, limit=limit)


@app.get("/api/forward-leaderboard", response_model=ForwardLeaderboard)
def forward_leaderboard(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> ForwardLeaderboard:
    return forward_leaderboard_report(limit=limit, symbol=symbol, factor_id=factor_id)


@app.get("/api/strategy-backtest-monitor", response_model=StrategyBacktestMonitorReport)
def strategy_backtest_monitor(limit: int = 12) -> StrategyBacktestMonitorReport:
    return strategy_backtest_monitor_report(limit=limit)


@app.get("/api/strategy-forward-monitor", response_model=StrategyForwardMonitorReport)
def strategy_forward_monitor(limit: int = 12) -> StrategyForwardMonitorReport:
    return strategy_forward_monitor_report(limit=limit)


@app.get("/api/strategy-sensitivity", response_model=StrategySensitivityReport)
def strategy_sensitivity(limit: int = 12) -> StrategySensitivityReport:
    return strategy_sensitivity_report(limit=limit)


@app.get("/api/strategy-revalidation-plan", response_model=StrategyRevalidationPlanReport)
def strategy_revalidation_plan(limit: int = 12) -> StrategyRevalidationPlanReport:
    return strategy_revalidation_plan_report(limit=limit)


@app.get("/api/strategy-validation-coverage", response_model=StrategyValidationCoverageReport)
def strategy_validation_coverage(limit: int = 12) -> StrategyValidationCoverageReport:
    return strategy_validation_coverage_report(limit=limit)


@app.post("/api/walk-forward-tests", response_model=WalkForwardResult)
def create_walk_forward_test(payload: WalkForwardRequest) -> WalkForwardResult:
    return run_walk_forward_test(payload)


@app.get("/api/walk-forward-tests/{symbol}", response_model=list[WalkForwardResult])
def walk_forward_tests(symbol: str, limit: int = 10) -> list[WalkForwardResult]:
    return list_walk_forward_tests(symbol, limit=limit)


@app.get("/api/walk-forward-leaderboard", response_model=WalkForwardLeaderboard)
def walk_forward_leaderboard(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> WalkForwardLeaderboard:
    return walk_forward_leaderboard_report(limit=limit, symbol=symbol, factor_id=factor_id)


@app.post("/api/stress-tests", response_model=StressTestResult)
def create_stress_test(payload: StressTestRequest) -> StressTestResult:
    return run_stress_test(payload)


@app.get("/api/stress-tests/{symbol}", response_model=list[StressTestResult])
def stress_tests(symbol: str, limit: int = 10) -> list[StressTestResult]:
    return list_stress_tests(symbol, limit=limit)


@app.get("/api/stress-leaderboard", response_model=StressLeaderboard)
def stress_leaderboard(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> StressLeaderboard:
    return stress_leaderboard_report(limit=limit, symbol=symbol, factor_id=factor_id)


@app.get("/api/robustness/{symbol}", response_model=RobustnessReport)
def robustness(symbol: str) -> RobustnessReport:
    return robustness_report(symbol)


@app.get("/api/drift/{symbol}", response_model=StrategyDriftReport)
def strategy_drift(symbol: str) -> StrategyDriftReport:
    return strategy_drift_report(symbol)


@app.get("/api/allocation/{symbol}", response_model=StrategyAllocationReport)
def strategy_allocation(symbol: str) -> StrategyAllocationReport:
    return strategy_allocation_report(symbol)


@app.get("/api/trade-readiness/{symbol}", response_model=TradeReadinessReport)
def trade_readiness(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    min_rsi: float | None = Query(default=None, ge=0, le=100),
    min_trend: int | None = Query(default=None, ge=0, le=100),
    signal: str | None = None,
) -> TradeReadinessReport:
    return trade_readiness_report(
        symbol=symbol,
        factor_id=factor_id,
        interval=interval,
        min_rsi=min_rsi,
        min_trend=min_trend,
        signal=signal,
    )


@app.get("/api/regime-risk-gate/{symbol}", response_model=RegimeRiskGate)
def regime_risk_gate(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
) -> RegimeRiskGate:
    return regime_risk_gate_report(symbol=symbol, factor_id=factor_id, interval=interval)


@app.post("/api/research-pipelines", response_model=ResearchPipelineResult)
def create_research_pipeline(payload: ResearchPipelineRequest) -> ResearchPipelineResult:
    return run_research_pipeline(payload)


@app.post("/api/factor-admission", response_model=FactorAdmissionReport)
def create_factor_admission(payload: FactorAdmissionRequest) -> FactorAdmissionReport:
    return factor_admission_report(payload)


@app.get("/api/factor-admission-queue", response_model=FactorAdmissionQueueReport)
def factor_admission_queue(
    limit: int = 6,
    factor_id: str | None = None,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
) -> FactorAdmissionQueueReport:
    return factor_admission_queue_report(limit=limit, factor_id=factor_id, interval=interval)


@app.get("/api/revalidation-queue", response_model=RevalidationQueueReport)
def revalidation_queue(limit: int = 8, stale_days: int = 7, factor_id: str | None = None) -> RevalidationQueueReport:
    return revalidation_queue_report(limit=limit, stale_days=stale_days, factor_id=factor_id)


@app.post("/api/sensitivity-tests", response_model=SensitivityTestResult)
def create_sensitivity_test(payload: SensitivityTestRequest) -> SensitivityTestResult:
    return run_sensitivity_test(payload)


@app.post("/api/parameter-optimization", response_model=ParameterOptimizationReport)
def create_parameter_optimization(payload: ParameterOptimizationRequest) -> ParameterOptimizationReport:
    return parameter_optimization_report(payload)


@app.get("/api/factor-leaderboard/{symbol}", response_model=FactorLeaderboard)
def factor_leaderboard(symbol: str) -> FactorLeaderboard:
    return factor_leaderboard_report(symbol)


@app.get("/api/factor-correlation/{symbol}", response_model=FactorCorrelationReport)
def factor_correlation(symbol: str, interval: Literal["15m", "1h", "4h", "1d"] = "1h", limit: int = 180) -> FactorCorrelationReport:
    return factor_correlation_report(symbol, interval=interval, limit=limit)


@app.get("/api/regime-exposure", response_model=RegimeExposureReport)
def regime_exposure(interval: Literal["15m", "1h", "4h", "1d"] = "1h") -> RegimeExposureReport:
    return regime_exposure_report(interval=interval)


@app.get("/api/regime-attribution/{symbol}", response_model=RegimeAttributionReport)
def regime_attribution(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 220,
) -> RegimeAttributionReport:
    return regime_attribution_report(symbol, factor_id=factor_id, interval=interval, limit=limit)


@app.get("/api/regime-factor-fit/{symbol}", response_model=RegimeFactorFitReport)
def regime_factor_fit(
    symbol: str,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 6,
) -> RegimeFactorFitReport:
    return regime_factor_fit_report(symbol, interval=interval, limit=limit)


@app.get("/api/strategy-leaderboard", response_model=StrategyLeaderboard)
def strategy_leaderboard() -> StrategyLeaderboard:
    return strategy_leaderboard_report()


@app.get("/api/strategy-regime-fit", response_model=StrategyRegimeFitReport)
def strategy_regime_fit(limit: int = 8) -> StrategyRegimeFitReport:
    return strategy_regime_fit_report(limit=limit)


@app.get("/api/strategy-regime-matrix", response_model=StrategyRegimeMatrixReport)
def strategy_regime_matrix() -> StrategyRegimeMatrixReport:
    return strategy_regime_matrix_report()


@app.get("/api/strategy-crowding", response_model=StrategyCrowdingReport)
def strategy_crowding() -> StrategyCrowdingReport:
    return strategy_crowding_report()


@app.get("/api/strategy-lifecycle", response_model=StrategyLifecycleReport)
def strategy_lifecycle() -> StrategyLifecycleReport:
    return strategy_lifecycle_report()


@app.get("/api/backtest-runs/{symbol}", response_model=list[BacktestRunResult])
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


@app.get("/api/backtests/{symbol}", response_model=list[BacktestSummary])
def backtest_summaries(symbol: str) -> list[BacktestSummary]:
    base_symbol = symbol.upper()
    persisted = list_backtest_runs(base_symbol, limit=3)
    if persisted:
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
            for item in persisted
        ]

    seed = sum(ord(char) for char in f"backtest:{base_symbol}")
    random = Random(seed)
    factors = factor_ideas(base_symbol)
    summaries: list[BacktestSummary] = []
    for index, factor in enumerate(factors[:3]):
        equity = [100.0]
        for step in range(18):
            drift = 0.35 if factor.signal == "long" else 0.08
            equity.append(round(max(82, equity[-1] * (1 + (drift + random.uniform(-1.2, 1.6)) / 100)), 2))
        total_return = round((equity[-1] / equity[0] - 1) * 100, 2)
        max_drawdown = round(random.uniform(3.8, 14.6), 2)
        summaries.append(
            BacktestSummary(
                id=f"{base_symbol.lower()}-bt-{index + 1}",
                name=f"{base_symbol} · {factor.name}",
                symbol=base_symbol,
                factor=factor.name,
                total_return=total_return,
                max_drawdown=max_drawdown,
                sharpe=round(random.uniform(0.6, 2.3), 2),
                win_rate=round(random.uniform(46, 68), 1),
                trades=random.randint(18, 76),
                status="completed",
                equity_curve=equity,
            )
        )
    return summaries
