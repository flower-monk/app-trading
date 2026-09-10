from __future__ import annotations

import ipaddress
import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import settings
from app.db.session import connect
from app.schemas import (
    DataSourceControlSummary,
    DataSourceHealthCheck,
    DataSourceProfile,
    DataSourceProfileCreate,
    DataSourceProfileUpdate,
    DataSourceRevision,
    DataSourceRoute,
    DataSourceRouteMember,
)


REGISTERED_ADAPTERS = {
    "finnhub", "binance", "coinbase", "public_index", "rss", "adanos",
    "gdelt", "cryptocompare", "alphavantage", "fred", "trading_economics",
    "calendar_ics", "bea_calendar", "nbs_calendar", "ecb_calendar", "fomc_calendar", "widget", "reference",
}
DATA_SOURCE_DB_PATH = settings.database_path

MARKET_SOURCE_SEEDS = [
    ("market:finnhub", "Finnhub 行情", "market", "market", "finnhub", "Finnhub", "https://finnhub.io/api/v1", ["US", "GLOBAL"], ["stock", "etf"], ["quote", "snapshot", "candles", "symbol_search", "fundamentals", "earnings_estimates", "earnings_calendar"]),
    ("market:binance", "Binance 加密行情", "market", "market", "binance", "Binance", "https://api.binance.com", ["CRYPTO"], ["crypto", "crypto_perpetual"], ["quote", "snapshot", "candles", "derivatives"]),
    ("market:coinbase", "Coinbase 加密行情", "market", "market", "coinbase", "Coinbase", "https://api.exchange.coinbase.com", ["CRYPTO"], ["crypto"], ["quote", "snapshot", "candles"]),
    ("index:public", "公共指数行情", "index", "market", "public_index", "Yahoo Finance", "https://query1.finance.yahoo.com/v8/finance/chart", ["US", "CN", "HK", "EU", "JP"], ["index"], ["index_candles", "candles"]),
    ("indicator:alphavantage", "Alpha Vantage 指标", "indicator", "indicator", "alphavantage", "Alpha Vantage", "https://www.alphavantage.co/query", ["US"], ["stock", "etf"], ["fundamentals", "earnings_estimates"]),
    ("indicator:fred", "FRED 宏观序列", "indicator", "indicator", "fred", "FRED", "https://api.stlouisfed.org/fred", ["GLOBAL", "US"], ["stock", "etf", "index", "crypto_perpetual"], ["macro_series"]),
    ("calendar:trading-economics", "Trading Economics 财经日历", "indicator", "calendar", "trading_economics", "Trading Economics", "https://api.tradingeconomics.com", ["GLOBAL"], ["index", "stock", "etf"], ["economic_calendar"]),
    ("calendar:bls", "BLS 官方发布日历", "indicator", "calendar", "calendar_ics", "U.S. Bureau of Labor Statistics", "https://www.bls.gov/schedule/news_release/bls.ics", ["US"], ["index", "stock", "etf"], ["economic_calendar"]),
    ("calendar:bea", "BEA 官方发布日历", "indicator", "calendar", "bea_calendar", "U.S. Bureau of Economic Analysis", "https://apps.bea.gov/API/signup/release_dates.json", ["US"], ["index", "stock", "etf"], ["economic_calendar"]),
    ("calendar:nbs", "国家统计局发布日历", "indicator", "calendar", "nbs_calendar", "国家统计局", "https://www.stats.gov.cn/sj/fbrc/bnxxfb/", ["CN"], ["index", "stock", "etf"], ["economic_calendar"]),
    ("calendar:ecb", "ECB 官方统计日历", "indicator", "calendar", "ecb_calendar", "European Central Bank", "https://www.ecb.europa.eu/press/calendars/statscal/html/index.en.html", ["EU"], ["index", "stock", "etf"], ["economic_calendar"]),
    ("calendar:fomc", "美联储 FOMC 日历", "indicator", "calendar", "fomc_calendar", "Federal Reserve", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", ["US"], ["index", "stock", "etf"], ["economic_calendar"]),
    ("indicator:adanos", "Adanos 市场情绪", "indicator", "indicator", "adanos", "Adanos", "https://api.adanos.org", ["US", "CRYPTO"], ["stock", "etf", "crypto_perpetual"], ["sentiment_snapshot"]),
]


def _connect():
    return connect(DATA_SOURCE_DB_PATH)


def set_database_path(path: str) -> None:
    global DATA_SOURCE_DB_PATH
    DATA_SOURCE_DB_PATH = path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_data_source_schema(connection, active_provider: str = "coinbase") -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS data_source_profiles (
            id TEXT PRIMARY KEY,
            legacy_source_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            category TEXT NOT NULL,
            adapter TEXT NOT NULL,
            provider TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            markets TEXT NOT NULL DEFAULT '[]',
            asset_classes TEXT NOT NULL DEFAULT '[]',
            capabilities TEXT NOT NULL DEFAULT '[]',
            auth_type TEXT NOT NULL DEFAULT 'none',
            api_key_env TEXT NOT NULL DEFAULT '',
            priority INTEGER NOT NULL DEFAULT 50,
            reliability_weight REAL NOT NULL DEFAULT 0.8,
            timeout_seconds INTEGER NOT NULL DEFAULT 8,
            cache_ttl_seconds INTEGER NOT NULL DEFAULT 60,
            stale_ttl_seconds INTEGER NOT NULL DEFAULT 900,
            sync_interval_seconds INTEGER NOT NULL DEFAULT 900,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            config TEXT NOT NULL DEFAULT '{}',
            health_status TEXT NOT NULL DEFAULT 'untested',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            consecutive_successes INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            last_latency_ms REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_data_source_profile_domain ON data_source_profiles(domain, enabled);
        CREATE TABLE IF NOT EXISTS data_source_routes (
            id TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            capability TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'fallback',
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            UNIQUE(market, capability)
        );
        CREATE TABLE IF NOT EXISTS data_source_route_members (
            route_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            role TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(route_id, source_id),
            FOREIGN KEY(route_id) REFERENCES data_source_routes(id),
            FOREIGN KEY(source_id) REFERENCES data_source_profiles(id)
        );
        CREATE TABLE IF NOT EXISTS data_source_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            message TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES data_source_profiles(id)
        );
        CREATE INDEX IF NOT EXISTS idx_data_source_health_time ON data_source_health_checks(source_id, checked_at DESC);
        CREATE TABLE IF NOT EXISTS data_source_revisions (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            config TEXT NOT NULL,
            validation TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(route_id) REFERENCES data_source_routes(id)
        );
        """
    )
    now = _now()
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(data_source_profiles)").fetchall()}
    if "sync_interval_seconds" not in columns:
        connection.execute("ALTER TABLE data_source_profiles ADD COLUMN sync_interval_seconds INTEGER NOT NULL DEFAULT 900")
    if "timezone" not in columns:
        connection.execute("ALTER TABLE data_source_profiles ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")
    connection.execute("DELETE FROM data_source_route_members WHERE source_id = 'market:mock'")
    connection.execute("DELETE FROM data_source_health_checks WHERE source_id = 'market:mock'")
    connection.execute("DELETE FROM data_source_profiles WHERE id = 'market:mock'")
    for source_id, name, domain, category, adapter, provider, endpoint, markets, assets, capabilities in MARKET_SOURCE_SEEDS:
        source_existed = connection.execute("SELECT 1 FROM data_source_profiles WHERE id=?", (source_id,)).fetchone() is not None
        api_key_env = {
            "finnhub": "FINNHUB_API_KEY", "alphavantage": "ALPHAVANTAGE_API_KEY",
            "fred": "FRED_API_KEY", "adanos": "ADANOS_API_KEY",
            "trading_economics": "TRADING_ECONOMICS_API_KEY",
        }.get(adapter, "")
        connection.execute(
            """
            INSERT OR IGNORE INTO data_source_profiles (
                id, name, domain, category, adapter, provider, endpoint, enabled, markets,
                asset_classes, capabilities, auth_type, api_key_env, priority,
                reliability_weight, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 80, 0.8, ?, ?)
            """,
            (source_id, name, domain, category, adapter, provider, endpoint, _json(markets),
             _json(assets), _json(capabilities), "query" if api_key_env else "none", api_key_env, now, now),
        )
        if source_id == "market:finnhub":
            row = connection.execute("SELECT capabilities FROM data_source_profiles WHERE id=?", (source_id,)).fetchone()
            merged = list(dict.fromkeys([
                *[capability for capability in json.loads(row["capabilities"]) if capability != "economic_calendar"],
                "earnings_calendar",
            ]))
            connection.execute("UPDATE data_source_profiles SET capabilities=?, updated_at=? WHERE id=?", (_json(merged), now, source_id))
        if source_id in {"calendar:bls", "calendar:bea", "calendar:nbs", "calendar:ecb", "calendar:fomc"}:
            timezone_name = "Asia/Shanghai" if source_id == "calendar:nbs" else "Europe/Frankfurt" if source_id == "calendar:ecb" else "America/New_York"
            connection.execute(
                "UPDATE data_source_profiles SET auth_type='none', api_key_env='', priority=?, reliability_weight=0.98, sync_interval_seconds=?, timezone=?, updated_at=? WHERE id=?",
                (100 if source_id in {"calendar:bls", "calendar:nbs"} else 95,
                 21600 if source_id == "calendar:nbs" else 3600, timezone_name, now, source_id),
            )
        if source_id == "calendar:trading-economics" and not source_existed:
            connection.execute("UPDATE data_source_profiles SET enabled=0, updated_at=? WHERE id=?", (now, source_id))
    rows = connection.execute("SELECT * FROM news_sources").fetchall()
    for row in rows:
        source_id = f"content:{row['id']}"
        connection.execute(
            """
            INSERT INTO data_source_profiles (
                id, legacy_source_id, name, domain, category, adapter, provider, endpoint,
                enabled, markets, asset_classes, capabilities, auth_type, api_key_env,
                priority, reliability_weight, timeout_seconds, cache_ttl_seconds,
                stale_ttl_seconds, config, created_at, updated_at
            ) VALUES (?, ?, ?, 'content', ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, 8, 300, 86400, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                legacy_source_id=excluded.legacy_source_id, name=excluded.name,
                category=excluded.category, adapter=excluded.adapter, provider=excluded.provider,
                endpoint=excluded.endpoint, enabled=excluded.enabled, markets=excluded.markets,
                capabilities=excluded.capabilities, auth_type=excluded.auth_type,
                api_key_env=excluded.api_key_env, priority=excluded.priority,
                reliability_weight=excluded.reliability_weight, config=excluded.config,
                updated_at=excluded.updated_at
            """,
            (source_id, row["id"], row["name"], row["category"], row["adapter"], row["provider"],
             row["url"], row["enabled"], _json([row["region"].upper()]), row["capabilities"],
             row["auth_type"], row["api_key_env"], row["priority"], row["reliability_weight"],
             row["config"], row["created_at"], now),
        )
    _ensure_default_routes(connection, active_provider, now)


def _ensure_route(connection, market: str, capability: str, source_ids: list[str], mode: str, now: str) -> None:
    route_id = f"{market.upper()}:{capability}"
    connection.execute(
        "INSERT OR IGNORE INTO data_source_routes (id, market, capability, mode, enabled, version, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?)",
        (route_id, market.upper(), capability, mode, now),
    )
    count = connection.execute("SELECT COUNT(*) AS total FROM data_source_route_members WHERE route_id = ?", (route_id,)).fetchone()["total"]
    if count:
        return
    for position, source_id in enumerate(source_ids):
        exists = connection.execute("SELECT 1 FROM data_source_profiles WHERE id = ?", (source_id,)).fetchone()
        if exists:
            role = "member" if mode == "aggregate" else "primary" if position == 0 else "fallback"
            connection.execute(
                "INSERT INTO data_source_route_members (route_id, source_id, position, role, enabled) VALUES (?, ?, ?, ?, 1)",
                (route_id, source_id, position, role),
            )


def _ensure_default_routes(connection, active_provider: str, now: str) -> None:
    active_id = f"market:{active_provider}" if active_provider in {"finnhub", "binance", "coinbase"} else "market:coinbase"
    for capability in ("quote", "snapshot"):
        _ensure_route(connection, "GLOBAL", capability, [active_id], "fallback", now)
    us_sources = ["market:finnhub"]
    crypto_sources = [f"market:{active_provider}", "market:coinbase", "market:binance"] if active_provider in {"binance", "coinbase"} else ["market:coinbase", "market:binance"]
    for capability in ("quote", "snapshot", "candles"):
        _ensure_route(connection, "US", capability, list(dict.fromkeys(us_sources)), "fallback", now)
        _ensure_route(connection, "CRYPTO", capability, list(dict.fromkeys(crypto_sources)), "fallback", now)
    for market in ("US", "CN", "HK", "EU", "JP"):
        _ensure_route(connection, market, "index_candles", ["index:public"], "fallback", now)
    _ensure_route(connection, "US", "fundamentals", ["market:finnhub", "indicator:alphavantage"], "fallback", now)
    _ensure_route(connection, "US", "earnings_estimates", ["market:finnhub", "indicator:alphavantage"], "fallback", now)
    _ensure_route(connection, "GLOBAL", "macro_series", ["indicator:fred"], "fallback", now)
    _ensure_route(connection, "GLOBAL", "economic_calendar", ["calendar:bls", "calendar:bea", "calendar:fomc", "calendar:nbs", "calendar:ecb"], "aggregate", now)
    _ensure_route(connection, "US", "economic_calendar", ["calendar:bls", "calendar:bea", "calendar:fomc"], "aggregate", now)
    _ensure_route(connection, "CN", "economic_calendar", ["calendar:nbs"], "aggregate", now)
    _ensure_route(connection, "EU", "economic_calendar", ["calendar:ecb"], "aggregate", now)
    _ensure_route(connection, "GLOBAL", "earnings_calendar", ["market:finnhub"], "fallback", now)
    _ensure_route(connection, "US", "sentiment_snapshot", ["indicator:adanos"], "fallback", now)
    _ensure_route(connection, "CRYPTO", "sentiment_snapshot", ["indicator:adanos"], "fallback", now)
    _ensure_route(connection, "CRYPTO", "derivatives", ["market:binance"], "fallback", now)
    global_calendar = connection.execute(
        "SELECT id, mode, version FROM data_source_routes WHERE market='GLOBAL' AND capability='economic_calendar'"
    ).fetchone()
    if global_calendar:
        members = [row["source_id"] for row in connection.execute(
            "SELECT source_id FROM data_source_route_members WHERE route_id=? ORDER BY position",
            (global_calendar["id"],),
        ).fetchall()]
        if global_calendar["version"] <= 2 and members and set(members).issubset({"calendar:trading-economics", "market:finnhub", "calendar:bls", "calendar:bea"}):
            connection.execute("DELETE FROM data_source_route_members WHERE route_id=?", (global_calendar["id"],))
            for position, source_id in enumerate(("calendar:bls", "calendar:bea", "calendar:fomc", "calendar:nbs", "calendar:ecb")):
                connection.execute(
                    "INSERT INTO data_source_route_members(route_id,source_id,position,role,enabled) VALUES(?,?,?,?,1)",
                    (global_calendar["id"], source_id, position, "member"),
                )
            connection.execute(
                "UPDATE data_source_routes SET mode='aggregate',version=version+1,updated_at=? WHERE id=?",
                (now, global_calendar["id"]),
            )
    content_ids = [f"content:{row['id']}" for row in connection.execute(
        "SELECT id FROM news_sources WHERE enabled = 1 ORDER BY priority DESC, id"
    ).fetchall()]
    if content_ids:
        _ensure_route(connection, "GLOBAL", "news", content_ids, "aggregate", now)

    for route in connection.execute("SELECT id, mode FROM data_source_routes").fetchall():
        members = connection.execute(
            "SELECT source_id FROM data_source_route_members WHERE route_id = ? ORDER BY position, source_id",
            (route["id"],),
        ).fetchall()
        for position, member in enumerate(members):
            role = "member" if route["mode"] == "aggregate" else "primary" if position == 0 else "fallback"
            connection.execute(
                "UPDATE data_source_route_members SET position = ?, role = ? WHERE route_id = ? AND source_id = ?",
                (position, role, route["id"], member["source_id"]),
            )


def validate_endpoint(endpoint: str, adapter: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Data source endpoint must use HTTPS")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise HTTPException(status_code=422, detail="Loopback and local endpoints are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise HTTPException(status_code=422, detail="Private network endpoints are not allowed")


def _loads(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _credential_state(connection, row) -> tuple[str | None, bool]:
    candidates = [("data_source", row["id"])]
    if row["legacy_source_id"] is not None:
        candidates.append(("information_source", str(row["legacy_source_id"])))
    else:
        candidates.append(("market_provider", row["adapter"]))
    binding = None
    for resource_type, resource_id in candidates:
        binding = connection.execute(
            """
            SELECT c.id, c.encrypted_value, c.env_alias
            FROM credential_bindings b JOIN api_credentials c ON c.id = b.credential_id
            WHERE b.resource_type = ? AND b.resource_id = ? LIMIT 1
            """,
            (resource_type, resource_id),
        ).fetchone()
        if binding:
            break
    if binding:
        return binding["id"], bool(binding["encrypted_value"] or os.getenv(binding["env_alias"], ""))
    return None, bool(row["api_key_env"] and os.getenv(row["api_key_env"], ""))


def profile_from_row(connection, row) -> DataSourceProfile:
    credential_id, configured = _credential_state(connection, row)
    return DataSourceProfile(
        id=row["id"], legacy_source_id=row["legacy_source_id"], name=row["name"], domain=row["domain"],
        category=row["category"], adapter=row["adapter"], provider=row["provider"], endpoint=row["endpoint"],
        enabled=bool(row["enabled"]), markets=_loads(row["markets"], []), asset_classes=_loads(row["asset_classes"], []),
        capabilities=_loads(row["capabilities"], []), auth_type=row["auth_type"], api_key_env=row["api_key_env"],
        credential_id=credential_id, credential_configured=configured, priority=row["priority"],
        reliability_weight=row["reliability_weight"], timeout_seconds=row["timeout_seconds"],
        cache_ttl_seconds=row["cache_ttl_seconds"], stale_ttl_seconds=row["stale_ttl_seconds"],
        sync_interval_seconds=row["sync_interval_seconds"], timezone=row["timezone"],
        config=_loads(row["config"], {}), health_status="disabled" if not row["enabled"] else row["health_status"],
        consecutive_failures=row["consecutive_failures"], consecutive_successes=row["consecutive_successes"],
        last_checked_at=row["last_checked_at"], last_success_at=row["last_success_at"], last_error=row["last_error"],
        last_latency_ms=row["last_latency_ms"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def list_profiles(domain: str | None = None, query: str = "") -> list[DataSourceProfile]:
    with _connect() as connection:
        sql = "SELECT * FROM data_source_profiles WHERE 1 = 1"
        params: list[object] = []
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if query.strip():
            sql += " AND (LOWER(name) LIKE ? OR LOWER(provider) LIKE ? OR LOWER(adapter) LIKE ?)"
            needle = f"%{query.strip().lower()}%"
            params.extend([needle, needle, needle])
        sql += " ORDER BY domain, priority DESC, name"
        return [profile_from_row(connection, row) for row in connection.execute(sql, params).fetchall()]


def get_profile(source_id: str) -> DataSourceProfile:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM data_source_profiles WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Data source not found")
        return profile_from_row(connection, row)


def create_profile(payload: DataSourceProfileCreate) -> DataSourceProfile:
    if payload.adapter not in REGISTERED_ADAPTERS:
        raise HTTPException(status_code=422, detail="Adapter is not registered")
    validate_endpoint(payload.endpoint, payload.adapter)
    now = _now()
    source_id = f"source:{sha256(f'{payload.name}:{payload.endpoint}:{now}'.encode()).hexdigest()[:16]}"
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO data_source_profiles (
                id, name, domain, category, adapter, provider, endpoint, enabled, markets,
                asset_classes, capabilities, auth_type, api_key_env, priority, reliability_weight,
                timeout_seconds, cache_ttl_seconds, stale_ttl_seconds, config, created_at, updated_at
                , sync_interval_seconds, timezone
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_id, payload.name, payload.domain, payload.category, payload.adapter, payload.provider,
             payload.endpoint, int(payload.enabled), _json(payload.markets), _json(payload.asset_classes),
             _json(payload.capabilities), payload.auth_type, payload.api_key_env, payload.priority,
             payload.reliability_weight, payload.timeout_seconds, payload.cache_ttl_seconds,
             payload.stale_ttl_seconds, _json(payload.config), now, now,
             payload.sync_interval_seconds, payload.timezone),
        )
        connection.commit()
    return get_profile(source_id)


def update_profile(source_id: str, payload: DataSourceProfileUpdate) -> DataSourceProfile:
    current = get_profile(source_id)
    values = payload.model_dump(exclude_none=True)
    endpoint = str(values.get("endpoint", current.endpoint))
    validate_endpoint(endpoint, current.adapter)
    json_fields = {"markets", "asset_classes", "capabilities", "config"}
    allowed = {
        "name", "endpoint", "enabled", "markets", "asset_classes", "capabilities", "auth_type",
        "api_key_env", "priority", "reliability_weight", "timeout_seconds", "cache_ttl_seconds",
        "stale_ttl_seconds", "sync_interval_seconds", "timezone", "config",
    }
    assignments: list[str] = []
    params: list[object] = []
    for key, value in values.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        params.append(_json(value) if key in json_fields else int(value) if key == "enabled" else value)
    if not assignments:
        return current
    assignments.append("updated_at = ?")
    params.extend([_now(), source_id])
    with _connect() as connection:
        connection.execute(f"UPDATE data_source_profiles SET {', '.join(assignments)} WHERE id = ?", params)
        if current.legacy_source_id is not None:
            legacy_map = {"name": "name", "endpoint": "url", "enabled": "enabled", "auth_type": "auth_type", "api_key_env": "api_key_env", "priority": "priority", "reliability_weight": "reliability_weight", "config": "config", "capabilities": "capabilities"}
            legacy_values = [(legacy_map[key], _json(value) if key in {"config", "capabilities"} else int(value) if key == "enabled" else value) for key, value in values.items() if key in legacy_map]
            if legacy_values:
                connection.execute(
                    f"UPDATE news_sources SET {', '.join(f'{column} = ?' for column, _ in legacy_values)} WHERE id = ?",
                    [value for _, value in legacy_values] + [current.legacy_source_id],
                )
        connection.commit()
    return get_profile(source_id)


def record_health(source_id: str, passed: bool, latency_ms: float, message: str) -> DataSourceProfile:
    now = _now()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM data_source_profiles WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Data source not found")
        failures = 0 if passed else row["consecutive_failures"] + 1
        successes = row["consecutive_successes"] + 1 if passed else 0
        if not row["enabled"]:
            status = "disabled"
        elif passed and (row["health_status"] != "offline" or successes >= 3):
            status = "healthy"
        elif passed:
            status = "offline"
        elif failures >= 3:
            status = "offline"
        else:
            status = "degraded"
        connection.execute(
            """
            UPDATE data_source_profiles SET health_status = ?, consecutive_failures = ?,
                consecutive_successes = ?, last_checked_at = ?, last_success_at = ?,
                last_error = ?, last_latency_ms = ?, updated_at = ? WHERE id = ?
            """,
            (status, failures, successes, now, now if passed else row["last_success_at"], None if passed else message[:500], latency_ms, now, source_id),
        )
        connection.execute(
            "INSERT INTO data_source_health_checks (source_id, status, latency_ms, message, checked_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, "pass" if passed else "fail", latency_ms, message[:500], now),
        )
        connection.commit()
    return get_profile(source_id)


def list_health_checks(source_id: str | None = None, limit: int = 100) -> list[DataSourceHealthCheck]:
    with _connect() as connection:
        if source_id:
            rows = connection.execute("SELECT * FROM data_source_health_checks WHERE source_id = ? ORDER BY checked_at DESC LIMIT ?", (source_id, limit)).fetchall()
        else:
            rows = connection.execute("SELECT * FROM data_source_health_checks ORDER BY checked_at DESC LIMIT ?", (limit,)).fetchall()
    return [DataSourceHealthCheck(**dict(row)) for row in rows]


def route_from_row(connection, row) -> DataSourceRoute:
    members = connection.execute(
        """
        SELECT m.*, p.name, p.adapter, p.enabled AS source_enabled, p.health_status
        FROM data_source_route_members m JOIN data_source_profiles p ON p.id = m.source_id
        WHERE m.route_id = ? ORDER BY m.position
        """,
        (row["id"],),
    ).fetchall()
    return DataSourceRoute(
        id=row["id"], market=row["market"], capability=row["capability"], mode=row["mode"],
        enabled=bool(row["enabled"]), version=row["version"], updated_at=row["updated_at"],
        members=[DataSourceRouteMember(
            source_id=item["source_id"], source_name=item["name"], adapter=item["adapter"], position=item["position"],
            role=item["role"], enabled=bool(item["enabled"] and item["source_enabled"]), health_status=item["health_status"],
        ) for item in members],
    )


def list_routes() -> list[DataSourceRoute]:
    with _connect() as connection:
        return [route_from_row(connection, row) for row in connection.execute("SELECT * FROM data_source_routes ORDER BY market, capability").fetchall()]


def get_route(market: str, capability: str) -> DataSourceRoute | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM data_source_routes WHERE market = ? AND capability = ?", (market.upper(), capability)).fetchone()
        if row is None and market.upper() != "GLOBAL":
            row = connection.execute("SELECT * FROM data_source_routes WHERE market = 'GLOBAL' AND capability = ?", (capability,)).fetchone()
        return route_from_row(connection, row) if row else None


def resolve_candidates(market: str, capability: str) -> list[DataSourceProfile]:
    route = get_route(market, capability)
    if route is None or not route.enabled:
        return []
    profiles = {item.id: item for item in list_profiles()}
    candidates = [profiles[member.source_id] for member in route.members if member.enabled and member.source_id in profiles]
    healthy = [item for item in candidates if item.health_status not in {"offline", "disabled"}]
    return healthy or [item for item in candidates if item.health_status != "disabled"]


def publish_route(market: str, capability: str, source_ids: list[str], mode: str, expected_version: int | None, actor: str, validation: list[dict[str, object]]) -> DataSourceRoute:
    market = market.upper()
    route_id = f"{market}:{capability}"
    now = _now()
    with _connect() as connection:
        existing = connection.execute("SELECT * FROM data_source_routes WHERE id = ?", (route_id,)).fetchone()
        version = int(existing["version"]) if existing else 0
        if expected_version is not None and version != expected_version:
            raise HTTPException(status_code=409, detail="Route configuration has changed; reload before publishing")
        placeholders = ",".join("?" for _ in source_ids)
        rows = connection.execute(f"SELECT id FROM data_source_profiles WHERE id IN ({placeholders})", source_ids).fetchall()
        if {row["id"] for row in rows} != set(source_ids):
            raise HTTPException(status_code=422, detail="Route contains an unknown data source")
        next_version = version + 1
        connection.execute(
            """
            INSERT INTO data_source_routes (id, market, capability, mode, enabled, version, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET mode=excluded.mode, enabled=1, version=excluded.version, updated_at=excluded.updated_at
            """,
            (route_id, market, capability, mode, next_version, now),
        )
        connection.execute("DELETE FROM data_source_route_members WHERE route_id = ?", (route_id,))
        for position, source_id in enumerate(source_ids):
            role = "member" if mode == "aggregate" else "primary" if position == 0 else "fallback"
            connection.execute(
                "INSERT INTO data_source_route_members (route_id, source_id, position, role, enabled) VALUES (?, ?, ?, ?, 1)",
                (route_id, source_id, position, role),
            )
        revision_id = f"revision:{sha256(f'{route_id}:{next_version}:{now}'.encode()).hexdigest()[:18]}"
        connection.execute("UPDATE data_source_revisions SET status = 'rolled_back' WHERE route_id = ? AND status = 'active'", (route_id,))
        connection.execute(
            "INSERT INTO data_source_revisions (id, route_id, version, status, config, validation, created_by, created_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
            (revision_id, route_id, next_version, _json({"market": market, "capability": capability, "mode": mode, "source_ids": source_ids}), _json(validation), actor, now),
        )
        connection.execute("DELETE FROM market_data_cache")
        connection.execute("DELETE FROM indicator_data_cache")
        connection.commit()
    route = get_route(market, capability)
    assert route is not None
    return route


def list_revisions(limit: int = 100) -> list[DataSourceRevision]:
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM data_source_revisions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [DataSourceRevision(
        id=row["id"], route_id=row["route_id"], version=row["version"], status=row["status"],
        config=_loads(row["config"], {}), validation=_loads(row["validation"], []),
        created_by=row["created_by"], created_at=row["created_at"],
    ) for row in rows]


def rollback_revision(revision_id: str, actor: str) -> DataSourceRoute:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM data_source_revisions WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Data source revision not found")
    config = _loads(row["config"], {})
    if not config.get("source_ids"):
        raise HTTPException(status_code=409, detail="Revision does not contain a restorable route")
    current = get_route(str(config["market"]), str(config["capability"]))
    route = publish_route(
        str(config["market"]), str(config["capability"]), list(config["source_ids"]),
        str(config.get("mode") or "fallback"), current.version if current else None, actor,
        [{"status": "pass", "message": f"Rollback from {revision_id}"}],
    )
    with _connect() as connection:
        connection.execute("UPDATE data_source_revisions SET status = 'rolled_back' WHERE id = ?", (revision_id,))
        connection.commit()
    return route


def control_summary() -> DataSourceControlSummary:
    profiles = list_profiles()
    revisions = list_revisions(limit=1)
    return DataSourceControlSummary(
        total_sources=len(profiles), enabled_sources=sum(item.enabled for item in profiles),
        healthy_sources=sum(item.health_status == "healthy" for item in profiles),
        degraded_sources=sum(item.health_status in {"degraded", "untested"} for item in profiles),
        offline_sources=sum(item.health_status == "offline" for item in profiles), routes=len(list_routes()),
        active_revision=revisions[0].version if revisions else 0, generated_at=_now(),
    )
