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
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
json_value = service_call('app.modules.ai.service', 'json_value')


DB_PATH = settings.database_path


_DB_INIT_LOCK = RLock()


_INITIALIZED_DB_PATHS: set[str] = set()


SESSION_COOKIE = settings.session_cookie


SESSION_HOURS = settings.session_hours


PASSWORD_ITERATIONS = settings.password_iterations


BUILTIN_INFORMATION_SOURCES: list[dict[str, object]] = [
    {
        "name": "Finnhub Market News",
        "type": "rest_api", "category": "news", "adapter": "finnhub", "provider": "Finnhub",
        "url": "https://finnhub.io/api/v1/news?category=general", "region": "global", "language": "en",
        "auth_type": "query", "api_key_env": "FINNHUB_API_KEY",
        "capabilities": ["news", "company_profile", "symbol_search", "quote"], "priority": 90, "reliability_weight": 0.9,
    },
    {
        "name": "TradingView Market Widgets",
        "type": "widget", "category": "market", "adapter": "widget", "provider": "TradingView",
        "url": "https://www.tradingview.com/widget-docs/", "region": "global", "language": "multi",
        "auth_type": "none", "api_key_env": "", "capabilities": ["chart", "heatmap", "quotes", "timeline"],
        "priority": 80, "reliability_weight": 0.85,
    },
    {
        "name": "Adanos Cross-source Sentiment",
        "type": "rest_api", "category": "sentiment", "adapter": "adanos", "provider": "Adanos",
        "url": "https://api.adanos.org", "region": "global", "language": "en",
        "auth_type": "header", "api_key_env": "ADANOS_API_KEY",
        "capabilities": ["sentiment", "reddit", "x", "news", "polymarket"], "priority": 70, "reliability_weight": 0.7,
    },
    {
        "name": "SEC EDGAR Latest Filings",
        "type": "rss", "category": "filing", "adapter": "rss", "provider": "SEC EDGAR",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom", "region": "US", "language": "en",
        "auth_type": "none", "api_key_env": "", "capabilities": ["filings", "regulatory", "news"],
        "priority": 95, "reliability_weight": 1.0,
    },
    {
        "name": "Federal Reserve Press Releases",
        "type": "rss", "category": "macro", "adapter": "rss", "provider": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml", "region": "US", "language": "en",
        "auth_type": "none", "api_key_env": "", "capabilities": ["macro", "rates", "regulatory", "news"],
        "priority": 95, "reliability_weight": 1.0,
    },
    {
        "name": "GDELT Global Financial News",
        "type": "rest_api", "category": "news", "adapter": "gdelt", "provider": "GDELT",
        "url": "https://api.gdeltproject.org/api/v2/doc/doc?query=(stockmarket%20OR%20cryptocurrency%20OR%20centralbank)&mode=artlist&format=json&maxrecords=50&sort=datedesc",
        "region": "global", "language": "multi", "auth_type": "none", "api_key_env": "",
        "capabilities": ["news", "global", "multilingual"], "priority": 60, "reliability_weight": 0.65,
    },
    {
        "name": "CryptoCompare Digital Asset News",
        "type": "rest_api", "category": "news", "adapter": "cryptocompare", "provider": "CryptoCompare",
        "url": "https://min-api.cryptocompare.com/data/v2/news/?lang=EN", "region": "global", "language": "en",
        "auth_type": "none", "api_key_env": "CRYPTOCOMPARE_API_KEY", "capabilities": ["news", "crypto"],
        "priority": 75, "reliability_weight": 0.75,
    },
    {
        "name": "Alpha Vantage News Sentiment",
        "type": "rest_api", "category": "sentiment", "adapter": "alphavantage", "provider": "Alpha Vantage",
        "url": "https://www.alphavantage.co/query?function=NEWS_SENTIMENT&sort=LATEST&limit=50", "region": "global", "language": "en",
        "auth_type": "query", "api_key_env": "ALPHAVANTAGE_API_KEY", "capabilities": ["news", "sentiment", "ticker_topics"],
        "priority": 70, "reliability_weight": 0.8,
    },
    {
        "name": "FRED Economic Releases",
        "type": "rest_api", "category": "macro", "adapter": "fred", "provider": "FRED",
        "url": "https://api.stlouisfed.org/fred/releases?file_type=json&limit=50", "region": "US", "language": "en",
        "auth_type": "query", "api_key_env": "FRED_API_KEY", "capabilities": ["macro", "economic_releases"],
        "priority": 85, "reliability_weight": 1.0,
    },
]


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
    "news_tagger": {
        "name": "News Tagger",
        "description": "逐条识别新闻的主关联与次关联标的，并按置信度自动生效或进入审核。",
        "interval": 300,
        "triggers": ["manual", "news_ingested"],
        "settings": {
            "auto_apply_confidence": 0.85, "review_confidence": 0.60,
            "secondary_display_confidence": 0.75, "max_primary": 5, "max_secondary": 8,
            "batch_size": 8, "max_requests_per_minute": 10, "tagging_version": 1,
        },
    },
    "news_event_extractor": {
        "name": "News Event Extractor",
        "description": "提取新闻事件类型、范围、主题、地域、宏观因子与事件聚类。",
        "interval": 300, "triggers": ["pipeline"],
        "settings": {"pipeline_version": 2, "recent_event_window_hours": 48},
    },
    "news_impact_mapper": {
        "name": "News Impact Mapper",
        "description": "结合已审批影响关系图，为平台标的生成可解释的直接与间接影响路径。",
        "interval": 300, "triggers": ["pipeline"],
        "settings": {"max_instrument_impacts": 10, "review_confidence": 0.65, "relationship_proposal_confidence": 0.75},
    },
    "news_impact_validator": {
        "name": "News Impact Validator",
        "description": "校验影响路径与评分，按直接和间接关系的独立阈值决定生效或审核。",
        "interval": 300, "triggers": ["pipeline"],
        "settings": {"direct_confidence": 0.85, "derived_confidence": 0.90, "derived_relevance": 0.75, "derived_impact": 70},
    },
    "news_analyst": {
        "name": "News Analyst",
        "description": "分析新闻影响、情绪、关联标的和潜在价格催化。",
        "interval": 300,
        "triggers": ["manual", "schedule", "high_impact_news"],
        "settings": {"min_impact_score": 60, "news_limit": 8, "min_confidence": 60},
    },
    "economic_event_analyst": {
        "name": "Economic Event Analyst",
        "description": "分析未来宏观与财报事件的传导路径、预期差、关联市场和风险情景。",
        "interval": 300,
        "triggers": ["manual", "calendar_event_upcoming", "calendar_event_released"],
        "settings": {"analysis_lead_minutes": 60, "min_importance": "high", "min_confidence": 60},
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
    return connect(DB_PATH)


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    add_column_if_missing(connection, table, column, definition)


def _initialize_database() -> None:
    set_data_source_database_path(DB_PATH)
    with db_connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'rss',
                category TEXT NOT NULL DEFAULT 'news',
                adapter TEXT NOT NULL DEFAULT 'rss',
                provider TEXT NOT NULL DEFAULT 'custom',
                url TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                symbols TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT 'global',
                language TEXT NOT NULL DEFAULT 'en',
                auth_type TEXT NOT NULL DEFAULT 'none',
                api_key_env TEXT NOT NULL DEFAULT '',
                capabilities TEXT NOT NULL DEFAULT '["news"]',
                config TEXT NOT NULL DEFAULT '{}',
                priority INTEGER NOT NULL DEFAULT 50,
                reliability_weight REAL NOT NULL DEFAULT 0.8,
                builtin INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS news_source_runs (
                id TEXT PRIMARY KEY,
                source_id INTEGER NOT NULL,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                fetched INTEGER NOT NULL DEFAULT 0,
                inserted INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                FOREIGN KEY(source_id) REFERENCES news_sources(id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_source_runs_source_time ON news_source_runs(source_id, started_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_source_runs_status ON news_source_runs(status, started_at DESC)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_news_source_runs_active ON news_source_runs(source_id) WHERE status = 'running'"
        )
        ensure_column(connection, "news_source_runs", "diagnostic_json", "TEXT")
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
            VALUES ('active_market_provider', 'coinbase', ?)
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
        ensure_column(connection, "alert_rules", "interval", "TEXT NOT NULL DEFAULT '1h'")
        ensure_column(connection, "alert_rules", "parameters", "TEXT NOT NULL DEFAULT '{}'")
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
        ensure_column(connection, "watchlist_items", "instrument_id", "TEXT")
        connection.execute(
            "UPDATE watchlist_items SET instrument_id = provider || ':' || symbol WHERE instrument_id IS NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_items_instrument ON watchlist_items(instrument_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist_groups (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                name TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT 'blue',
                is_default INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, name)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_groups_provider ON watchlist_groups(provider, sort_order, name)")
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
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                theme TEXT NOT NULL DEFAULT 'system',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chart_layouts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                interval TEXT NOT NULL DEFAULT '15m',
                range_value TEXT NOT NULL DEFAULT '1M',
                indicators TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                UNIQUE(user_id, asset_class, name)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chart_layouts_user_asset ON chart_layouts(user_id, asset_class, is_default)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_data_cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                interval TEXT NOT NULL,
                range_value TEXT NOT NULL,
                payload TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                stale_until TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_data_cache_lookup ON market_data_cache(instrument_id, interval, range_value)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS indicator_data_cache (
                cache_key TEXT PRIMARY KEY,
                instrument_id TEXT NOT NULL,
                interval TEXT NOT NULL,
                metric TEXT NOT NULL,
                payload TEXT NOT NULL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                stale_until TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_indicator_data_cache_lookup ON indicator_data_cache(instrument_id, interval, metric)"
        )
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
            CREATE TABLE IF NOT EXISTS api_credentials (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                env_alias TEXT NOT NULL UNIQUE,
                encrypted_value TEXT,
                secret_hint TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 0,
                test_status TEXT NOT NULL DEFAULT 'unknown',
                test_message TEXT NOT NULL DEFAULT '',
                last_tested_at TEXT,
                last_rotated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS credential_bindings (
                credential_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(resource_type, resource_id),
                FOREIGN KEY(credential_id) REFERENCES api_credentials(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_credential_bindings_credential ON credential_bindings(credential_id)")
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
            CREATE TABLE IF NOT EXISTS model_channels (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, provider_type TEXT NOT NULL, protocol TEXT NOT NULL,
                base_url TEXT NOT NULL, api_key_env TEXT NOT NULL, timeout_seconds INTEGER NOT NULL,
                config TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
                health_status TEXT NOT NULL DEFAULT 'unknown', consecutive_failures INTEGER NOT NULL DEFAULT 0,
                response_time_ms INTEGER, last_tested_at TEXT, last_error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_channel_models (
                channel_id TEXT NOT NULL, model TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual',
                capabilities TEXT NOT NULL DEFAULT '["chat"]', enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(channel_id, model), FOREIGN KEY(channel_id) REFERENCES model_channels(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_routes (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, alias TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
                temperature REAL NOT NULL DEFAULT 0.2, max_tokens INTEGER NOT NULL DEFAULT 1200,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_route_targets (
                id TEXT PRIMARY KEY, route_id TEXT NOT NULL, channel_id TEXT NOT NULL, model TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0, weight INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(route_id) REFERENCES model_routes(id), FOREIGN KEY(channel_id) REFERENCES model_channels(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_model_targets_route ON model_route_targets(route_id, enabled, priority)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_invocations (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL, source TEXT NOT NULL, route_id TEXT,
                channel_id TEXT, channel_name TEXT, model TEXT, status TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_model_invocations_time ON model_invocations(created_at)")
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
        ensure_column(connection, "agent_configs", "model_route_id", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, agent_name TEXT NOT NULL, trigger TEXT NOT NULL,
                symbol TEXT, status TEXT NOT NULL, runtime_mode TEXT NOT NULL, input_payload TEXT NOT NULL,
                input_snapshot TEXT,
                output TEXT, error TEXT, model TEXT, prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0,
                test INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                FOREIGN KEY(agent_id) REFERENCES agent_configs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_time ON agent_runs(agent_id, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_queue ON agent_runs(status, created_at)")
        ensure_column(connection, "agent_runs", "input_snapshot", "TEXT")
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
        ensure_column(connection, "agent_runs", "trace_id", "TEXT")
        ensure_column(connection, "agent_runs", "parent_run_id", "TEXT")
        ensure_column(connection, "agent_runs", "workflow_run_id", "TEXT")
        ensure_column(connection, "agent_runs", "parent_span_id", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_definitions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, objective TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft', enabled INTEGER NOT NULL DEFAULT 0,
                draft_version INTEGER NOT NULL DEFAULT 1, published_version INTEGER,
                draft_graph TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_definitions_status ON workflow_definitions(status, updated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_versions (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
                graph TEXT NOT NULL, content_hash TEXT NOT NULL, published_by TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(workflow_id, version),
                FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_name TEXT NOT NULL,
                workflow_version INTEGER NOT NULL, trace_id TEXT NOT NULL UNIQUE,
                trigger TEXT NOT NULL, status TEXT NOT NULL, symbol TEXT,
                input_payload TEXT NOT NULL, output TEXT, error TEXT,
                test INTEGER NOT NULL DEFAULT 0, checkpoint_thread_id TEXT,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_runs_queue ON workflow_runs(status, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_spans (
                id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, run_id TEXT NOT NULL, parent_span_id TEXT,
                node_id TEXT NOT NULL, node_name TEXT NOT NULL, kind TEXT NOT NULL, operation TEXT NOT NULL,
                status TEXT NOT NULL, input_payload TEXT NOT NULL DEFAULT '{}', output TEXT,
                decision_summary TEXT NOT NULL DEFAULT '', resource_refs TEXT NOT NULL DEFAULT '[]',
                model TEXT, prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0, error TEXT, started_at TEXT NOT NULL, finished_at TEXT,
                FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_spans_trace ON workflow_spans(trace_id, started_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_approvals (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, span_id TEXT NOT NULL, node_id TEXT NOT NULL,
                title TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by TEXT, review_note TEXT, created_at TEXT NOT NULL, reviewed_at TEXT,
                FOREIGN KEY(run_id) REFERENCES workflow_runs(id), FOREIGN KEY(span_id) REFERENCES workflow_spans(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_approvals_status ON workflow_approvals(status, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_artifacts (
                id TEXT PRIMARY KEY, artifact_type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                scope_type TEXT NOT NULL, scope_ids TEXT NOT NULL, direction TEXT NOT NULL,
                severity TEXT NOT NULL, confidence REAL NOT NULL, evidence TEXT NOT NULL,
                payload TEXT NOT NULL, workflow_id TEXT NOT NULL, workflow_version INTEGER NOT NULL,
                run_id TEXT NOT NULL, trace_id TEXT NOT NULL, span_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', valid_from TEXT NOT NULL, valid_until TEXT NOT NULL,
                created_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES workflow_runs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_investment_artifacts_scope ON investment_artifacts(artifact_type, status, valid_until)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_investment_artifacts_trace ON investment_artifacts(trace_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_article_symbol_tags (
                id TEXT PRIMARY KEY, article_id TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL,
                name TEXT NOT NULL, relation TEXT NOT NULL, confidence REAL NOT NULL, direction TEXT NOT NULL,
                origin TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
                agent_run_id TEXT, model TEXT, tagging_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES news_articles(id), FOREIGN KEY(agent_run_id) REFERENCES agent_runs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_symbol_tags_article ON news_article_symbol_tags(article_id, status, relation)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_symbol_tags_symbol ON news_article_symbol_tags(symbol, status, relation)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_tagging_jobs (
                article_id TEXT PRIMARY KEY, status TEXT NOT NULL, trigger TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, run_id TEXT, error TEXT,
                tagging_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES news_articles(id), FOREIGN KEY(run_id) REFERENCES agent_runs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_tagging_jobs_queue ON news_tagging_jobs(status, updated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_article_symbol_overrides (
                article_id TEXT PRIMARY KEY, locked INTEGER NOT NULL DEFAULT 1,
                note TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, FOREIGN KEY(article_id) REFERENCES news_articles(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_event_annotations (
                article_id TEXT PRIMARY KEY, taxonomy TEXT NOT NULL, scope TEXT NOT NULL, summary TEXT NOT NULL,
                topics TEXT NOT NULL DEFAULT '[]', countries TEXT NOT NULL DEFAULT '[]', regions TEXT NOT NULL DEFAULT '[]',
                actors TEXT NOT NULL DEFAULT '[]', macro_factors TEXT NOT NULL DEFAULT '[]', horizon TEXT NOT NULL,
                significance INTEGER NOT NULL, novelty INTEGER NOT NULL, content_completeness REAL NOT NULL,
                event_cluster_id TEXT NOT NULL, origin TEXT NOT NULL, model TEXT, pipeline_version INTEGER NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(article_id) REFERENCES news_articles(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_event_cluster ON news_event_annotations(event_cluster_id, updated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_impact_graph_edges (
                id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_id TEXT NOT NULL, source_label TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, target_label TEXT NOT NULL, relation TEXT NOT NULL,
                direction TEXT NOT NULL, weight REAL NOT NULL, evidence TEXT NOT NULL, provenance TEXT NOT NULL,
                status TEXT NOT NULL, created_by TEXT NOT NULL, reviewed_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_impact_graph_source ON news_impact_graph_edges(source_type, source_id, status)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_impact_links (
                id TEXT PRIMARY KEY, article_id TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                target_label TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '', link_type TEXT NOT NULL, channel TEXT NOT NULL,
                direction TEXT NOT NULL, relevance REAL NOT NULL, impact_score INTEGER NOT NULL, confidence REAL NOT NULL,
                horizon TEXT NOT NULL, path TEXT NOT NULL DEFAULT '[]', evidence TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL,
                status TEXT NOT NULL, graph_edge_ids TEXT NOT NULL DEFAULT '[]', agent_run_id TEXT, model TEXT,
                pipeline_version INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES news_articles(id), FOREIGN KEY(agent_run_id) REFERENCES agent_runs(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_impacts_article ON news_impact_links(article_id, status, impact_score)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_news_impacts_symbol ON news_impact_links(symbol, status, link_type)")
        connection.execute(
            """INSERT OR IGNORE INTO news_impact_links
               (id, article_id, target_type, target_id, target_label, symbol, link_type, channel, direction,
                relevance, impact_score, confidence, horizon, path, evidence, origin, status, graph_edge_ids,
                agent_run_id, model, pipeline_version, created_at, updated_at)
               SELECT 'impact-' || id, article_id, 'instrument', instrument_id, name, symbol,
                      'legacy_unspecified', 'legacy_symbol_tag', direction, confidence,
                      CAST(confidence * 100 AS INTEGER), confidence, 'short', '[]', evidence,
                      'legacy', status, '[]', agent_run_id, model, 1, created_at, updated_at
               FROM news_article_symbol_tags"""
        )
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
        connection.execute(
            """UPDATE agent_configs
               SET runtime_mode=(SELECT runtime_mode FROM agent_configs WHERE id='news_tagger'),
                   provider_id=(SELECT provider_id FROM agent_configs WHERE id='news_tagger'),
                   model_route_id=(SELECT model_route_id FROM agent_configs WHERE id='news_tagger'),
                   updated_at=?
               WHERE id IN ('news_event_extractor','news_impact_mapper') AND created_at=?""",
            (agent_created_at, agent_created_at),
        )
        for template_type, template in AGENT_TEMPLATES.items():
            row = connection.execute("SELECT settings FROM agent_configs WHERE id=?", (template_type,)).fetchone()
            if not row:
                continue
            merged_settings = {**template["settings"], **json_value(row["settings"], {})}
            connection.execute(
                "UPDATE agent_configs SET settings=? WHERE id=?",
                (json.dumps(merged_settings, ensure_ascii=False), template_type),
            )
        ensure_column(connection, "admin_audit_logs", "before_state", "TEXT")
        ensure_column(connection, "admin_audit_logs", "after_state", "TEXT")
        ensure_column(connection, "watchlist_items", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "watchlist_items", "group_id", "TEXT")
        ensure_column(connection, "watchlist_items", "tags", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(connection, "watchlist_items", "note", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "watchlist_items", "sort_order", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(connection, "watchlist_items", "updated_at", "TEXT")
        connection.execute(
            """
            INSERT OR IGNORE INTO watchlist_groups (
                id, provider, name, color, is_default, sort_order, created_at, updated_at
            )
            SELECT 'default-' || provider, provider, '默认分组', 'blue', 1, 0,
                   MIN(created_at), MIN(created_at)
            FROM watchlist_items
            GROUP BY provider
            """
        )
        connection.execute(
            """
            UPDATE watchlist_items
            SET group_id = 'default-' || provider
            WHERE group_id IS NULL OR group_id = ''
            """
        )
        connection.execute(
            """
            UPDATE watchlist_items
            SET updated_at = created_at
            WHERE updated_at IS NULL OR updated_at = ''
            """
        )
        ensure_column(connection, "screener_presets", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "portfolio_positions", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "portfolio_positions", "instrument_id", "TEXT")
        ensure_column(connection, "portfolio_positions", "quantity_unit", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "portfolio_positions", "unit_multiplier", "REAL NOT NULL DEFAULT 1")
        connection.execute(
            """UPDATE portfolio_positions
               SET instrument_id = CASE
                   WHEN UPPER(symbol) = 'BTCUSD' THEN 'BINANCE_FUTURES:BTCUSDT'
                   WHEN UPPER(symbol) = 'ETHUSD' THEN 'BINANCE_FUTURES:ETHUSDT'
                   ELSE 'FINNHUB:' || UPPER(symbol)
               END
               WHERE instrument_id IS NULL OR instrument_id = ''"""
        )
        ensure_column(connection, "news_articles", "enabled", "INTEGER NOT NULL DEFAULT 1")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_articles_enabled_time ON news_articles(enabled, published_at DESC)"
        )
        ensure_column(connection, "news_sources", "category", "TEXT NOT NULL DEFAULT 'news'")
        ensure_column(connection, "news_sources", "adapter", "TEXT NOT NULL DEFAULT 'rss'")
        ensure_column(connection, "news_sources", "provider", "TEXT NOT NULL DEFAULT 'custom'")
        ensure_column(connection, "news_sources", "region", "TEXT NOT NULL DEFAULT 'global'")
        ensure_column(connection, "news_sources", "language", "TEXT NOT NULL DEFAULT 'en'")
        ensure_column(connection, "news_sources", "auth_type", "TEXT NOT NULL DEFAULT 'none'")
        ensure_column(connection, "news_sources", "api_key_env", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "news_sources", "capabilities", "TEXT NOT NULL DEFAULT '[\"news\"]'")
        ensure_column(connection, "news_sources", "config", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(connection, "news_sources", "priority", "INTEGER NOT NULL DEFAULT 50")
        ensure_column(connection, "news_sources", "reliability_weight", "REAL NOT NULL DEFAULT 0.8")
        ensure_column(connection, "news_sources", "builtin", "INTEGER NOT NULL DEFAULT 0")
        builtin_source_count = int(connection.execute(
            "SELECT COUNT(*) AS total FROM news_sources WHERE builtin = 1"
        ).fetchone()["total"])
        if builtin_source_count < len(BUILTIN_INFORMATION_SOURCES):
            source_created_at = datetime.now(timezone.utc).isoformat()
            for source in BUILTIN_INFORMATION_SOURCES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO news_sources (
                        name, type, category, adapter, provider, url, enabled, symbols, region, language,
                        auth_type, api_key_env, capabilities, config, priority, reliability_weight,
                        builtin, poll_interval_seconds, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, '', ?, ?, ?, ?, ?, '{}', ?, ?, 1, 900, ?)
                    """,
                    (
                        source["name"], source["type"], source["category"], source["adapter"], source["provider"],
                        source["url"], source["region"], source["language"], source["auth_type"], source["api_key_env"],
                        json.dumps(source["capabilities"], ensure_ascii=False), source["priority"],
                        source["reliability_weight"], source_created_at,
                    ),
                )
        connection.execute(
            """
            UPDATE news_sources
            SET adapter = 'adanos', auth_type = 'header'
            WHERE builtin = 1 AND provider = 'Adanos' AND adapter = 'reference'
            """
        )
        credential_created_at = datetime.now(timezone.utc).isoformat()
        legacy_providers = connection.execute("SELECT * FROM agent_provider_profiles").fetchall()
        for provider in legacy_providers:
            channel_id = provider["id"]
            route_id = f"route-{channel_id}"
            target_id = f"target-{channel_id}"
            connection.execute(
                """INSERT OR IGNORE INTO model_channels (
                    id, name, provider_type, protocol, base_url, api_key_env, timeout_seconds, config,
                    enabled, health_status, created_at, updated_at
                ) VALUES (?, ?, 'openai_compatible', 'openai_chat', ?, ?, ?, '{}', ?, 'unknown', ?, ?)""",
                (channel_id, provider["name"], provider["base_url"], provider["api_key_env"],
                 provider["timeout_seconds"], provider["enabled"], provider["created_at"], provider["updated_at"]),
            )
            connection.execute(
                """INSERT OR IGNORE INTO model_channel_models
                   (channel_id, model, source, capabilities, enabled, created_at, updated_at)
                   VALUES (?, ?, 'legacy', '["chat","json"]', 1, ?, ?)""",
                (channel_id, provider["model"], provider["created_at"], provider["updated_at"]),
            )
            connection.execute(
                """INSERT OR IGNORE INTO model_routes
                   (id, name, alias, description, temperature, max_tokens, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, '由旧模型服务自动迁移', ?, ?, ?, ?, ?)""",
                (route_id, provider["name"], f"legacy-{channel_id}", provider["temperature"],
                 provider["max_tokens"], provider["enabled"], provider["created_at"], provider["updated_at"]),
            )
            connection.execute(
                """INSERT OR IGNORE INTO model_route_targets
                   (id, route_id, channel_id, model, priority, weight, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, 100, 1, ?, ?)""",
                (target_id, route_id, channel_id, provider["model"], provider["created_at"], provider["updated_at"]),
            )
            connection.execute(
                "UPDATE agent_configs SET model_route_id = ? WHERE provider_id = ? AND model_route_id IS NULL",
                (route_id, channel_id),
            )
        credential_resources: list[tuple[str, str, str, str]] = [
            ("market_provider", "finnhub", "Finnhub 行情", "FINNHUB_API_KEY"),
            ("data_source", "market:finnhub", "Finnhub 行情", "FINNHUB_API_KEY"),
            ("data_source", "indicator:alphavantage", "Alpha Vantage 指标", "ALPHAVANTAGE_API_KEY"),
            ("data_source", "indicator:fred", "FRED 宏观序列", "FRED_API_KEY"),
            ("data_source", "indicator:adanos", "Adanos 市场情绪", "ADANOS_API_KEY"),
            ("data_source", "calendar:trading-economics", "Trading Economics 财经日历", "TRADING_ECONOMICS_API_KEY"),
        ]
        credential_resources.extend(
            ("information_source", str(row["id"]), row["name"], row["api_key_env"])
            for row in connection.execute(
                "SELECT id, name, api_key_env FROM news_sources WHERE api_key_env != ''"
            ).fetchall()
        )
        credential_resources.extend(
            ("agent_provider", row["id"], row["name"], row["api_key_env"])
            for row in connection.execute(
                "SELECT id, name, api_key_env FROM agent_provider_profiles WHERE api_key_env != ''"
            ).fetchall()
        )
        credential_resources.extend(
            ("model_channel", row["id"], row["name"], row["api_key_env"])
            for row in connection.execute(
                "SELECT id, name, api_key_env FROM model_channels WHERE api_key_env != ''"
            ).fetchall()
        )
        for resource_type, resource_id, resource_name, env_alias in credential_resources:
            credential_id = f"credential-{sha256(env_alias.encode('utf-8')).hexdigest()[:16]}"
            connection.execute(
                """
                INSERT OR IGNORE INTO api_credentials (
                    id, name, env_alias, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (credential_id, env_alias.replace("_API_KEY", "").replace("_", " ").title(), env_alias, credential_created_at, credential_created_at),
            )
            connection.execute(
                """
                INSERT INTO credential_bindings (
                    credential_id, resource_type, resource_id, resource_name, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(resource_type, resource_id) DO UPDATE SET
                    credential_id = excluded.credential_id,
                    resource_name = excluded.resource_name
                """,
                (credential_id, resource_type, resource_id, resource_name, credential_created_at),
            )
        for table in ("backtest_runs", "forward_test_runs", "walk_forward_runs", "strategy_validation_runs", "stress_test_runs"):
            ensure_column(connection, table, "request_payload", "TEXT")
        connection.execute(
            "UPDATE app_config SET value = 'coinbase', updated_at = ? WHERE key = 'active_market_provider' AND value = 'mock'",
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute("DELETE FROM watchlist_items WHERE provider = 'mock'")
        connection.execute("DELETE FROM watchlist_groups WHERE provider = 'mock'")
        connection.execute("DELETE FROM market_data_cache WHERE provider = 'mock'")
        connection.execute("DELETE FROM indicator_data_cache WHERE lower(source) LIKE '%mock%'")
        active_provider_row = connection.execute(
            "SELECT value FROM app_config WHERE key = 'active_market_provider'"
        ).fetchone()
        ensure_data_source_schema(connection, active_provider_row["value"] if active_provider_row else "coinbase")
        from app.modules.calendar.service import ensure_financial_calendar_schema, set_database_path as set_calendar_database_path
        set_calendar_database_path(DB_PATH)
        ensure_financial_calendar_schema(connection)


def init_db() -> None:
    database_key = os.path.abspath(os.fspath(DB_PATH))
    if database_key in _INITIALIZED_DB_PATHS:
        set_data_source_database_path(DB_PATH)
        from app.modules.calendar.service import set_database_path as set_calendar_database_path
        set_calendar_database_path(DB_PATH)
        return
    with _DB_INIT_LOCK:
        if database_key in _INITIALIZED_DB_PATHS:
            set_data_source_database_path(DB_PATH)
            from app.modules.calendar.service import set_database_path as set_calendar_database_path
            set_calendar_database_path(DB_PATH)
            return
        _initialize_database()
        _INITIALIZED_DB_PATHS.add(database_key)
