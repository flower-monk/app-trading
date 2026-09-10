from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ChartInterval = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1mo"]
ChartRange = Literal["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "Max"]
ChartAssetClass = Literal["stock", "etf", "crypto", "crypto_perpetual", "index"]
ChartPane = Literal["main", "sub"]
ChartSeriesType = Literal["line", "histogram", "band"]
ChartNewsMode = Literal["key", "all", "off"]
ChartCachePolicy = Literal["prefer_cache", "refresh"]


class PositionQuantityUnit(BaseModel):
    code: str
    label: str
    multiplier: float = Field(gt=0)
    step: float = Field(gt=0)


class MarketInstrument(BaseModel):
    id: str
    symbol: str
    provider_symbol: str
    name: str
    asset_class: ChartAssetClass
    market: str
    currency: str
    provider: str
    timezone: str
    realtime: bool
    delayed: bool
    intervals: list[ChartInterval]
    capabilities: list[str] = Field(default_factory=list)
    positionable: bool = True
    quantity_units: list[PositionQuantityUnit] = Field(default_factory=list)


class InstrumentQuote(BaseModel):
    instrument_id: str
    symbol: str
    price: float = Field(gt=0)
    currency: str
    as_of: str
    source: str
    stale: bool = False


class IndicatorParameter(BaseModel):
    key: str
    label: str
    type: Literal["integer", "number", "source"]
    default: int | float | str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: list[str] = Field(default_factory=list)


class IndicatorDefinition(BaseModel):
    id: str
    name: str
    category: Literal["trend", "momentum", "volatility", "volume", "derivatives", "fundamental", "valuation", "macro", "sentiment"]
    pane: ChartPane
    series_type: ChartSeriesType
    description: str
    parameters: list[IndicatorParameter] = Field(default_factory=list)
    asset_classes: list[ChartAssetClass] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    available: bool = True
    unavailable_reason: str = ""
    data_kind: Literal["series", "snapshot"] = "series"
    source_mode: Literal["calculated", "provider"] = "calculated"
    frequency: str = "bar"
    backtest_eligible: bool = True


class IndicatorConfig(BaseModel):
    id: str
    instance_id: str = Field(default="", max_length=80)
    pane: ChartPane | None = None
    parameters: dict[str, int | float | str] = Field(default_factory=dict)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    visible: bool = True


class ChartQuery(BaseModel):
    instrument_id: str
    interval: ChartInterval = "15m"
    range: ChartRange = "1M"
    indicators: list[IndicatorConfig] = Field(default_factory=list, max_length=20)
    news_mode: ChartNewsMode = "key"
    include_series: bool = True
    cache_policy: ChartCachePolicy = "prefer_cache"
    before: str | None = None
    limit: int = Field(default=500, ge=100, le=1500)


class ChartCandle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trades: int | None = None
    taker_buy_volume: float | None = None


class ChartSeriesPoint(BaseModel):
    time: int
    value: float | None = None
    upper: float | None = None
    middle: float | None = None
    lower: float | None = None
    color: str | None = None


class ChartIndicatorSeries(BaseModel):
    id: str
    label: str
    pane: ChartPane
    series_type: ChartSeriesType
    color: str
    points: list[ChartSeriesPoint]
    current_value: float | None = None
    source: str
    owner_id: str = ""
    source_id: str | None = None
    fetched_at: str | None = None
    stale: bool = False


class IndicatorSnapshot(BaseModel):
    id: str
    label: str
    category: Literal["fundamental", "valuation", "macro", "sentiment"]
    value: float | str | None = None
    unit: str = ""
    as_of: str
    source_id: str
    source_name: str
    stale: bool = False
    available: bool = True
    unavailable_reason: str = ""
    backtest_eligible: bool = False


class IndicatorSnapshotBundle(BaseModel):
    instrument_id: str
    generated_at: str
    items: list[IndicatorSnapshot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChartEventPreview(BaseModel):
    id: str
    title: str
    summary: str
    source: str
    published_at: str
    event_type: Literal["earnings", "macro", "regulatory", "product", "analyst", "crypto", "flow"]
    sentiment: Literal["bullish", "bearish", "neutral", "mixed"]
    impact_score: int
    price_reaction: str
    mapping_reason: Literal["exact", "within_candle", "next_trading_candle"] = "within_candle"


class ChartEventMarker(BaseModel):
    id: str
    time: int
    candle_time: int
    title: str
    sentiment: Literal["bullish", "bearish", "neutral", "mixed"]
    aggregate_sentiment: Literal["bullish", "bearish", "neutral", "mixed"]
    impact_score: int
    count: int = 1
    mapping_reason: Literal["exact", "within_candle", "next_trading_candle"] = "within_candle"
    events: list[ChartEventPreview] = Field(default_factory=list)


class ChartDataStatus(BaseModel):
    provider: str
    provider_label: str
    mode: Literal["realtime", "delayed", "historical", "cached"]
    fetched_at: str
    stale: bool = False
    message: str = ""
    age_seconds: int = 0
    refresh_required: bool = False
    revision: str = ""
    source_id: str | None = None
    health_status: str = "untested"
    fallback_reason: str | None = None


class ChartBundle(BaseModel):
    instrument: MarketInstrument
    interval: ChartInterval
    range: ChartRange
    candles: list[ChartCandle]
    series: list[ChartIndicatorSeries]
    markers: list[ChartEventMarker] = Field(default_factory=list)
    status: ChartDataStatus
    warnings: list[str] = Field(default_factory=list)
    next_before: str | None = None


class ChartSeriesQuery(BaseModel):
    instrument_id: str
    interval: ChartInterval = "15m"
    range: ChartRange = "1M"
    indicators: list[IndicatorConfig] = Field(default_factory=list, max_length=20)
    expected_revision: str = ""
    before: str | None = None
    limit: int = Field(default=500, ge=100, le=1500)


class ChartSeriesBundle(BaseModel):
    revision: str
    series: list[ChartIndicatorSeries]
    warnings: list[str] = Field(default_factory=list)
    status_by_indicator: dict[str, str] = Field(default_factory=dict)


class ChartLayout(BaseModel):
    id: str
    user_id: str
    name: str
    asset_class: ChartAssetClass
    is_default: bool
    interval: ChartInterval
    range: ChartRange
    indicators: list[IndicatorConfig]
    created_at: str
    updated_at: str


class ChartLayoutCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    asset_class: ChartAssetClass
    is_default: bool = False
    interval: ChartInterval = "15m"
    range: ChartRange = "1M"
    indicators: list[IndicatorConfig] = Field(default_factory=list, max_length=20)


class ChartLayoutUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_default: bool | None = None
    interval: ChartInterval | None = None
    range: ChartRange | None = None
    indicators: list[IndicatorConfig] | None = Field(default=None, max_length=20)
