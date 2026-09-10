from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, HttpUrl

from app.schemas.agents import AgentRun
from app.schemas.market import HealthComponent, NewsRefreshResult, SymbolSnapshot

class AlertRule(BaseModel):
    id: str
    symbol: str
    name: str
    metric: str
    interval: str = "1h"
    parameters: dict[str, int | float | str] = Field(default_factory=dict)
    operator: Literal[">=", "<="]
    threshold: float
    enabled: bool
    created_at: str
    last_triggered_at: str | None = None


class AlertRuleCreate(BaseModel):
    symbol: str
    name: str
    metric: str = Field(min_length=1, max_length=80)
    interval: str = Field(default="1h", max_length=12)
    parameters: dict[str, int | float | str] = Field(default_factory=dict)
    operator: Literal[">=", "<="]
    threshold: float
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    operator: Literal[">=", "<="] | None = None
    threshold: float | None = None
    enabled: bool | None = None
    interval: str | None = Field(default=None, max_length=12)
    parameters: dict[str, int | float | str] | None = None


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


class DashboardMetric(BaseModel):
    id: str
    label: str
    value: str
    status: Literal["ok", "warn", "error"]
    detail: str
    href: str


class DashboardOverview(BaseModel):
    generated_at: str
    status: Literal["ok", "warn", "error"]
    metrics: list[DashboardMetric]
    health: list[HealthComponent]
    agent_runs: list[AgentRun]
    automation_runs: list[AutomationRunRecord]
    events: list[EventLog]
    watchlist: list[SymbolSnapshot]


class AdminOverviewMetric(BaseModel):
    id: str
    label: str
    value: str
    status: Literal["ok", "warn", "error"] = "ok"
    detail: str
    href: str


class AdminAttentionItem(BaseModel):
    id: str
    severity: Literal["info", "warn", "error"]
    title: str
    detail: str
    href: str


class AdminActivityItem(BaseModel):
    id: str
    actor: str
    action: str
    resource: str
    created_at: str


class AdminOverview(BaseModel):
    generated_at: str
    status: Literal["ok", "warn", "error"]
    metrics: list[AdminOverviewMetric]
    attention: list[AdminAttentionItem]
    activity: list[AdminActivityItem]


class AutomationSettings(BaseModel):
    enabled: bool
    interval_seconds: int
    next_run_hint: str | None = None


class ScheduledTask(BaseModel):
    id: str
    task_type: Literal["automation", "news_source", "data_source", "model_gateway", "agent"]
    name: str
    description: str
    owner_id: str
    owner_name: str
    enabled: bool
    effective_enabled: bool
    interval_seconds: int
    minimum_interval_seconds: int
    maximum_interval_seconds: int = 86400
    status: Literal["scheduled", "paused", "blocked", "running", "warning", "failed"]
    dependency: str | None = None
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_status: str | None = None


class ScheduledTaskUpdate(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)


class ScheduledTaskBulkUpdate(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    enabled: bool


class ScheduledTaskBulkResult(BaseModel):
    updated: int
    enabled: bool


class ScheduledTaskRunResult(BaseModel):
    task_id: str
    status: Literal["completed", "queued", "warning", "failed"]
    message: str
    run_id: str | None = None


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
    active_market_provider: Literal["binance", "coinbase", "finnhub"] | None = None
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
    instrument_id: str = ""
    symbol: str = ""
    quantity: float
    quantity_unit: str = ""
    average_price: float | None = None
    side: Literal["long", "short"] = "long"
    note: str = ""


class PortfolioPositionUpdate(BaseModel):
    instrument_id: str | None = None
    symbol: str | None = None
    quantity: float | None = None
    quantity_unit: str | None = None
    average_price: float | None = None
    side: Literal["long", "short"] | None = None
    note: str | None = None


class PortfolioPosition(BaseModel):
    id: str
    instrument_id: str
    symbol: str
    name: str
    asset_class: str
    currency: str
    side: Literal["long", "short"]
    quantity: float
    quantity_unit: str
    unit_label: str
    unit_multiplier: float
    base_quantity: float
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
