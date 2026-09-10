from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, HttpUrl, SecretStr

from app.schemas.agents import NewsEventAnnotation, NewsImpactLink, NewsSymbolTag

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
    asset_class: Literal["stock", "etf", "crypto", "crypto_perpetual", "index"]
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
    provider: Literal["binance", "coinbase", "finnhub"] = "coinbase"


class WatchlistItemCreate(BaseModel):
    symbol: str


WatchlistGroupColor = Literal["blue", "green", "yellow", "red", "gray", "purple"]


class WatchlistGroup(BaseModel):
    id: str
    provider: str
    name: str
    color: WatchlistGroupColor
    is_default: bool
    sort_order: int
    item_count: int = 0
    created_at: str
    updated_at: str


class WatchlistGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: WatchlistGroupColor = "blue"


class WatchlistGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: WatchlistGroupColor | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)


class WatchlistResourceCreate(BaseModel):
    symbol: str
    group_id: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=12)
    note: str = Field(default="", max_length=500)


class WatchlistResourceUpdate(BaseModel):
    group_id: str | None = None
    tags: list[str] | None = Field(default=None, max_length=12)
    note: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class WatchlistResourceItem(BaseModel):
    provider: str
    symbol: str
    name: str
    market: str
    enabled: bool
    group_id: str
    group_name: str
    group_color: WatchlistGroupColor
    tags: list[str]
    note: str
    sort_order: int
    created_at: str
    updated_at: str
    snapshot: SymbolSnapshot | None = None


class WatchlistResourcePage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[WatchlistResourceItem]


class WatchlistBulkAction(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=100)
    action: Literal["disable", "restore", "move", "add_tags", "remove_tags"]
    group_id: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=12)


class WatchlistBulkResult(BaseModel):
    updated: int
    action: str


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
    provenance: list[dict[str, object]] = Field(default_factory=list)


class ScreenerMatch(BaseModel):
    snapshot: SymbolSnapshot
    reasons: list[str]


class ScreenerResult(BaseModel):
    generated_at: str
    total: int
    filters: dict[str, str | float]
    items: list[ScreenerMatch]
    provenance: list[dict[str, object]] = Field(default_factory=list)


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
    source_id: int | None = None
    symbol: str
    symbols: list[str] = Field(default_factory=list)
    title: str
    summary: str
    source: str
    url: str
    published_at: str
    event_type: Literal["earnings", "macro", "regulatory", "product", "analyst", "crypto", "flow"]
    sentiment: Literal["bullish", "bearish", "neutral", "mixed"]
    impact_score: int
    price_reaction: str
    manual_association: bool = False
    association_tags: list[NewsSymbolTag] = Field(default_factory=list)
    association_status: str = "unprocessed"


class NewsDetail(NewsEvent):
    related_symbols: list[str]
    classification_summary: str
    key_points: list[str]
    detail: str
    symbol_tags: list[NewsSymbolTag] = Field(default_factory=list)
    tagging_status: str = "unprocessed"
    manual_override: bool = False
    override_note: str = ""
    override_updated_by: str | None = None
    override_updated_at: str | None = None
    event_annotation: NewsEventAnnotation | None = None
    impact_links: list[NewsImpactLink] = Field(default_factory=list)
    pipeline_status: dict[str, object] = Field(default_factory=dict)


class NewsSourceFacet(BaseModel):
    source_id: int
    name: str
    count: int
    provider: str = ""
    adapter: str = ""


class NewsFeedPage(BaseModel):
    items: list[NewsEvent]
    total: int
    limit: int
    offset: int
    source_counts: list[NewsSourceFacet]


InformationSourceCategory = Literal["market", "news", "filing", "macro", "sentiment", "social", "alternative"]
InformationSourceAdapter = Literal["rss", "finnhub", "adanos", "gdelt", "cryptocompare", "alphavantage", "fred", "widget", "reference"]
InformationSourceType = Literal["rss", "rest_api", "websocket", "widget", "reference"]
InformationSourceAuth = Literal["none", "query", "header", "bearer"]


class NewsSource(BaseModel):
    id: int
    name: str
    type: InformationSourceType
    category: InformationSourceCategory = "news"
    adapter: InformationSourceAdapter = "rss"
    provider: str = "custom"
    url: str
    enabled: bool
    symbols: list[str]
    region: str = "global"
    language: str = "en"
    auth_type: InformationSourceAuth = "none"
    api_key_env: str = ""
    api_key_configured: bool = False
    credential_id: str | None = None
    credential_origin: Literal["database", "environment", "none", "error"] = "none"
    secret_hint: str = ""
    credential_test_status: Literal["unknown", "pending", "pass", "partial", "fail"] = "unknown"
    capabilities: list[str] = Field(default_factory=lambda: ["news"])
    config: dict[str, object] = Field(default_factory=dict)
    priority: int = 50
    reliability_weight: float = 0.8
    poll_interval_seconds: int
    last_fetched_at: str | None
    last_error: str | None
    created_at: str


class NewsSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    type: InformationSourceType = "rss"
    category: InformationSourceCategory = "news"
    adapter: InformationSourceAdapter = "rss"
    provider: str = Field(default="custom", max_length=60)
    symbols: list[str] = Field(default_factory=list)
    region: str = Field(default="global", max_length=40)
    language: str = Field(default="en", max_length=12)
    auth_type: InformationSourceAuth = "none"
    api_key_env: str = Field(default="", max_length=80)
    api_key: SecretStr | None = Field(default=None, max_length=10000)
    capabilities: list[str] = Field(default_factory=lambda: ["news"], max_length=20)
    config: dict[str, object] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=0, le=100)
    reliability_weight: float = Field(default=0.8, ge=0, le=1)
    enabled: bool = True
    poll_interval_seconds: int = Field(default=900, ge=60, le=86400)


class NewsSourceUpdate(BaseModel):
    name: str | None = None
    url: HttpUrl | None = None
    type: InformationSourceType | None = None
    category: InformationSourceCategory | None = None
    adapter: InformationSourceAdapter | None = None
    provider: str | None = None
    enabled: bool | None = None
    symbols: list[str] | None = None
    region: str | None = None
    language: str | None = None
    auth_type: InformationSourceAuth | None = None
    api_key_env: str | None = None
    api_key: SecretStr | None = Field(default=None, max_length=10000)
    capabilities: list[str] | None = None
    config: dict[str, object] | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    reliability_weight: float | None = Field(default=None, ge=0, le=1)
    poll_interval_seconds: int | None = Field(default=None, ge=60, le=86400)


class InformationSourceSummary(BaseModel):
    total: int
    enabled: int
    configured: int
    errors: int
    by_category: dict[str, int]
    by_adapter: dict[str, int]


class InformationSourceHealth(BaseModel):
    id: int
    name: str
    category: InformationSourceCategory
    adapter: InformationSourceAdapter
    status: Literal["online", "idle", "disabled", "unconfigured", "error"]
    message: str
    last_checked_at: str | None = None


class NewsSourceAttempt(BaseModel):
    attempt: int
    duration_ms: int
    outcome: Literal["succeeded", "failed"]
    code: str | None = None
    message: str | None = None


class NewsSourceEvidence(BaseModel):
    http_status: int | None = None
    final_host: str | None = None
    content_type: str | None = None
    content_length: int | None = None
    content_sha256: str | None = None
    parser_line: int | None = None
    parser_column: int | None = None
    feed_type: str | None = None
    total_entries: int | None = None
    valid_entries: int | None = None


class NewsSourceDiagnostic(BaseModel):
    stage: Literal["configuration", "request", "response", "parse", "validate", "persist", "complete"]
    category: Literal["configuration", "dns", "connection", "tls", "timeout", "http", "content", "xml", "feed", "entry", "database", "unknown", "success"]
    code: str
    message: str
    suggestion: str
    retryable: bool = False
    attempt_count: int = 0
    attempts: list[NewsSourceAttempt] = Field(default_factory=list)
    evidence: NewsSourceEvidence = Field(default_factory=NewsSourceEvidence)


class NewsRefreshResult(BaseModel):
    source_id: int
    fetched: int
    inserted: int
    error: str | None = None
    run_id: str | None = None
    status: Literal["running", "succeeded", "failed", "skipped"] = "succeeded"
    diagnostic: NewsSourceDiagnostic | None = None


class NewsSourceTestResult(BaseModel):
    source_id: int
    status: Literal["succeeded", "failed"]
    fetched: int = 0
    run_id: str
    diagnostic: NewsSourceDiagnostic


class NewsSourceRun(BaseModel):
    id: str
    source_id: int
    trigger: Literal["schedule", "manual", "automation", "test"]
    status: Literal["running", "succeeded", "failed", "skipped"]
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    fetched: int = 0
    inserted: int = 0
    error: str | None = None
    diagnostic: NewsSourceDiagnostic | None = None


class NewsSourceMonitor(BaseModel):
    source_id: int
    name: str
    provider: str
    adapter: InformationSourceAdapter
    enabled: bool
    configured: bool
    ingestible: bool
    status: Literal["running", "healthy", "idle", "paused", "unconfigured", "unsupported", "error"]
    poll_interval_seconds: int
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_status: str | None = None
    last_duration_ms: int | None = None
    last_fetched: int = 0
    last_inserted: int = 0
    runs_24h: int = 0
    success_rate_24h: float | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    last_diagnostic: NewsSourceDiagnostic | None = None


class NewsSourceOverview(BaseModel):
    sources: list[NewsSource]
    monitors: list[NewsSourceMonitor]
