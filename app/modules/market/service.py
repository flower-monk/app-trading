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
    Candle,
    ChartQuery,
    DataSourceProfile,
    FilterLibraryScoreItem,
    FilterLibraryScoreReport,
    FilterStrategyCoverageItem,
    FilterStrategyCoverageReport,
    FilterValidationRequest,
    IndicatorSummary,
    Literal,
    MarketBreadth,
    MarketBreadthGroup,
    MarketProvider,
    MarketProviderStatus,
    MarketSymbol,
    ScreenerMatch,
    ScreenerPreset,
    ScreenerPresetCreate,
    ScreenerPresetUpdate,
    ScreenerPresetValidation,
    ScreenerResult,
    StrategyCandidateItem,
    StrategyCandidateReport,
    StrategyTemplate,
    StrategyTemplateCreate,
    StrategyTemplateUpdate,
    SymbolSnapshot,
    WatchlistBulkAction,
    WatchlistBulkResult,
    WatchlistGroup,
    WatchlistGroupCreate,
    WatchlistGroupUpdate,
    WatchlistItemCreate,
    WatchlistResourceCreate,
    WatchlistResourceItem,
    WatchlistResourcePage,
    WatchlistResourceUpdate,
    financial_calendar,
    market,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
credential_metadata_for_resource = service_call('app.modules.platform.service', 'credential_metadata_for_resource')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
fetch_information_source_items = service_call('app.modules.news.service', 'fetch_information_source_items')
filter_admission_report = service_call('app.modules.research.service', 'filter_admission_report')
get_factor_definition = service_call('app.modules.research.service', 'get_factor_definition')
information_source_is_ingestible = service_call('app.modules.news.service', 'information_source_is_ingestible')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_factor_definitions = service_call('app.modules.research.service', 'list_factor_definitions')
normalize_symbols = service_call('app.modules.news.service', 'normalize_symbols')
resolve_api_key = service_call('app.modules.platform.service', 'resolve_api_key')
row_to_source = service_call('app.modules.news.service', 'row_to_source')
slugify_factor_id = service_call('app.modules.research.service', 'slugify_factor_id')
trade_readiness_report = service_call('app.modules.research.service', 'trade_readiness_report')
validate_filters = service_call('app.modules.research.service', 'validate_filters')


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


class FinnhubMarketProvider:
    name = "finnhub"
    display_name = "Finnhub U.S. equities REST"
    snapshot_cache_seconds = 30
    _snapshot_cache: list[SymbolSnapshot] = []
    _snapshot_cached_at: datetime | None = None
    supported_symbols = {
        "AAPL": ("Apple Inc.", "NASDAQ", "stock"),
        "NVDA": ("NVIDIA Corp.", "NASDAQ", "stock"),
        "TSLA": ("Tesla Inc.", "NASDAQ", "stock"),
        "MSFT": ("Microsoft Corp.", "NASDAQ", "stock"),
        "SPY": ("S&P 500 ETF", "NYSE Arca", "etf"),
        "QQQ": ("Nasdaq 100 ETF", "NASDAQ", "etf"),
    }

    @property
    def base_url(self) -> str:
        return getattr(self, "_base_url_override", "") or os.getenv("FINNHUB_BASE_URL", "https://finnhub.io/api/v1").rstrip("/")

    def _api_key(self) -> str:
        key = getattr(self, "_api_key_override", "") or resolve_api_key("market_provider", "finnhub", "FINNHUB_API_KEY")
        if not key:
            raise HTTPException(status_code=503, detail="FINNHUB_API_KEY is not configured")
        return key

    def _get(self, path: str, params: dict[str, object]) -> object:
        query = urlencode({**params, "token": self._api_key()})
        try:
            return fetch_json_url(f"{self.base_url}{path}?{query}")
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Finnhub request failed: {exc}") from exc

    def _symbol(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if normalized not in self.supported_symbols:
            raise HTTPException(status_code=404, detail="Symbol is not supported by Finnhub provider")
        return normalized

    def register_symbol(self, symbol: str, name: str = "", market: str = "US", asset_class: str = "stock") -> None:
        normalized = symbol.strip().upper()
        self.supported_symbols[normalized] = (
            name.strip() or normalized,
            market.strip() or "US",
            "etf" if asset_class == "etf" else "stock",
        )
        self._snapshot_cached_at = None

    def list_symbols(self) -> list[MarketSymbol]:
        return [
            MarketSymbol(
                symbol=symbol,
                name=metadata[0],
                asset_class=metadata[2],
                market=metadata[1],
                currency="USD",
                active=True,
            )
            for symbol, metadata in self.supported_symbols.items()
        ]

    def _quote(self, symbol: str) -> dict[str, object]:
        payload = self._get("/quote", {"symbol": self._symbol(symbol)})
        if not isinstance(payload, dict) or not payload.get("c"):
            raise HTTPException(status_code=502, detail="Unexpected Finnhub quote response")
        return payload

    def watchlist(self) -> list[SymbolSnapshot]:
        now = datetime.now(timezone.utc)
        try:
            persisted = persisted_watchlist_symbols(self.name)
        except (HTTPException, sqlite3.Error):
            persisted = []
        for symbol in persisted:
            if symbol not in self.supported_symbols:
                self.register_symbol(symbol)
        if self._snapshot_cached_at and (now - self._snapshot_cached_at).total_seconds() < self.snapshot_cache_seconds:
            return [item.model_copy() for item in self._snapshot_cache]
        snapshots: list[SymbolSnapshot] = []
        symbols = persisted or list(self.supported_symbols)
        for symbol in symbols:
            name, market, _asset_class = self.supported_symbols[symbol]
            quote = self._quote(symbol)
            price = float(quote.get("c", 0) or 0)
            change = float(quote.get("dp", 0) or 0)
            trend_score = max(5, min(95, int(52 + change * 5)))
            if trend_score >= 75:
                signal: Literal["strong_buy", "buy", "neutral", "sell", "strong_sell"] = "strong_buy"
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
                    name=name,
                    market=f"{market} / Finnhub",
                    price=round(price, 2),
                    change_percent=round(change, 2),
                    volume="N/A",
                    signal=signal,
                    rsi=round(max(5, min(95, 50 + change * 2)), 1),
                    macd=round(change / 3, 2),
                    trend_score=trend_score,
                )
            )
        self._snapshot_cache = snapshots
        self._snapshot_cached_at = now
        return snapshots

    def candles(self, symbol: str, interval: Literal["15m", "1h", "4h", "1d"], limit: int) -> list[Candle]:
        normalized = self._symbol(symbol)
        resolution = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}[interval]
        step_seconds = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[interval]
        bounded_limit = max(40, min(240, limit))
        end_time = int(datetime.now(timezone.utc).timestamp())
        start_time = end_time - step_seconds * (bounded_limit + 10)
        payload = self._get(
            "/stock/candle",
            {"symbol": normalized, "resolution": resolution, "from": start_time, "to": end_time},
        )
        if not isinstance(payload, dict) or payload.get("s") != "ok":
            detail = payload.get("error") if isinstance(payload, dict) else None
            raise HTTPException(status_code=502, detail=f"Finnhub candle data unavailable{f': {detail}' if detail else ''}")
        timestamps = payload.get("t", [])
        opens = payload.get("o", [])
        highs = payload.get("h", [])
        lows = payload.get("l", [])
        closes_raw = payload.get("c", [])
        volumes = payload.get("v", [])
        if not all(isinstance(values, list) for values in (timestamps, opens, highs, lows, closes_raw, volumes)):
            raise HTTPException(status_code=502, detail="Unexpected Finnhub candle response")

        closes: list[float] = []
        rows: list[Candle] = []
        records = zip(timestamps, opens, highs, lows, closes_raw, volumes)
        for timestamp, open_price, high, low, close_raw, volume in list(records)[-bounded_limit:]:
            close = float(close_raw)
            closes.append(close)
            ma20 = sum(closes[-20:]) / min(len(closes), 20)
            ma60 = sum(closes[-60:]) / min(len(closes), 60)
            if len(closes) >= 15:
                gains = [max(0, closes[index] - closes[index - 1]) for index in range(len(closes) - 14, len(closes))]
                losses = [max(0, closes[index - 1] - closes[index]) for index in range(len(closes) - 14, len(closes))]
                average_gain = sum(gains) / 14
                average_loss = sum(losses) / 14
                rsi = 100 if average_loss == 0 else 100 - (100 / (1 + average_gain / average_loss))
            else:
                rsi = 50
            rows.append(
                Candle(
                    time=datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat(),
                    open=round(float(open_price), 2),
                    high=round(float(high), 2),
                    low=round(float(low), 2),
                    close=round(close, 2),
                    volume=int(float(volume or 0)),
                    ma20=round(ma20, 2),
                    ma60=round(ma60, 2),
                    rsi=round(max(5, min(95, rsi)), 1),
                )
            )
        if not rows:
            raise HTTPException(status_code=502, detail="Finnhub returned no candle data")
        return rows

    def indicators(self, symbol: str) -> list[IndicatorSummary]:
        rows = self.candles(symbol, interval="1h", limit=80)
        latest = rows[-1]
        previous = rows[-2] if len(rows) > 1 else latest
        trend_up = latest.close >= latest.ma20 >= latest.ma60
        volatility = (max(item.high for item in rows[-20:]) - min(item.low for item in rows[-20:])) / latest.close * 100
        macd_proxy = (latest.ma20 - latest.ma60) / latest.close * 100
        return [
            IndicatorSummary(label="RSI 14", value=str(latest.rsi), state="up" if latest.rsi > 55 else "down" if latest.rsi < 45 else "flat", detail="按 Finnhub 1h K线估算"),
            IndicatorSummary(label="趋势结构", value="多头排列" if trend_up else "震荡/转弱", state="up" if trend_up else "flat", detail="MA20 与 MA60 对比"),
            IndicatorSummary(label="波动率", value=f"{volatility:.1f}%", state="up" if volatility > 5 else "flat", detail="近 20 根 1h K线区间"),
            IndicatorSummary(label="MACD Proxy", value=f"{macd_proxy:+.2f}%", state="up" if macd_proxy > 0 else "down", detail=f"上一收盘 {previous.close:.2f}"),
        ]

    def status(self, is_active: bool) -> MarketProviderStatus:
        _credential_id, origin, configured, _hint, _test_status = credential_metadata_for_resource(
            "market_provider", "finnhub", "FINNHUB_API_KEY"
        )
        return MarketProviderStatus(
            name=self.name,
            display_name=self.display_name,
            is_active=is_active,
            is_realtime=False,
            is_delayed=True,
            status="online" if configured else "degraded",
            coverage=["U.S. stocks and ETFs: AAPL, NVDA, TSLA, MSFT, SPY, QQQ"],
            capabilities=["quotes", "candles", "indicator-estimates", "backtest-data"],
            message=f"Server-side Finnhub REST adapter. Credential: {origin}." if configured else "Set FINNHUB_API_KEY or configure it in Credential Management.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


class PublicIndexMarketProvider:
    name = "public_index"
    display_name = "Public index delayed data"

    def list_symbols(self) -> list[MarketSymbol]:
        from app.modules.market.charting import list_instruments

        return [
            MarketSymbol(
                symbol=item.symbol, name=item.name, asset_class="index", market=item.market,
                currency=item.currency, active=True,
            )
            for item in list_instruments(asset_class="index")
        ]

    @staticmethod
    def _snapshot(symbol: str) -> SymbolSnapshot:
        from app.modules.market.charting import build_chart_bundle, list_instruments

        instrument = next(
            (item for item in list_instruments(asset_class="index") if item.symbol.upper() == symbol.upper()),
            None,
        )
        if instrument is None:
            raise HTTPException(status_code=404, detail="Index is not supported")
        bundle = build_chart_bundle(ChartQuery(
            instrument_id=instrument.id, interval="1d", range="1M", indicators=[],
            news_mode="off", include_series=False, limit=100,
        ))
        candles = bundle.candles
        if not candles:
            raise HTTPException(status_code=502, detail="Index snapshot is unavailable")
        latest = candles[-1]
        previous = candles[-2] if len(candles) > 1 else latest
        change = (latest.close / previous.close - 1) * 100 if previous.close else 0.0
        closes = [item.close for item in candles]
        changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))][-14:]
        gains = [max(0.0, value) for value in changes]
        losses = [max(0.0, -value) for value in changes]
        average_gain = sum(gains) / len(gains) if gains else 0.0
        average_loss = sum(losses) / len(losses) if losses else 0.0
        if average_loss == 0:
            rsi = 100.0 if average_gain > 0 else 50.0
        else:
            rsi = 100 - 100 / (1 + average_gain / average_loss)
        ma12 = sum(closes[-12:]) / min(12, len(closes))
        ma26 = sum(closes[-26:]) / min(26, len(closes))
        macd = ma12 - ma26
        trend_score = max(5, min(95, int(50 + change * 4 + (8 if macd > 0 else -8))))
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
        volume = latest.volume
        volume_label = f"{volume / 1_000_000_000:.1f}B" if volume >= 1_000_000_000 else f"{volume / 1_000_000:.1f}M"
        return SymbolSnapshot(
            symbol=instrument.symbol, name=instrument.name, market=instrument.market,
            price=round(latest.close, 2), change_percent=round(change, 2), volume=volume_label,
            signal=signal, rsi=round(max(0, min(100, rsi)), 1), macd=round(macd, 2),
            trend_score=trend_score,
        )

    def watchlist(self) -> list[SymbolSnapshot]:
        symbols = persisted_watchlist_symbols(self.name)
        snapshots: list[SymbolSnapshot] = []
        for symbol in symbols:
            try:
                snapshots.append(self._snapshot(symbol))
            except (HTTPException, RuntimeError, ValueError):
                continue
        return snapshots

    def status(self, is_active: bool) -> MarketProviderStatus:
        return MarketProviderStatus(
            name=self.name, display_name=self.display_name, is_active=is_active,
            is_realtime=False, is_delayed=True, status="online",
            coverage=["12 major global market indexes"],
            capabilities=["symbols", "delayed quotes", "candles", "indicator-estimates"],
            message="Uses the managed public index candle route and local chart cache.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


MARKET_PROVIDERS: dict[str, MarketProvider] = {
    "binance": BinanceMarketProvider(),
    "coinbase": CoinbaseMarketProvider(),
    "finnhub": FinnhubMarketProvider(),
}


PUBLIC_INDEX_PROVIDER = PublicIndexMarketProvider()


def watchlist_provider(provider_name: str) -> MarketProvider | None:
    return PUBLIC_INDEX_PROVIDER if provider_name == "public_index" else MARKET_PROVIDERS.get(provider_name)


def list_supported_market_symbols() -> list[MarketSymbol]:
    symbols: dict[str, MarketSymbol] = {}
    for provider in [*MARKET_PROVIDERS.values(), PUBLIC_INDEX_PROVIDER]:
        for item in provider.list_symbols():
            symbols.setdefault(item.symbol, item)
    return list(symbols.values())


def search_market_symbols(query: str = "") -> list[MarketSymbol]:
    """Return the normalized built-in and dynamic instrument catalog."""

    needle = query.strip()
    rows = list_supported_market_symbols()
    from app.modules.market.charting import list_instruments

    catalog = list_instruments(needle if len(needle) >= 2 else "")
    dynamic = [
        MarketSymbol(
            symbol=item.symbol,
            name=item.name,
            asset_class=item.asset_class,
            market=item.market,
            currency=item.currency,
            active=True,
        )
        for item in catalog
    ]
    merged = {item.symbol: item for item in rows}
    merged.update({item.symbol: item for item in dynamic})
    result = list(merged.values())
    if not needle:
        return result
    normalized = needle.lower()
    return [
        item
        for item in result
        if normalized in item.symbol.lower() or normalized in item.name.lower()
    ]


def get_active_market_provider_name() -> str:
    init_db()
    with db_connect() as connection:
        row = connection.execute("SELECT value FROM app_config WHERE key = 'active_market_provider'").fetchone()
    provider = row["value"] if row else "coinbase"
    return provider if provider in MARKET_PROVIDERS else "coinbase"


def get_market_provider() -> MarketProvider:
    return MARKET_PROVIDERS[get_active_market_provider_name()]


def get_market_provider_status() -> MarketProviderStatus:
    active_name = get_active_market_provider_name()
    return MARKET_PROVIDERS[active_name].status(is_active=True)


def get_market_candles(
    symbol: str,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 120,
) -> list[Candle]:
    provider_name = get_active_market_provider_name()
    return MARKET_PROVIDERS[provider_name].candles(
        symbol=symbol,
        interval=interval,
        limit=max(40, min(240, limit)),
    )


def get_market_indicators(symbol: str) -> list[IndicatorSummary]:
    return get_market_provider().indicators(symbol)


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
        route_markets = ["GLOBAL"]
        if provider == "finnhub":
            route_markets.append("US")
        if provider in {"binance", "coinbase"}:
            route_markets.append("CRYPTO")
        for market in route_markets:
            for capability in ("quote", "snapshot"):
                route_id = f"{market}:{capability}"
                connection.execute("DELETE FROM data_source_route_members WHERE route_id = ?", (route_id,))
                source_ids = [f"market:{provider}"]
                for position, source_id in enumerate(source_ids):
                    connection.execute(
                        "INSERT INTO data_source_route_members (route_id, source_id, position, role, enabled) VALUES (?, ?, ?, ?, 1)",
                        (route_id, source_id, position, "primary" if position == 0 else "fallback"),
                    )
                connection.execute(
                    "UPDATE data_source_routes SET version = version + 1, updated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), route_id),
                )
    return MARKET_PROVIDERS[provider].status(is_active=True)


def test_managed_data_source(source_id: str) -> DataSourceProfile:
    from app.modules.integrations.service import get_profile, record_health

    init_db()
    profile = get_profile(source_id)
    if not profile.enabled:
        return record_health(source_id, False, 0, "数据源已停用")
    started = perf_counter()
    try:
        if profile.adapter in MARKET_PROVIDERS:
            provider = MARKET_PROVIDERS[profile.adapter]
            if profile.adapter in {"binance", "coinbase"}:
                provider.base_url = profile.endpoint.rstrip("/")
            elif profile.adapter == "finnhub":
                provider._base_url_override = profile.endpoint.rstrip("/")
                provider._api_key_override = resolve_api_key("data_source", profile.id, profile.api_key_env)
            status = provider.status(is_active=False)
            if status.status == "offline" or (profile.adapter == "finnhub" and not profile.credential_configured):
                raise RuntimeError(status.message)
            snapshots = provider.watchlist()
            if not snapshots:
                raise RuntimeError("行情源没有返回快照")
            message = f"连接成功，返回 {len(snapshots)} 个行情快照"
        elif profile.adapter == "public_index":
            endpoint = profile.endpoint.rstrip("/")
            request = UrlRequest(
                f"{endpoint}/%5EGSPC?range=5d&interval=1d",
                headers={"User-Agent": "Mozilla/5.0 AppTrading/1.0", "Accept": "application/json"},
            )
            payload = json.loads(urlopen(request, timeout=profile.timeout_seconds).read())
            result = payload.get("chart", {}).get("result") if isinstance(payload, dict) else None
            if not result:
                raise RuntimeError("公共指数源没有返回有效数据")
            message = "公共指数样例查询成功"
        elif profile.adapter in {"calendar_ics", "bea_calendar", "nbs_calendar", "ecb_calendar", "fomc_calendar"}:
            from app.modules.calendar import service as financial_calendar
            if profile.adapter == "calendar_ics":
                payload = financial_calendar._request_text(profile.endpoint, profile.timeout_seconds)
                if "BEGIN:VCALENDAR" not in payload:
                    raise RuntimeError("官方日历源未返回有效 iCalendar 内容")
                count = payload.count("BEGIN:VEVENT")
            elif profile.adapter == "bea_calendar":
                payload = financial_calendar._request_json(profile.endpoint, profile.timeout_seconds)
                if not isinstance(payload, dict) or not any(isinstance(value, dict) for value in payload.values()):
                    raise RuntimeError("官方日历源未返回有效 JSON 内容")
                count = sum(len(value.get("release_dates", [])) for value in payload.values() if isinstance(value, dict))
            else:
                payload = financial_calendar._request_text(profile.endpoint, profile.timeout_seconds)
                parser = financial_calendar._TableTextParser()
                parser.feed(payload)
                if not parser.rows and not parser.text:
                    raise RuntimeError("官方网页日历没有可解析内容")
                if profile.adapter == "nbs_calendar":
                    count = len(parser.rows)
                elif profile.adapter == "fomc_calendar":
                    count = sum("FOMC Meetings" in value for value in parser.text)
                else:
                    count = sum(bool(re.match(r"\d{2}/\d{2}/20\d{2}\s+\d{2}:\d{2}", value)) for value in parser.text)
            message = f"官方日历连接成功，可解析 {count} 条发布计划"
        elif profile.adapter in {"alphavantage", "fred", "adanos", "trading_economics"}:
            key = resolve_api_key("data_source", profile.id, profile.api_key_env)
            if not key:
                raise RuntimeError(f"缺少 {profile.api_key_env}")
            if profile.adapter == "alphavantage":
                url = f"{profile.endpoint}?{urlencode({'function': 'OVERVIEW', 'symbol': 'IBM', 'apikey': key})}"
                request = UrlRequest(url, headers={"User-Agent": "AppTrading/0.3 source-check"})
            elif profile.adapter == "fred":
                url = f"{profile.endpoint.rstrip('/')}/series/observations?{urlencode({'series_id': 'DFF', 'api_key': key, 'file_type': 'json', 'limit': 1})}"
                request = UrlRequest(url, headers={"User-Agent": "AppTrading/0.3 source-check"})
            elif profile.adapter == "adanos":
                url = f"{profile.endpoint.rstrip('/')}/reddit/stocks/v1/compare?tickers=AAPL&days=1"
                request = UrlRequest(url, headers={"User-Agent": "AppTrading/0.3 source-check", "X-API-Key": key})
            else:
                today = datetime.now(timezone.utc).date().isoformat()
                url = f"{profile.endpoint.rstrip('/')}/calendar/country/All/{today}/{today}?{urlencode({'c': key, 'f': 'json'})}"
                request = UrlRequest(url, headers={"User-Agent": "AppTrading/0.3 source-check"})
            payload = json.loads(urlopen(request, timeout=profile.timeout_seconds).read())
            if profile.adapter == "trading_economics" and not isinstance(payload, list):
                raise RuntimeError("财经日历源没有返回有效 JSON 数组")
            if profile.adapter != "trading_economics" and not isinstance(payload, dict):
                raise RuntimeError("指标源没有返回有效 JSON 对象")
            message = f"{profile.provider} 样例查询成功"
        elif profile.legacy_source_id is not None:
            with db_connect() as connection:
                row = connection.execute("SELECT * FROM news_sources WHERE id = ?", (profile.legacy_source_id,)).fetchone()
            if row is None:
                raise RuntimeError("关联的信息源不存在")
            source = row_to_source(row)
            if not information_source_is_ingestible(source):
                message = "参考型数据源配置有效，无需主动采集"
            else:
                items = fetch_information_source_items(source)
                message = f"连接成功，样例解析 {len(items)} 条记录"
        else:
            raise RuntimeError("当前适配器不支持连接检测")
        latency = (perf_counter() - started) * 1000
        return record_health(source_id, True, latency, message)
    except Exception as exc:  # noqa: BLE001 - normalize adapter failures into health state
        latency = (perf_counter() - started) * 1000
        return record_health(source_id, False, latency, str(exc)[:500])


def run_due_data_source_health_checks() -> list[DataSourceProfile]:
    from app.modules.integrations.service import list_profiles

    now = datetime.now(timezone.utc)
    results: list[DataSourceProfile] = []
    for profile in list_profiles():
        if not profile.enabled or profile.domain == "content":
            continue
        if profile.last_checked_at:
            checked = datetime.fromisoformat(profile.last_checked_at.replace("Z", "+00:00"))
            if (now - checked).total_seconds() < 60:
                continue
        results.append(test_managed_data_source(profile.id))
    return results


def normalize_watchlist_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        value = tag.strip()
        if value and value.lower() not in {item.lower() for item in normalized}:
            normalized.append(value[:24])
    return normalized[:12]


def ensure_default_watchlist_group(provider_name: str) -> WatchlistGroup:
    init_db()
    group_id = f"default-{provider_name}"
    group_name = "市场指数" if provider_name == "public_index" else "默认分组"
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO watchlist_groups (
                id, provider, name, color, is_default, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, 'blue', 1, 0, ?, ?)
            """,
            (group_id, provider_name, group_name, now, now),
        )
        row = connection.execute(
            """
            SELECT g.*, COUNT(w.symbol) AS item_count
            FROM watchlist_groups g
            LEFT JOIN watchlist_items w ON w.provider = g.provider AND w.group_id = g.id
            WHERE g.id = ?
            GROUP BY g.id
            """,
            (group_id,),
        ).fetchone()
    return row_to_watchlist_group(row)


def row_to_watchlist_group(row: sqlite3.Row) -> WatchlistGroup:
    return WatchlistGroup(
        id=row["id"],
        provider=row["provider"],
        name=row["name"],
        color=row["color"],
        is_default=bool(row["is_default"]),
        sort_order=int(row["sort_order"]),
        item_count=int(row["item_count"]) if "item_count" in row.keys() else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_watchlist_groups() -> list[WatchlistGroup]:
    provider_name = get_active_market_provider_name()
    ensure_default_watchlist_group(provider_name)
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT g.*, COUNT(w.symbol) AS item_count
            FROM watchlist_groups g
            LEFT JOIN watchlist_items w ON w.provider = g.provider AND w.group_id = g.id
            GROUP BY g.id
            ORDER BY g.is_default DESC, g.provider, g.sort_order, g.name COLLATE NOCASE
            """,
        ).fetchall()
    return [row_to_watchlist_group(row) for row in rows]


def create_watchlist_group(payload: WatchlistGroupCreate) -> WatchlistGroup:
    provider_name = get_active_market_provider_name()
    ensure_default_watchlist_group(provider_name)
    name = payload.name.strip()
    now = datetime.now(timezone.utc).isoformat()
    group_id = sha256(f"watchlist-group:{provider_name}:{name}:{now}".encode("utf-8")).hexdigest()[:20]
    try:
        with db_connect() as connection:
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS value FROM watchlist_groups WHERE provider = ?",
                (provider_name,),
            ).fetchone()["value"]
            connection.execute(
                """
                INSERT INTO watchlist_groups (
                    id, provider, name, color, is_default, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (group_id, provider_name, name, payload.color, next_order, now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Watchlist group name already exists") from exc
    return next(item for item in list_watchlist_groups() if item.id == group_id)


def update_watchlist_group(group_id: str, payload: WatchlistGroupUpdate) -> WatchlistGroup:
    groups = {item.id: item for item in list_watchlist_groups()}
    current = groups.get(group_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Watchlist group not found")
    name = payload.name.strip() if payload.name is not None else current.name
    color = payload.color or current.color
    sort_order = payload.sort_order if payload.sort_order is not None else current.sort_order
    now = datetime.now(timezone.utc).isoformat()
    try:
        with db_connect() as connection:
            connection.execute(
                "UPDATE watchlist_groups SET name = ?, color = ?, sort_order = ?, updated_at = ? WHERE id = ?",
                (name, color, sort_order, now, group_id),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Watchlist group name already exists") from exc
    return next(item for item in list_watchlist_groups() if item.id == group_id)


def delete_watchlist_group(group_id: str) -> dict[str, int | bool]:
    groups = {item.id: item for item in list_watchlist_groups()}
    current = groups.get(group_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Watchlist group not found")
    if current.is_default:
        raise HTTPException(status_code=409, detail="Default watchlist group cannot be deleted")
    provider_name = current.provider
    default_group = ensure_default_watchlist_group(provider_name)
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE watchlist_items SET group_id = ?, updated_at = ? WHERE provider = ? AND group_id = ?",
            (default_group.id, now, provider_name, group_id),
        )
        connection.execute("DELETE FROM watchlist_groups WHERE id = ? AND provider = ?", (group_id, provider_name))
    return {"deleted": True, "moved_items": cursor.rowcount}


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
    default_group = ensure_default_watchlist_group(provider_name)
    symbols = persisted_watchlist_symbols(provider_name)
    with db_connect() as connection:
        initialized = connection.execute("SELECT 1 FROM watchlist_items WHERE provider = ? LIMIT 1", (provider_name,)).fetchone()
    if initialized:
        return symbols

    if provider_name == "public_index":
        return symbols

    provider = MARKET_PROVIDERS[provider_name]
    created_at = datetime.now(timezone.utc).isoformat()
    default_symbols = [item.symbol for item in provider.list_symbols()]
    with db_connect() as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO watchlist_items (
                provider, symbol, group_id, tags, note, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, '[]', '', ?, ?, ?)
            """,
            [
                (provider_name, symbol, default_group.id, index, created_at, created_at)
                for index, symbol in enumerate(default_symbols)
            ],
        )
    return default_symbols


def list_watchlist_snapshots() -> list[SymbolSnapshot]:
    ensure_watchlist_initialized(get_active_market_provider_name())
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT provider, symbol FROM watchlist_items WHERE enabled = 1 ORDER BY created_at, symbol"
        ).fetchall()
    requested: dict[str, list[str]] = {}
    order: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows):
        requested.setdefault(row["provider"], []).append(row["symbol"])
        order[(row["provider"], row["symbol"])] = index
    results: list[tuple[str, SymbolSnapshot]] = []
    for provider_name, symbols in requested.items():
        provider = watchlist_provider(provider_name)
        if provider is None:
            continue
        try:
            snapshots = provider.watchlist()
        except HTTPException:
            continue
        expected = set(symbols)
        results.extend((provider_name, item) for item in snapshots if item.symbol in expected)
    results.sort(key=lambda pair: order.get((pair[0], pair[1].symbol), 10**9))
    return [item for _provider_name, item in results]


def list_managed_market_snapshots() -> tuple[list[SymbolSnapshot], list[dict[str, object]]]:
    from app.modules.integrations.service import record_health, resolve_candidates

    merged: dict[str, SymbolSnapshot] = {}
    provenance: list[dict[str, object]] = []
    for market in ("US", "CRYPTO"):
        candidates = resolve_candidates(market, "snapshot")
        failures: list[str] = []
        for position, profile in enumerate(candidates):
            provider = MARKET_PROVIDERS.get(profile.adapter)
            if provider is None:
                failures.append(f"{profile.name}: 不支持快照能力")
                continue
            started = perf_counter()
            try:
                if profile.adapter in {"binance", "coinbase"}:
                    provider.base_url = profile.endpoint.rstrip("/")
                elif profile.adapter == "finnhub":
                    provider._base_url_override = profile.endpoint.rstrip("/")
                    provider._api_key_override = resolve_api_key("data_source", profile.id, profile.api_key_env)
                rows = provider.watchlist()
                if market == "CRYPTO":
                    rows = [item for item in rows if item.symbol in {"BTCUSD", "ETHUSD"} or "crypto" in item.market.lower()]
                else:
                    rows = [item for item in rows if item.symbol not in {"BTCUSD", "ETHUSD"} and "crypto" not in item.market.lower()]
                if not rows:
                    raise RuntimeError("该市场没有可用行情快照")
                latency = (perf_counter() - started) * 1000
                checked_at = datetime.fromisoformat(profile.last_checked_at.replace("Z", "+00:00")) if profile.last_checked_at else None
                if profile.health_status != "healthy" or checked_at is None or (datetime.now(timezone.utc) - checked_at).total_seconds() >= 60:
                    profile = record_health(profile.id, True, latency, f"运行时快照查询成功，共 {len(rows)} 条")
                for item in rows:
                    merged.setdefault(item.symbol, item)
                provenance.append({
                    "source_id": profile.id,
                    "source_name": profile.name,
                    "adapter": profile.adapter,
                    "market": market,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "health_status": profile.health_status,
                    "stale": False,
                    "fallback_reason": "；".join(failures) if position > 0 and failures else None,
                })
                break
            except Exception as exc:  # noqa: BLE001 - continue through the configured fallback chain
                latency = (perf_counter() - started) * 1000
                record_health(profile.id, False, latency, str(exc)[:500])
                failures.append(f"{profile.name}: {str(exc)[:120]}")
        else:
            provenance.append({
                "source_id": "none", "source_name": f"{market} 行情不可用", "adapter": "none",
                "market": market, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "health_status": "offline", "stale": False, "fallback_reason": "；".join(failures),
            })
    return list(merged.values()), provenance


def _resolve_watchlist_provider(symbol: str) -> tuple[str, MarketProvider]:
    active_name = get_active_market_provider_name()
    ordered_names = [active_name, *[name for name in MARKET_PROVIDERS if name != active_name], "public_index"]
    for provider_name in ordered_names:
        provider = watchlist_provider(provider_name)
        if provider is None:
            continue
        if symbol in {item.symbol for item in provider.list_symbols()}:
            return provider_name, provider
    finnhub = MARKET_PROVIDERS.get("finnhub")
    if isinstance(finnhub, FinnhubMarketProvider):
        from app.modules.market.charting import list_instruments

        matches = list_instruments(symbol, asset_class=None)
        instrument = next(
            (item for item in matches if item.symbol.upper() == symbol and item.asset_class in {"stock", "etf"}),
            None,
        )
        if instrument:
            finnhub.register_symbol(symbol, instrument.name, instrument.market, instrument.asset_class)
            return "finnhub", finnhub
    raise HTTPException(status_code=400, detail="Symbol is not supported by configured market providers")


def add_watchlist_symbol(payload: WatchlistItemCreate) -> SymbolSnapshot:
    symbol = payload.symbol.strip().upper()
    provider_name, provider = _resolve_watchlist_provider(symbol)
    from app.modules.market.charting import list_instruments

    instrument = next(
        (item for item in list_instruments(symbol) if item.symbol.upper() == symbol),
        None,
    )
    instrument_id = instrument.id if instrument else f"{provider_name.upper()}:{symbol}"
    index_snapshot = provider._snapshot(symbol) if provider_name == "public_index" else None

    ensure_watchlist_initialized(provider_name)
    default_group = ensure_default_watchlist_group(provider_name)
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO watchlist_items (
                provider, symbol, instrument_id, group_id, tags, note, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '[]', '', 0, ?, ?)
            """,
            (provider_name, symbol, instrument_id, default_group.id, now, now),
        )
        connection.execute(
            "UPDATE watchlist_items SET instrument_id = ?, enabled = 1, updated_at = ? WHERE provider = ? AND symbol = ?",
            (instrument_id, now, provider_name, symbol),
        )

    if provider_name == "finnhub":
        provider._snapshot_cached_at = None
    snapshot = index_snapshot or next((item for item in provider.watchlist() if item.symbol == symbol), None)
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
    normalized = symbol.strip().upper()
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE watchlist_items SET enabled = 0, updated_at = ? WHERE symbol = ?",
            (datetime.now(timezone.utc).isoformat(), normalized),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    add_event_log(
        event_type="system",
        severity="info",
        symbol=normalized,
        title="自选标的已停用",
        message=f"{normalized} 已从自选观察停用，可在管理中心恢复。",
    )
    return {"deleted": False, "disabled": cursor.rowcount > 0}


def list_watchlist_resources(
    status: Literal["active", "disabled", "all"] = "active",
    group_id: str | None = None,
    market: str | None = None,
    signal: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    sort_by: Literal["symbol", "price", "change", "rsi", "macd", "signal", "updated_at"] = "updated_at",
    direction: Literal["asc", "desc"] = "desc",
    page: int = 1,
    page_size: int = 25,
) -> WatchlistResourcePage:
    ensure_watchlist_initialized(get_active_market_provider_name())
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT w.*, g.name AS group_name, g.color AS group_color
            FROM watchlist_items w
            JOIN watchlist_groups g ON g.id = w.group_id
            """,
        ).fetchall()

    symbol_catalog: dict[tuple[str, str], MarketSymbol] = {}
    snapshots: dict[tuple[str, str], SymbolSnapshot] = {}
    requested_providers = {row["provider"] for row in rows}
    source_providers = [*MARKET_PROVIDERS.items(), ("public_index", PUBLIC_INDEX_PROVIDER)]
    for source_name, source_provider in source_providers:
        symbol_catalog.update({(source_name, item.symbol): item for item in source_provider.list_symbols()})
        if source_name not in requested_providers:
            continue
        try:
            snapshots.update({(source_name, item.symbol): item for item in source_provider.watchlist()})
        except HTTPException:
            continue
    items: list[WatchlistResourceItem] = []
    for row in rows:
        enabled = bool(row["enabled"])
        if status == "active" and not enabled or status == "disabled" and enabled:
            continue
        provider_name = row["provider"]
        catalog_item = symbol_catalog.get((provider_name, row["symbol"]))
        snapshot = snapshots.get((provider_name, row["symbol"]))
        name = snapshot.name if snapshot else catalog_item.name if catalog_item else row["symbol"]
        item_market = snapshot.market if snapshot else catalog_item.market if catalog_item else "--"
        tags = normalize_watchlist_tags(json.loads(row["tags"] or "[]"))
        item = WatchlistResourceItem(
            provider=provider_name,
            symbol=row["symbol"],
            name=name,
            market=item_market,
            enabled=enabled,
            group_id=row["group_id"],
            group_name=row["group_name"],
            group_color=row["group_color"],
            tags=tags,
            note=row["note"] or "",
            sort_order=int(row["sort_order"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"] or row["created_at"],
            snapshot=snapshot,
        )
        search_text = f"{item.symbol} {item.name} {item.group_name} {' '.join(item.tags)} {item.note}".lower()
        if group_id and item.group_id != group_id:
            continue
        if market and item.market.lower() != market.lower():
            continue
        if signal and (item.snapshot is None or item.snapshot.signal != signal):
            continue
        if tag and tag.lower() not in {value.lower() for value in item.tags}:
            continue
        if query and query.strip().lower() not in search_text:
            continue
        items.append(item)

    def sort_value(item: WatchlistResourceItem) -> object:
        snapshot = item.snapshot
        values: dict[str, object] = {
            "symbol": item.symbol,
            "price": snapshot.price if snapshot else float("-inf"),
            "change": snapshot.change_percent if snapshot else float("-inf"),
            "rsi": snapshot.rsi if snapshot else float("-inf"),
            "macd": snapshot.macd if snapshot else float("-inf"),
            "signal": snapshot.signal if snapshot else "",
            "updated_at": item.updated_at,
        }
        return values[sort_by]

    items.sort(key=sort_value, reverse=direction == "desc")
    total = len(items)
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    start = (page - 1) * page_size
    return WatchlistResourcePage(page=page, page_size=page_size, total=total, items=items[start:start + page_size])


def create_watchlist_resource(payload: WatchlistResourceCreate) -> WatchlistResourceItem:
    snapshot = add_watchlist_symbol(WatchlistItemCreate(symbol=payload.symbol))
    current = next(
        item for item in list_watchlist_resources(status="all", query=snapshot.symbol, page_size=100).items
        if item.symbol == snapshot.symbol
    )
    allowed_group = next(
        (group.id for group in list_watchlist_groups() if group.id == payload.group_id and group.provider == current.provider),
        None,
    )
    update_watchlist_resource(
        snapshot.symbol,
        WatchlistResourceUpdate(group_id=allowed_group, tags=payload.tags, note=payload.note, enabled=True),
    )
    page = list_watchlist_resources(status="all", query=snapshot.symbol, page_size=100)
    return next(item for item in page.items if item.symbol == snapshot.symbol)


def update_watchlist_resource(symbol: str, payload: WatchlistResourceUpdate) -> WatchlistResourceItem:
    normalized = symbol.strip().upper()
    ensure_watchlist_initialized(get_active_market_provider_name())
    with db_connect() as connection:
        row = connection.execute(
            "SELECT * FROM watchlist_items WHERE symbol = ? ORDER BY enabled DESC, created_at LIMIT 1",
            (normalized,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        provider_name = row["provider"]
        group = payload.group_id if payload.group_id is not None else row["group_id"]
        if connection.execute(
            "SELECT 1 FROM watchlist_groups WHERE id = ? AND provider = ?", (group, provider_name)
        ).fetchone() is None:
            raise HTTPException(status_code=400, detail="Watchlist group is invalid")
        tags = normalize_watchlist_tags(payload.tags) if payload.tags is not None else json.loads(row["tags"] or "[]")
        note = payload.note.strip() if payload.note is not None else row["note"]
        enabled = int(payload.enabled) if payload.enabled is not None else int(row["enabled"])
        connection.execute(
            """
            UPDATE watchlist_items
            SET group_id = ?, tags = ?, note = ?, enabled = ?, updated_at = ?
            WHERE provider = ? AND symbol = ?
            """,
            (group, json.dumps(tags, ensure_ascii=False), note, enabled, datetime.now(timezone.utc).isoformat(), provider_name, normalized),
        )
    page = list_watchlist_resources(status="all", query=normalized, page_size=100)
    return next(item for item in page.items if item.symbol == normalized)


def bulk_update_watchlist(payload: WatchlistBulkAction) -> WatchlistBulkResult:
    provider_name = get_active_market_provider_name()
    symbols = normalize_symbols(payload.symbols)
    ensure_watchlist_initialized(provider_name)
    if payload.action == "move":
        if not payload.group_id:
            raise HTTPException(status_code=422, detail="group_id is required for move")
        with db_connect() as connection:
            if connection.execute(
                "SELECT 1 FROM watchlist_groups WHERE id = ? AND provider = ?", (payload.group_id, provider_name)
            ).fetchone() is None:
                raise HTTPException(status_code=400, detail="Watchlist group is invalid")
    updated = 0
    for symbol in symbols:
        page = list_watchlist_resources(status="all", query=symbol, page_size=100)
        current = next((item for item in page.items if item.symbol == symbol), None)
        if current is None:
            continue
        if payload.action in {"disable", "restore"}:
            update = WatchlistResourceUpdate(enabled=payload.action == "restore")
        elif payload.action == "move":
            update = WatchlistResourceUpdate(group_id=payload.group_id)
        else:
            requested = normalize_watchlist_tags(payload.tags)
            if payload.action == "add_tags":
                tags = normalize_watchlist_tags(current.tags + requested)
            else:
                removed = {tag.lower() for tag in requested}
                tags = [tag for tag in current.tags if tag.lower() not in removed]
            update = WatchlistResourceUpdate(tags=tags)
        update_watchlist_resource(symbol, update)
        updated += 1
    return WatchlistBulkResult(updated=updated, action=payload.action)


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


def get_market_breadth(
    snapshots: list[SymbolSnapshot] | None = None,
    provenance: list[dict[str, object]] | None = None,
) -> MarketBreadth:
    snapshots = list_watchlist_snapshots() if snapshots is None else snapshots
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
        provenance=provenance or [],
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
    snapshots: list[SymbolSnapshot] | None = None,
    provenance: list[dict[str, object]] | None = None,
) -> ScreenerResult:
    valid_signals = {"strong_buy", "buy", "neutral", "sell", "strong_sell"}
    selected_signal = signal if signal in valid_signals else None
    selected_market = market.strip().lower() if market else None
    matches: list[ScreenerMatch] = []
    for snapshot in list_watchlist_snapshots() if snapshots is None else snapshots:
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
        provenance=provenance or [],
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
