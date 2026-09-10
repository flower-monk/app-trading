from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings
from app.db.session import connect
from app.schemas import IndicatorSnapshot, IndicatorSnapshotBundle, MarketInstrument
from app.modules.market.charting import resolve_instrument
from app.modules.integrations.service import record_health, resolve_candidates


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_key(instrument_id: str) -> str:
    return sha256(f"resource-snapshot:{instrument_id}".encode()).hexdigest()


def _read_cache(instrument_id: str, *, stale: bool = False) -> IndicatorSnapshotBundle | None:
    try:
        with connect(settings.database_path) as connection:
            row = connection.execute(
                "SELECT payload, expires_at, stale_until FROM indicator_data_cache WHERE cache_key = ?",
                (_cache_key(instrument_id),),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or datetime.fromisoformat(row["stale_until" if stale else "expires_at"]) < _now():
        return None
    bundle = IndicatorSnapshotBundle.model_validate_json(row["payload"])
    if stale:
        bundle.items = [item.model_copy(update={"stale": True}) for item in bundle.items]
        bundle.warnings.append("外部指标源暂不可用，当前显示最近一次缓存")
    return bundle


def _write_cache(bundle: IndicatorSnapshotBundle) -> None:
    now = _now()
    with connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO indicator_data_cache (
                cache_key, instrument_id, interval, metric, payload, source,
                fetched_at, expires_at, stale_until
            ) VALUES (?, ?, 'snapshot', 'resource_bundle', ?, 'managed-routes', ?, ?, ?)
            """,
            (
                _cache_key(bundle.instrument_id), bundle.instrument_id, bundle.model_dump_json(),
                now.isoformat(), (now + timedelta(minutes=10)).isoformat(),
                (now + timedelta(days=1)).isoformat(),
            ),
        )


def _json_request(url: str, *, headers: dict[str, str] | None = None, timeout: int = 10) -> object:
    request = Request(url, headers={"User-Agent": "AppTrading/0.3 indicator-service", **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _snapshot(
    identifier: str, label: str, category: str, value: object, source_id: str,
    source_name: str, *, unit: str = "", as_of: str | None = None,
) -> IndicatorSnapshot | None:
    normalized = _number(value)
    if normalized is None:
        return None
    return IndicatorSnapshot(
        id=identifier, label=label, category=category, value=round(normalized, 4), unit=unit,
        as_of=as_of or _now().isoformat(), source_id=source_id, source_name=source_name,
    )


def _finnhub_resources(instrument: MarketInstrument, profile) -> list[IndicatorSnapshot]:
    from app.modules.platform.service import resolve_api_key

    key = resolve_api_key("data_source", profile.id, profile.api_key_env)
    if not key:
        raise RuntimeError(f"{profile.name} 缺少 API Key")
    base = profile.endpoint.rstrip("/")
    common = {"symbol": instrument.provider_symbol, "token": key}
    metric_payload = _json_request(f"{base}/stock/metric?{urlencode({**common, 'metric': 'all'})}", timeout=profile.timeout_seconds)
    metric = metric_payload.get("metric", {}) if isinstance(metric_payload, dict) else {}
    fields = (
        ("market_cap", "市值", "fundamental", "marketCapitalization", "M USD"),
        ("revenue_growth", "营收增长", "fundamental", "revenueGrowthTTMYoy", "%"),
        ("eps_growth", "EPS 增长", "fundamental", "epsGrowthTTMYoy", "%"),
        ("pe_ttm", "市盈率 TTM", "valuation", "peTTM", "x"),
        ("pb", "市净率", "valuation", "pbAnnual", "x"),
        ("roe", "净资产收益率", "fundamental", "roeTTM", "%"),
        ("dividend_yield", "股息率", "valuation", "dividendYieldIndicatedAnnual", "%"),
        ("beta", "Beta", "valuation", "beta", ""),
    )
    items = [
        _snapshot(identifier, label, category, metric.get(key_name), profile.id, profile.name, unit=unit)
        for identifier, label, category, key_name, unit in fields
    ]
    earnings_payload = _json_request(
        f"{base}/stock/earnings?{urlencode({**common, 'limit': 4})}", timeout=profile.timeout_seconds
    )
    earnings = earnings_payload if isinstance(earnings_payload, list) else []
    if earnings and isinstance(earnings[0], dict):
        latest = earnings[0]
        items.extend([
            _snapshot("eps_actual", "最近 EPS", "fundamental", latest.get("actual"), profile.id, profile.name, as_of=str(latest.get("period") or _now().date())),
            _snapshot("eps_surprise", "EPS 超预期", "fundamental", latest.get("surprisePercent"), profile.id, profile.name, unit="%", as_of=str(latest.get("period") or _now().date())),
        ])
    return [item for item in items if item is not None]


def _alphavantage_resources(instrument: MarketInstrument, profile) -> list[IndicatorSnapshot]:
    from app.modules.platform.service import resolve_api_key

    key = resolve_api_key("data_source", profile.id, profile.api_key_env)
    if not key:
        raise RuntimeError(f"{profile.name} 缺少 API Key")
    payload = _json_request(f"{profile.endpoint}?{urlencode({'function': 'OVERVIEW', 'symbol': instrument.provider_symbol, 'apikey': key})}", timeout=profile.timeout_seconds)
    if not isinstance(payload, dict) or payload.get("Note") or payload.get("Information"):
        raise RuntimeError("Alpha Vantage 未返回公司概览")
    fields = (
        ("market_cap", "市值", "fundamental", "MarketCapitalization", "USD"),
        ("revenue_growth", "营收增长", "fundamental", "QuarterlyRevenueGrowthYOY", "%"),
        ("eps_growth", "EPS 增长", "fundamental", "QuarterlyEarningsGrowthYOY", "%"),
        ("pe_ttm", "市盈率 TTM", "valuation", "PERatio", "x"),
        ("pb", "市净率", "valuation", "PriceToBookRatio", "x"),
        ("roe", "净资产收益率", "fundamental", "ReturnOnEquityTTM", "%"),
        ("dividend_yield", "股息率", "valuation", "DividendYield", "%"),
        ("beta", "Beta", "valuation", "Beta", ""),
    )
    percent_keys = {"revenue_growth", "eps_growth", "roe", "dividend_yield"}
    items: list[IndicatorSnapshot] = []
    for identifier, label, category, key_name, unit in fields:
        value = _number(payload.get(key_name))
        if value is not None and identifier in percent_keys:
            value *= 100
        item = _snapshot(identifier, label, category, value, profile.id, profile.name, unit=unit)
        if item:
            items.append(item)
    return items


def _fundamental_resources(instrument: MarketInstrument, warnings: list[str]) -> list[IndicatorSnapshot]:
    if instrument.asset_class not in {"stock", "etf"}:
        return []
    for profile in resolve_candidates("US", "fundamentals"):
        started = perf_counter()
        try:
            if profile.adapter == "finnhub":
                items = _finnhub_resources(instrument, profile)
            elif profile.adapter == "alphavantage":
                items = _alphavantage_resources(instrument, profile)
            else:
                continue
            if not items:
                raise RuntimeError("没有可展示的指标")
            record_health(profile.id, True, (perf_counter() - started) * 1000, "指标快照查询成功")
            return items
        except RuntimeError as exc:
            record_health(profile.id, False, (perf_counter() - started) * 1000, str(exc))
            warnings.append(f"{profile.name}: {str(exc)[:100]}")
    return []


def _sentiment_resources(instrument: MarketInstrument, warnings: list[str]) -> list[IndicatorSnapshot]:
    from app.modules.news.service import ADANOS_SOURCE_CONFIG
    from app.modules.platform.service import resolve_api_key

    market = "CRYPTO" if instrument.asset_class == "crypto_perpetual" else "US"
    profiles = [item for item in resolve_candidates(market, "sentiment_snapshot") if item.adapter == "adanos"]
    if not profiles:
        return []
    profile = profiles[0]
    key = resolve_api_key("data_source", profile.id, profile.api_key_env)
    if not key:
        warnings.append(f"{profile.name}: 缺少 API Key")
        return []
    symbol = instrument.symbol.removesuffix("USDT") if instrument.asset_class == "crypto_perpetual" else instrument.symbol
    items: list[IndicatorSnapshot] = []
    for source_key, (path, label, metric_field, metric_label) in ADANOS_SOURCE_CONFIG.items():
        try:
            payload = _json_request(
                f"{profile.endpoint.rstrip('/')}{path}?{urlencode({'tickers': symbol, 'days': 7})}",
                headers={"X-API-Key": key}, timeout=profile.timeout_seconds,
            )
        except RuntimeError:
            continue
        rows = payload.get("stocks", []) if isinstance(payload, dict) else []
        row = next((entry for entry in rows if isinstance(entry, dict)), None)
        if not row:
            continue
        for suffix, item_label, value, unit in (
            ("bullish", f"{label} 看多", row.get("bullish_pct"), "%"),
            ("activity", f"{label} {metric_label}", row.get(metric_field), ""),
        ):
            item = _snapshot(f"{source_key}_{suffix}", item_label, "sentiment", value, profile.id, profile.name, unit=unit)
            if item:
                items.append(item)
    return items


def indicator_snapshots(instrument_id: str) -> IndicatorSnapshotBundle:
    instrument = resolve_instrument(instrument_id)
    cached = _read_cache(instrument.id)
    if cached:
        return cached
    warnings: list[str] = []
    try:
        items = _fundamental_resources(instrument, warnings)
        items.extend(_sentiment_resources(instrument, warnings))
        bundle = IndicatorSnapshotBundle(
            instrument_id=instrument.id, generated_at=_now().isoformat(), items=items, warnings=warnings,
        )
        _write_cache(bundle)
        return bundle
    except (RuntimeError, sqlite3.OperationalError):
        stale = _read_cache(instrument.id, stale=True)
        if stale:
            return stale
        raise
