from __future__ import annotations

import json
import math
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from time import perf_counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import pandas_ta_classic as ta
from fastapi import HTTPException

from app.core.config import settings
from app.db.session import connect
from app.schemas import (
    ChartAssetClass,
    ChartBundle,
    ChartCandle,
    ChartDataStatus,
    ChartIndicatorSeries,
    ChartInterval,
    ChartLayout,
    ChartLayoutCreate,
    ChartLayoutUpdate,
    ChartQuery,
    ChartRange,
    ChartSeriesPoint,
    ChartSeriesBundle,
    ChartSeriesQuery,
    IndicatorConfig,
    IndicatorDefinition,
    IndicatorParameter,
    InstrumentQuote,
    MarketInstrument,
    PositionQuantityUnit,
)
from app.modules.integrations.service import record_health, resolve_candidates


ALL_INTERVALS: list[ChartInterval] = [
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1mo"
]
DERIVATIVE_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "1d"}
_DERIVATIVE_LOCKS: dict[str, Lock] = {}
_DERIVATIVE_LOCKS_GUARD = Lock()
_SYMBOL_SEARCH_CACHE: dict[str, tuple[datetime, list[MarketInstrument]]] = {}
RANGE_ORDER: tuple[ChartRange, ...] = (
    "1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "Max",
)
MINIMUM_RANGE_BY_INTERVAL: dict[ChartInterval, ChartRange] = {
    "1m": "1D",
    "3m": "1D",
    "5m": "1D",
    "15m": "1D",
    "30m": "1D",
    "1h": "1D",
    "2h": "5D",
    "4h": "5D",
    "1d": "1M",
    "1w": "6M",
    "1mo": "3Y",
}


def validate_interval_range(interval: ChartInterval, range_value: ChartRange) -> None:
    minimum = MINIMUM_RANGE_BY_INTERVAL[interval]
    if RANGE_ORDER.index(range_value) < RANGE_ORDER.index(minimum):
        raise HTTPException(
            status_code=422,
            detail=f"{interval} candles require a range of {minimum} or longer",
        )


def _instrument(
    instrument_id: str,
    symbol: str,
    provider_symbol: str,
    name: str,
    asset_class: ChartAssetClass,
    market: str,
    provider: str,
    timezone_name: str,
    *,
    currency: str = "USD",
    realtime: bool = False,
    delayed: bool = True,
    capabilities: list[str] | None = None,
    intervals: list[ChartInterval] | None = None,
) -> MarketInstrument:
    positionable = asset_class != "index"
    if asset_class == "stock":
        quantity_units = [PositionQuantityUnit(code="share", label="股", multiplier=1, step=1)]
    elif asset_class == "etf":
        quantity_units = [PositionQuantityUnit(code="fund_unit", label="份", multiplier=1, step=1)]
    elif asset_class in {"crypto", "crypto_perpetual"}:
        base_symbol = symbol.removesuffix("USDT").removesuffix("USD") or symbol
        quantity_units = [PositionQuantityUnit(code="base_asset", label=base_symbol, multiplier=1, step=0.0001)]
    else:
        quantity_units = []
    return MarketInstrument(
        id=instrument_id,
        symbol=symbol,
        provider_symbol=provider_symbol,
        name=name,
        asset_class=asset_class,
        market=market,
        currency=currency,
        provider=provider,
        timezone=timezone_name,
        realtime=realtime,
        delayed=delayed,
        intervals=intervals or ALL_INTERVALS,
        capabilities=capabilities or ["ohlcv", "technical_indicators"],
        positionable=positionable,
        quantity_units=quantity_units,
    )


INSTRUMENTS: list[MarketInstrument] = [
    _instrument("FINNHUB:AAPL", "AAPL", "AAPL", "Apple Inc.", "stock", "NASDAQ", "finnhub", "America/New_York"),
    _instrument("FINNHUB:NVDA", "NVDA", "NVDA", "NVIDIA Corp.", "stock", "NASDAQ", "finnhub", "America/New_York"),
    _instrument("FINNHUB:TSLA", "TSLA", "TSLA", "Tesla Inc.", "stock", "NASDAQ", "finnhub", "America/New_York"),
    _instrument("FINNHUB:MSFT", "MSFT", "MSFT", "Microsoft Corp.", "stock", "NASDAQ", "finnhub", "America/New_York"),
    _instrument("FINNHUB:SPY", "SPY", "SPY", "S&P 500 ETF", "etf", "NYSE Arca", "finnhub", "America/New_York"),
    _instrument("FINNHUB:QQQ", "QQQ", "QQQ", "Nasdaq 100 ETF", "etf", "NASDAQ", "finnhub", "America/New_York"),
    _instrument(
        "BINANCE_FUTURES:BTCUSDT", "BTCUSDT", "BTCUSDT", "Bitcoin Perpetual", "crypto_perpetual",
        "Binance USD-M", "binance_futures", "UTC", realtime=True, delayed=False,
        capabilities=["ohlcv", "technical_indicators", "derivatives", "streaming"],
    ),
    _instrument(
        "BINANCE_FUTURES:ETHUSDT", "ETHUSDT", "ETHUSDT", "Ether Perpetual", "crypto_perpetual",
        "Binance USD-M", "binance_futures", "UTC", realtime=True, delayed=False,
        capabilities=["ohlcv", "technical_indicators", "derivatives", "streaming"],
    ),
    _instrument("INDEX:SHCOMP", "SHCOMP", "000001.SS", "上证指数", "index", "SSE", "public_index", "Asia/Shanghai", currency="CNY"),
    _instrument("INDEX:SZCOMP", "SZCOMP", "399001.SZ", "深证成指", "index", "SZSE", "public_index", "Asia/Shanghai", currency="CNY"),
    _instrument("INDEX:CSI300", "CSI300", "000300.SS", "沪深300", "index", "CSI", "public_index", "Asia/Shanghai", currency="CNY"),
    _instrument("INDEX:HSI", "HSI", "^HSI", "恒生指数", "index", "HKEX", "public_index", "Asia/Hong_Kong", currency="HKD"),
    _instrument("INDEX:SPX", "SPX", "^GSPC", "标普500", "index", "S&P", "public_index", "America/New_York"),
    _instrument("INDEX:NDX", "NDX", "^NDX", "纳斯达克100", "index", "NASDAQ", "public_index", "America/New_York"),
    _instrument("INDEX:DJI", "DJI", "^DJI", "道琼斯工业指数", "index", "DJIA", "public_index", "America/New_York"),
    _instrument("INDEX:RUT", "RUT", "^RUT", "罗素2000", "index", "Russell", "public_index", "America/New_York"),
    _instrument("INDEX:VIX", "VIX", "^VIX", "CBOE 波动率指数", "index", "CBOE", "public_index", "America/Chicago"),
    _instrument("INDEX:FTSE", "FTSE", "^FTSE", "富时100", "index", "LSE", "public_index", "Europe/London", currency="GBP"),
    _instrument("INDEX:DAX", "DAX", "^GDAXI", "德国 DAX", "index", "XETRA", "public_index", "Europe/Berlin", currency="EUR"),
    _instrument("INDEX:N225", "N225", "^N225", "日经225", "index", "TSE", "public_index", "Asia/Tokyo", currency="JPY"),
]
INSTRUMENT_BY_ID = {item.id.upper(): item for item in INSTRUMENTS}


def list_instruments(query: str = "", asset_class: ChartAssetClass | None = None) -> list[MarketInstrument]:
    needle = query.strip().lower()
    local = [
        item for item in INSTRUMENTS
        if (asset_class is None or item.asset_class == asset_class)
        and (
            not needle
            or needle in item.id.lower()
            or needle in item.symbol.lower()
            or needle in item.name.lower()
            or needle in item.market.lower()
        )
    ]
    if len(needle) < 2 or asset_class not in {None, "stock", "etf"}:
        return local
    cached = _SYMBOL_SEARCH_CACHE.get(needle)
    if cached and cached[0] > datetime.now(timezone.utc):
        remote = cached[1]
    else:
        remote = _search_finnhub_symbols(needle)
        _SYMBOL_SEARCH_CACHE[needle] = (datetime.now(timezone.utc) + timedelta(minutes=10), remote)
    merged = {item.id: item for item in [*local, *remote]}
    return [item for item in merged.values() if asset_class is None or item.asset_class == asset_class][:30]


def _search_finnhub_symbols(query: str) -> list[MarketInstrument]:
    from app.modules.platform.service import resolve_api_key

    key = resolve_api_key("data_source", "market:finnhub", "FINNHUB_API_KEY") or resolve_api_key(
        "market_provider", "finnhub", "FINNHUB_API_KEY"
    )
    if not key:
        return []
    try:
        payload = _http_json(f"https://finnhub.io/api/v1/search?{urlencode({'q': query, 'token': key})}")
    except RuntimeError:
        return []
    results: list[MarketInstrument] = []
    for row in payload.get("result", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).strip().upper()
        display = str(row.get("displaySymbol", symbol)).strip().upper()
        kind = str(row.get("type", "")).lower()
        if not symbol or "." in display or kind not in {"common stock", "etp", "reit"}:
            continue
        asset_class: ChartAssetClass = "etf" if kind == "etp" else "stock"
        results.append(_instrument(
            f"FINNHUB:{symbol}", display, symbol, str(row.get("description") or display), asset_class,
            "US", "finnhub", "America/New_York",
        ))
    return results[:25]


def resolve_instrument(value: str) -> MarketInstrument:
    normalized = value.strip().upper()
    direct = INSTRUMENT_BY_ID.get(normalized)
    if direct:
        return direct
    aliases = {
        "BTCUSD": "BINANCE_FUTURES:BTCUSDT",
        "ETHUSD": "BINANCE_FUTURES:ETHUSDT",
        "^GSPC": "INDEX:SPX",
        "^NDX": "INDEX:NDX",
        "000001.SS": "INDEX:SHCOMP",
    }
    if normalized in aliases:
        return INSTRUMENT_BY_ID[aliases[normalized]]
    matches = [item for item in INSTRUMENTS if item.symbol.upper() == normalized]
    if matches:
        return matches[0]
    if normalized.startswith("FINNHUB:"):
        symbol = normalized.split(":", 1)[1].strip()
        if symbol and all(character.isalnum() or character in ".-" for character in symbol):
            return _instrument(
                normalized, symbol, symbol, symbol, "stock", "US", "finnhub", "America/New_York"
            )
    raise HTTPException(status_code=404, detail="Instrument not found")


def _length_parameter(default: int = 14) -> list[IndicatorParameter]:
    return [IndicatorParameter(key="length", label="周期", type="integer", default=default, minimum=2, maximum=300, step=1)]


INDICATOR_DEFINITIONS: list[IndicatorDefinition] = [
    IndicatorDefinition(id="sma", name="SMA", category="trend", pane="main", series_type="line", description="简单移动平均", parameters=_length_parameter(20)),
    IndicatorDefinition(id="ema", name="EMA", category="trend", pane="main", series_type="line", description="指数移动平均", parameters=_length_parameter(20)),
    IndicatorDefinition(id="bbands", name="Bollinger Bands", category="volatility", pane="main", series_type="band", description="布林带", parameters=_length_parameter(20)),
    IndicatorDefinition(id="vwap", name="VWAP", category="volume", pane="main", series_type="line", description="成交量加权平均价"),
    IndicatorDefinition(id="ichimoku", name="Ichimoku", category="trend", pane="main", series_type="line", description="一目均衡表"),
    IndicatorDefinition(id="supertrend", name="Supertrend", category="trend", pane="main", series_type="line", description="ATR 趋势跟踪", parameters=_length_parameter(7)),
    IndicatorDefinition(id="donchian", name="Donchian Channel", category="volatility", pane="main", series_type="band", description="唐奇安通道", parameters=_length_parameter(20)),
    IndicatorDefinition(id="keltner", name="Keltner Channel", category="volatility", pane="main", series_type="band", description="肯特纳通道", parameters=_length_parameter(20)),
    IndicatorDefinition(id="psar", name="Parabolic SAR", category="trend", pane="main", series_type="line", description="抛物线转向"),
    IndicatorDefinition(id="volume", name="Volume", category="volume", pane="sub", series_type="histogram", description="成交量"),
    IndicatorDefinition(id="macd", name="MACD", category="momentum", pane="sub", series_type="histogram", description="指数平滑异同移动平均"),
    IndicatorDefinition(id="rsi", name="RSI", category="momentum", pane="sub", series_type="line", description="相对强弱指标", parameters=_length_parameter(14)),
    IndicatorDefinition(id="stoch", name="KDJ / Stochastic", category="momentum", pane="sub", series_type="line", description="随机指标", parameters=_length_parameter(14)),
    IndicatorDefinition(id="cci", name="CCI", category="momentum", pane="sub", series_type="line", description="顺势指标", parameters=_length_parameter(20)),
    IndicatorDefinition(id="adx", name="ADX / DMI", category="trend", pane="sub", series_type="line", description="趋势强度与方向", parameters=_length_parameter(14)),
    IndicatorDefinition(id="atr", name="ATR", category="volatility", pane="sub", series_type="line", description="平均真实波幅", parameters=_length_parameter(14)),
    IndicatorDefinition(id="obv", name="OBV", category="volume", pane="sub", series_type="line", description="能量潮"),
    IndicatorDefinition(id="mfi", name="MFI", category="volume", pane="sub", series_type="line", description="资金流量指标", parameters=_length_parameter(14)),
    IndicatorDefinition(id="roc", name="ROC", category="momentum", pane="sub", series_type="line", description="变动率", parameters=_length_parameter(10)),
    IndicatorDefinition(id="willr", name="Williams %R", category="momentum", pane="sub", series_type="line", description="威廉指标", parameters=_length_parameter(14)),
    IndicatorDefinition(id="cmf", name="CMF", category="volume", pane="sub", series_type="line", description="蔡金资金流", parameters=_length_parameter(20)),
    IndicatorDefinition(id="ad", name="A/D", category="volume", pane="sub", series_type="line", description="累积/派发线"),
    IndicatorDefinition(id="efi", name="Force Index", category="volume", pane="sub", series_type="line", description="强力指数", parameters=_length_parameter(13)),
]

for indicator_id, name, category, description, default_length in (
    ("wma", "WMA", "trend", "加权移动平均", 20),
    ("dema", "DEMA", "trend", "双重指数移动平均", 20),
    ("tema", "TEMA", "trend", "三重指数移动平均", 20),
    ("hma", "HMA", "trend", "Hull 移动平均", 20),
    ("alma", "ALMA", "trend", "Arnaud Legoux 移动平均", 20),
    ("trix", "TRIX", "momentum", "三重指数平滑动量", 14),
    ("ppo", "PPO", "momentum", "百分比价格振荡器", 12),
    ("stochrsi", "Stoch RSI", "momentum", "随机相对强弱指标", 14),
    ("uo", "Ultimate Oscillator", "momentum", "终极振荡器", 7),
    ("ao", "Awesome Oscillator", "momentum", "动量振荡器", 5),
    ("rvi", "RVI", "momentum", "相对活力指数", 14),
    ("cfo", "CFO", "momentum", "Chande 预测振荡器", 14),
    ("natr", "NATR", "volatility", "归一化平均真实波幅", 14),
    ("stdev", "Standard Deviation", "volatility", "滚动标准差", 20),
    ("chop", "Choppiness", "volatility", "震荡指数", 14),
    ("squeeze", "Squeeze", "volatility", "波动率挤压动量", 20),
    ("pvt", "PVT", "volume", "价量趋势", 14),
    ("nvi", "NVI", "volume", "负成交量指数", 14),
    ("pvi", "PVI", "volume", "正成交量指数", 14),
    ("kvo", "KVO", "volume", "Klinger 成交量振荡器", 34),
):
    INDICATOR_DEFINITIONS.append(IndicatorDefinition(
        id=indicator_id, name=name, category=category, pane="main" if category == "trend" else "sub",
        series_type="line", description=description, parameters=_length_parameter(default_length),
    ))

for derivative_id, derivative_name, description in (
    ("open_interest", "Open Interest", "持仓量"),
    ("open_interest_change", "OI Change", "持仓量变化率"),
    ("global_long_short_ratio", "Global Long/Short", "全市场多空人数比"),
    ("top_account_ratio", "Top Trader Accounts", "大户账户多空比"),
    ("top_position_ratio", "Top Trader Positions", "大户持仓多空比"),
    ("taker_buy_sell_ratio", "Taker Buy/Sell", "主动买卖比"),
    ("funding_rate", "Funding Rate", "资金费率"),
    ("basis", "Basis", "永续合约基差"),
    ("taker_buy_pct", "Taker Buy %", "主动买入占比"),
    ("volume_delta", "Volume Delta", "主动买卖量差"),
    ("cvd", "Cumulative Volume Delta", "累计主动买卖量差"),
    ("annualized_funding", "Annualized Funding", "年化资金费率"),
    ("annualized_basis", "Annualized Basis", "年化基差"),
):
    INDICATOR_DEFINITIONS.append(
        IndicatorDefinition(
            id=derivative_id,
            name=derivative_name,
            category="derivatives",
            pane="sub",
            series_type="histogram" if derivative_id in {"open_interest_change", "funding_rate", "basis", "volume_delta"} else "line",
            description=description,
            asset_classes=["crypto_perpetual"],
            required_capabilities=["derivatives"],
        )
    )

for series_id, name, description in (
    ("fred_dff", "Fed Funds Rate", "联邦基金有效利率"),
    ("fred_dgs2", "US 2Y Yield", "美国两年期国债收益率"),
    ("fred_dgs10", "US 10Y Yield", "美国十年期国债收益率"),
    ("fred_t10y2y", "US 10Y-2Y Spread", "美国十年与两年期限利差"),
    ("fred_cpi", "US CPI", "美国消费者价格指数"),
    ("fred_unrate", "US Unemployment", "美国失业率"),
):
    INDICATOR_DEFINITIONS.append(IndicatorDefinition(
        id=series_id, name=name, category="macro", pane="sub", series_type="line",
        description=description, source_mode="provider", frequency="daily/monthly",
        backtest_eligible=False,
    ))

INDICATOR_BY_ID = {item.id: item for item in INDICATOR_DEFINITIONS}
INDICATOR_COLORS = [
    "#f3ba2f", "#4aa3ff", "#22ab94", "#f23645", "#a78bfa", "#00bcd4",
    "#fb923c", "#84cc16", "#e879f9", "#94a3b8",
]


def indicator_catalog(instrument_id: str | None = None) -> list[IndicatorDefinition]:
    instrument = resolve_instrument(instrument_id) if instrument_id else None
    results: list[IndicatorDefinition] = []
    for definition in INDICATOR_DEFINITIONS:
        available = True
        reason = ""
        if instrument and definition.asset_classes and instrument.asset_class not in definition.asset_classes:
            available = False
            reason = "当前资产类型不提供该指标"
        if instrument and any(capability not in instrument.capabilities for capability in definition.required_capabilities):
            available = False
            reason = "当前数据源缺少所需能力"
        results.append(definition.model_copy(update={"available": available, "unavailable_reason": reason}))
    return results


def _http_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": "AppTrading/0.2 chart-service"})
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def _range_start(range_value: ChartRange, end: datetime) -> datetime:
    if range_value == "YTD":
        return datetime(end.year, 1, 1, tzinfo=timezone.utc)
    days = {
        "1D": 1, "5D": 5, "1M": 31, "3M": 93, "6M": 186,
        "1Y": 366, "3Y": 1096, "5Y": 1827, "Max": 7305,
    }[range_value]
    return end - timedelta(days=days)


def _interval_seconds(interval: ChartInterval) -> int:
    return {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "1d": 86400,
        "1w": 604800,
        "1mo": 2_592_000,
    }[interval]


def _aggregate_candles(rows: list[ChartCandle], target_seconds: int) -> list[ChartCandle]:
    groups: dict[int, list[ChartCandle]] = {}
    for row in rows:
        bucket = row.time - row.time % target_seconds
        groups.setdefault(bucket, []).append(row)
    aggregated: list[ChartCandle] = []
    for bucket, items in sorted(groups.items()):
        aggregated.append(
            ChartCandle(
                time=bucket,
                open=items[0].open,
                high=max(item.high for item in items),
                low=min(item.low for item in items),
                close=items[-1].close,
                volume=sum(item.volume for item in items),
                quote_volume=sum(item.quote_volume or 0 for item in items) or None,
                trades=sum(item.trades or 0 for item in items) or None,
                taker_buy_volume=sum(item.taker_buy_volume or 0 for item in items) or None,
            )
        )
    return aggregated


def _fetch_binance(instrument: MarketInstrument, query: ChartQuery, start: datetime, end: datetime) -> list[ChartCandle]:
    interval = "1M" if query.interval == "1mo" else query.interval
    params = {
        "symbol": instrument.provider_symbol,
        "interval": interval,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": min(query.limit + 350, 1500),
    }
    payload = _http_json(f"https://fapi.binance.com/fapi/v1/klines?{urlencode(params)}")
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Binance Futures response")
    return [
        ChartCandle(
            time=int(item[0]) // 1000,
            open=float(item[1]),
            high=float(item[2]),
            low=float(item[3]),
            close=float(item[4]),
            volume=float(item[5]),
            quote_volume=float(item[7]),
            trades=int(item[8]),
            taker_buy_volume=float(item[9]),
        )
        for item in payload if isinstance(item, list) and len(item) >= 10
    ]


def _fetch_coinbase_fallback(instrument: MarketInstrument, query: ChartQuery) -> list[ChartCandle]:
    product = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}[instrument.provider_symbol]
    source_interval = {
        "3m": "1m", "30m": "5m", "2h": "1h", "4h": "1h", "1w": "1d", "1mo": "1d",
    }.get(query.interval, query.interval)
    granularity = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400,
    }.get(source_interval)
    if granularity is None:
        raise RuntimeError("Coinbase fallback does not support this interval")
    payload = _http_json(
        f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={granularity}"
    )
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Coinbase fallback response")
    rows = [
        ChartCandle(
            time=int(item[0]),
            low=float(item[1]),
            high=float(item[2]),
            open=float(item[3]),
            close=float(item[4]),
            volume=float(item[5]),
        )
        for item in payload if isinstance(item, list) and len(item) >= 6
    ]
    rows.sort(key=lambda item: item.time)
    target_seconds = {"3m": 180, "30m": 1800, "2h": 7200, "4h": 14400, "1w": 604800, "1mo": 2_592_000}.get(query.interval)
    return _aggregate_candles(rows, target_seconds) if target_seconds else rows


def _finnhub_key() -> str:
    from app.modules.platform.service import resolve_api_key

    return resolve_api_key("market_provider", "finnhub", "FINNHUB_API_KEY")


def _fetch_finnhub(instrument: MarketInstrument, query: ChartQuery, start: datetime, end: datetime) -> list[ChartCandle]:
    key = _finnhub_key()
    if not key:
        raise RuntimeError("Finnhub credential is not configured")
    source_interval = {"3m": "1", "2h": "60", "4h": "60"}.get(query.interval, query.interval)
    resolution = {
        "1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60",
        "1d": "D", "1w": "W", "1mo": "M",
    }[source_interval]
    params = {
        "symbol": instrument.provider_symbol,
        "resolution": resolution,
        "from": int(start.timestamp()),
        "to": int(end.timestamp()),
        "token": key,
    }
    base_url = os.getenv("FINNHUB_BASE_URL", "https://finnhub.io/api/v1").rstrip("/")
    payload = _http_json(f"{base_url}/stock/candle?{urlencode(params)}")
    if not isinstance(payload, dict) or payload.get("s") != "ok":
        raise RuntimeError(str(payload.get("error", "Finnhub candle data unavailable")) if isinstance(payload, dict) else "Unexpected Finnhub response")
    values = zip(
        payload.get("t", []), payload.get("o", []), payload.get("h", []),
        payload.get("l", []), payload.get("c", []), payload.get("v", []),
    )
    rows = [
        ChartCandle(time=int(ts), open=float(op), high=float(hi), low=float(lo), close=float(cl), volume=float(vol or 0))
        for ts, op, hi, lo, cl, vol in values
    ]
    if query.interval == "3m":
        return _aggregate_candles(rows, 180)
    if query.interval == "2h":
        return _aggregate_candles(rows, 7200)
    if query.interval == "4h":
        return _aggregate_candles(rows, 14400)
    return rows


def _validate_public_index_query(query: ChartQuery) -> None:
    intraday_limits = {"1m": 7, "3m": 7, "5m": 60, "15m": 60, "30m": 60}
    maximum_days = intraday_limits.get(query.interval)
    requested_days = (_range_start(query.range, datetime.now(timezone.utc)) - datetime.now(timezone.utc)).days * -1
    if maximum_days and requested_days > maximum_days:
        raise HTTPException(
            status_code=422,
            detail=f"{query.interval} index data is available for at most {maximum_days} days",
        )


def _fetch_public_index(
    instrument: MarketInstrument,
    query: ChartQuery,
    start: datetime,
    end: datetime,
    endpoint: str = "https://query1.finance.yahoo.com/v8/finance/chart",
) -> list[ChartCandle]:
    _validate_public_index_query(query)
    source_interval = {"3m": "1m", "2h": "1h", "4h": "1h"}.get(query.interval, query.interval)
    interval = {"1w": "1wk", "1mo": "1mo"}.get(source_interval, source_interval)
    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": interval,
        "events": "history",
        "includeAdjustedClose": "true",
    }
    encoded_symbol = instrument.provider_symbol.replace("^", "%5E")
    payload = _http_json(f"{endpoint.rstrip('/')}/{encoded_symbol}?{urlencode(params)}")
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Unexpected public index response") from exc
    rows: list[ChartCandle] = []
    for index, timestamp in enumerate(timestamps):
        values = [quote.get(key, [None] * len(timestamps))[index] for key in ("open", "high", "low", "close")]
        if any(value is None for value in values):
            continue
        rows.append(
            ChartCandle(
                time=int(timestamp),
                open=float(values[0]),
                high=float(values[1]),
                low=float(values[2]),
                close=float(values[3]),
                volume=float(quote.get("volume", [0] * len(timestamps))[index] or 0),
            )
        )
    if query.interval == "3m":
        return _aggregate_candles(rows, 180)
    if query.interval == "2h":
        return _aggregate_candles(rows, 7200)
    if query.interval == "4h":
        return _aggregate_candles(rows, 14400)
    return rows


def _cache_key(instrument: MarketInstrument, query: ChartQuery, end: datetime) -> str:
    raw = f"{instrument.id}:{query.interval}:{query.range}:{query.before or ''}:{query.limit}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _status_cache_fields(fetched_at: str, *, stale: bool = False) -> dict[str, object]:
    fetched = datetime.fromisoformat(fetched_at)
    age_seconds = max(0, int((datetime.now(timezone.utc) - fetched).total_seconds()))
    return {
        "age_seconds": age_seconds,
        "refresh_required": stale,
        "revision": sha256(fetched_at.encode("utf-8")).hexdigest()[:16],
    }


def _read_cache(
    cache_key: str,
    allow_stale: bool = False,
    *,
    instrument_id: str = "",
    interval: str = "",
    range_value: str = "",
) -> tuple[list[ChartCandle], str, bool, dict[str, object], list[str]] | None:
    now = datetime.now(timezone.utc)
    with connect(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM market_data_cache WHERE cache_key = ?", (cache_key,)).fetchone()
        if row is None and allow_stale and instrument_id:
            row = connection.execute(
                """
                SELECT * FROM market_data_cache
                WHERE instrument_id = ? AND interval = ? AND range_value = ?
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (instrument_id, interval, range_value),
            ).fetchone()
    if not row:
        return None
    cutoff = datetime.fromisoformat(row["stale_until"] if allow_stale else row["expires_at"])
    if cutoff < now:
        return None
    payload = json.loads(row["payload"])
    if isinstance(payload, list):
        candle_payload = payload
        metadata: dict[str, object] = {}
        warnings: list[str] = []
    else:
        candle_payload = payload.get("candles", [])
        metadata = payload.get("status", {})
        warnings = payload.get("warnings", [])
    return (
        [ChartCandle.model_validate(item) for item in candle_payload],
        row["fetched_at"],
        allow_stale,
        metadata,
        warnings,
    )


def _write_cache(
    cache_key: str,
    instrument: MarketInstrument,
    query: ChartQuery,
    rows: list[ChartCandle],
    *,
    status: dict[str, object] | None = None,
    warnings: list[str] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    ttl = 15 if instrument.realtime else 60
    if query.interval in {"1d", "1w", "1mo"}:
        ttl = 900
    stale_seconds = 86400 if query.interval in {"1d", "1w", "1mo"} else 900
    fetched_at = now.isoformat()
    with connect(settings.database_path) as connection:
        connection.execute("DELETE FROM market_data_cache WHERE stale_until < ?", (now.isoformat(),))
        connection.execute(
            """
            INSERT OR REPLACE INTO market_data_cache (
                cache_key, provider, instrument_id, interval, range_value, payload,
                fetched_at, expires_at, stale_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key, instrument.provider, instrument.id, query.interval, query.range,
                json.dumps({
                    "candles": [item.model_dump(mode="json") for item in rows],
                    "status": status or {},
                    "warnings": warnings or [],
                }),
                fetched_at,
                (now + timedelta(seconds=ttl)).isoformat(),
                (now + timedelta(seconds=stale_seconds)).isoformat(),
            ),
        )
    return fetched_at


def _load_candles(instrument: MarketInstrument, query: ChartQuery) -> tuple[list[ChartCandle], ChartDataStatus, list[str]]:
    end = datetime.fromisoformat(query.before.replace("Z", "+00:00")) if query.before else datetime.now(timezone.utc)
    visible_start = _range_start(query.range, end)
    warmup_bars = 350
    start = visible_start - timedelta(seconds=_interval_seconds(query.interval) * warmup_bars)
    cache_key = _cache_key(instrument, query, end)
    cached = None if query.cache_policy == "refresh" else _read_cache(cache_key)
    if cached:
        rows, fetched_at, _, metadata, cached_warnings = cached
        return rows, ChartDataStatus(
            provider=str(metadata.get("provider") or instrument.provider),
            provider_label=str(metadata.get("provider_label") or instrument.market),
            mode="cached", fetched_at=fetched_at, stale=False,
            message=str(metadata.get("message") or "命中短期行情缓存"),
            source_id=metadata.get("source_id"), health_status=str(metadata.get("health_status") or "untested"),
            fallback_reason=metadata.get("fallback_reason"),
            **_status_cache_fields(fetched_at),
        ), cached_warnings
    if query.cache_policy == "prefer_cache":
        stale_cached = _read_cache(
            cache_key,
            allow_stale=True,
            instrument_id=instrument.id,
            interval=query.interval,
            range_value=query.range,
        )
        if stale_cached:
            rows, fetched_at, _, metadata, cached_warnings = stale_cached
            maximum_age = 86400 if query.interval in {"1d", "1w", "1mo"} else 900
            fields = _status_cache_fields(fetched_at, stale=True)
            if int(fields["age_seconds"]) <= maximum_age:
                return rows, ChartDataStatus(
                    provider=str(metadata.get("provider") or instrument.provider),
                    provider_label=str(metadata.get("provider_label") or instrument.market),
                    mode="cached", fetched_at=fetched_at, stale=True,
                    message="先显示最近缓存，正在后台刷新",
                    source_id=metadata.get("source_id"), health_status=str(metadata.get("health_status") or "degraded"),
                    fallback_reason=metadata.get("fallback_reason"),
                    **fields,
                ), cached_warnings
    try:
        if instrument.asset_class == "index":
            if instrument.id in {"INDEX:SHCOMP", "INDEX:SZCOMP", "INDEX:CSI300"}:
                route_market = "CN"
            elif instrument.id == "INDEX:HSI":
                route_market = "HK"
            elif instrument.id in {"INDEX:FTSE", "INDEX:DAX"}:
                route_market = "EU"
            elif instrument.id == "INDEX:N225":
                route_market = "JP"
            else:
                route_market = "US"
            candidates = resolve_candidates(route_market, "index_candles")
            if not candidates:
                raise RuntimeError(f"{route_market} index_candles 路由没有可用数据源")
            failures: list[str] = []
            for position, profile in enumerate(candidates):
                source_started = perf_counter()
                try:
                    if profile.adapter != "public_index":
                        raise RuntimeError(f"适配器 {profile.adapter} 不支持指数 K 线")
                    rows = _fetch_public_index(instrument, query, start, end, profile.endpoint)
                    rows = sorted(rows, key=lambda item: item.time)[-query.limit:]
                    if not rows:
                        raise RuntimeError("Provider returned no candles")
                    latency = (perf_counter() - source_started) * 1000
                    profile = record_health(profile.id, True, latency, f"指数 K 线查询成功，共 {len(rows)} 条")
                    fetched_at = _write_cache(
                        cache_key, instrument, query, rows,
                        status={
                            "provider": profile.adapter, "provider_label": profile.name,
                            "source_id": profile.id, "health_status": profile.health_status,
                            "fallback_reason": "；".join(failures) if position else None,
                        },
                    )
                    return rows, ChartDataStatus(
                        provider=profile.adapter, provider_label=profile.name, mode="delayed",
                        fetched_at=fetched_at, stale=False, source_id=profile.id,
                        health_status=profile.health_status,
                        fallback_reason="；".join(failures) if position else None,
                        **_status_cache_fields(fetched_at),
                    ), ([f"数据源降级：{'；'.join(failures)}"] if failures else [])
                except Exception as source_error:  # noqa: BLE001 - continue through route fallbacks
                    latency = (perf_counter() - source_started) * 1000
                    record_health(profile.id, False, latency, str(source_error)[:500])
                    failures.append(f"{profile.name}: {str(source_error)[:120]}")
            raise RuntimeError("；".join(failures) or "指数数据源不可用")
        elif instrument.provider == "binance_futures":
            try:
                rows = _fetch_binance(instrument, query, start, end)
            except RuntimeError as binance_error:
                rows = _fetch_coinbase_fallback(instrument, query)
                rows = sorted(rows, key=lambda item: item.time)[-query.limit:]
                if not rows:
                    raise binance_error
                fallback_warning = "Binance Futures 当前网络不可达；现货 K 线仅用于技术指标，衍生品指标不会使用代理数据。"
                fetched_at = _write_cache(
                    cache_key, instrument, query, rows,
                    status={
                        "provider": "coinbase_fallback",
                        "provider_label": "Coinbase Spot Fallback",
                        "message": "Binance Futures 不可达，临时显示 Coinbase 现货 K 线",
                    },
                    warnings=[fallback_warning],
                )
                return rows, ChartDataStatus(
                    provider="coinbase_fallback",
                    provider_label="Coinbase Spot Fallback",
                    mode="delayed",
                    fetched_at=fetched_at,
                    stale=False,
                    message="Binance Futures 不可达，临时显示 Coinbase 现货 K 线",
                    **_status_cache_fields(fetched_at),
                ), [fallback_warning]
        elif instrument.provider == "finnhub":
            try:
                rows = _fetch_finnhub(instrument, query, start, end)
            except RuntimeError as finnhub_error:
                rows = _fetch_public_index(instrument, query, start, end)
                rows = sorted(rows, key=lambda item: item.time)[-query.limit:]
                if not rows:
                    raise finnhub_error
                fallback_warning = "Finnhub 报价与新闻保持启用；历史 K 线由公开延迟数据补充。"
                fetched_at = _write_cache(
                    cache_key, instrument, query, rows,
                    status={
                        "provider": "public_chart_fallback",
                        "provider_label": "Public Chart Fallback",
                        "message": "Finnhub 历史 K 线不可用，显示公开延迟行情",
                    },
                    warnings=[fallback_warning],
                )
                return rows, ChartDataStatus(
                    provider="public_chart_fallback",
                    provider_label="Public Chart Fallback",
                    mode="delayed",
                    fetched_at=fetched_at,
                    stale=False,
                    message="Finnhub 历史 K 线不可用，显示公开延迟行情",
                    **_status_cache_fields(fetched_at),
                ), [fallback_warning]
        else:
            rows = _fetch_public_index(instrument, query, start, end)
        rows = sorted(rows, key=lambda item: item.time)[-query.limit:]
        if not rows:
            raise RuntimeError("Provider returned no candles")
        mode = "realtime" if instrument.realtime else "delayed" if instrument.delayed else "historical"
        fetched_at = _write_cache(
            cache_key, instrument, query, rows,
            status={"provider": instrument.provider, "provider_label": instrument.market},
        )
        return rows, ChartDataStatus(
            provider=instrument.provider, provider_label=instrument.market, mode=mode,
            fetched_at=fetched_at, stale=False,
            **_status_cache_fields(fetched_at),
        ), []
    except HTTPException:
        raise
    except RuntimeError as exc:
        stale = _read_cache(
            cache_key,
            allow_stale=True,
            instrument_id=instrument.id,
            interval=query.interval,
            range_value=query.range,
        )
        if stale:
            rows, fetched_at, _, metadata, cached_warnings = stale
            return rows, ChartDataStatus(
                provider=str(metadata.get("provider") or instrument.provider),
                provider_label=str(metadata.get("provider_label") or instrument.market),
                mode="cached",
                fetched_at=fetched_at, stale=True, message="实时请求失败，显示最近成功缓存",
                source_id=metadata.get("source_id"), health_status="degraded",
                fallback_reason=str(exc)[:240],
                **_status_cache_fields(fetched_at, stale=True),
            ), [*cached_warnings, f"数据源降级：{str(exc)[:160]}"]
        raise HTTPException(status_code=502, detail=f"Chart provider failed: {str(exc)[:240]}") from exc


def _frame(candles: list[ChartCandle]) -> pd.DataFrame:
    frame = pd.DataFrame([item.model_dump() for item in candles])
    frame.index = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame


def _series_points(values: Iterable[object], times: list[int]) -> list[ChartSeriesPoint]:
    points: list[ChartSeriesPoint] = []
    for time_value, value in zip(times, values):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        points.append(ChartSeriesPoint(time=time_value, value=round(float(value), 8)))
    return points


def _line_series(
    config: IndicatorConfig,
    label: str,
    values: Iterable[object],
    times: list[int],
    color: str,
    *,
    suffix: str = "",
    source: str = "pandas-ta-classic",
) -> ChartIndicatorSeries:
    points = _series_points(values, times)
    return ChartIndicatorSeries(
        id=f"{config.id}{suffix}",
        label=label,
        pane=config.pane or INDICATOR_BY_ID[config.id].pane,
        series_type="line",
        color=config.color or color,
        points=points,
        current_value=points[-1].value if points else None,
        source=source,
    )


def _band_series(
    config: IndicatorConfig,
    label: str,
    lower: Iterable[object],
    middle: Iterable[object],
    upper: Iterable[object],
    times: list[int],
    color: str,
) -> ChartIndicatorSeries:
    points: list[ChartSeriesPoint] = []
    for timestamp, low, mid, high in zip(times, lower, middle, upper):
        values = (low, mid, high)
        if any(value is None or pd.isna(value) for value in values):
            continue
        points.append(
            ChartSeriesPoint(
                time=timestamp,
                lower=round(float(low), 8),
                middle=round(float(mid), 8),
                upper=round(float(high), 8),
            )
        )
    return ChartIndicatorSeries(
        id=config.id,
        label=label,
        pane=config.pane or "main",
        series_type="band",
        color=config.color or color,
        points=points,
        current_value=points[-1].middle if points else None,
        source="pandas-ta-classic",
    )


def _calculate_standard_indicator(
    config: IndicatorConfig, frame: pd.DataFrame, color_index: int
) -> list[ChartIndicatorSeries]:
    identifier = config.id
    definition = INDICATOR_BY_ID.get(identifier)
    if definition is None or definition.category == "derivatives":
        return []
    length = int(config.parameters.get("length", definition.parameters[0].default if definition.parameters else 14))
    length = max(2, min(300, length))
    times = frame["time"].astype(int).tolist()
    close, high, low, volume = frame["close"], frame["high"], frame["low"], frame["volume"]
    color = INDICATOR_COLORS[color_index % len(INDICATOR_COLORS)]
    if identifier == "sma":
        return [_line_series(config, f"SMA {length}", ta.sma(close, length=length), times, color)]
    if identifier == "ema":
        return [_line_series(config, f"EMA {length}", ta.ema(close, length=length), times, color)]
    if identifier == "bbands":
        data = ta.bbands(close, length=length)
        return [_band_series(config, f"Bollinger {length}", data.iloc[:, 0], data.iloc[:, 1], data.iloc[:, 2], times, color)]
    if identifier == "vwap":
        return [_line_series(config, "VWAP", ta.vwap(high, low, close, volume), times, color)]
    if identifier == "ichimoku":
        data, _future = ta.ichimoku(high, low, close)
        return [
            _line_series(config, column.split("_")[0], data[column], times, INDICATOR_COLORS[(color_index + index) % len(INDICATOR_COLORS)], suffix=f":{index}")
            for index, column in enumerate(data.columns[:5])
        ]
    if identifier == "supertrend":
        data = ta.supertrend(high, low, close, length=length)
        return [_line_series(config, f"Supertrend {length}", data.iloc[:, 0], times, color)]
    if identifier == "donchian":
        data = ta.donchian(high, low, lower_length=length, upper_length=length)
        return [_band_series(config, f"Donchian {length}", data.iloc[:, 0], data.iloc[:, 1], data.iloc[:, 2], times, color)]
    if identifier == "keltner":
        data = ta.kc(high, low, close, length=length)
        return [_band_series(config, f"Keltner {length}", data.iloc[:, 0], data.iloc[:, 1], data.iloc[:, 2], times, color)]
    if identifier == "psar":
        data = ta.psar(high, low, close)
        values = data.iloc[:, 0].combine_first(data.iloc[:, 1])
        return [_line_series(config, "Parabolic SAR", values, times, color)]
    if identifier == "volume":
        points = [
            ChartSeriesPoint(
                time=int(row.time),
                value=float(row.volume),
                color="#22ab9488" if row.close >= row.open else "#f2364588",
            )
            for row in frame.itertuples()
        ]
        return [ChartIndicatorSeries(
            id="volume", label="Volume", pane=config.pane or "sub", series_type="histogram",
            color=config.color or color, points=points, current_value=points[-1].value if points else None, source="provider",
        )]
    if identifier == "macd":
        data = ta.macd(close)
        return [
            _line_series(config, "MACD", data.iloc[:, 0], times, color, suffix=":macd"),
            ChartIndicatorSeries(
                id="macd:hist", label="MACD Histogram", pane=config.pane or "sub", series_type="histogram",
                color=config.color or "#94a3b8", points=_series_points(data.iloc[:, 1], times),
                current_value=float(data.iloc[:, 1].dropna().iloc[-1]) if not data.iloc[:, 1].dropna().empty else None,
                source="pandas-ta-classic",
            ),
            _line_series(config, "Signal", data.iloc[:, 2], times, INDICATOR_COLORS[(color_index + 1) % len(INDICATOR_COLORS)], suffix=":signal"),
        ]
    simple = {
        "rsi": ("RSI", ta.rsi(close, length=length)),
        "cci": ("CCI", ta.cci(high, low, close, length=length)),
        "atr": ("ATR", ta.atr(high, low, close, length=length)),
        "obv": ("OBV", ta.obv(close, volume)),
        "mfi": ("MFI", ta.mfi(high, low, close, volume, length=length)),
        "roc": ("ROC", ta.roc(close, length=length)),
        "willr": ("Williams %R", ta.willr(high, low, close, length=length)),
        "cmf": ("CMF", ta.cmf(high, low, close, volume, length=length)),
        "ad": ("A/D", ta.ad(high, low, close, volume)),
        "efi": ("Force Index", ta.efi(close, volume, length=length)),
    }
    if identifier in simple:
        label, values = simple[identifier]
        return [_line_series(config, f"{label} {length}" if definition.parameters else label, values, times, color)]
    if identifier == "stoch":
        data = ta.stoch(high, low, close, k=length)
        return [
            _line_series(config, "%K", data.iloc[:, 0], times, color, suffix=":k"),
            _line_series(config, "%D", data.iloc[:, 1], times, INDICATOR_COLORS[(color_index + 1) % len(INDICATOR_COLORS)], suffix=":d"),
        ]
    if identifier == "adx":
        data = ta.adx(high, low, close, length=length)
        return [
            _line_series(config, "ADX", data.iloc[:, 0], times, color, suffix=":adx"),
            _line_series(config, "DI+", data.iloc[:, 1], times, "#22ab94", suffix=":plus"),
            _line_series(config, "DI-", data.iloc[:, 2], times, "#f23645", suffix=":minus"),
        ]
    extended: dict[str, object] = {
        "wma": ta.wma(close, length=length), "dema": ta.dema(close, length=length),
        "tema": ta.tema(close, length=length), "hma": ta.hma(close, length=length),
        "alma": ta.alma(close, length=length), "trix": ta.trix(close, length=length),
        "ppo": ta.ppo(close), "stochrsi": ta.stochrsi(close, length=length),
        "uo": ta.uo(high, low, close), "ao": ta.ao(high, low),
        "rvi": ta.rvi(close, length=length), "cfo": ta.cfo(close, length=length),
        "natr": ta.natr(high, low, close, length=length), "stdev": ta.stdev(close, length=length),
        "chop": ta.chop(high, low, close, length=length), "squeeze": ta.squeeze(high, low, close),
        "pvt": ta.pvt(close, volume), "nvi": ta.nvi(close, volume), "pvi": ta.pvi(close, volume),
        "kvo": ta.kvo(high, low, close, volume),
    }
    result = extended.get(identifier)
    if result is not None:
        if isinstance(result, pd.DataFrame):
            return [
                _line_series(config, str(column), result[column], times,
                             INDICATOR_COLORS[(color_index + offset) % len(INDICATOR_COLORS)], suffix=f":{offset}")
                for offset, column in enumerate(result.columns[:3])
            ]
        return [_line_series(config, f"{definition.name} {length}", result, times, color)]
    return []


def _binance_metric_url(path: str, params: dict[str, object]) -> str:
    return f"https://fapi.binance.com{path}?{urlencode(params)}"


def _fetch_derivative_metric(
    instrument: MarketInstrument, query: ChartQuery, identifier: str
) -> tuple[list[tuple[int, float]], str]:
    period = query.interval if query.interval in DERIVATIVE_PERIODS else None
    if identifier == "taker_buy_sell_ratio" and query.interval in ALL_INTERVALS:
        # Candle-native taker volume is handled separately and works at every kline interval.
        return [], "binance-futures-kline"
    if not period:
        raise ValueError(f"{query.interval} 不提供该衍生品统计周期")
    common = {"symbol": instrument.provider_symbol, "period": period, "limit": min(query.limit, 500)}
    routes = {
        "open_interest": ("/futures/data/openInterestHist", "sumOpenInterest"),
        "global_long_short_ratio": ("/futures/data/globalLongShortAccountRatio", "longShortRatio"),
        "top_account_ratio": ("/futures/data/topLongShortAccountRatio", "longShortRatio"),
        "top_position_ratio": ("/futures/data/topLongShortPositionRatio", "longShortRatio"),
    }
    if identifier in routes:
        path, key = routes[identifier]
        payload = _http_json(_binance_metric_url(path, common))
        return [
            (int(item["timestamp"]) // 1000, float(item[key]))
            for item in payload if isinstance(item, dict) and item.get(key) is not None
        ], "Binance Futures"
    if identifier == "funding_rate":
        payload = _http_json(_binance_metric_url(
            "/fapi/v1/fundingRate", {"symbol": instrument.provider_symbol, "limit": min(query.limit, 1000)}
        ))
        return [
            (int(item["fundingTime"]) // 1000, float(item["fundingRate"]) * 100)
            for item in payload if isinstance(item, dict)
        ], "Binance Futures"
    if identifier == "basis":
        payload = _http_json(_binance_metric_url(
            "/futures/data/basis",
            {"pair": instrument.provider_symbol, "contractType": "PERPETUAL", "period": period, "limit": min(query.limit, 500)},
        ))
        return [
            (int(item["timestamp"]) // 1000, float(item.get("basisRate") or 0) * 100)
            for item in payload if isinstance(item, dict)
        ], "Binance Futures"
    return [], "Binance Futures"


def _derivative_cache_key(instrument: MarketInstrument, query: ChartQuery, identifier: str) -> str:
    raw = f"{instrument.id}:{query.interval}:{query.range}:{query.before or ''}:{query.limit}:{identifier}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _read_derivative_cache(cache_key: str, *, allow_stale: bool = False) -> tuple[list[tuple[int, float]], str] | None:
    now = datetime.now(timezone.utc)
    try:
        with connect(settings.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM indicator_data_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    cutoff = datetime.fromisoformat(row["stale_until"] if allow_stale else row["expires_at"])
    if cutoff < now:
        return None
    payload = json.loads(row["payload"])
    return [(int(item[0]), float(item[1])) for item in payload], str(row["source"])


def _write_derivative_cache(
    cache_key: str,
    instrument: MarketInstrument,
    query: ChartQuery,
    identifier: str,
    pairs: list[tuple[int, float]],
    source: str,
) -> None:
    now = datetime.now(timezone.utc)
    with connect(settings.database_path) as connection:
        connection.execute("DELETE FROM indicator_data_cache WHERE stale_until < ?", (now.isoformat(),))
        connection.execute(
            """
            INSERT OR REPLACE INTO indicator_data_cache (
                cache_key, instrument_id, interval, metric, payload, source,
                fetched_at, expires_at, stale_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key, instrument.id, query.interval, identifier, json.dumps(pairs), source,
                now.isoformat(), (now + timedelta(seconds=30)).isoformat(),
                (now + timedelta(minutes=15)).isoformat(),
            ),
        )


def _derivative_lock(cache_key: str) -> Lock:
    with _DERIVATIVE_LOCKS_GUARD:
        return _DERIVATIVE_LOCKS.setdefault(cache_key, Lock())


def _derivative_metric(
    instrument: MarketInstrument, query: ChartQuery, identifier: str
) -> tuple[list[tuple[int, float]], str]:
    cache_key = _derivative_cache_key(instrument, query, identifier)
    cached = _read_derivative_cache(cache_key)
    if cached:
        return cached
    with _derivative_lock(cache_key):
        cached = _read_derivative_cache(cache_key)
        if cached:
            return cached
        try:
            pairs, source = _fetch_derivative_metric(instrument, query, identifier)
            _write_derivative_cache(cache_key, instrument, query, identifier, pairs, source)
            return pairs, source
        except RuntimeError:
            stale = _read_derivative_cache(cache_key, allow_stale=True)
            if stale:
                return stale[0], f"{stale[1]} stale cache"
            raise


def _calculate_derivative_indicator(
    config: IndicatorConfig,
    instrument: MarketInstrument,
    query: ChartQuery,
    candles: list[ChartCandle],
    color_index: int,
) -> ChartIndicatorSeries:
    color = config.color or INDICATOR_COLORS[color_index % len(INDICATOR_COLORS)]
    if config.id in {"taker_buy_sell_ratio", "taker_buy_pct", "volume_delta", "cvd"}:
        points = []
        cumulative = 0.0
        for candle in candles:
            if candle.taker_buy_volume is None:
                continue
            sell_volume = max(0.0, candle.volume - candle.taker_buy_volume)
            delta = candle.taker_buy_volume - sell_volume
            cumulative += delta
            if config.id == "taker_buy_sell_ratio":
                value = candle.taker_buy_volume / sell_volume if sell_volume else None
            elif config.id == "taker_buy_pct":
                value = candle.taker_buy_volume / candle.volume * 100 if candle.volume else None
            elif config.id == "volume_delta":
                value = delta
            else:
                value = cumulative
            points.append(ChartSeriesPoint(
                time=candle.time,
                value=round(value, 6) if value is not None else None,
            ))
        source = "Binance Futures Kline"
    else:
        metric_id = "funding_rate" if config.id == "annualized_funding" else "basis" if config.id == "annualized_basis" else config.id
        pairs, source = _derivative_metric(instrument, query, metric_id)
        if config.id == "open_interest_change":
            oi_pairs, source = _derivative_metric(instrument, query, "open_interest")
            pairs = [
                (current[0], (current[1] / previous[1] - 1) * 100 if previous[1] else 0)
                for previous, current in zip(oi_pairs, oi_pairs[1:])
            ]
        if config.id == "annualized_funding":
            pairs = [(timestamp, value * 3 * 365) for timestamp, value in pairs]
        if config.id == "annualized_basis":
            pairs = [(timestamp, value * 365) for timestamp, value in pairs]
        points = [ChartSeriesPoint(time=timestamp, value=round(value, 8)) for timestamp, value in pairs]
    definition = INDICATOR_BY_ID[config.id]
    return ChartIndicatorSeries(
        id=config.id,
        label=definition.name,
        pane=config.pane or "sub",
        series_type=definition.series_type,
        color=color,
        points=points,
        current_value=points[-1].value if points else None,
        source=source,
    )


def _calculate_fred_indicator(
    config: IndicatorConfig, query: ChartQuery, color_index: int,
) -> ChartIndicatorSeries:
    from app.modules.platform.service import resolve_api_key

    definition = INDICATOR_BY_ID[config.id]
    fred_series = {
        "fred_dff": "DFF", "fred_dgs2": "DGS2", "fred_dgs10": "DGS10",
        "fred_t10y2y": "T10Y2Y", "fred_cpi": "CPIAUCSL", "fred_unrate": "UNRATE",
    }[config.id]
    candidates = [item for item in resolve_candidates("GLOBAL", "macro_series") if item.adapter == "fred"]
    if not candidates:
        raise RuntimeError("未配置可用的 FRED 数据源")
    profile = candidates[0]
    key = resolve_api_key("data_source", profile.id, profile.api_key_env)
    if not key:
        raise RuntimeError(f"{profile.name} 缺少 API Key")
    end = datetime.fromisoformat(query.before.replace("Z", "+00:00")) if query.before else datetime.now(timezone.utc)
    start = _range_start(query.range, end).date().isoformat()
    url = f"{profile.endpoint.rstrip('/')}/series/observations?{urlencode({'series_id': fred_series, 'api_key': key, 'file_type': 'json', 'observation_start': start, 'sort_order': 'asc'})}"
    started = perf_counter()
    try:
        payload = _http_json(url)
        record_health(profile.id, True, (perf_counter() - started) * 1000, f"{fred_series} 查询成功")
    except RuntimeError as exc:
        record_health(profile.id, False, (perf_counter() - started) * 1000, str(exc))
        raise
    points = [
        ChartSeriesPoint(
            time=int(datetime.fromisoformat(str(row["date"])).replace(tzinfo=timezone.utc).timestamp()),
            value=round(float(row["value"]), 8),
        )
        for row in payload.get("observations", []) if isinstance(payload, dict) and isinstance(row, dict)
        and row.get("value") not in {None, "."}
    ]
    return ChartIndicatorSeries(
        id=config.id, label=definition.name, pane=config.pane or "sub", series_type="line",
        color=config.color or INDICATOR_COLORS[color_index % len(INDICATOR_COLORS)], points=points,
        current_value=points[-1].value if points else None, source=profile.name, source_id=profile.id,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def build_chart_bundle(query: ChartQuery, timings: dict[str, float] | None = None) -> ChartBundle:
    started = perf_counter()
    instrument = resolve_instrument(query.instrument_id)
    if query.interval not in instrument.intervals:
        raise HTTPException(status_code=422, detail="Interval is not supported by this instrument")
    validate_interval_range(query.interval, query.range)
    market_started = perf_counter()
    candles, status, warnings = _load_candles(instrument, query)
    if timings is not None:
        timings["market"] = (perf_counter() - market_started) * 1000
    configs = query.indicators
    series: list[ChartIndicatorSeries] = []
    indicator_started = perf_counter()
    if query.include_series and configs:
        valid_configs: list[tuple[int, IndicatorConfig, IndicatorDefinition, str]] = []
        for index, config in enumerate(configs):
            if not config.visible:
                continue
            definition = INDICATOR_BY_ID.get(config.id)
            if definition is None:
                warnings.append(f"未知指标：{config.id}")
                continue
            if definition.asset_classes and instrument.asset_class not in definition.asset_classes:
                warnings.append(f"{definition.name} 不适用于 {instrument.asset_class}")
                continue
            owner_id = config.instance_id or f"{config.id}:{index}"
            valid_configs.append((index, config, definition, owner_id))

        generated_by_index: dict[int, list[ChartIndicatorSeries]] = {}
        standard_configs = [item for item in valid_configs if item[2].source_mode == "calculated" and item[2].category != "derivatives"]
        derivative_configs = [item for item in valid_configs if item[2].category == "derivatives"]
        provider_configs = [item for item in valid_configs if item[2].source_mode == "provider"]
        frame = _frame(candles) if standard_configs else None
        for index, config, definition, owner_id in standard_configs:
            try:
                generated = _calculate_standard_indicator(config, frame, index)  # type: ignore[arg-type]
                generated_by_index[index] = [item.model_copy(update={"owner_id": owner_id}) for item in generated]
            except (RuntimeError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                warnings.append(f"{definition.name} 暂不可用：{str(exc)[:120]}")
        if derivative_configs:
            with ThreadPoolExecutor(max_workers=min(4, len(derivative_configs))) as executor:
                futures = {
                    executor.submit(_calculate_derivative_indicator, config, instrument, query, candles, index):
                    (index, definition, owner_id)
                    for index, config, definition, owner_id in derivative_configs
                }
                for future in as_completed(futures):
                    index, definition, owner_id = futures[future]
                    try:
                        generated_by_index[index] = [future.result().model_copy(update={"owner_id": owner_id})]
                    except (RuntimeError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                        warnings.append(f"{definition.name} 暂不可用：{str(exc)[:120]}")
        for index, config, definition, owner_id in provider_configs:
            try:
                generated_by_index[index] = [
                    _calculate_fred_indicator(config, query, index).model_copy(update={"owner_id": owner_id})
                ]
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                warnings.append(f"{definition.name} 暂不可用：{str(exc)[:120]}")
        for index in sorted(generated_by_index):
            series.extend(generated_by_index[index])
    if timings is not None:
        timings["indicators"] = (perf_counter() - indicator_started) * 1000
    end = datetime.fromisoformat(query.before.replace("Z", "+00:00")) if query.before else datetime.now(timezone.utc)
    visible_start_timestamp = int(_range_start(query.range, end).timestamp())
    visible_candles = [item for item in candles if item.time >= visible_start_timestamp]
    if visible_candles:
        candles = visible_candles[-query.limit:]
        first_visible = candles[0].time
        series = [
            item.model_copy(update={"points": [point for point in item.points if point.time >= first_visible]})
            for item in series
        ]
    next_before = datetime.fromtimestamp(candles[0].time, tz=timezone.utc).isoformat() if len(candles) >= query.limit else None
    bundle = ChartBundle(
        instrument=instrument,
        interval=query.interval,
        range=query.range,
        candles=candles,
        series=series,
        status=status,
        warnings=warnings,
        next_before=next_before,
    )
    if timings is not None:
        timings["total"] = (perf_counter() - started) * 1000
    return bundle


def latest_instrument_quote(instrument_id: str) -> InstrumentQuote:
    instrument = resolve_instrument(instrument_id)
    failures: list[str] = []
    bundle = None
    for interval, range_value in (("1m", "1D"), ("15m", "1D"), ("1d", "1M")):
        if interval not in instrument.intervals:
            continue
        try:
            candidate = build_chart_bundle(ChartQuery(
                instrument_id=instrument.id,
                interval=interval,
                range=range_value,
                indicators=[],
                news_mode="off",
                include_series=False,
                cache_policy="prefer_cache",
                limit=100,
            ))
            if candidate.candles:
                bundle = candidate
                break
        except (HTTPException, RuntimeError, ValueError) as exc:
            failures.append(str(getattr(exc, "detail", exc)))
    if bundle is None:
        detail = "；".join(failures[-2:]) or "当前标的暂无可用行情价格"
        raise HTTPException(status_code=503, detail=detail)
    candle = bundle.candles[-1]
    return InstrumentQuote(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        price=candle.close,
        currency=instrument.currency,
        as_of=datetime.fromtimestamp(candle.time, tz=timezone.utc).isoformat(),
        source=bundle.status.provider_label or bundle.status.provider,
        stale=bundle.status.stale,
    )


def build_chart_series(query: ChartSeriesQuery, timings: dict[str, float] | None = None) -> ChartSeriesBundle:
    bundle = build_chart_bundle(ChartQuery(
        instrument_id=query.instrument_id,
        interval=query.interval,
        range=query.range,
        indicators=query.indicators,
        news_mode="off",
        include_series=True,
        cache_policy="prefer_cache",
        before=query.before,
        limit=query.limit,
    ), timings=timings)
    statuses = {config.instance_id or config.id: "ready" for config in query.indicators}
    for warning in bundle.warnings:
        for config in query.indicators:
            definition = INDICATOR_BY_ID.get(config.id)
            if definition and definition.name in warning:
                statuses[config.instance_id or config.id] = "warning"
    return ChartSeriesBundle(
        revision=bundle.status.revision,
        series=bundle.series,
        warnings=bundle.warnings,
        status_by_indicator=statuses,
    )


def _row_to_layout(row: sqlite3.Row) -> ChartLayout:
    return ChartLayout(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        asset_class=row["asset_class"],
        is_default=bool(row["is_default"]),
        interval=row["interval"],
        range=row["range_value"],
        indicators=[IndicatorConfig.model_validate(item) for item in json.loads(row["indicators"])],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_chart_layouts(user_id: str, asset_class: ChartAssetClass | None = None) -> list[ChartLayout]:
    with connect(settings.database_path) as connection:
        if asset_class:
            rows = connection.execute(
                "SELECT * FROM chart_layouts WHERE user_id = ? AND asset_class = ? ORDER BY is_default DESC, name",
                (user_id, asset_class),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM chart_layouts WHERE user_id = ? ORDER BY asset_class, is_default DESC, name",
                (user_id,),
            ).fetchall()
    return [_row_to_layout(row) for row in rows]


def create_chart_layout(user_id: str, payload: ChartLayoutCreate) -> ChartLayout:
    validate_interval_range(payload.interval, payload.range)
    now = datetime.now(timezone.utc).isoformat()
    layout_id = f"layout-{sha256(f'{user_id}:{payload.name}:{now}'.encode()).hexdigest()[:16]}"
    with connect(settings.database_path) as connection:
        if payload.is_default:
            connection.execute(
                "UPDATE chart_layouts SET is_default = 0 WHERE user_id = ? AND asset_class = ?",
                (user_id, payload.asset_class),
            )
        try:
            connection.execute(
                """
                INSERT INTO chart_layouts (
                    id, user_id, name, asset_class, is_default, interval, range_value,
                    indicators, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layout_id, user_id, payload.name.strip(), payload.asset_class, int(payload.is_default),
                    payload.interval, payload.range,
                    json.dumps([item.model_dump(mode="json") for item in payload.indicators]),
                    now, now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Layout name already exists for this asset class") from exc
        row = connection.execute("SELECT * FROM chart_layouts WHERE id = ?", (layout_id,)).fetchone()
    return _row_to_layout(row)


def update_chart_layout(user_id: str, layout_id: str, payload: ChartLayoutUpdate) -> ChartLayout:
    with connect(settings.database_path) as connection:
        current = connection.execute(
            "SELECT * FROM chart_layouts WHERE id = ? AND user_id = ?", (layout_id, user_id)
        ).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Chart layout not found")
        values = {
            "name": payload.name.strip() if payload.name is not None else current["name"],
            "is_default": int(payload.is_default) if payload.is_default is not None else current["is_default"],
            "interval": payload.interval or current["interval"],
            "range_value": payload.range or current["range_value"],
            "indicators": json.dumps([item.model_dump(mode="json") for item in payload.indicators])
            if payload.indicators is not None else current["indicators"],
        }
        validate_interval_range(values["interval"], values["range_value"])
        if values["is_default"]:
            connection.execute(
                "UPDATE chart_layouts SET is_default = 0 WHERE user_id = ? AND asset_class = ? AND id != ?",
                (user_id, current["asset_class"], layout_id),
            )
        try:
            connection.execute(
                """
                UPDATE chart_layouts
                SET name = ?, is_default = ?, interval = ?, range_value = ?, indicators = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    values["name"], values["is_default"], values["interval"], values["range_value"],
                    values["indicators"], datetime.now(timezone.utc).isoformat(), layout_id, user_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Layout name already exists for this asset class") from exc
        row = connection.execute("SELECT * FROM chart_layouts WHERE id = ?", (layout_id,)).fetchone()
    return _row_to_layout(row)


def delete_chart_layout(user_id: str, layout_id: str) -> dict[str, bool]:
    with connect(settings.database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM chart_layouts WHERE id = ? AND user_id = ?", (layout_id, user_id)
        )
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="Chart layout not found")
    return {"deleted": True}


def latest_indicator_value(
    instrument_id: str,
    indicator_id: str,
    interval: str,
    parameters: dict[str, int | float | str],
) -> float:
    if interval not in ALL_INTERVALS:
        raise HTTPException(status_code=422, detail="Unsupported alert interval")
    bundle = build_chart_bundle(
        ChartQuery(
            instrument_id=instrument_id,
            interval=interval,
            range=MINIMUM_RANGE_BY_INTERVAL[interval],
            indicators=[IndicatorConfig(id=indicator_id, parameters=parameters)],
            limit=300,
        )
    )
    matching = next((item for item in bundle.series if item.id == indicator_id or item.id.startswith(f"{indicator_id}:")), None)
    if matching is None or matching.current_value is None:
        raise HTTPException(status_code=409, detail="Indicator value is unavailable")
    return float(matching.current_value)


def latest_chart_price(instrument_id: str, interval: str = "1h") -> float:
    if interval not in ALL_INTERVALS:
        raise HTTPException(status_code=422, detail="Unsupported alert interval")
    bundle = build_chart_bundle(
        ChartQuery(
            instrument_id=instrument_id,
            interval=interval,
            range=MINIMUM_RANGE_BY_INTERVAL[interval],
            indicators=[],
            limit=100,
        )
    )
    if not bundle.candles:
        raise HTTPException(status_code=409, detail="Price is unavailable")
    return float(bundle.candles[-1].close)
