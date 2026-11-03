from pathlib import Path
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from types import SimpleNamespace

from app.services import runtime as main


def workspace_javascript() -> str:
    """Return the complete native-module workspace source for static contracts."""

    root = Path("app/web/static/workspace")
    paths = [*sorted(root.joinpath("core").glob("*.js")), *sorted(root.joinpath("features").glob("*.js")), root / "bootstrap.js"]
    return "\n".join(path.read_text() for path in paths)


def workspace_styles() -> str:
    root = Path("app/web/static/styles/workspace")
    return "\n".join(path.read_text() for path in sorted(root.glob("*.css")))
from app.modules.ai import service as ai_service
from app.modules.market import service as market_service
from app.modules.news import service as news_service
from app.modules.platform import service as platform_service


class FixtureMarketProvider:
    name = "coinbase"
    display_name = "Test market provider"
    rows = [
        ("AAPL", "Apple Inc.", "NASDAQ", 212.44), ("NVDA", "NVIDIA Corp.", "NASDAQ", 154.31),
        ("TSLA", "Tesla Inc.", "NASDAQ", 328.52), ("MSFT", "Microsoft Corp.", "NASDAQ", 498.18),
        ("BTCUSD", "Bitcoin / USD", "Crypto", 108420.0), ("ETHUSD", "Ether / USD", "Crypto", 3890.2),
        ("SPY", "S&P 500 ETF", "NYSE Arca", 612.84), ("QQQ", "Nasdaq 100 ETF", "NASDAQ", 541.63),
    ]

    def list_symbols(self):
        return [main.MarketSymbol(
            symbol=symbol, name=name, asset_class="crypto" if market == "Crypto" else "etf" if symbol in {"SPY", "QQQ"} else "stock",
            market=market, currency="USD", active=True,
        ) for symbol, name, market, _price in self.rows]

    def watchlist(self):
        return [main.SymbolSnapshot(
            symbol=symbol, name=name, market=market, price=price, change_percent=1.0,
            volume="1.0M", signal="buy", rsi=58.0, macd=0.5, trend_score=65,
        ) for symbol, name, market, price in self.rows]

    def candles(self, symbol, interval, limit):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [main.Candle(
            time=(start + timedelta(hours=index)).isoformat(), open=100 + index, high=102 + index,
            low=99 + index, close=101 + index, volume=1_000 + index,
            ma20=100 + index, ma60=99 + index, rsi=58,
        ) for index in range(limit)]

    def indicators(self, symbol):
        return [main.IndicatorSummary(label=label, value="1", state="flat", detail="test fixture")
                for label in ("RSI 14", "MACD", "波动率", "趋势结构")]

    def status(self, is_active):
        return main.MarketProviderStatus(
            name=self.name, display_name=self.display_name, is_active=is_active, is_realtime=False,
            is_delayed=True, status="online", coverage=["test fixtures"], capabilities=["test"],
            message="Isolated test provider", updated_at=datetime.now(timezone.utc).isoformat(),
        )


def test_extract_symbols_uses_token_boundaries():
    assert main.extract_symbols("Bitcoin and Ethereum rally", "BTC and ETH gain", []) == ["BTCUSD", "ETHUSD"]
    assert main.extract_symbols("U.S. CPI cools", "Whether the Fed cuts rates remains unclear", []) == []
    assert main.extract_symbols("Apple launches product", "AAPL shares rise", []) == ["AAPL"]


def test_authentication_roles_and_admin_resource_catalog(isolated_app_db):
    assert main.auth_setup_required() is True
    admin = main.create_initial_admin(
        main.SetupRequest(username="admin", display_name="Administrator", password="strong-password")
    )
    assert admin.role == "admin"
    assert main.auth_setup_required() is False
    assert main.authenticate_user("admin", "strong-password").id == admin.id
    assert main.authenticate_user("admin", "wrong-password") is None

    token, session = main.create_auth_session(admin)
    assert session.csrf_token
    assert session.preferences.theme == "system"
    assert main.lookup_auth_session(token)[0].id == admin.id
    saved_preferences = main.set_user_preferences(admin.id, main.UserPreferencesUpdate(theme="light"))
    assert saved_preferences.theme == "light"
    assert main.create_auth_session(admin)[1].preferences.theme == "light"

    request = SimpleNamespace(state=SimpleNamespace(user=admin))
    viewer = main.admin_create_user(
        main.UserCreate(username="observer", display_name="Observer", password="observer-password", role="viewer"),
        request,
    )
    assert viewer.role == "viewer"

    async def downstream(_request):
        return main.JSONResponse({"ok": True})

    def middleware_request(method: str, cookie_token: str | None = None, csrf: str | None = None):
        headers = []
        if cookie_token:
            headers.append((b"cookie", f"{main.SESSION_COOKIE}={cookie_token}".encode()))
        if csrf:
            headers.append((b"x-csrf-token", csrf.encode()))
        return main.Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": "/api/settings",
                "raw_path": b"/api/settings",
                "query_string": b"",
                "headers": headers,
                "client": ("127.0.0.1", 1),
                "server": ("test", 80),
            }
        )

    assert asyncio.run(main.authentication_middleware(middleware_request("GET"), downstream)).status_code == 401
    assert asyncio.run(main.authentication_middleware(middleware_request("PATCH", token), downstream)).status_code == 403
    assert asyncio.run(main.authentication_middleware(middleware_request("PATCH", token, session.csrf_token), downstream)).status_code == 200
    viewer_token, viewer_session = main.create_auth_session(viewer)
    assert asyncio.run(main.authentication_middleware(middleware_request("PATCH", viewer_token, viewer_session.csrf_token), downstream)).status_code == 403
    with pytest.raises(main.HTTPException) as error:
        main.admin_update_user(admin.id, main.UserUpdate(enabled=False), request)
    assert error.value.status_code == 409

    resources = main.list_admin_resources()
    resource_ids = {item.id for item in resources}
    assert {"users", "backtests", "events", "admin-audit"} <= resource_ids
    assert {"news-sources", "factors", "strategies"}.isdisjoint(resource_ids)
    factor_descriptor = main.admin_resource_descriptor("factors")
    assert factor_descriptor.management_mode == "dedicated"
    assert factor_descriptor.canonical_href == "/factors"
    factor_page = main.query_admin_resource("factors", query="momentum")
    assert factor_page.total >= 1
    assert factor_page.items[0]["_resource_id"]
    with pytest.raises(main.HTTPException) as dedicated_error:
        main.admin_resource_status("factors", "momentum-news", main.ResourceStatusUpdate(enabled=False), request)
    assert dedicated_error.value.status_code == 409
    assert dedicated_error.value.detail["canonical_href"] == "/factors"
    audit = main.query_admin_resource("admin-audit")
    assert audit.total >= 2


@pytest.fixture()
def isolated_app_db(tmp_path: Path, monkeypatch):
    original_db = main.DB_PATH
    monkeypatch.setitem(main.MARKET_PROVIDERS, "coinbase", FixtureMarketProvider())
    main.DB_PATH = tmp_path / "test_app_data.db"
    main.init_db()
    main.set_automation_settings(main.AutomationSettings(enabled=False, interval_seconds=300))
    main.update_market_provider(main.MarketProviderConfig(provider="coinbase"))
    with main.db_connect() as connection:
        connection.execute("DELETE FROM data_source_route_members WHERE route_id = 'US:snapshot'")
        connection.execute(
            "INSERT INTO data_source_route_members (route_id, source_id, position, role, enabled) VALUES ('US:snapshot', 'market:coinbase', 0, 'primary', 1)"
        )
    yield
    main.DB_PATH = original_db


def test_market_provider_and_core_market_data(isolated_app_db):
    options = main.market_provider_options()
    assert {item.name for item in options} == {"binance", "coinbase", "finnhub"}
    assert main.market_provider_status().name == "coinbase"
    assert len(main.symbols()) >= 20
    assert {"SPX", "NDX", "SHCOMP"} <= {item.symbol for item in main.symbols()}
    assert len(main.watchlist()) == 8
    assert len(main.candles("AAPL", interval="15m", limit=40)) == 40
    assert len(main.indicators("AAPL")) == 4


def test_professional_chart_catalog_derivatives_layouts_and_alerts(isolated_app_db, monkeypatch):
    from app.modules.market import charting

    monkeypatch.setenv("APP_DB_PATH", str(main.DB_PATH))
    base_time = 1_700_000_000_000

    def fake_chart_json(url: str):
        if "/fapi/v1/klines" in url:
            return [
                [
                    base_time + index * 900_000,
                    100 + index,
                    102 + index,
                    99 + index,
                    101 + index,
                    1000 + index,
                    0,
                    100_000 + index,
                    100,
                    600 + index,
                    0,
                    0,
                ]
                for index in range(180)
            ]
        if "openInterestHist" in url:
            return [
                {"timestamp": base_time + index * 900_000, "sumOpenInterest": str(10_000 + index * 5)}
                for index in range(120)
            ]
        if "globalLongShortAccountRatio" in url or "topLongShort" in url:
            return [
                {"timestamp": base_time + index * 900_000, "longShortRatio": "1.12"}
                for index in range(120)
            ]
        if "fundingRate" in url:
            return [
                {"fundingTime": base_time + index * 28_800_000, "fundingRate": "0.0001"}
                for index in range(20)
            ]
        if "/basis" in url:
            return [
                {"timestamp": base_time + index * 900_000, "basisRate": "0.0002"}
                for index in range(120)
            ]
        raise AssertionError(url)

    monkeypatch.setattr(charting, "_http_json", fake_chart_json)
    instruments = charting.list_instruments()
    assert {item.id for item in instruments} >= {
        "INDEX:SHCOMP", "INDEX:SPX", "INDEX:NDX", "BINANCE_FUTURES:BTCUSDT",
    }
    assert len([item for item in instruments if item.asset_class == "index"]) == 12

    catalog = charting.indicator_catalog("BINANCE_FUTURES:BTCUSDT")
    assert {item.id for item in catalog} >= {
        "ema", "bbands", "macd", "rsi", "adx", "open_interest",
        "global_long_short_ratio", "taker_buy_sell_ratio", "funding_rate", "basis",
    }
    index_catalog = charting.indicator_catalog("INDEX:SPX")
    assert next(item for item in index_catalog if item.id == "open_interest").available is False

    query = main.ChartQuery(
        instrument_id="BINANCE_FUTURES:BTCUSDT",
        interval="15m",
        range="1M",
        indicators=[
            main.IndicatorConfig(id="ema", parameters={"length": 20}),
            main.IndicatorConfig(id="bbands", parameters={"length": 20}),
            main.IndicatorConfig(id="macd"),
            main.IndicatorConfig(id="open_interest"),
            main.IndicatorConfig(id="taker_buy_sell_ratio"),
        ],
    )
    bundle = charting.build_chart_bundle(query)
    assert len(bundle.candles) == 180
    assert {item.id for item in bundle.series} >= {
        "ema", "bbands", "macd:hist", "open_interest", "taker_buy_sell_ratio",
    }
    taker = next(item for item in bundle.series if item.id == "taker_buy_sell_ratio")
    expected_ratio = bundle.candles[-1].taker_buy_volume / (
        bundle.candles[-1].volume - bundle.candles[-1].taker_buy_volume
    )
    assert taker.current_value == pytest.approx(expected_ratio, rel=1e-5)
    assert bundle.warnings == []

    series_bundle = charting.build_chart_series(main.ChartSeriesQuery(
        instrument_id="BINANCE_FUTURES:BTCUSDT",
        interval="15m",
        range="1M",
        indicators=[main.IndicatorConfig(
            id="rsi", instance_id="rsi-test-instance", parameters={"length": 14},
        )],
    ))
    assert series_bundle.revision
    assert {item.owner_id for item in series_bundle.series} == {"rsi-test-instance"}
    assert series_bundle.status_by_indicator == {"rsi-test-instance": "ready"}

    first_key = charting._cache_key(
        charting.resolve_instrument("BINANCE_FUTURES:BTCUSDT"), query,
        datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
    )
    later_key = charting._cache_key(
        charting.resolve_instrument("BINANCE_FUTURES:BTCUSDT"), query,
        datetime(2026, 8, 3, 10, 5, tzinfo=timezone.utc),
    )
    assert first_key == later_key

    with pytest.raises(Exception) as invalid_time_selection:
        charting.build_chart_bundle(
            main.ChartQuery(
                instrument_id="BINANCE_FUTURES:BTCUSDT",
                interval="1mo",
                range="1D",
                indicators=[],
            )
        )
    assert getattr(invalid_time_selection.value, "status_code", None) == 422
    assert "3Y" in str(getattr(invalid_time_selection.value, "detail", ""))
    with pytest.raises(Exception) as invalid_layout:
        charting.create_chart_layout(
            "chart-user",
            main.ChartLayoutCreate(
                name="无效月线布局",
                asset_class="crypto_perpetual",
                interval="1mo",
                range="1D",
                indicators=[],
            ),
        )
    assert getattr(invalid_layout.value, "status_code", None) == 422

    layout = charting.create_chart_layout(
        "chart-user",
        main.ChartLayoutCreate(
            name="合约盯盘",
            asset_class="crypto_perpetual",
            is_default=True,
            interval="15m",
            range="1M",
            indicators=query.indicators,
        ),
    )
    assert layout.is_default is True
    assert charting.list_chart_layouts("chart-user", "crypto_perpetual")[0].id == layout.id
    updated_layout = charting.update_chart_layout(
        "chart-user", layout.id, main.ChartLayoutUpdate(interval="1h")
    )
    assert updated_layout.interval == "1h"

    alert = main.create_alert(
        main.AlertRuleCreate(
            symbol="BINANCE_FUTURES:BTCUSDT",
            name="RSI 合约预警",
            metric="rsi",
            interval="15m",
            parameters={"length": 14},
            operator=">=",
            threshold=50,
        )
    )
    assert alert.interval == "15m"
    assert alert.parameters == {"length": 14}
    evaluation = main.evaluate_alert_rule(alert)
    assert evaluation.current_value > 0


def test_professional_chart_frontend_contract():
    html = Path("app/web/pages/index.html").read_text()
    app_javascript = workspace_javascript()
    javascript = Path("app/web/static/workspace/professional-chart.js").read_text()
    styles = workspace_styles()
    assert "lightweight-charts.standalone.production.js" not in html
    assert 'loadScriptOnce("/static/vendor/lightweight-charts.standalone.production.js")' in app_javascript
    assert 'loadScriptOnce("/static/workspace/professional-chart.js?v=20260909.2")' in app_javascript
    assert 'id="chartRanges" class="segmented chart-segment-scroll chart-range-selector"' in html
    assert 'id="chartIntervals"' in html
    assert 'id="chartRanges"' in html
    assert "K线周期" in html
    assert 'data-chart-interval="1mo" type="button">1月' in html
    assert 'id="indicatorDrawer"' in html
    assert "createSeriesMarkers" in javascript
    assert "autoScale: false" in javascript
    assert 'position: "inBar"' in javascript
    assert 'shape: "circle"' in javascript
    assert 'shape: marker.sentiment === "bearish"' not in javascript
    assert 'news_mode: chartState.newsMode' in javascript
    assert 'id="newsModeMenu"' in html
    assert "news-event-pane-legend" in styles
    assert "mountPaneLegends" in javascript
    assert "renderPaneLegends" in javascript
    assert "pane.getHTMLElement" in javascript
    assert "indicator-pane-legend" in styles
    assert 'id="positionInstrumentResults"' in html
    assert 'id="positionQuantityUnit"' in html
    assert 'id="positionQuoteStatus"' in html
    assert "selectPositionInstrument" in app_javascript
    assert "/quote`" in app_javascript
    assert "setPortfolioPositions" in javascript
    assert "chart-position-zone" in styles
    assert "/api/chart/layouts" in javascript
    assert "/api/chart/series/query" in javascript
    assert "include_series: includeSeries" in javascript
    assert "AbortController" in javascript
    assert "removeSeries" in javascript
    assert "moveToPane" in javascript
    assert "loadIndicatorSeries({ configs: [config], replaceAll: false })" in javascript
    assert "BINANCE_FUTURES" not in javascript
    assert "minimumRangeByInterval" in javascript
    assert "intervalRangeCompatible" in javascript
    assert "reconcileTimeSelection" not in javascript
    assert "timeFormatter: formatTimeScaleLabel" in javascript
    assert "tickMarkFormatter: formatTickMark" in javascript
    assert 'chartState.instrument?.timezone || "UTC"' in javascript
    assert 'reason === "next_trading_candle"' in javascript
    assert "function accessibleChartColor" in javascript
    assert 'handleScroll: { mouseWheel: false' in javascript
    assert 'handleScale: { axisPressedMouseMove: true, mouseWheel: false' in javascript
    assert "if (!event.altKey) return" in javascript
    assert 'addEventListener("wheel", chartState.altWheelHandler, { passive: false })' in javascript
    assert "luminance(channels)" in javascript
    assert "lightTheme ? channel * 0.82 : channel + (255 - channel) * 0.18" in javascript
    assert "布局“${layout.name}”未应用" in javascript
    assert ".segmented button:disabled" in styles
    assert "#priceChart td" in styles
    assert "#priceChart table" in styles


def test_market_symbol_switch_uses_lightweight_view_loading():
    javascript = workspace_javascript()
    professional_javascript = Path("app/web/static/workspace/professional-chart.js").read_text()
    styles = workspace_styles()
    select_symbol = javascript.split("function selectSymbol", 1)[1].split("async function loadWatchlist", 1)[0]
    market_context = javascript.split("function loadMarketSymbolContext", 1)[1].split("function loadNewsSymbolContext", 1)[0]

    assert "beginSymbolRequest" in select_symbol
    assert "ProfessionalChart.selectLegacySymbol" in select_symbol
    assert "loadSymbolDataForView" in select_symbol
    assert "/api/backtests/" not in select_symbol
    assert "/api/stress-tests/" not in select_symbol
    assert "/api/candles/" not in select_symbol
    assert "/api/indicators/" not in select_symbol
    assert market_context.count("loadSymbolResource(") == 5
    assert "/api/indicator-snapshots/" in market_context
    assert "symbolRequestController?.abort()" in javascript
    assert "isCurrentSymbolRequest" in javascript
    assert 'if (viewName === "market") return loadMarketView()' in javascript
    market_loader = javascript.split("async function loadMarketView", 1)[1].split("function loadViewData", 1)[0]
    assert "const requestedSymbol = state.activeSymbol" in market_loader
    assert "selectLegacySymbol(requestedSymbol)" in market_loader
    assert 'factors: [loadFactorDefinitions(), loadResearchLibraries()]' in javascript
    market_strip = javascript.split("function renderMarketStrip", 1)[1].split("function renderWatchlist", 1)[0]
    assert "state.filtered" not in market_strip
    assert "[current, ...state.symbols]" in market_strip
    assert ".slice(0, 4)" not in market_strip
    assert "ticker-card-metrics" in market_strip
    assert "marketStripCount" in market_strip
    assert "function updateMarketStripControls" in market_strip
    assert "scroll-snap-type: x mandatory" in styles
    assert "function openSymbolInMarket" in javascript
    legacy_switch = professional_javascript.split("async function selectLegacySymbol", 1)[1].split("async function loadLayouts", 1)[0]
    assert "/api/instruments?query=" in legacy_switch
    assert "renderInstrumentOptions()" in legacy_switch
    assert ".ticker-tile.active" in styles


def test_professional_news_summary_is_not_cleared_by_legacy_mouseleave_handlers():
    javascript = workspace_javascript()
    price_leave = javascript.split('$("#priceChart").addEventListener("mouseleave"', 1)[1].split("});", 1)[0]
    tooltip_leave = javascript.split('$("#eventTooltip").addEventListener("mouseleave"', 1)[1].split("});", 1)[0]

    assert "if (window.ProfessionalChart) return;" in price_leave
    assert "if (window.ProfessionalChart) return;" in tooltip_leave


def test_chart_news_markers_group_and_map_market_gaps(isolated_app_db, monkeypatch):
    from app.api.routes import charting as chart_routes

    candles = [
        main.ChartCandle(time=1_700_000_000, open=100, high=102, low=99, close=101, volume=1000),
        main.ChartCandle(time=1_700_086_400, open=101, high=103, low=100, close=102, volume=1100),
        main.ChartCandle(time=1_700_345_600, open=102, high=105, low=101, close=104, volume=1200),
    ]
    bundle = main.ChartBundle(
        instrument=main.MarketInstrument(
            id="NASDAQ:AAPL", symbol="AAPL", provider_symbol="AAPL", name="Apple Inc.",
            asset_class="stock", market="NASDAQ", currency="USD", provider="finnhub",
            timezone="America/New_York", realtime=False, delayed=False, intervals=["1d"],
        ),
        interval="1d",
        range="1M",
        candles=candles,
        series=[],
            status=main.ChartDataStatus(
                provider="finnhub", provider_label="Finnhub", mode="delayed", fetched_at="2026-08-02T00:00:00Z",
            ),
    )
    events = [
        main.NewsEvent(
            id="weekend-positive", symbol="AAPL", title="Weekend upgrade", summary="Target raised",
            source="Test", url="https://example.com/positive",
            published_at="2023-11-17T08:00:00+00:00", event_type="analyst", sentiment="bullish",
            impact_score=80, price_reaction="pending",
        ),
        main.NewsEvent(
            id="weekend-negative", symbol="AAPL", title="Weekend risk", summary="New investigation",
            source="Test", url="https://example.com/negative",
            published_at="2023-11-17T09:00:00+00:00", event_type="regulatory", sentiment="bearish",
            impact_score=75, price_reaction="pending",
        ),
        main.NewsEvent(
            id="low-impact", symbol="AAPL", title="Minor note", summary="No material change",
            source="Test", url="https://example.com/minor",
            published_at="2023-11-17T10:00:00+00:00", event_type="analyst", sentiment="neutral",
            impact_score=40, price_reaction="pending",
        ),
        main.NewsEvent(
            id="manual-low-impact", symbol="AAPL", title="Manually linked note", summary="Reviewed association",
            source="Test", url="https://example.com/manual",
            published_at="2023-11-17T11:00:00+00:00", event_type="analyst", sentiment="neutral",
            impact_score=35, price_reaction="pending", manual_association=True,
        ),
    ]
    monkeypatch.setattr(chart_routes, "news_events", lambda symbol, limit: events)

    result = chart_routes._attach_news_markers(bundle, "key")

    assert len(result.markers) == 1
    marker = result.markers[0]
    assert marker.time == candles[2].time
    assert marker.count == 3
    assert marker.aggregate_sentiment == "mixed"
    assert marker.mapping_reason == "next_trading_candle"
    assert {event.mapping_reason for event in marker.events} == {"next_trading_candle"}
    assert {event.id for event in marker.events} == {"weekend-positive", "weekend-negative", "manual-low-impact"}
    assert marker.id == f"news:NASDAQ:AAPL:1d:{candles[2].time}"


def test_chart_news_mapping_reasons_and_timezone_normalization():
    from app.api.routes import charting as chart_routes

    candle_times = [1_700_000_000, 1_700_000_900, 1_700_003_600]
    assert chart_routes._event_candle_mapping(candle_times, candle_times[0], "15m") == (0, "exact")
    assert chart_routes._event_candle_mapping(candle_times, candle_times[0] + 300, "15m") == (0, "within_candle")
    assert chart_routes._event_candle_mapping(candle_times, candle_times[1] + 1200, "15m") == (2, "next_trading_candle")
    assert chart_routes._event_timestamp("2023-11-17T08:00:00") == chart_routes._event_timestamp("2023-11-17T08:00:00+00:00")


def test_professional_chart_cache_preserves_fallback_provenance(isolated_app_db, monkeypatch):
    from app.modules.market import charting

    monkeypatch.setenv("APP_DB_PATH", str(main.DB_PATH))

    def fake_fallback_json(url: str):
        if "fapi.binance.com" in url:
            raise RuntimeError("HTTP 451")
        if "api.exchange.coinbase.com" in url:
            return [
                [1_700_000_000 + index * 900, 99 + index, 102 + index, 100 + index, 101 + index, 1000]
                for index in range(180)
            ]
        raise AssertionError(url)

    monkeypatch.setattr(charting, "_http_json", fake_fallback_json)
    query = main.ChartQuery(
        instrument_id="BINANCE_FUTURES:BTCUSDT",
        interval="15m",
        range="1M",
        indicators=[],
    )
    initial = charting.build_chart_bundle(query)
    cached = charting.build_chart_bundle(query)

    assert initial.status.provider == "coinbase_fallback"
    assert initial.warnings and "Binance Futures" in initial.warnings[0]
    assert cached.status.provider == "coinbase_fallback"
    assert cached.status.mode == "cached"
    assert cached.warnings == initial.warnings
    assert cached.series == []


def test_finnhub_market_provider_normalizes_quotes_and_candles(isolated_app_db, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "server-only-key")

    def fake_fetch(url: str, timeout: int = 10):
        assert "server-only-key" in url
        if "/quote?" in url:
            return {"c": 210.5, "dp": 1.25}
        if "/stock/candle?" in url:
            timestamps = list(range(1_720_000_000, 1_720_000_000 + 45 * 3600, 3600))
            return {
                "s": "ok",
                "t": timestamps,
                "o": [200 + index for index in range(45)],
                "h": [202 + index for index in range(45)],
                "l": [199 + index for index in range(45)],
                "c": [201 + index for index in range(45)],
                "v": [1_000_000 + index for index in range(45)],
            }
        raise AssertionError(url)

    monkeypatch.setattr(market_service, "fetch_json_url", fake_fetch)
    provider = main.MARKET_PROVIDERS["finnhub"]
    assert provider.status(is_active=True).status == "online"
    assert len(provider.list_symbols()) == 6
    assert provider.watchlist()[0].price == 210.5
    candles = provider.candles("AAPL", interval="1h", limit=40)
    assert len(candles) == 40
    assert candles[-1].close == 245
    assert len(provider.indicators("AAPL")) == 4


def test_encrypted_credential_override_is_shared_and_hot_reloadable(isolated_app_db, monkeypatch):
    master_key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_CREDENTIAL_MASTER_KEY", master_key)
    monkeypatch.setenv("APP_CREDENTIAL_ASYNC_TESTS", "false")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)

    credential_id = main.credential_id_for_resource("market_provider", "finnhub")
    credential = main.get_credential(credential_id)
    assert credential.binding_count == 3
    assert any(
        binding.resource_type == "data_source" and binding.resource_id == "market:finnhub"
        for binding in credential.bindings
    )
    assert credential.origin == "none"

    updated = main.set_resource_secret(
        "market_provider", "finnhub", "Finnhub 行情", "FINNHUB_API_KEY", "new-finnhub-secret"
    )
    assert updated.origin == "database"
    assert updated.secret_hint.endswith("cret")
    assert main.resolve_api_key("market_provider", "finnhub", "FINNHUB_API_KEY") == "new-finnhub-secret"

    source = next(item for item in main.list_news_sources() if item.provider == "Finnhub")
    assert source.credential_id == updated.id
    assert source.credential_origin == "database"
    assert main.resolve_api_key("information_source", str(source.id), source.api_key_env) == "new-finnhub-secret"

    with main.db_connect() as connection:
        row = connection.execute(
            "SELECT encrypted_value FROM api_credentials WHERE id = ?", (updated.id,)
        ).fetchone()
    assert row["encrypted_value"]
    assert "new-finnhub-secret" not in row["encrypted_value"]
    assert "new-finnhub-secret" not in updated.model_dump_json()

    monkeypatch.setenv("APP_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode())
    assert main.get_credential(updated.id).origin == "error"
    with pytest.raises(RuntimeError):
        main.resolve_api_key("market_provider", "finnhub", "FINNHUB_API_KEY")

    monkeypatch.setenv("APP_CREDENTIAL_MASTER_KEY", master_key)
    monkeypatch.setenv("FINNHUB_API_KEY", "environment-fallback")
    restored = main.remove_credential_secret(updated.id)
    assert restored.origin == "environment"
    assert main.resolve_api_key("market_provider", "finnhub", "FINNHUB_API_KEY") == "environment-fallback"


def test_agent_provider_inline_secret_is_encrypted(isolated_app_db, monkeypatch):
    monkeypatch.setenv("APP_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_CREDENTIAL_ASYNC_TESTS", "false")
    provider = main.create_agent_provider(main.AgentProviderCreate(
        name="Encrypted Gateway",
        base_url="https://models.example.test/v1",
        model="test-model",
        api_key_env="ENCRYPTED_AGENT_API_KEY",
        api_key="agent-secret-value",
    ))
    assert provider.secret_configured is True
    assert provider.credential_origin == "database"
    assert "agent-secret-value" not in provider.model_dump_json()
    assert main.resolve_api_key("agent_provider", provider.id, provider.api_key_env) == "agent-secret-value"

    updated = main.update_agent_provider(
        provider.id,
        main.AgentProviderUpdate(api_key="rotated-agent-value"),
    )
    assert updated.credential_id == provider.credential_id
    assert main.resolve_api_key("agent_provider", provider.id, provider.api_key_env) == "rotated-agent-value"


def test_app_settings_persist_filters_and_automation(isolated_app_db):
    settings = main.app_settings()
    assert settings.active_market_provider == "coinbase"
    assert settings.filters.signal_filter == "all"

    updated = main.patch_app_settings(
        main.AppSettingsUpdate(
            signal_filter="buy",
            rsi_min=35,
            positive_only=True,
            automation_enabled=True,
            automation_interval_seconds=120,
        )
    )

    assert updated.filters.signal_filter == "buy"
    assert updated.filters.rsi_min == 35
    assert updated.filters.positive_only is True
    assert updated.automation.enabled is True
    assert updated.automation.interval_seconds == 120


def test_persisted_watchlist_management(isolated_app_db):
    assert len(main.watchlist()) == 8

    main.remove_watchlist_item("AAPL")
    symbols_after_remove = {item.symbol for item in main.watchlist()}
    assert "AAPL" not in symbols_after_remove
    assert len(symbols_after_remove) == 7

    added = main.add_watchlist_item(main.WatchlistItemCreate(symbol="AAPL"))
    assert added.symbol == "AAPL"
    assert "AAPL" in {item.symbol for item in main.watchlist()}


def test_public_index_catalog_and_watchlist_support(isolated_app_db, monkeypatch):
    from app.api.routes.market_news import symbols as list_symbols_api
    from app.services import runtime

    catalog = list_symbols_api()
    indexes = {item.symbol: item for item in catalog if item.asset_class == "index"}
    assert {"SHCOMP", "CSI300", "SPX", "NDX", "HSI"} <= set(indexes)

    provider = runtime.PUBLIC_INDEX_PROVIDER
    monkeypatch.setattr(provider, "_snapshot", lambda symbol: main.SymbolSnapshot(
        symbol=symbol, name=indexes[symbol].name, market=indexes[symbol].market,
        price=6500.25, change_percent=0.8, volume="1.2B", signal="buy",
        rsi=58.0, macd=12.5, trend_score=64,
    ))
    snapshot = runtime.add_watchlist_symbol(main.WatchlistItemCreate(symbol="SPX"))
    assert snapshot.name == "标普500"
    assert "SPX" in {item.symbol for item in runtime.list_watchlist_snapshots()}
    resource = next(item for item in runtime.list_watchlist_resources(query="SPX").items if item.symbol == "SPX")
    assert resource.provider == "public_index"
    assert resource.group_name == "市场指数"

    with runtime.db_connect() as connection:
        row = connection.execute(
            "SELECT instrument_id FROM watchlist_items WHERE provider='public_index' AND symbol='SPX'"
        ).fetchone()
    assert row["instrument_id"] == "INDEX:SPX"

    workspace = Path("app/web/pages/index.html").read_text()
    application = workspace_javascript()
    professional_chart = Path("app/web/static/workspace/professional-chart.js").read_text()
    assert 'id="watchlistInstrumentResults"' in workspace
    assert 'id="chartInstrumentResults"' in workspace
    assert "data-watchlist-instrument" in application
    assert "data-chart-instrument-id" in professional_chart


def test_watchlist_resource_groups_metadata_and_soft_restore(isolated_app_db):
    default_group = next(group for group in main.list_watchlist_groups() if group.is_default)
    research_group = main.create_watchlist_group(main.WatchlistGroupCreate(name="重点研究", color="purple"))

    updated = main.update_watchlist_resource(
        "AAPL",
        main.WatchlistResourceUpdate(
            group_id=research_group.id,
            tags=["Earnings", "earnings", "AI"],
            note="等待财报确认趋势",
        ),
    )
    assert updated.group_id == research_group.id
    assert updated.tags == ["Earnings", "AI"]
    assert updated.note == "等待财报确认趋势"

    filtered = main.list_watchlist_resources(group_id=research_group.id, tag="ai", query="财报")
    assert filtered.total == 1
    assert filtered.items[0].symbol == "AAPL"

    result = main.bulk_update_watchlist(main.WatchlistBulkAction(symbols=["AAPL"], action="disable"))
    assert result.updated == 1
    assert main.list_watchlist_resources(status="disabled", query="AAPL").items[0].enabled is False
    assert "AAPL" not in {item.symbol for item in main.watchlist()}

    main.bulk_update_watchlist(main.WatchlistBulkAction(symbols=["AAPL"], action="restore"))
    assert main.list_watchlist_resources(status="active", query="AAPL").items[0].enabled is True

    deleted = main.delete_watchlist_group(research_group.id)
    assert deleted["moved_items"] == 1
    restored = main.list_watchlist_resources(status="all", query="AAPL").items[0]
    assert restored.group_id == default_group.id
    with pytest.raises(main.HTTPException) as error:
        main.delete_watchlist_group(default_group.id)
    assert error.value.status_code == 409


def test_dashboard_overview_exposes_operational_sections(isolated_app_db):
    overview = main.get_dashboard_overview()

    assert overview.status in {"ok", "warn", "error"}
    assert {metric.id for metric in overview.metrics} == {
        "platform",
        "market-provider",
        "agents",
        "automation",
        "news",
        "notifications",
    }
    assert overview.health
    assert overview.watchlist


def test_information_source_catalog_configuration_and_ingestion(isolated_app_db, monkeypatch):
    sources = main.list_news_sources()
    assert len(sources) == 9
    assert {source.provider for source in sources} >= {
        "Finnhub",
        "TradingView",
        "Adanos",
        "SEC EDGAR",
        "Federal Reserve",
        "GDELT",
        "CryptoCompare",
        "Alpha Vantage",
        "FRED",
    }
    assert {source.category for source in sources} >= {"market", "news", "filing", "macro", "sentiment"}
    summary = main.information_source_summary()
    assert summary.total == 9
    assert summary.by_adapter["rss"] == 2
    assert len(main.information_source_health()) == summary.total
    assert main.information_source_is_ingestible(next(source for source in sources if source.provider == "TradingView")) is False
    assert main.information_source_is_ingestible(next(source for source in sources if source.provider == "Adanos")) is True
    assert main.information_source_is_ingestible(next(source for source in sources if source.provider == "SEC EDGAR")) is True

    custom = main.create_news_source(main.NewsSourceCreate(
        name="Company IR Atom",
        url="https://example.com/investor.atom",
        type="rss",
        category="filing",
        adapter="rss",
        provider="Example IR",
        symbols=["AAPL"],
        region="US",
        language="en",
        capabilities=["filings", "news"],
        priority=88,
        reliability_weight=0.95,
        enabled=True,
    ))
    assert custom.category == "filing"
    assert custom.capabilities == ["filings", "news"]

    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "Apple files material corporate update",
        "summary": "AAPL regulatory filing published",
        "url": "https://example.com/filing/1",
        "published_at": "2026-07-20T12:00:00+00:00",
        "symbols": ["AAPL"],
    }])
    refreshed = main.refresh_source(custom.id)
    assert refreshed.fetched == 1
    assert refreshed.inserted == 1
    assert main.source_articles_to_events("AAPL", 10)[0].source == "Company IR Atom"


def test_global_news_without_symbol_mapping_is_persisted(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Global Chinese News",
        url="https://example.com/global-news.xml",
        symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "国际新闻综合报道",
        "summary": "这条新闻没有可映射的交易标的代码。",
        "url": "https://example.com/global-news/1",
        "published_at": "2026-08-24T08:00:00+00:00",
    }])

    refreshed = main.refresh_source(source.id)
    assert refreshed.fetched == 1
    assert refreshed.inserted == 1
    source_page = main.news_feed_page([source.id])
    assert source_page.total == 1
    assert source_page.items[0].symbol == "MARKET"
    assert source_page.items[0].symbols == []
    assert main.source_articles_to_events_filtered("AAPL", 10, [source.id]) == []


def test_news_tagger_assigns_symbols_per_article_and_reviews_uncertain_tags(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Mixed Market Feed", url="https://example.com/mixed.xml", symbols=["AAPL"],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [
        {"title": "Bitcoin rally accelerates", "summary": "BTC demand rises", "url": "https://example.com/btc",
         "published_at": "2026-08-24T08:00:00+00:00"},
        {"title": "NVIDIA launches new platform", "summary": "NVDA announces product", "url": "https://example.com/nvda",
         "published_at": "2026-08-24T09:00:00+00:00"},
    ])
    assert main.refresh_source(source.id).inserted == 2
    page = main.news_feed_page([source.id])
    by_title = {item.title: item for item in page.items}
    assert by_title["Bitcoin rally accelerates"].symbols == ["BTCUSD"]
    assert by_title["NVIDIA launches new platform"].symbols == ["NVDA"]
    assert all("AAPL" not in item.symbols for item in page.items)
    assert main.process_next_news_tagging_job() is True
    assert main.process_next_news_tagging_job() is True
    assert main.news_tagging_status().succeeded == 2

    article = by_title["Bitcoin rally accelerates"]
    agent = main.get_agent("news_tagger")
    run = main.enqueue_agent_run(agent.id, trigger="news_ingested", context={"article_id": article.id})
    main.persist_news_tagger_output(run, agent, {
        "article_id": article.id,
        "tags": [{"symbol": "ETHUSD", "relation": "primary", "confidence": 0.70,
                  "direction": "positive", "evidence": "行业联动"}],
    }, "test-model")
    proposal = next(item for item in main.list_agent_proposals("pending") if item.resource_type == "news-tags")
    assert main.get_news_detail(article.id).tagging_status == "review"
    assert "ETHUSD" not in main.get_news_detail(article.id).related_symbols

    now = datetime.now(timezone.utc).isoformat()
    actor = main.AuthUser(id="admin", username="admin", display_name="Admin", role="admin", enabled=True,
                          created_at=now, updated_at=now)
    reviewed = main.review_agent_proposal(
        proposal.id, True, actor, payload_override={"primary_symbols": ["ETHUSD"], "secondary_symbols": ["BTCUSD"]},
    )
    assert reviewed.status == "executed"
    detail = main.get_news_detail(article.id)
    assert any(tag.symbol == "ETHUSD" and tag.relation == "primary" for tag in detail.symbol_tags)
    assert main.source_articles_to_events_filtered("ETHUSD", 10, [source.id])[0].id == article.id


def test_news_impact_pipeline_supports_macro_multi_target_and_indirect_links(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Impact Pipeline Feed", url="https://example.com/impact-pipeline.xml", symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [
        {"title": "Middle East tensions escalate", "summary": "Iran conflict raises broad geopolitical risk",
         "url": "https://example.com/macro", "published_at": "2026-08-26T08:00:00+00:00"},
        {"title": "Data center expansion accelerates", "summary": "Operators increase AI infrastructure capital spending",
         "url": "https://example.com/datacenter", "published_at": "2026-08-26T09:00:00+00:00"},
    ])
    assert main.refresh_source(source.id).inserted == 2
    articles = {item.title: item for item in main.news_feed_page([source.id]).items}
    macro = articles["Middle East tensions escalate"]
    macro_annotation, macro_impacts = main.persist_news_annotation_pipeline(macro.id)
    assert macro_annotation.taxonomy == "geopolitical"
    assert macro_annotation.scope == "global"
    assert "geopolitical_risk" in macro_annotation.macro_factors
    assert not [item for item in macro_impacts if item.link_type == "derived"]
    _, untrusted = main.persist_news_annotation_pipeline(macro.id, {"impacts": [{
        "target_type": "instrument", "target_id": "MSFT", "link_type": "derived",
        "channel": "model_claim", "direction": "negative", "relevance": 0.99,
        "impact_score": 95, "confidence": 0.99, "horizon": "short",
        "path": ["geopolitical risk", "risk appetite", "Microsoft"],
        "graph_edge_ids": ["fabricated-edge"], "evidence": "unsupported",
    }]}, model="test-model")
    assert next(item for item in untrusted if item.symbol == "MSFT").status == "pending"

    now = datetime.now(timezone.utc).isoformat()
    actor = main.AuthUser(id="admin", username="admin", display_name="Admin", role="admin", enabled=True,
                          created_at=now, updated_at=now)
    for symbol in ("NVDA", "QQQ"):
        edge = main.create_news_impact_graph_edge(main.NewsImpactGraphEdgeCreate(
            source_type="topic", source_id="data_center_expansion", source_label="数据中心扩张",
            target_type="instrument" if symbol == "NVDA" else "etf", target_id=symbol, target_label=symbol,
            relation="AI算力需求", direction="positive", weight=0.95,
            evidence="平台管理员确认的需求暴露关系", provenance="research://approved-test",
        ), actor)
        assert edge.status == "pending"
        main.review_news_impact_graph_edge(edge.id, main.NewsImpactGraphEdgeReview(status="approved"), actor)

    data_center = articles["Data center expansion accelerates"]
    annotation, impacts = main.persist_news_annotation_pipeline(data_center.id, {
        "event_annotation": {
            "taxonomy": "supply_chain", "scope": "sector", "summary": "数据中心资本开支扩张",
            "topics": ["data_center_expansion"], "macro_factors": ["ai_compute_demand"],
            "significance": 90, "horizon": "medium",
        },
    }, model="test-model")
    accepted = {item.symbol: item for item in impacts if item.status == "accepted"}
    assert set(accepted) == {"NVDA", "QQQ"}
    assert all(item.link_type == "derived" for item in accepted.values())
    assert accepted["NVDA"].path == ["数据中心扩张", "AI算力需求", accepted["NVDA"].target_label]
    assert main.source_articles_to_events_filtered("NVDA", 10, [source.id])[0].id == data_center.id
    detail = main.get_news_detail(data_center.id)
    assert detail.event_annotation.event_cluster_id == annotation.event_cluster_id
    assert {item.symbol for item in detail.impact_links if item.status == "accepted"} == {"NVDA", "QQQ"}


def test_news_agent_proposes_relationship_and_approval_activates_it(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Relationship Proposal Feed", url="https://example.com/relationship-proposal.xml", symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "Data center expansion accelerates", "summary": "AI infrastructure capital spending rises",
        "url": "https://example.com/relationship", "published_at": "2026-08-27T09:00:00+00:00",
    }])
    main.refresh_source(source.id)
    article = main.news_feed_page([source.id]).items[0]
    output = {
        "article_id": article.id, "tags": [],
        "event_annotation": {"taxonomy": "supply_chain", "scope": "sector", "summary": "数据中心扩张",
                             "topics": ["data_center_expansion"], "significance": 90, "horizon": "medium"},
        "impacts": [{"target_type": "instrument", "target_id": "NVDA", "target_label": "NVIDIA",
                     "link_type": "derived", "channel": "AI算力需求", "direction": "positive",
                     "relevance": 0.92, "impact_score": 86, "confidence": 0.93, "horizon": "medium",
                     "path": ["数据中心扩张", "AI算力需求", "NVIDIA"], "evidence": "资本开支增加GPU需求"}],
    }
    main.persist_news_annotation_pipeline(article.id, output, model="proposal-model")
    run = main.enqueue_agent_run("news_tagger", trigger="news_ingested", context={"article_id": article.id})
    with main.db_connect() as connection:
        connection.execute("UPDATE agent_runs SET status='succeeded', model='proposal-model', finished_at=? WHERE id=?",
                           (datetime.now(timezone.utc).isoformat(), run.id))
    completed = main.get_agent_run(run.id)
    main.persist_agent_proposals(completed, output)
    main.persist_agent_proposals(completed, output)
    proposals = [item for item in main.list_agent_proposals("pending") if item.resource_type == "impact-graph"]
    assert len(proposals) == 1
    assert proposals[0].payload["source_id"] == "data_center_expansion"
    assert proposals[0].payload["target_id"] == "FINNHUB:NVDA"

    now = datetime.now(timezone.utc).isoformat()
    actor = main.AuthUser(id="admin", username="admin", display_name="Admin", role="admin", enabled=True,
                          created_at=now, updated_at=now)
    reviewed = main.review_agent_proposal(proposals[0].id, True, actor, note="关系证据有效")
    assert reviewed.status == "executed"
    edge = main.list_news_impact_graph_edges("approved")[0]
    assert edge.source_id == "data_center_expansion"
    assert edge.target_id == "FINNHUB:NVDA"
    accepted = [item for item in main.list_news_impact_links(article.id) if item.symbol == "NVDA"]
    assert accepted and accepted[0].status == "accepted"


def test_relationship_candidates_are_normalized_and_failures_are_auditable(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Relationship Normalization Feed", url="https://example.com/relationship-normalization.xml", symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "Bitcoin infrastructure demand expands", "summary": "Data center capacity supports crypto infrastructure",
        "url": "https://example.com/normalized-relationship", "published_at": "2026-09-02T09:00:00+00:00",
    }])
    main.refresh_source(source.id)
    article = main.news_feed_page([source.id]).items[0]
    output = {
        "article_id": article.id, "tags": [],
        "event_annotation": {"taxonomy": "supply_chain", "scope": "sector", "summary": "基础设施扩张",
                             "topics": ["data_center_expansion"], "significance": "high", "horizon": "medium"},
        "impacts": [],
        "relationship_proposals": [
            {"source_type": "company", "source_id": "invented-node", "source_label": "Invented",
             "target_type": "crypto_asset", "target_id": "COINBASE:BTCUSD", "target_label": "Bitcoin",
             "relation": "infrastructure_demand", "direction": "bullish", "weight": 90, "confidence": "high",
             "evidence": "基础设施需求提供间接支持", "path": "数据中心扩张 → 算力基础设施需求 → Bitcoin"},
            {"source_type": "event", "source_id": "invented-event", "source_label": "Unknown",
             "target_type": "entity", "target_id": "not-a-tradable-entity", "target_label": "Unknown entity",
             "relation": "unknown", "direction": "neutral", "weight": 0.2, "confidence": "low",
             "evidence": "", "path": "insufficient"},
        ],
    }
    annotation, _ = main.persist_news_annotation_pipeline(article.id, output, model="proposal-model")
    assert annotation.significance == 85
    run = main.enqueue_agent_run("news_tagger", trigger="news_ingested", context={"article_id": article.id})
    with main.db_connect() as connection:
        connection.execute("UPDATE agent_runs SET status='succeeded', model='proposal-model', finished_at=? WHERE id=?",
                           (datetime.now(timezone.utc).isoformat(), run.id))
    main.persist_agent_proposals(main.get_agent_run(run.id), output)

    pending = main.list_agent_proposals("pending", resource_type="impact-graph")
    failed = main.list_agent_proposals("failed", resource_type="impact-graph")
    assert len(pending) == 1
    assert pending[0].payload["source_type"] == "topic"
    assert pending[0].payload["source_id"] == "data_center_expansion"
    assert pending[0].payload["target_type"] == "instrument"
    assert pending[0].payload["target_id"].endswith(":BTCUSD")
    assert pending[0].payload["path"] == ["数据中心扩张", "算力基础设施需求", "Bitcoin"]
    assert pending[0].payload["normalization_warnings"]
    assert len(failed) == 1
    assert "置信度" in failed[0].review_note
    assert failed[0].payload["validation_errors"]
    graph = main.news_impact_graph_view(["pending", "failed"])
    assert graph.counts["pending"] == 1
    assert graph.counts["failed"] == 1
    assert len(graph.edges) == 2
    diagnostics = main.list_news_impact_diagnostics(limit=10)
    assert diagnostics.total == 1
    assert diagnostics.items[0].validation_errors
    assert sum(item.count for item in diagnostics.facets) >= 1
    reason = diagnostics.items[0].reason_keys[0]
    assert main.list_news_impact_diagnostics(reason=reason, limit=10).total == 1

    with main.db_connect() as connection:
        template = connection.execute(
            "SELECT * FROM agent_proposals WHERE id=?", (failed[0].id,),
        ).fetchone()
        for index in range(205):
            values = dict(template)
            values["id"] = f"impact-failed-overflow-{index}"
            connection.execute(
                """INSERT INTO agent_proposals
                   (id, run_id, agent_id, resource_type, action, title, payload, status,
                    reviewed_by, review_note, created_at, reviewed_at)
                   VALUES (:id, :run_id, :agent_id, :resource_type, :action, :title, :payload, :status,
                           :reviewed_by, :review_note, :created_at, :reviewed_at)""",
                values,
            )
    complete_graph = main.news_impact_graph_view(["approved"])
    assert complete_graph.counts["failed"] == 206
    first_page = main.list_news_impact_diagnostics(limit=25)
    assert first_page.total == 206
    assert len(first_page.items) == 25
    assert len(main.list_news_impact_diagnostics(limit=25, offset=200).items) == 6


def test_impact_priority_backfill_excludes_unlinked_filing_metadata(isolated_app_db, monkeypatch):
    filing = main.create_news_source(main.NewsSourceCreate(
        name="Priority Filing Feed", url="https://example.com/priority-filings.xml", category="filing", symbols=[],
    ))
    news = main.create_news_source(main.NewsSourceCreate(
        name="Priority News Feed", url="https://example.com/priority-news.xml", symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda source: [{
        "title": "AAPL filing" if source.id == filing.id else "Macro market shock",
        "summary": "Material event", "url": f"https://example.com/item/{source.id}",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }, *([{
        "title": "Unknown issuer filing", "summary": "Metadata only",
        "url": "https://example.com/item/unlinked", "published_at": datetime.now(timezone.utc).isoformat(),
    }] if source.id == filing.id else [])])
    main.refresh_source(filing.id)
    main.refresh_source(news.id)
    with main.db_connect() as connection:
        connection.execute("UPDATE news_articles SET impact_score=70")
    result = main.queue_news_tagging_backfill(days=30, scope="impact_priority", engine="agent")
    assert result.matched == 2
    with main.db_connect() as connection:
        queued_titles = {row["title"] for row in connection.execute(
            "SELECT a.title FROM news_tagging_jobs j JOIN news_articles a ON a.id=j.article_id WHERE j.status='queued' AND j.trigger='backfill'"
        ).fetchall()}
    assert queued_titles == {"AAPL filing", "Macro market shock"}


def test_manual_news_symbol_override_controls_chart_materialization(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Manual Mapping Feed", url="https://example.com/manual-mapping.xml", symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "未自动识别的公司新闻", "summary": "正文不包含代码或已知别名。",
        "url": "https://example.com/manual/1", "published_at": "2026-08-25T10:00:00+00:00",
    }])
    assert main.refresh_source(source.id).inserted == 1
    article = main.news_feed_page([source.id]).items[0]
    now = datetime.now(timezone.utc).isoformat()
    actor = main.AuthUser(id="admin", username="admin", display_name="Admin", role="admin", enabled=True,
                          created_at=now, updated_at=now)

    detail = main.update_news_symbol_override(
        article.id,
        main.NewsSymbolOverrideUpdate(
            primary_instrument_ids=["FINNHUB:NVDA"], secondary_instrument_ids=["FINNHUB:MSFT"], note="人工复核",
        ),
        actor,
    )
    assert detail.manual_override is True
    assert detail.tagging_status == "manual"
    assert detail.related_symbols == ["NVDA"]
    assert {tag.symbol: tag.relation for tag in detail.symbol_tags} == {"NVDA": "primary", "MSFT": "secondary"}
    list_item = main.news_feed_page([source.id]).items[0]
    assert list_item.manual_association is True
    assert list_item.association_status == "manual"
    assert {tag.symbol: tag.relation for tag in list_item.association_tags} == {"NVDA": "primary", "MSFT": "secondary"}
    assert main.source_articles_to_events_filtered("NVDA", 10, [source.id])[0].id == article.id
    assert main.source_articles_to_events_filtered("MSFT", 10, [source.id]) == []
    assert main.enqueue_news_tagging([article.id], force=True) == 0

    empty = main.update_news_symbol_override(article.id, main.NewsSymbolOverrideUpdate(note="确认无关联"), actor)
    assert empty.manual_override is True
    assert empty.related_symbols == []
    assert empty.symbol_tags == []
    empty_list_item = main.news_feed_page([source.id]).items[0]
    assert empty_list_item.manual_association is True
    assert empty_list_item.association_status == "manual"
    assert empty_list_item.association_tags == []
    backfill = main.queue_news_tagging_backfill(scope="unlinked", engine="rule")
    with main.db_connect() as connection:
        rule_jobs = {row["article_id"] for row in connection.execute(
            "SELECT article_id FROM news_tagging_jobs WHERE trigger='rule_backfill'"
        )}
    assert article.id not in rule_jobs
    assert backfill.skipped == 0

    restored = main.clear_news_symbol_override(article.id)
    assert restored.manual_override is False
    with main.db_connect() as connection:
        job = connection.execute("SELECT status FROM news_tagging_jobs WHERE article_id=?", (article.id,)).fetchone()
    assert job["status"] == "queued"


def test_rule_backfill_matches_chinese_symbol_aliases(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Chinese Company Feed", url="https://example.com/chinese-company.xml", symbols=[],
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "英伟达正在洽谈新的收购交易", "summary": "交易仍处于磋商阶段。",
        "url": "https://example.com/chinese/nvidia", "published_at": "2026-08-25T11:00:00+00:00",
    }])
    assert main.refresh_source(source.id).inserted == 1
    article = main.news_feed_page([source.id]).items[0]
    assert article.symbols == ["NVDA"]
    tags = main.list_news_symbol_tags(article.id)
    assert any(tag.symbol == "NVDA" and tag.status == "accepted" for tag in tags)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    run = main.enqueue_agent_run("news_tagger", trigger="news_ingested", context={"article_id": article.id})
    with main.db_connect() as connection:
        connection.execute("UPDATE agent_runs SET status='running', started_at=? WHERE id=?", (stale, run.id))
        connection.execute(
            "UPDATE news_tagging_jobs SET status='running', run_id=?, updated_at=? WHERE article_id=?",
            (run.id, stale, article.id),
        )
    assert main.process_next_news_tagging_job() is True
    assert main.get_agent_run(run.id).status == "failed"


def test_news_source_runs_monitoring_and_independent_schedule(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Operations RSS", url="https://example.com/operations.xml", symbols=["AAPL"],
        poll_interval_seconds=120, enabled=True,
    ))
    monkeypatch.setattr(news_service, "fetch_information_source_items", lambda _source: [{
        "title": "AAPL operations update", "summary": "Apple update", "url": "https://example.com/ops/1",
        "published_at": "2026-08-20T12:00:00+00:00", "symbols": ["AAPL"],
    }])

    result = main.refresh_source(source.id, trigger="schedule")
    assert result.status == "succeeded"
    assert result.run_id
    run = main.list_news_source_runs(source.id)[0]
    assert run.trigger == "schedule"
    assert run.fetched == 1
    assert run.inserted == 1
    monitor = next(item for item in main.news_source_monitoring() if item.source_id == source.id)
    assert monitor.status == "healthy"
    assert monitor.last_status == "succeeded"
    assert monitor.runs_24h == 1
    assert monitor.success_rate_24h == 100.0
    second = main.create_news_source(main.NewsSourceCreate(
        name="Scoped Operations RSS", url="https://example.com/scoped-operations.xml", symbols=["AAPL"],
        poll_interval_seconds=120, enabled=True,
    ))
    scoped_results = main.refresh_due_news_sources(True, "manual", [second.id])
    assert [item.source_id for item in scoped_results] == [second.id]
    assert len(main.list_news_source_runs(source.id)) == 1
    assert len(main.list_news_source_runs(second.id)) == 1
    overview = main.news_sources_overview()
    assert any(item.id == source.id for item in overview.sources)
    assert any(item.source_id == source.id for item in overview.monitors)

    main.set_automation_settings(main.AutomationSettings(enabled=False, interval_seconds=300))
    task = next(item for item in main.list_scheduled_tasks() if item.id == f"news_source:{source.id}")
    assert task.effective_enabled is True
    assert task.dependency is None


def test_news_events_can_be_filtered_by_source(isolated_app_db):
    first = main.create_news_source(main.NewsSourceCreate(name="Feed One", url="https://example.com/feed-one.xml", symbols=["AAPL"]))
    second = main.create_news_source(main.NewsSourceCreate(name="Feed Two", url="https://example.com/feed-two.xml", symbols=["AAPL"]))
    empty = main.create_news_source(main.NewsSourceCreate(name="Feed Without Articles", url="https://example.com/feed-empty.xml", symbols=["AAPL"]))
    now = datetime.now(timezone.utc).isoformat()
    with main.db_connect() as connection:
        for source, suffix in ((first, "one"), (second, "two")):
            connection.execute(
                """INSERT INTO news_articles
                   (id, source_id, source_name, title, summary, url, published_at, fetched_at, symbols,
                    sentiment, impact_score, event_type, enabled)
                   VALUES (?, ?, ?, ?, 'Summary', ?, ?, ?, 'AAPL', 'neutral', 60, 'product', 1)""",
                (f"filter-{suffix}", source.id, source.name, f"Headline {suffix}", f"https://example.com/{suffix}", now, now),
            )
    events = main.source_articles_to_events_filtered("AAPL", 10, [second.id])
    assert [item.id for item in events] == ["filter-two"]
    assert events[0].source_id == second.id

    page = main.news_feed_page([second.id], limit=1, offset=0)
    assert page.total == 1
    assert [item.id for item in page.items] == ["filter-two"]
    source_counts = {item.source_id: item.count for item in page.source_counts}
    assert source_counts[first.id] == 1
    assert source_counts[second.id] == 1
    assert source_counts[empty.id] == 0

    first_page = main.news_feed_page(limit=1, offset=0)
    second_page = main.news_feed_page(limit=1, offset=1)
    assert first_page.total == second_page.total == 2
    assert first_page.items[0].id != second_page.items[0].id
    assert first_page.limit == second_page.limit == 1
    assert second_page.offset == 1

    symbol_page = main.news_feed_page(symbol="AAPL", limit=30)
    assert symbol_page.total == 2
    assert {item.id for item in symbol_page.items} == {"filter-one", "filter-two"}
    assert all(item.symbols == ["AAPL"] for item in symbol_page.items)


def test_news_source_control_center_frontend_contract():
    html = Path("app/web/pages/news-sources.html").read_text()
    javascript = Path("app/web/static/admin/news-sources.js").read_text()
    workspace = Path("app/web/pages/index.html").read_text()
    app_javascript = workspace_javascript()
    workspace_css = workspace_styles()
    assert 'id="newsSourceTableBody"' in html
    assert 'id="newsSourceRunsDrawer"' in html
    assert 'id="newsSourceDiagnosticDrawer"' in html
    assert 'id="copyNewsSourceDiagnostic"' in html
    assert "/api/news-sources/overview" in javascript
    assert "/api/news-sources/${sourceId}/runs" in javascript
    assert "/api/news-sources/${sourceId}/test" in javascript
    assert "renderNewsAssociations" in app_javascript
    assert "data-edit-news-association" in app_javascript
    assert "openNewsSymbolEditorForArticle" in app_javascript
    assert 'tags.slice(0, 3)' in app_javascript
    assert 'class="news-association-inline"' in app_javascript
    assert 'aria-label="${escapeHtml(translateText("编辑关联标的"))}"' in app_javascript
    assert 'data-test-source=' in javascript
    assert 'id="refreshAllNewsSources"' in html
    assert "refreshAllSources" in javascript
    assert "openDiagnostic(result.diagnostic" in javascript
    assert 'id="newsSourceFilterButton"' in workspace
    assert 'id="newsSourceFilterOptions"' in workspace
    assert 'id="refreshNewsFeed"' in workspace
    assert 'id="newsSyncMenuButton"' in workspace
    assert 'id="syncNewsSources"' in workspace
    assert 'id="newsRefreshStatus"' in workspace
    assert 'data-news-scope="global"' in workspace
    assert 'id="loadMoreNews"' in workspace
    assert '.bottom-workbench > .workbench-panel' in workspace_css
    assert 'align-items: stretch' in workspace_css
    assert 'align-self: stretch' in workspace_css
    assert '#newsPanel:not(.news-feed-mode) .news-timeline' in workspace_css
    assert 'contain: size' in workspace_css
    assert 'id="breadthWorkbenchPanel"' in workspace
    assert '#breadthWorkbenchPanel .breadth-heatmap' in workspace_css
    assert 'scrollbar-gutter: stable' in workspace_css
    assert 'canvas instanceof HTMLCanvasElement' in app_javascript
    assert 'fetchJson(`/api/news?${params}`' in app_javascript
    assert 'app-trading.news-source-filter.v2' in app_javascript
    impact_graph_html = Path("app/web/pages/impact-graph.html").read_text()
    impact_graph_javascript = Path("app/web/static/admin/impact-graph.js").read_text()
    assert 'id="impactProposalList"' in impact_graph_html
    assert "/api/news-impact-graph/view" in impact_graph_javascript
    assert "data-review-proposal" in impact_graph_javascript
    assert 'id="impactNetwork"' in impact_graph_html
    assert 'id="backfillImpactGraph"' in impact_graph_html
    assert 'data-impact-tab="diagnostics"' in impact_graph_html
    assert 'id="impactNodeLegend"' in impact_graph_html
    assert 'cytoscape-dagre.js' in impact_graph_html
    assert '/api/news-impact-graph/diagnostics' in impact_graph_javascript
    assert 'name: "dagre"' in impact_graph_javascript
    assert 'id="sourceList"' not in workspace
    assert 'fetchJson("/api/news-sources")' not in app_javascript
    assert "sourceFacetSearchScore" in app_javascript
    assert "const emptySourceSelection" in app_javascript
    assert "refreshVisibleNewsFeed" in app_javascript
    assert "syncVisibleNewsSources" in app_javascript
    assert 'params.append("source_id"' in app_javascript
    assert main.workspace_page().headers["cache-control"] == "no-cache"


def test_information_source_api_adapter_normalization(isolated_app_db, monkeypatch):
    source = next(item for item in main.list_news_sources() if item.adapter == "cryptocompare")
    monkeypatch.setattr(news_service, "fetch_json_document", lambda _source: {"Data": [{
        "title": "Bitcoin market update",
        "body": "BTC and ETH liquidity improves",
        "url": "https://example.com/crypto/1",
        "published_on": 1_720_000_000,
    }]})

    items = main.fetch_api_items(source)
    assert items[0]["title"] == "Bitcoin market update"
    assert items[0]["symbols"] == ["BTCUSD", "ETHUSD"]
    assert items[0]["published_at"].endswith("+00:00")


def test_adanos_adapter_normalizes_cross_source_sentiment(isolated_app_db, monkeypatch):
    monkeypatch.setenv("ADANOS_API_KEY", "adanos-secret")
    source = next(item for item in main.list_news_sources() if item.provider == "Adanos")
    assert source.adapter == "adanos"

    class Response:
        def __init__(self, request):
            self.request = request

        def __enter__(self):
            assert self.request.headers["X-api-key"] == "adanos-secret"
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            metric = "trade_count" if "/polymarket/" in self.request.full_url else "mentions"
            row = {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "buzz_score": 72,
                "bullish_pct": 68,
                "trend": "rising",
                metric: 135,
            }
            return main.json.dumps({"stocks": [row]}).encode()

    monkeypatch.setattr(news_service, "urlopen", lambda request, timeout=10: Response(request))
    updated = main.update_news_source(source.id, main.NewsSourceUpdate(enabled=True, symbols=["AAPL"]))
    items = main.fetch_adanos_items(updated)
    assert len(items) == 4
    assert {item["sentiment"] for item in items} == {"bullish"}
    assert {item["symbols"][0] for item in items} == {"AAPL"}

    refreshed = main.refresh_source(updated.id)
    assert refreshed.fetched == 4
    assert refreshed.inserted == 4
    events = main.source_articles_to_events("AAPL", 10)
    assert {event.sentiment for event in events} == {"bullish"}
    assert all(event.event_type == "flow" for event in events)


def test_market_tools_use_backend_data_without_external_widget_dependency():
    html = Path("app/web/pages/index.html").read_text()
    javascript = workspace_javascript()
    assert 'data-view="market-tools"' in html
    assert 'data-module="market-tools"' in html
    assert 'id="nativeMarketHeatmap"' in html
    assert 'id="marketOverviewChart"' in html
    assert 'id="marketQuoteBody"' in html
    assert 'id="marketTopStories"' in html
    assert 'id="financialCalendarAgenda"' in html
    assert 'id="financialCalendarDrawer"' in html
    assert '/static/vendor/d3-hierarchy.min.js' not in html
    assert 'loadScriptOnce("/static/vendor/d3-hierarchy.min.js")' in javascript
    assert 'fetchJson("/api/screener?limit=100")' in javascript
    assert 'fetchJson("/api/market-breadth")' in javascript
    assert 'fetchJson("/api/market-news?limit=10")' in javascript
    assert 'fetchJson("/api/chart/query"' in javascript
    assert 'fetchJson(`/api/financial-calendar?' in javascript
    assert 'fetchJson("/api/financial-calendar/refresh"' in javascript
    assert "s3.tradingview.com" not in javascript
    assert 'data-market-tool-symbol' in javascript
    assert 'setActiveView("market")' in javascript
    assert 'window.d3.treemap()' in javascript
    assert 'style="background:${background}"' not in javascript
    assert Path("app/web/static/vendor/d3-hierarchy.min.js").exists()
    assert Path("app/web/static/vendor/d3-hierarchy.LICENSE").exists()
    assert '--app-control-hover:' in Path("app/web/static/styles/theme.css").read_text()
    assert ':root[data-theme="light"] .text-button:hover' in Path("app/web/static/styles/theme.css").read_text()
    assert ':root[data-theme="dark"] select option' in Path("app/web/static/styles/theme.css").read_text()
    assert ':root[data-theme="dark"] input:-webkit-autofill' in Path("app/web/static/styles/theme.css").read_text()
    assert ':root[data-theme="dark"] .chart-range-tabs button:disabled' in Path("app/web/static/styles/theme.css").read_text()
    assert 'button { appearance: none; -webkit-appearance: none;' in Path("app/web/static/styles/theme.css").read_text()
    assert '.primary-button:hover { background-color: var(--app-primary-hover);' in Path("app/web/static/styles/theme.css").read_text()
    market_panel = html.split('id="marketPanel"', 1)[1].split("</section>", 1)[0]
    assert "tradingview" not in market_panel.lower()


def test_financial_calendar_sync_filters_reminders_and_agent_analysis(isolated_app_db, monkeypatch):
    from app.modules.calendar import service as financial_calendar
    from app.services import runtime
    from app.modules.integrations import service as data_sources

    now = datetime.now(timezone.utc).replace(microsecond=0)
    release = now + timedelta(minutes=30)
    monkeypatch.setattr(platform_service, "resolve_api_key", lambda _type, source_id, _env: f"key-{source_id}")
    trading_economics = data_sources.get_profile("calendar:trading-economics")
    finnhub = data_sources.get_profile("market:finnhub")
    monkeypatch.setattr(
        data_sources, "resolve_candidates",
        lambda _market, capability: [trading_economics] if capability == "economic_calendar" else [finnhub],
    )
    monkeypatch.setattr(data_sources, "get_route", lambda *_args: SimpleNamespace(mode="fallback"))

    def fake_request(url, _timeout):
        if "tradingeconomics.com" in url:
            return [{
                "CalendarId": "macro-1", "Date": release.isoformat(), "Country": "United States",
                "Category": "Inflation Rate", "Event": "CPI YoY", "Actual": "", "Previous": "2.8%",
                "Forecast": "2.7%", "TEForecast": "2.6%", "Importance": 3, "Unit": "%",
                "ActualValue": None, "PreviousValue": 2.8, "ForecastValue": 2.7,
            }]
        if "/calendar/earnings" in url:
            assert "international=false" in url
            return {"earningsCalendar": [{
                "date": release.date().isoformat(), "symbol": "AAPL", "hour": "bmo",
                "epsActual": None, "epsEstimate": 1.65, "revenueEstimate": 95_000_000_000,
                "quarter": 3, "year": release.year,
            }]}
        raise AssertionError(url)

    monkeypatch.setattr(financial_calendar, "_request_json", fake_request)
    result = financial_calendar.refresh(start=release.date(), end=release.date())
    assert result.status == "completed"
    assert result.fetched == 2
    page = financial_calendar.list_events(release.date(), release.date(), kind="macro", importance="high", query="CPI")
    assert page.total == 1
    event = page.items[0]
    assert event.forecast_value == 2.7
    assert event.reminder_enabled is True
    assert event.reminder_offsets == [30, 5]

    due = financial_calendar.due_reminders(now)
    assert [(item.id, offset) for item, offset in due] == [(event.id, 30)]
    financial_calendar.mark_reminder_delivered(event.id, 30, now.isoformat())
    assert financial_calendar.due_reminders(now) == []

    queued = financial_calendar.queue_analysis(event.id)
    assert queued.status == "queued"
    completed = runtime.execute_agent_run(queued.run_id)
    assert completed.status == "succeeded"
    detail = financial_calendar.get_event(event.id)
    assert detail.analysis.status == "ready"
    assert detail.analysis.affected_markets
    assert detail.analysis.evidence


def test_official_calendar_sources_aggregate_without_credentials(isolated_app_db, monkeypatch):
    from app.modules.integrations import service as data_sources
    from app.modules.calendar import service as financial_calendar

    release = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    bls = data_sources.get_profile("calendar:bls")
    bea = data_sources.get_profile("calendar:bea")
    monkeypatch.setattr(
        data_sources, "resolve_candidates",
        lambda _market, capability: [bls, bea] if capability == "economic_calendar" else [],
    )
    monkeypatch.setattr(data_sources, "get_route", lambda *_args: SimpleNamespace(mode="aggregate"))
    monkeypatch.setattr(financial_calendar, "_request_text", lambda *_args: "\r\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:bls-cpi", "SUMMARY:Consumer Price Index",
        f"DTSTART:{release.strftime('%Y%m%dT%H%M%SZ')}", "END:VEVENT", "END:VCALENDAR",
    ]))
    monkeypatch.setattr(financial_calendar, "_request_json", lambda *_args: {
        "Consumer Price Index": {"release_dates": [(release + timedelta(minutes=5)).isoformat()]},
        "file_last_updated": release.isoformat(),
    })

    result = financial_calendar.refresh(start=release.date(), end=release.date())
    assert result.status == "completed"
    assert result.fetched == 2
    assert result.merged == 1
    assert result.conflicts == 1
    page = financial_calendar.list_events(release.date(), release.date(), kind="macro")
    assert page.total == 1
    assert page.items[0].source_count == 2
    assert page.items[0].conflicts == ["scheduled_at"]
    assert set(page.items[0].provenance.values()) <= {"calendar:bls", "calendar:bea"}
    detail = financial_calendar.get_event(page.items[0].id)
    assert {item.source_id for item in detail.source_snapshots} == {"calendar:bls", "calendar:bea"}
    assert financial_calendar.list_events(
        release.date(), release.date(), kind="macro", source_id="calendar:bea"
    ).total == 1
    assert financial_calendar.list_events(
        release.date(), release.date(), kind="macro", source_id="calendar:missing"
    ).total == 0
    single = financial_calendar.refresh_source("calendar:bls", release.date(), release.date())
    assert single.status == "completed"
    bls_status = next(item for item in financial_calendar.source_statuses() if item.source_id == "calendar:bls")
    assert bls_status.fetched == 1
    assert bls_status.last_run_at is not None


def test_calendar_release_value_overrides_schedule_status_and_is_preserved(isolated_app_db):
    from app.modules.integrations import service as data_sources
    from app.modules.calendar import service as financial_calendar

    released_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    official = data_sources.get_profile("calendar:bls")
    result_source = data_sources.get_profile("calendar:trading-economics")
    title = "Consumer Price Index"

    def event(profile, *, actual=None, status="scheduled"):
        return {
            "id": f"source-{profile.id}", "source_event_id": f"event-{profile.id}",
            "kind": "macro", "title": title, "category": "CPI",
            "scheduled_at": released_at.isoformat(), "country": "US", "currency": "USD",
            "symbol": None, "importance": "high", "status": status, "actual": actual,
            "forecast": "2.8%", "previous": "2.7%", "provider_forecast": None,
            "actual_value": financial_calendar._number(actual), "forecast_value": 2.8,
            "previous_value": 2.7, "unit": "%", "reference": None,
            "release_session": "unknown", "source_id": profile.id, "source_name": profile.name,
            "source_url": profile.endpoint,
        }

    financial_calendar._upsert_events([
        event(official), event(result_source, actual="2.9%", status="released"),
    ], datetime.now(timezone.utc).isoformat())
    page = financial_calendar.list_events(released_at.date(), released_at.date(), kind="macro")
    assert page.total == 1
    assert page.items[0].status == "released"
    assert page.items[0].actual == "2.9%"

    financial_calendar._upsert_events(
        [event(result_source)], (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    )
    preserved = financial_calendar.list_events(released_at.date(), released_at.date(), kind="macro").items[0]
    assert preserved.status == "released"
    assert preserved.actual == "2.9%"


def test_calendar_overdue_event_reports_awaiting_backfill(isolated_app_db):
    from app.modules.integrations import service as data_sources
    from app.modules.calendar import service as financial_calendar

    released_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    profile = data_sources.get_profile("calendar:bls")
    financial_calendar._upsert_events([
        financial_calendar._official_event(
            profile, "overdue-cpi", "Consumer Price Index", released_at, "US", profile.endpoint,
        )
    ], datetime.now(timezone.utc).isoformat())

    page = financial_calendar.list_events(released_at.date(), released_at.date(), kind="macro")
    assert page.items[0].status == "awaiting_result"
    assert "官方日程源只提供发布时间" in page.warning


def test_nbs_and_ecb_official_calendar_html_adapters(isolated_app_db, monkeypatch):
    from app.modules.integrations import service as data_sources
    from app.modules.calendar import service as financial_calendar

    target = (datetime.now(timezone.utc) + timedelta(days=2)).date()
    month_cells = ["……"] * 12
    month_cells[target.month - 1] = f"{target.day}/一<br>9:30"
    nbs_html = (
        f"<html><h1>{target.year}年国家统计局主要统计信息发布日程表</h1><table><tr>"
        "<td>1</td><td>居民消费价格指数月度报告</td>"
        + "".join(f"<td>{value}</td>" for value in month_cells)
        + "</tr></table></html>"
    )
    nbs = data_sources.get_profile("calendar:nbs")
    monkeypatch.setattr(financial_calendar, "_request_text", lambda *_args: nbs_html)
    nbs_events = financial_calendar._nbs_calendar_events(nbs, "", target, target)
    assert len(nbs_events) == 1
    assert nbs_events[0]["category"] == "CPI"
    assert nbs_events[0]["country"] == "CN"

    ecb = data_sources.get_profile("calendar:ecb")
    monkeypatch.setattr(
        financial_calendar, "_request_text",
        lambda *_args: f"<html><div>{target.strftime('%d/%m/%Y')} 10:00 CET</div><div>Euro area HICP flash estimate</div></html>",
    )
    ecb_events = financial_calendar._ecb_calendar_events(ecb, "", target, target)
    assert len(ecb_events) == 1
    assert ecb_events[0]["country"] == "EU"

    fomc = data_sources.get_profile("calendar:fomc")
    monkeypatch.setattr(
        financial_calendar, "_request_text",
        lambda *_args: f"<html><h3>{target.year} FOMC Meetings</h3><div>{target.strftime('%B')}</div><div>{target.day - 1}-{target.day}*</div></html>",
    )
    fomc_events = financial_calendar._fomc_calendar_events(fomc, "", target, target)
    assert len(fomc_events) == 1
    assert fomc_events[0]["category"] == "RATE_DECISION"


def test_financial_calendar_entitlement_is_capability_scoped(isolated_app_db, monkeypatch):
    from app.modules.integrations import service as data_sources
    from app.modules.calendar import service as financial_calendar
    from app.services import runtime

    profile = SimpleNamespace(
        id="market:finnhub", name="Finnhub 行情", adapter="finnhub",
        api_key_env="FINNHUB_API_KEY", endpoint="https://finnhub.io/api/v1",
        timeout_seconds=8, config={},
    )
    health = []
    monkeypatch.setattr(data_sources, "resolve_candidates", lambda _market, _capability: [profile])
    monkeypatch.setattr(data_sources, "record_health", lambda source_id, ok, _latency, message: health.append((source_id, ok, message)))
    monkeypatch.setattr(platform_service, "resolve_api_key", lambda *_args: "free-tier-key")
    monkeypatch.setattr(
        financial_calendar, "_finnhub_macro_events",
        lambda *_args: (_ for _ in ()).throw(financial_calendar.CalendarEntitlementError("HTTP 403")),
    )
    monkeypatch.setattr(financial_calendar, "_finnhub_earnings_events", lambda *_args: [])

    result = financial_calendar.refresh()
    assert result.status == "warning"
    assert any("配置 Trading Economics API Key" in item for item in result.errors)
    assert all("You don't have access" not in item for item in result.errors)
    assert not any(ok is False for _source_id, ok, _message in health)
    assert financial_calendar._calendar_warning(result.errors, "earnings") is None
    assert "Finnhub Premium" in financial_calendar._calendar_warning(result.errors, "macro")


def test_unified_data_source_registry_routes_and_atomic_versions(isolated_app_db):
    from app.services.data_sources import (
        get_route,
        list_profiles,
        list_revisions,
        publish_route,
        record_health,
        validate_endpoint,
    )

    profiles = list_profiles()
    profile_ids = {item.id for item in profiles}
    assert {"market:finnhub", "market:binance", "market:coinbase", "index:public"} <= profile_ids
    assert "market:mock" not in profile_ids
    assert any(item.id.startswith("content:") and item.legacy_source_id for item in profiles)

    current = get_route("US", "snapshot")
    assert current is not None
    published = publish_route(
        "US", "snapshot", ["market:finnhub"], "fallback", current.version,
        "test-admin", [{"source_id": "market:finnhub", "status": "pass"}],
    )
    assert published.version == current.version + 1
    assert [item.source_id for item in published.members] == ["market:finnhub"]
    assert list_revisions()[0].route_id == "US:snapshot"
    with pytest.raises(main.HTTPException) as conflict:
        publish_route("US", "snapshot", ["market:finnhub"], "fallback", current.version, "test-admin", [])
    assert conflict.value.status_code == 409

    for _ in range(3):
        finnhub = record_health("market:finnhub", False, 12, "upstream unavailable")
    assert finnhub.health_status == "offline"
    for _ in range(3):
        finnhub = record_health("market:finnhub", True, 8, "recovered")
    assert finnhub.health_status == "healthy"

    with pytest.raises(main.HTTPException):
        validate_endpoint("http://127.0.0.1/private", "rss")


def test_data_source_control_frontend_exposes_management_workflows():
    html = Path("app/web/pages/data-sources.html").read_text()
    javascript = Path("app/web/static/admin/data-sources.js").read_text()
    assert 'data-source-tab="sources"' in html
    assert 'data-source-tab="routes"' in html
    assert 'data-source-tab="health"' in html
    assert 'data-source-tab="revisions"' in html
    assert "/api/data-sources" in javascript
    assert "/api/data-source-routes/" in javascript
    assert "检测并原子发布" in html
    assert 'href="/data-sources"' in Path("app/web/pages/admin.html").read_text()


def test_credential_management_frontend_exposes_safe_rotation_controls():
    html = Path("app/web/pages/credentials.html").read_text()
    javascript = Path("app/web/static/admin/credentials.js").read_text()
    admin_javascript = Path("app/web/static/admin/admin.js").read_text()
    agents_javascript = Path("app/web/static/admin/agents.js").read_text()
    assert 'id="rotateSecret" type="password"' in html
    assert "/api/credentials/" in javascript
    assert "encrypted_value" not in javascript
    assert 'key: "api_key"' in admin_javascript
    assert "providerApiKey" in agents_javascript


def test_information_source_uses_dedicated_management(isolated_app_db):
    admin = main.create_initial_admin(main.SetupRequest(
        username="source-admin",
        display_name="Source Admin",
        password="strong-source-password",
    ))
    request = SimpleNamespace(state=SimpleNamespace(user=admin))
    descriptor = main.admin_resource_descriptor("news-sources")
    assert descriptor.management_mode == "dedicated"
    assert descriptor.canonical_href == "/admin/news-sources"
    with pytest.raises(main.HTTPException) as create_error:
        main.admin_resource_create("news-sources", {"name": "Duplicate entry"}, request)
    assert create_error.value.status_code == 409
    assert create_error.value.detail["canonical_href"] == "/admin/news-sources"
    overview = main.get_admin_overview()
    assert {"data", "news", "agents", "models", "credentials", "proposals"} == {item.id for item in overview.metrics}
    assert "api_key" not in overview.model_dump_json().lower()


def test_unified_admin_center_contract():
    overview = Path("app/web/pages/admin-overview.html").read_text()
    resources = Path("app/web/pages/admin.html").read_text()
    center_styles = Path("app/web/static/styles/admin-center.css").read_text()
    shell = Path("app/web/static/core/admin-center.js").read_text()
    workspace = Path("app/web/pages/index.html").read_text()
    theme_styles = Path("app/web/static/styles/theme.css").read_text()
    assert 'data-admin-section="overview"' in overview
    assert '<h1>资源与审计</h1>' in resources
    assert 'class="admin-page-body resource-admin-page"' in resources
    assert '#resourceNav.admin-context-tabs section > strong' in center_styles
    assert '#resourceNav.admin-context-tabs .resource-nav-item.active' in center_styles
    assert 'href: "/admin/news-sources"' in shell
    assert 'href: "/admin/agents"' in shell
    assert workspace.count('href="/admin" title="管理中心"') == 1
    assert 'href="/data-sources" title="数据源管理"' not in workspace
    assert "--sidebar-width: 220px" in theme_styles
    assert "--sidebar-item-height: 40px" in theme_styles
    assert "grid-template-columns: var(--sidebar-width)" in center_styles
    assert "background: var(--sidebar-item-active-bg)" in center_styles
    assert "@media (max-width: 1080px)" in center_styles
    assert "inset 3px 0 0" not in center_styles
    management_pages = [
        "admin-overview.html", "admin.html", "data-sources.html", "news-sources.html",
        "credentials.html", "agents.html", "models.html", "schedules.html", "impact-graph.html",
    ]
    for page_name in management_pages:
        page = Path("app/web/pages", page_name).read_text()
        assert "admin-center.css?v=20260907.1" in page
        assert page.rfind("admin-center.css") > page.rfind("theme.css")


def test_information_source_atom_auth_and_poll_schedule(isolated_app_db, monkeypatch):
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Federal Reserve publishes policy decision</title>
        <summary>Rates remain unchanged.</summary>
        <link href="https://example.com/policy/1" />
        <updated>2026-07-20T18:00:00Z</updated>
      </entry>
    </feed>"""

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return atom

    monkeypatch.setattr(news_service, "urlopen", lambda *_args, **_kwargs: Response())
    source = next(item for item in main.list_news_sources() if item.provider == "Federal Reserve")
    items = main.fetch_rss_items(source)
    assert items == [{
        "title": "Federal Reserve publishes policy decision",
        "summary": "Rates remain unchanged.",
        "url": "https://example.com/policy/1",
        "published_at": "2026-07-20T18:00:00+00:00",
    }]

    finnhub = next(item for item in main.list_news_sources() if item.provider == "Finnhub")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-secret")
    request_url = main.information_source_url(finnhub)
    assert "token=test-secret" in request_url
    assert "test-secret" not in finnhub.model_dump_json()
    assert main.information_source_is_due(source) is True


def test_rss_diagnostics_retry_and_redact_sensitive_url(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Private RSS", url="https://feeds.example.com/private.xml?token=super-secret", symbols=["AAPL"],
    ))
    calls = 0

    def timeout(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise main.URLError(TimeoutError("read operation timed out"))

    monkeypatch.setattr(news_service, "urlopen", timeout)
    monkeypatch.setattr(news_service, "sleep", lambda _seconds: None)
    with pytest.raises(main.NewsSourceDiagnosticError) as raised:
        main.fetch_rss_items(source)
    diagnostic = raised.value.diagnostic
    assert calls == 3
    assert diagnostic.code == "RSS_REQUEST_TIMEOUT"
    assert diagnostic.retryable is True
    assert diagnostic.attempt_count == 3
    assert diagnostic.evidence.final_host == "feeds.example.com"
    assert "super-secret" not in diagnostic.model_dump_json()
    tls_diagnostic = main.rss_failure_diagnostic(
        main.URLError(TimeoutError("The handshake operation timed out")), [],
        main.NewsSourceEvidence(final_host="feeds.example.com"),
    )
    assert tls_diagnostic.code == "RSS_TLS_TIMEOUT"
    assert tls_diagnostic.category == "tls"


def test_rss_diagnostics_reject_html_without_retry(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="HTML Login", url="https://example.com/login", symbols=["AAPL"],
    ))

    class Headers:
        def get_content_type(self):
            return "text/html"

    class Response:
        status = 200
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"<html><body>Sign in</body></html>"

        def geturl(self):
            return "https://login.example.com/session?secret=hidden"

    monkeypatch.setattr(news_service, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(main.NewsSourceDiagnosticError) as raised:
        main.fetch_rss_items(source)
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "RSS_UNEXPECTED_CONTENT_TYPE"
    assert diagnostic.attempt_count == 1
    assert diagnostic.evidence.final_host == "login.example.com"
    assert diagnostic.evidence.content_sha256
    assert "Sign in" not in diagnostic.model_dump_json()
    assert "hidden" not in diagnostic.model_dump_json()


def test_rss_connection_test_is_read_only_and_persists_diagnostic(isolated_app_db, monkeypatch):
    source = main.create_news_source(main.NewsSourceCreate(
        name="Probe RSS", url="https://example.com/probe.xml", symbols=["AAPL"],
    ))
    body = b"""<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>AAPL product update</title><link>https://example.com/articles/1</link>
      <description>Apple publishes an update.</description><pubDate>Sat, 23 Aug 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""

    class Headers:
        def get_content_type(self):
            return "application/rss+xml"

    class Response:
        status = 200
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return body

        def geturl(self):
            return "https://example.com/probe.xml"

    monkeypatch.setattr(news_service, "urlopen", lambda *_args, **_kwargs: Response())
    result = main.test_news_source(source.id)
    assert result.status == "succeeded"
    assert result.fetched == 1
    assert result.diagnostic.code == "RSS_OK"
    assert main.source_articles_to_events("AAPL", 10) == []
    unchanged = next(item for item in main.list_news_sources() if item.id == source.id)
    assert unchanged.last_fetched_at is None
    run = main.list_news_source_runs(source.id)[0]
    assert run.trigger == "test"
    assert run.diagnostic.code == "RSS_OK"
    monitor = next(item for item in main.news_source_monitoring() if item.source_id == source.id)
    assert monitor.runs_24h == 0
    assert monitor.last_status is None


def test_market_breadth_summarizes_watchlist(isolated_app_db):
    breadth = main.market_breadth()

    assert breadth.total == len(main.watchlist())
    assert breadth.advancers + breadth.decliners + breadth.unchanged == breadth.total
    assert breadth.risk_level in {"risk", "watch", "neutral", "constructive"}
    assert breadth.groups
    assert sum(group.count for group in breadth.groups) == breadth.total


def test_screener_filters_watchlist_with_reasons(isolated_app_db):
    result = main.run_screener(min_change=0, min_rsi=30, min_trend=50, signal="buy")

    assert result.total >= len(result.items)
    assert result.items
    assert all(item.snapshot.change_percent >= 0 for item in result.items)
    assert all(item.snapshot.rsi >= 30 for item in result.items)
    assert all(item.snapshot.trend_score >= 50 for item in result.items)
    assert all(item.snapshot.signal == "buy" for item in result.items)
    assert all(item.reasons for item in result.items)

    preset = main.add_screener_preset(
        main.ScreenerPresetCreate(
            name="Buy Momentum",
            filters={"min_change": 0, "min_rsi": 30, "min_trend": 50, "signal": "buy"},
        )
    )
    assert preset.id in {item.id for item in main.screener_presets()}
    validations = main.screener_preset_validations()
    saved_validation = next(item for item in validations if item.id == preset.id)
    assert saved_validation.status in {"pass", "watch", "fail"}
    assert saved_validation.match_count >= 0
    assert saved_validation.filters["signal"] == "buy"
    library_score = main.filter_library_score()
    assert library_score.status in {"pass", "watch", "fail"}
    assert library_score.items
    saved_score = next(item for item in library_score.items if item.preset_id == preset.id)
    assert saved_score.decision in {"accept", "revise", "reject"}
    assert 0 <= saved_score.score <= 100
    assert saved_score.actions
    coverage = main.filter_strategy_coverage()
    assert coverage.status in {"pass", "watch", "fail"}
    assert 0 <= coverage.score <= 100
    assert any(item.preset_id == preset.id for item in coverage.items)
    assert any(item.match_type == "unregistered_strategy_filter" for item in coverage.items)
    assert coverage.actions
    candidates = main.strategy_candidates(min_rsi=30, min_trend=50, signal="buy", factor_id="momentum-news", limit=3)
    assert candidates.status in {"pass", "watch", "fail"}
    assert candidates.total <= 3
    assert [item.rank for item in candidates.items] == list(range(1, len(candidates.items) + 1))
    assert all(item.factor_id == "momentum-news" for item in candidates.items)
    assert all(item.decision in {"go", "size_down", "hold", "block"} for item in candidates.items)
    assert main.remove_screener_preset(preset.id)["disabled"] is True
    assert preset.id not in {item.id for item in main.screener_presets()}


def test_csv_exports_core_datasets(isolated_app_db):
    watchlist_export = main.export_dataset("watchlist")
    assert watchlist_export.media_type.startswith("text/csv")
    assert "symbol,name,market" in watchlist_export.body.decode()

    screener_export = main.export_dataset("screener")
    assert "reasons" in screener_export.body.decode()

    assert "check_name" in main.export_dataset("data-quality").body.decode()
    assert "dataset_id,name,status,rows" in main.export_dataset("data-freshness").body.decode()
    assert "backtest_count" in main.export_dataset("research-coverage").body.decode()
    assert "priority,source,severity" in main.export_dataset("research-action-plan").body.decode()
    assert "strategy_id,strategy_name" in main.export_dataset("strategy-promotion-gates").body.decode()
    assert "decision,report_status" in main.export_dataset("research-health").body.decode()
    assert "factor_id,factor_name,category" in main.export_dataset("factor-lifecycle").body.decode()
    assert "run_type,symbol,factor_id" in main.export_dataset("research-run-audit").body.decode()
    assert "strategy_id,strategy_name,status,score" in main.export_dataset("data-lineage").body.decode()
    assert "allocation_status,correlation_status" in main.export_dataset("regime-exposure").body.decode()
    assert "regime_label,status,score,open_count" in main.export_dataset("regime-scenario-risk").body.decode()
    assert "regime_label,cell_status,decision" in main.export_dataset("strategy-regime-matrix").body.decode()
    assert "strategy_a_id,strategy_a_name,strategy_b_id" in main.export_dataset("strategy-crowding").body.decode()
    assert "backtest_return,forward_return,decay" in main.export_dataset("strategy-validation-decay").body.decode()
    assert "strategy_id,strategy_name,category" in main.export_dataset("strategy-lifecycle").body.decode()
    assert "risk_control_count,validation_rule_count" in main.export_dataset("strategy-risk-controls").body.decode()
    assert "report_status,report_score,decision" in main.export_dataset("market-state-controls").body.decode()
    assert "decision,recommended_weight,score" in main.export_dataset("strategy-operating-plan").body.decode()
    assert "operating_decision,filter_valid,filter_match_count" in main.export_dataset("strategy-evidence-packs").body.decode()
    assert "run_count,positive_count,average_return" in main.export_dataset("strategy-backtest-monitor").body.decode()
    assert "factor_count,tested_factor_count" in main.export_dataset("strategy-forward-monitor").body.decode()
    assert "positive_backtest_rate,forward_pass_rate" in main.export_dataset("strategy-sensitivity").body.decode()
    assert "rank,strategy_id,strategy_name,category,status,priority" in main.export_dataset("strategy-revalidation-plan").body.decode()
    assert "backtest_factor_count,forward_factor_count,walk_forward_factor_count,stress_factor_count" in main.export_dataset("strategy-validation-coverage").body.decode()
    assert "preset_id,name,status,decision" in main.export_dataset("filter-library").body.decode()
    assert "preset_id,preset_name,strategy_id" in main.export_dataset("filter-strategy-coverage").body.decode()


def test_market_stream_payload_contains_realtime_dashboard_data(isolated_app_db):
    payload = main.market_stream_payload()

    assert payload["type"] == "market_snapshot"
    assert payload["provider"]["name"] == "coinbase"
    assert len(payload["watchlist"]) == len(main.watchlist())
    assert payload["breadth"]["total"] == len(payload["watchlist"])


def test_ai_briefing_requires_real_model_output(isolated_app_db):
    briefing = main.ai_briefing()

    assert briefing.title == "AI 全局盯盘简报已停用"
    assert briefing.risk_level == "watch"
    assert briefing.opportunities == []
    assert briefing.risks
    assert briefing.actions
    assert briefing.evidence == []


def test_news_events_ai_factors_and_backtest(isolated_app_db):
    assert main.news_events("AAPL") == []
    assert main.factor_ideas("AAPL") == []
    assert main.backtest_summaries("AAPL") == []
    source_id = main.list_news_sources()[0].id
    now = datetime.now(timezone.utc).isoformat()
    with main.db_connect() as connection:
        connection.execute(
            """INSERT INTO news_articles
               (id, source_id, source_name, title, summary, url, published_at, fetched_at, symbols,
                sentiment, impact_score, event_type, enabled)
               VALUES ('real-aapl-news', ?, 'Fixture source', 'AAPL filing', 'Company filing published',
                       'https://example.test/aapl', ?, ?, 'AAPL', 'neutral', 70, 'regulatory', 1)""",
            (source_id, now, now),
        )
    events = main.news_events("AAPL")
    assert len(events) == 1
    detail = main.news_detail(events[0].id)
    assert detail.id == events[0].id
    assert detail.classification_summary
    assert detail.key_points
    assert len(main.chart_events("AAPL")) > 0
    assert main.ai_insights("AAPL") == []

    run = main.run_backtest(
        main.BacktestRunRequest(symbol="AAPL", factor_id="momentum-news", interval="1h", limit=100)
    )
    assert run.symbol == "AAPL"
    assert len(run.equity_curve) >= 60
    summaries = main.backtest_summaries("AAPL")
    assert summaries[0].id == run.id
    assert main.factor_ideas("AAPL")[0].name == "新闻确认动量"


def test_market_news_returns_enabled_articles_in_latest_order(isolated_app_db):
    source_id = main.list_news_sources()[0].id
    now = datetime.now(timezone.utc)
    rows = [
        ("market-news-old", "Older headline", "AAPL", now.replace(hour=8).isoformat(), 90, 1),
        ("market-news-new", "Latest headline", "", now.replace(hour=10).isoformat(), 55, 1),
        ("market-news-hidden", "Hidden headline", "NVDA", now.replace(hour=11).isoformat(), 99, 0),
    ]
    main.update_news_source(source_id, main.NewsSourceUpdate(enabled=True))
    with main.db_connect() as connection:
        for news_id, title, symbols, published_at, impact, enabled in rows:
            connection.execute(
                """
                INSERT INTO news_articles
                (id, source_id, source_name, title, summary, url, published_at, fetched_at,
                 symbols, sentiment, impact_score, event_type, enabled)
                VALUES (?, ?, 'Test Feed', ?, 'Summary', ?, ?, ?, ?, 'neutral', ?, 'macro', ?)
                """,
                (news_id, source_id, title, f"https://example.com/{news_id}", published_at,
                 now.isoformat(), symbols, impact, enabled),
            )
        connection.commit()

    events = main.latest_market_news(limit=2)
    assert [item.id for item in events] == ["market-news-new", "market-news-old"]
    assert events[0].symbol == "MARKET"


def test_factor_library_create_toggle_and_backtest_guard(isolated_app_db):
    definitions = main.factor_definitions()
    assert {item.id for item in definitions} >= {"momentum-news", "vol-compression", "rsi-reversion", "event-drift"}

    custom = main.add_factor_definition(
        main.FactorDefinitionCreate(
            name="Custom Momentum Gate",
            category="自定义",
            description="Use momentum and non-negative catalyst context as a custom research factor.",
            signal="long",
            horizon="5-15 days",
        )
    )
    assert custom.enabled is True
    assert custom.id in {item.id for item in main.factor_definitions()}

    run = main.run_backtest(
        main.BacktestRunRequest(symbol="AAPL", factor_id=custom.id, interval="1h", limit=100)
    )
    assert run.factor_id == custom.id
    assert run.factor_name == custom.name

    disabled = main.patch_factor_definition(custom.id, main.FactorDefinitionUpdate(enabled=False))
    assert disabled.enabled is False
    with pytest.raises(main.HTTPException):
        main.run_backtest(main.BacktestRunRequest(symbol="AAPL", factor_id=custom.id, interval="1h", limit=100))


def test_research_libraries_regime_validation_and_forward_test(isolated_app_db):
    catalog = main.data_catalog()
    assert {item.id for item in catalog} >= {"active-watchlist", "factor-library", "strategy-library"}

    quality = main.data_quality()
    assert quality.status in {"pass", "watch", "fail"}
    assert 0 <= quality.score <= 100
    assert quality.checks
    assert any(item.name == "K线完整性" for item in quality.checks)

    coverage = main.research_coverage()
    assert coverage.status in {"pass", "watch", "fail"}
    assert coverage.items
    assert all(item.symbol for item in coverage.items)
    assert all(0 <= item.score <= 100 for item in coverage.items)
    factor_coverage = main.factor_coverage()
    assert factor_coverage.status in {"pass", "watch", "fail"}
    assert factor_coverage.items
    assert all(item.factor_id for item in factor_coverage.items)
    assert all(0 <= item.score <= 100 for item in factor_coverage.items)
    assert all(item.symbol_count >= 1 for item in factor_coverage.items)
    assert all(item.actions for item in factor_coverage.items)
    lifecycle = main.factor_lifecycle(symbol="AAPL")
    assert lifecycle.status in {"pass", "watch", "fail"}
    assert lifecycle.items
    assert all(item.stage in {"idea", "research", "validation", "candidate", "active", "paused"} for item in lifecycle.items)
    assert all(0 <= item.score <= 100 for item in lifecycle.items)
    assert all(item.factor_id for item in lifecycle.items)
    assert lifecycle.actions
    lineage = main.data_lineage()
    assert lineage.status in {"pass", "watch", "fail"}
    assert 0 <= lineage.score <= 100
    assert lineage.nodes
    assert lineage.strategies
    assert {item.id for item in lineage.nodes} >= {"active-watchlist", "factor-library", "strategy-library"}
    assert all(item.validation_coverage.keys() >= {"backtest", "forward", "walk_forward", "stress"} for item in lineage.strategies)
    assert all(item.actions for item in lineage.strategies)

    action_plan = main.research_actions()
    assert action_plan.status in {"pass", "watch", "fail"}
    assert action_plan.items
    assert action_plan.items[0].priority >= action_plan.items[-1].priority

    dashboard = main.research_dashboard()
    assert dashboard.status in {"pass", "watch", "fail"}
    assert 0 <= dashboard.score <= 100
    assert {item.name for item in dashboard.metrics} >= {"数据质量", "研究就绪", "覆盖完整", "策略准入", "市场状态"}
    health_report = main.research_health()
    assert health_report.status in {"pass", "watch", "fail"}
    assert health_report.decision in {"go", "caution", "stop"}
    assert 0 <= health_report.score <= 100
    assert {item.name for item in health_report.checks} >= {"研究工作台", "研究就绪度", "因子生命周期", "运行审计", "市场暴露", "过滤器准入"}
    assert health_report.actions
    freshness = main.data_freshness()
    assert freshness.status in {"pass", "watch", "fail"}
    assert 0 <= freshness.score <= 100
    assert {item.dataset_id for item in freshness.items} >= {"market-candles", "news-articles", "backtest-runs", "strategy-validations"}
    assert all(0 <= item.lag_score <= 100 for item in freshness.items)
    assert freshness.actions

    backfill = main.research_coverage_backfill(
        main.ResearchCoverageBackfillRequest(symbol="AAPL", factor_id="momentum-news", max_tasks=1)
    )
    assert backfill.factor_id == "momentum-news"
    assert backfill.requested_symbols == ["AAPL"]
    assert len(backfill.executed_tasks) <= 1
    assert backfill.coverage.items
    factor_backfill = main.factor_coverage_backfill(
        main.FactorCoverageBackfillRequest(symbol="AAPL", factor_id="momentum-news", max_tasks=1)
    )
    assert factor_backfill.factor_id == "momentum-news"
    assert factor_backfill.requested_symbols == ["AAPL"]
    assert len(factor_backfill.executed_tasks) <= 1
    assert factor_backfill.coverage.items
    run_audit = main.research_run_audit(limit=6, symbol="AAPL", factor_id="momentum-news")
    assert run_audit.status in {"pass", "watch", "fail"}
    assert 0 <= run_audit.score <= 100
    assert run_audit.total_runs == len(run_audit.items)
    assert len(run_audit.items) <= 6
    assert all(item.symbol == "AAPL" for item in run_audit.items)
    assert all(item.factor_id == "momentum-news" for item in run_audit.items)
    assert all(item.run_type in {"backtest", "forward", "walk_forward", "stress"} for item in run_audit.items)
    assert run_audit.actions

    strategies = main.strategy_templates()
    assert {item.id for item in strategies} >= {"trend-news-confirmation", "volatility-breakout"}
    strategy_leaderboard = main.strategy_leaderboard()
    assert strategy_leaderboard.items
    assert [item.rank for item in strategy_leaderboard.items] == list(range(1, len(strategy_leaderboard.items) + 1))
    assert strategy_leaderboard.items[0].score >= strategy_leaderboard.items[-1].score
    assert {item.strategy_id for item in strategy_leaderboard.items} >= {"trend-news-confirmation", "volatility-breakout"}
    strategy_regime_fit = main.strategy_regime_fit(limit=3)
    assert strategy_regime_fit.status in {"pass", "watch", "fail"}
    assert strategy_regime_fit.regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert 1 <= len(strategy_regime_fit.items) <= 3
    assert [item.rank for item in strategy_regime_fit.items] == list(range(1, len(strategy_regime_fit.items) + 1))
    assert all(item.current_regime == strategy_regime_fit.regime.label for item in strategy_regime_fit.items)
    assert all(item.filter_status in {"pass", "watch", "fail"} for item in strategy_regime_fit.items)
    strategy_matrix = main.strategy_regime_matrix()
    assert strategy_matrix.status in {"pass", "watch", "fail"}
    assert strategy_matrix.current_regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert strategy_matrix.items
    assert all(len(item.cells) == 5 for item in strategy_matrix.items)
    assert all(cell.decision in {"open", "reduced", "paused", "blocked"} for item in strategy_matrix.items for cell in item.cells)
    assert all(0 <= cell.score <= 100 for item in strategy_matrix.items for cell in item.cells)
    assert strategy_matrix.actions
    strategy_crowding = main.strategy_crowding()
    assert strategy_crowding.status in {"pass", "watch", "fail"}
    assert 0 <= strategy_crowding.score <= 100
    assert strategy_crowding.current_regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert strategy_crowding.pairs
    assert all(0 <= item.overlap_score <= 100 for item in strategy_crowding.pairs)
    assert strategy_crowding.actions
    strategy_lifecycle = main.strategy_lifecycle()
    assert strategy_lifecycle.status in {"pass", "watch", "fail"}
    assert strategy_lifecycle.items
    assert {item.strategy_id for item in strategy_lifecycle.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(item.stage in {"draft", "validation", "candidate", "active", "paused"} for item in strategy_lifecycle.items)
    assert all(0 <= item.score <= 100 for item in strategy_lifecycle.items)
    assert strategy_lifecycle.actions
    risk_controls = main.strategy_risk_controls()
    assert risk_controls.status in {"pass", "watch", "fail"}
    assert 0 <= risk_controls.score <= 100
    assert risk_controls.items
    assert {item.strategy_id for item in risk_controls.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(item.gate_status in {"pass", "watch", "fail", "missing"} for item in risk_controls.items)
    assert risk_controls.actions
    market_controls = main.market_state_controls()
    assert market_controls.status in {"pass", "watch", "fail"}
    assert market_controls.decision in {"expand", "observe", "reduce", "halt"}
    assert 0 <= market_controls.score <= 100
    assert market_controls.current_regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert {item.name for item in market_controls.items} >= {"本地数据质量", "研究就绪度", "市场状态矩阵", "策略风控审计"}
    assert all(0 <= item.score <= 100 for item in market_controls.items)
    assert market_controls.actions
    operating_plan = main.strategy_operating_plan()
    assert operating_plan.status in {"pass", "watch", "fail"}
    assert operating_plan.market_decision in {"expand", "observe", "reduce", "halt"}
    assert operating_plan.items
    assert {item.strategy_id for item in operating_plan.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(item.decision in {"open", "observe", "reduce", "block"} for item in operating_plan.items)
    assert all(0 <= item.recommended_weight <= 8 for item in operating_plan.items)
    assert operating_plan.actions
    evidence_packs = main.strategy_evidence_packs()
    assert evidence_packs.status in {"pass", "watch", "fail"}
    assert 0 <= evidence_packs.score <= 100
    assert evidence_packs.items
    assert {item.strategy_id for item in evidence_packs.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(item.operating_decision in {"open", "observe", "reduce", "block", "missing"} for item in evidence_packs.items)
    assert all(item.latest_validation_status in {"pass", "watch", "fail", "missing"} for item in evidence_packs.items)
    assert all(item.gate_status in {"pass", "watch", "fail", "missing"} for item in evidence_packs.items)
    assert evidence_packs.actions
    sensitivity = main.strategy_sensitivity()
    assert sensitivity.status in {"pass", "watch", "fail"}
    assert 0 <= sensitivity.score <= 100
    assert sensitivity.items
    assert {item.strategy_id for item in sensitivity.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(0 <= item.positive_backtest_rate <= 100 for item in sensitivity.items)
    assert all(0 <= item.forward_pass_rate <= 100 for item in sensitivity.items)
    assert sensitivity.actions
    strategy_revalidation = main.strategy_revalidation_plan()
    assert strategy_revalidation.status in {"pass", "watch", "fail"}
    assert 0 <= strategy_revalidation.score <= 100
    assert strategy_revalidation.current_regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert strategy_revalidation.items
    assert [item.rank for item in strategy_revalidation.items] == list(range(1, len(strategy_revalidation.items) + 1))
    assert all(0 <= item.priority <= 100 for item in strategy_revalidation.items)
    assert all(item.regime_decision in {"open", "reduced", "paused", "blocked", "missing"} for item in strategy_revalidation.items)
    assert strategy_revalidation.actions
    validation_coverage = main.strategy_validation_coverage()
    assert validation_coverage.status in {"pass", "watch", "fail"}
    assert 0 <= validation_coverage.score <= 100
    assert validation_coverage.items
    assert {item.strategy_id for item in validation_coverage.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(0 <= item.score <= 100 for item in validation_coverage.items)
    assert all(item.fully_validated_factor_count <= item.factor_count for item in validation_coverage.items)
    assert all(item.backtest_factor_count <= item.factor_count for item in validation_coverage.items)
    assert validation_coverage.actions
    promotion_gates = main.strategy_promotion_gate_list()
    assert promotion_gates
    assert {item.strategy_id for item in promotion_gates} >= {"trend-news-confirmation", "volatility-breakout"}
    first_gate = main.strategy_promotion_gate("trend-news-confirmation")
    assert first_gate.status in {"pass", "watch", "fail"}
    assert first_gate.checks
    assert first_gate.recommendation

    custom_strategy = main.add_strategy_template(
        main.StrategyTemplateCreate(
            name="Validated Momentum",
            category="验证",
            description="Momentum strategy template with explicit validation rules.",
            factor_ids=["momentum-news"],
            default_filters={"min_rsi": 50, "min_trend": 50},
            risk_controls=["Forward test required"],
            validation_rules=["Sample count >= 2"],
        )
    )
    assert custom_strategy.id in {item.id for item in main.strategy_templates()}
    assert custom_strategy.factor_ids == ["momentum-news"]
    disabled_strategy = main.patch_strategy_template(custom_strategy.id, main.StrategyTemplateUpdate(enabled=False))
    assert disabled_strategy.enabled is False
    with pytest.raises(main.HTTPException):
        main.validate_strategy_template(custom_strategy.id, main.StrategyValidationRequest(symbol_limit=1))
    enabled_strategy = main.patch_strategy_template(custom_strategy.id, main.StrategyTemplateUpdate(enabled=True, category="验证更新"))
    assert enabled_strategy.enabled is True
    assert enabled_strategy.category == "验证更新"

    regime = main.market_regime()
    assert regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert regime.evidence
    assert regime.actions
    regime_history = main.market_regime_history()
    assert regime_history
    assert regime_history[0].generated_at == regime.generated_at
    regime_transition = main.market_regime_transition()
    assert regime_transition.current.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert regime_transition.status in {"pass", "watch", "fail"}
    assert 0 <= regime_transition.transition_score <= 100
    assert regime_transition.evidence
    assert regime_transition.actions
    scenario_risk = main.regime_scenario_risk()
    assert scenario_risk.status in {"pass", "watch", "fail"}
    assert scenario_risk.current_regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert {item.regime_label for item in scenario_risk.items} == {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert all(0 <= item.score <= 100 for item in scenario_risk.items)
    assert all(0 <= item.risk_budget <= 100 for item in scenario_risk.items)
    assert scenario_risk.actions

    valid = main.filter_validation(main.FilterValidationRequest(min_rsi=30, min_trend=40))
    assert valid.valid is True
    assert valid.match_count >= 1
    filter_stress = main.filter_stress_test(main.FilterValidationRequest(min_rsi=30, min_trend=40))
    assert filter_stress.status in {"pass", "watch", "fail"}
    assert 0 <= filter_stress.stability_score <= 100
    assert filter_stress.baseline_match_count == valid.match_count
    assert len(filter_stress.scenarios) >= 4
    assert filter_stress.actions
    contribution = main.filter_contribution(main.FilterValidationRequest(min_rsi=30, min_trend=40))
    assert contribution.status in {"pass", "watch", "fail"}
    assert contribution.baseline_match_count == valid.match_count
    assert 0 <= contribution.concentration_score <= 100
    assert {item.filter_name for item in contribution.items} >= {"min_rsi", "min_trend"}
    assert all(item.relaxed_match_count >= item.baseline_match_count for item in contribution.items)
    assert contribution.actions
    admission = main.filter_admission(main.FilterValidationRequest(min_rsi=30, min_trend=40))
    assert admission.status in {"pass", "watch", "fail"}
    assert admission.decision in {"accept", "revise", "reject"}
    assert 0 <= admission.score <= 100
    assert admission.validation.match_count == valid.match_count
    assert admission.contribution.baseline_match_count == contribution.baseline_match_count
    assert admission.stress_test.baseline_match_count == filter_stress.baseline_match_count
    assert {item.name for item in admission.checks} >= {"基础验证", "贡献集中度", "压力稳定性", "市场状态"}
    assert admission.actions

    regime_templates = main.filter_regime_templates()
    assert len(regime_templates) >= 3
    assert all(item.filters for item in regime_templates)
    assert all(item.match_count >= 0 for item in regime_templates)
    assert all(item.rationale for item in regime_templates)
    playbook = main.regime_playbook(symbol="AAPL")
    assert playbook.status in {"pass", "watch", "fail"}
    assert playbook.preferred_factors
    assert playbook.filter_templates
    assert {item.name for item in playbook.steps} >= {"筛选模板", "策略预算", "市场状态"}
    assert playbook.risk_controls
    assert playbook.next_actions

    invalid = main.filter_validation(main.FilterValidationRequest(min_rsi=80, max_rsi=20))
    assert invalid.valid is False
    assert any("高于上限" in item for item in invalid.warnings)

    forward = main.create_forward_test(
        main.ForwardTestRequest(symbol="AAPL", factor_id="momentum-news", interval="1h", train_bars=120, forward_bars=30)
    )
    assert forward.symbol == "AAPL"
    assert forward.pass_fail in {"pass", "watch", "fail"}
    assert forward.checks
    assert main.forward_tests("AAPL")[0].id == forward.id
    forward_leaders = main.forward_leaderboard(symbol="AAPL", factor_id="momentum-news")
    assert forward_leaders.status in {"pass", "watch", "fail"}
    assert forward_leaders.items
    assert forward_leaders.items[0].symbol == "AAPL"
    assert forward_leaders.items[0].factor_id == "momentum-news"
    assert [item.rank for item in forward_leaders.items] == list(range(1, len(forward_leaders.items) + 1))
    strategy_backtest = main.strategy_backtest_monitor(limit=5)
    assert strategy_backtest.status in {"pass", "watch", "fail"}
    assert strategy_backtest.items
    assert {item.strategy_id for item in strategy_backtest.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(0 <= item.score <= 100 for item in strategy_backtest.items)
    assert strategy_backtest.actions
    strategy_forward = main.strategy_forward_monitor(limit=5)
    assert strategy_forward.status in {"pass", "watch", "fail"}
    assert strategy_forward.items
    assert {item.strategy_id for item in strategy_forward.items} >= {"trend-news-confirmation", "volatility-breakout"}
    assert all(0 <= item.score <= 100 for item in strategy_forward.items)
    assert strategy_forward.actions

    walk_forward = main.create_walk_forward_test(
        main.WalkForwardRequest(
            symbol="AAPL",
            factor_id="momentum-news",
            interval="1h",
            train_bars=80,
            forward_bars=25,
            windows=3,
        )
    )
    assert walk_forward.symbol == "AAPL"
    assert walk_forward.pass_fail in {"pass", "watch", "fail"}
    assert walk_forward.window_count >= 2
    assert len(walk_forward.windows) == walk_forward.window_count
    assert 0 <= walk_forward.pass_rate <= 100
    assert main.walk_forward_tests("AAPL")[0].id == walk_forward.id
    walk_leaders = main.walk_forward_leaderboard(symbol="AAPL", factor_id="momentum-news")
    assert walk_leaders.status in {"pass", "watch", "fail"}
    assert walk_leaders.items
    assert walk_leaders.items[0].symbol == "AAPL"
    assert walk_leaders.items[0].factor_id == "momentum-news"
    assert [item.rank for item in walk_leaders.items] == list(range(1, len(walk_leaders.items) + 1))

    stress = main.create_stress_test(
        main.StressTestRequest(symbol="AAPL", factor_id="momentum-news", interval="1h")
    )
    assert stress.symbol == "AAPL"
    assert stress.scenarios
    assert stress.pass_fail in {"pass", "watch", "fail"}
    assert main.stress_tests("AAPL")[0].id == stress.id
    stress_leaders = main.stress_leaderboard(symbol="AAPL", factor_id="momentum-news")
    assert stress_leaders.status in {"pass", "watch", "fail"}
    assert stress_leaders.items
    assert stress_leaders.items[0].symbol == "AAPL"
    assert stress_leaders.items[0].factor_id == "momentum-news"
    assert [item.rank for item in stress_leaders.items] == list(range(1, len(stress_leaders.items) + 1))
    benchmark = main.backtest_benchmark("AAPL", factor_id="momentum-news", interval="1h")
    assert benchmark.symbol == "AAPL"
    assert benchmark.factor_id == "momentum-news"
    assert benchmark.status in {"pass", "watch", "fail"}
    assert isinstance(benchmark.alpha, float)
    assert benchmark.checks
    assert benchmark.actions
    consistency = main.backtest_forward_consistency("AAPL", factor_id="momentum-news")
    assert consistency.symbol == "AAPL"
    assert consistency.factor_id == "momentum-news"
    assert consistency.status in {"pass", "watch", "fail"}
    assert 0 <= consistency.score <= 100
    assert consistency.backtest_return == benchmark.strategy_return
    assert consistency.forward_return == forward.forward_return
    assert consistency.walk_forward_pass_rate == walk_forward.pass_rate
    assert consistency.stress_worst_return == stress.worst_return
    assert {item.name for item in consistency.checks} >= {"回测基线", "前向验证", "滚动样本外", "压力验证"}
    assert consistency.actions

    robustness = main.robustness("AAPL")
    assert robustness.symbol == "AAPL"
    assert robustness.status in {"pass", "watch", "fail"}
    assert robustness.factors
    momentum_report = next(item for item in robustness.factors if item.factor_id == "momentum-news")
    assert momentum_report.backtest_status in {"pass", "watch", "fail"}
    assert momentum_report.forward_status == forward.pass_fail
    assert momentum_report.walk_forward_status == walk_forward.pass_fail
    assert momentum_report.latest_walk_forward_pass_rate == walk_forward.pass_rate
    assert momentum_report.stress_status == stress.pass_fail
    assert momentum_report.actions

    drift = main.strategy_drift("AAPL")
    assert drift.symbol == "AAPL"
    assert drift.status in {"pass", "watch", "fail"}
    assert 0 <= drift.score <= 100
    momentum_drift = next(item for item in drift.factors if item.factor_id == "momentum-news")
    assert momentum_drift.baseline_return is not None
    assert momentum_drift.forward_return == forward.forward_return
    assert momentum_drift.walk_forward_pass_rate == walk_forward.pass_rate
    assert momentum_drift.regime_alignment in {"pass", "watch", "fail"}
    assert momentum_drift.actions

    allocation = main.strategy_allocation("AAPL")
    assert allocation.symbol == "AAPL"
    assert allocation.status in {"pass", "watch", "fail"}
    assert 0 <= allocation.risk_budget <= 100
    assert allocation.items
    assert all(0 <= item.target_weight <= item.max_weight for item in allocation.items)
    assert all(item.status in {"increase", "hold", "reduce", "avoid"} for item in allocation.items)
    assert allocation.actions

    correlation = main.factor_correlation("AAPL")
    assert correlation.symbol == "AAPL"
    assert correlation.status in {"pass", "watch", "fail"}
    assert 0 <= correlation.crowding_score <= 100
    assert correlation.exposures
    assert correlation.pairs
    assert all(-1 <= item.correlation <= 1 for item in correlation.pairs)
    assert correlation.actions
    exposure = main.regime_exposure(interval="1h")
    assert exposure.status in {"pass", "watch", "fail"}
    assert 0 <= exposure.score <= 100
    assert exposure.regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert exposure.items
    assert all(item.status in {"pass", "watch", "fail"} for item in exposure.items)
    assert all(item.target_weight >= 0 for item in exposure.items)
    assert exposure.actions

    attribution = main.regime_attribution("AAPL", factor_id="momentum-news")
    assert attribution.symbol == "AAPL"
    assert attribution.factor_id == "momentum-news"
    assert attribution.status in {"pass", "watch", "fail"}
    assert attribution.slices
    assert all(item.bars > 0 for item in attribution.slices)
    assert attribution.actions

    regime_fit = main.regime_factor_fit("AAPL", interval="1h", limit=3)
    assert regime_fit.symbol == "AAPL"
    assert regime_fit.status in {"pass", "watch", "fail"}
    assert regime_fit.regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert 1 <= len(regime_fit.items) <= 3
    assert [item.rank for item in regime_fit.items] == list(range(1, len(regime_fit.items) + 1))
    assert all(item.current_regime == regime_fit.regime.label for item in regime_fit.items)
    assert all(item.regime_fit in {"aligned", "neutral", "misaligned"} for item in regime_fit.items)
    assert all(0 <= item.score <= 100 for item in regime_fit.items)

    optimization = main.create_parameter_optimization(
        main.ParameterOptimizationRequest(symbol="AAPL", factor_id="momentum-news", interval="1h", limit=180)
    )
    assert optimization.symbol == "AAPL"
    assert optimization.factor_id == "momentum-news"
    assert optimization.status in {"pass", "watch", "fail"}
    assert optimization.best is not None
    assert optimization.results
    assert [item.rank for item in optimization.results] == list(range(1, len(optimization.results) + 1))
    assert optimization.actions

    admission = main.create_factor_admission(
        main.FactorAdmissionRequest(
            symbol="AAPL",
            factor_id="momentum-news",
            interval="1h",
            filters=main.FilterValidationRequest(min_rsi=30, min_trend=40),
        )
    )
    assert admission.symbol == "AAPL"
    assert admission.factor_id == "momentum-news"
    assert admission.status in {"pass", "watch", "fail"}
    assert 0 <= admission.score <= 100
    assert {item.name for item in admission.checks} >= {"过滤器", "稳健性", "漂移", "配置", "相关性", "市场状态归因", "参数优化"}
    assert admission.actions
    queue = main.factor_admission_queue(limit=3, factor_id="momentum-news")
    assert queue.status in {"pass", "watch", "fail"}
    assert 1 <= len(queue.items) <= 3
    assert [item.rank for item in queue.items] == list(range(1, len(queue.items) + 1))
    assert all(item.factor_id == "momentum-news" for item in queue.items)
    revalidation = main.revalidation_queue(limit=4, factor_id="momentum-news")
    assert revalidation.status in {"pass", "watch", "fail"}
    assert revalidation.stale_days == 7
    assert len(revalidation.items) <= 4
    assert [item.rank for item in revalidation.items] == list(range(1, len(revalidation.items) + 1))
    trade_gate = main.trade_readiness(
        "AAPL",
        factor_id="momentum-news",
        interval="1h",
        min_rsi=30,
        min_trend=40,
        signal="buy",
    )
    assert trade_gate.symbol == "AAPL"
    assert trade_gate.factor_id == "momentum-news"
    assert trade_gate.status in {"pass", "watch", "fail"}
    assert trade_gate.decision in {"go", "size_down", "hold", "block"}
    assert 0 <= trade_gate.score <= 100
    assert {item.name for item in trade_gate.checks} >= {"过滤器", "市场状态", "稳健性", "漂移", "仓位配置", "复验缺口", "新闻风险", "组合风险"}
    regime_gate = main.regime_risk_gate("AAPL", factor_id="momentum-news", interval="1h")
    assert regime_gate.symbol == "AAPL"
    assert regime_gate.factor_id == "momentum-news"
    assert regime_gate.status in {"pass", "watch", "fail"}
    assert regime_gate.decision in {"open", "reduced", "paused", "blocked"}
    assert 0 <= regime_gate.score <= 100
    assert regime_gate.allowed_weight >= 0
    assert regime_gate.transition.current.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert {item.name for item in regime_gate.checks} >= {"市场状态", "状态迁移", "交易门禁", "策略预算"}

    pipeline = main.create_research_pipeline(
        main.ResearchPipelineRequest(
            symbol="AAPL",
            factor_id="momentum-news",
            interval="1h",
            filters=main.FilterValidationRequest(min_rsi=30, min_trend=40),
        )
    )
    assert pipeline.symbol == "AAPL"
    assert pipeline.factor_id == "momentum-news"
    assert pipeline.pass_fail in {"pass", "watch", "fail"}
    assert pipeline.filter_validation.valid is True
    assert pipeline.backtest.id
    assert pipeline.forward_test.id
    assert pipeline.stress_test.id
    assert pipeline.robustness.factor_id == "momentum-news"
    assert pipeline.actions

    sensitivity = main.create_sensitivity_test(
        main.SensitivityTestRequest(symbol="AAPL", factor_id="momentum-news")
    )
    assert sensitivity.symbol == "AAPL"
    assert sensitivity.factor_id == "momentum-news"
    assert sensitivity.pass_fail in {"pass", "watch", "fail"}
    assert len(sensitivity.scenarios) == 4
    assert sensitivity.return_spread >= 0
    assert sensitivity.actions

    leaderboard = main.factor_leaderboard("AAPL")
    assert leaderboard.symbol == "AAPL"
    assert leaderboard.items
    assert [item.rank for item in leaderboard.items] == list(range(1, len(leaderboard.items) + 1))
    assert leaderboard.items[0].composite_score >= leaderboard.items[-1].composite_score
    assert any(item.factor_id == "momentum-news" for item in leaderboard.items)

    validation = main.validate_strategy_template(
        "trend-news-confirmation",
        main.StrategyValidationRequest(symbol_limit=2, interval="1h"),
    )
    assert validation.strategy_id == "trend-news-confirmation"
    assert validation.symbols
    assert validation.backtest_ids
    assert validation.forward_test_ids
    assert validation.pass_fail in {"pass", "watch", "fail"}
    assert validation.checks
    assert main.strategy_validations(strategy_id="trend-news-confirmation")[0].id == validation.id
    decay = main.strategy_validation_decay(limit=5)
    assert decay.status in {"pass", "watch", "fail"}
    assert decay.items
    assert any(item.strategy_id == "trend-news-confirmation" for item in decay.items)
    assert all(0 <= item.score <= 100 for item in decay.items)
    assert decay.actions
    assert any(item.event_type == "strategy_validation" for item in main.list_event_logs(limit=20))
    assert any(item.event_type == "walk_forward_test" for item in main.list_event_logs(limit=20))
    assert any(item.event_type == "stress_test" for item in main.list_event_logs(limit=20))
    assert any(item.event_type == "sensitivity_test" for item in main.list_event_logs(limit=20))
    assert any(item.event_type == "research_pipeline" for item in main.list_event_logs(limit=20))
    readiness = main.research_readiness()
    assert readiness.status in {"pass", "watch", "fail"}
    assert 0 <= readiness.score <= 100
    assert readiness.checks
    assert readiness.regime.label in {"risk_on", "risk_off", "trend", "range", "volatile"}
    assert any(item.name == "策略覆盖" for item in readiness.checks)
    assert any(item.name == "漂移监控" for item in readiness.checks)
    assert main.remove_strategy_template(custom_strategy.id)["disabled"] is True
    assert next(item for item in main.strategy_templates() if item.id == custom_strategy.id).enabled is False


def test_alert_automation_events_and_notifications(isolated_app_db):
    channel = main.create_notification_channel(
        main.NotificationChannelCreate(name="Test webhook", target="http://127.0.0.1:9/webhook")
    )
    disabled_channel = main.patch_notification_channel(channel.id, main.NotificationChannelUpdate(enabled=False))
    assert disabled_channel.enabled is False
    channel = main.patch_notification_channel(channel.id, main.NotificationChannelUpdate(enabled=True))
    assert channel.enabled is True
    rule = main.create_alert(
        main.AlertRuleCreate(symbol="AAPL", name="Test price alert", metric="price", operator=">=", threshold=1)
    )
    disabled = main.update_alert(rule.id, main.AlertRuleUpdate(enabled=False, threshold=999999))
    assert disabled.enabled is False
    assert disabled.threshold == 999999
    enabled = main.update_alert(rule.id, main.AlertRuleUpdate(enabled=True, threshold=1))
    assert enabled.enabled is True

    evaluations = main.evaluate_alerts("AAPL")
    assert any(item.rule.id == rule.id and item.triggered for item in evaluations)

    deliveries = main.list_notification_deliveries(channel_id=channel.id)
    assert deliveries
    assert deliveries[0].success is False

    result = main.run_automation_cycle()
    assert result.triggered_alerts >= 1
    runs = main.automation_runs(limit=5)
    assert runs
    assert runs[0].id == result.id
    assert runs[0].triggered_alerts == result.triggered_alerts
    logs = main.list_event_logs(limit=20)
    assert any(item.event_type == "alert_triggered" for item in logs)
    assert any(item.event_type == "automation_run" for item in logs)
    assert main.remove_alert(rule.id)["disabled"] is True
    assert next(item for item in main.list_alert_rules("AAPL") if item.id == rule.id).enabled is False
    assert main.remove_notification_channel(channel.id)["disabled"] is True
    assert next(item for item in main.list_notification_channels() if item.id == channel.id).enabled is False


def test_agent_registry_custom_instances_and_scheduling(isolated_app_db):
    agents = main.list_agents()
    assert {item.template_type for item in agents if item.builtin} == {
        "market_monitor", "news_tagger", "news_event_extractor", "news_impact_mapper", "news_impact_validator",
        "news_analyst", "economic_event_analyst", "risk_sentinel", "factor_research", "briefing"
    }

    custom = main.create_agent(
        main.AgentCreate(
            template_type="market_monitor",
            name="US Tech Monitor",
            symbol_scope="custom",
            symbols=["aapl", "NVDA"],
            schedule_enabled=True,
            interval_seconds=60,
            triggers=["manual", "schedule", "alert_triggered"],
        )
    )
    assert custom.builtin is False
    assert custom.symbols == ["AAPL", "NVDA"]
    assert custom.next_run_at is not None

    updated = main.update_agent(custom.id, main.AgentUpdate(enabled=False, interval_seconds=120))
    assert updated.enabled is False
    assert updated.interval_seconds == 120
    assert updated.next_run_at is not None


def test_unified_schedule_management(isolated_app_db):
    tasks = main.list_scheduled_tasks()
    assert {item.task_type for item in tasks} == {"automation", "news_source", "data_source", "model_gateway", "agent", "maintenance"}

    retention = main.get_scheduled_task("maintenance:agent-data-retention")
    retention = main.update_scheduled_task(
        retention.id,
        main.ScheduledTaskUpdate(
            enabled=True,
            interval_seconds=43200,
            checkpoint_retention_days=7,
            trace_retention_days=60,
            artifact_retention_days=120,
            vacuum_after_cleanup=False,
        ),
    )
    assert retention.interval_seconds == 43200
    assert retention.configuration == {
        "checkpoint_retention_days": 7,
        "trace_retention_days": 60,
        "artifact_retention_days": 120,
        "vacuum_after_cleanup": False,
    }

    health = main.get_scheduled_task("data_source:health-check")
    health = main.update_scheduled_task(
        health.id,
        main.ScheduledTaskUpdate(enabled=True, interval_seconds=180),
    )
    assert health.enabled is True
    assert health.interval_seconds == 180

    automation = main.get_scheduled_task("automation:market-monitor")
    assert automation.enabled is False
    assert automation.status == "paused"
    automation = main.update_scheduled_task(
        automation.id,
        main.ScheduledTaskUpdate(enabled=True, interval_seconds=120),
    )
    assert automation.enabled is True
    assert automation.effective_enabled is True
    assert automation.interval_seconds == 120
    assert automation.next_run_at is not None

    source = next(item for item in main.list_scheduled_tasks() if item.task_type == "news_source")
    source = main.update_scheduled_task(
        source.id,
        main.ScheduledTaskUpdate(enabled=False, interval_seconds=600),
    )
    assert source.enabled is False
    assert source.status == "paused"
    assert source.interval_seconds == 600

    calendar = main.get_scheduled_task("calendar_source:calendar:bls")
    assert calendar.effective_enabled is True
    calendar = main.update_scheduled_task(
        calendar.id,
        main.ScheduledTaskUpdate(enabled=False, interval_seconds=7200),
    )
    assert calendar.enabled is False
    assert calendar.status == "paused"
    assert calendar.interval_seconds == 7200

    agent = next(item for item in main.list_scheduled_tasks() if item.task_type == "agent")
    agent = main.update_scheduled_task(
        agent.id,
        main.ScheduledTaskUpdate(enabled=True, interval_seconds=90),
    )
    assert agent.enabled is True
    assert agent.interval_seconds == 90

    result = main.bulk_update_scheduled_tasks(
        main.ScheduledTaskBulkUpdate(ids=[automation.id, agent.id], enabled=False)
    )
    assert result.updated == 2
    assert main.get_scheduled_task(automation.id).enabled is False
    assert main.get_scheduled_task(agent.id).enabled is False

    with pytest.raises(main.HTTPException) as error:
        main.update_scheduled_task(source.id, main.ScheduledTaskUpdate(interval_seconds=30))
    assert error.value.status_code == 422

    run = main.run_scheduled_task_now(agent.id)
    assert run.status == "queued"
    assert run.run_id


def test_agent_llm_execution_fallback_and_secret_redaction(isolated_app_db, monkeypatch):
    monkeypatch.setenv("TEST_AGENT_API_KEY", "secret-value")
    provider = main.create_agent_provider(
        main.AgentProviderCreate(
            name="Test Gateway",
            base_url="https://models.example.test/v1",
            model="test-model",
            api_key_env="TEST_AGENT_API_KEY",
        )
    )
    assert provider.secret_configured is True
    assert "secret-value" not in provider.model_dump_json()

    agent = main.get_agent("market_monitor")
    main.update_agent(agent.id, main.AgentUpdate(runtime_mode="hybrid", provider_id=provider.id))

    def fake_post(url, payload, api_key, timeout):
        assert api_key == "secret-value"
        return {
            "choices": [{"message": {"content": '{"summary":"model output","risk_level":"watch","confidence":82,"symbols":["AAPL"],"evidence":["price"],"actions":["review"],"proposals":[]}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }

    monkeypatch.setattr(ai_service, "agent_http_post", fake_post)
    run = main.enqueue_agent_run(agent.id, symbol="AAPL", test=True)
    completed = main.execute_agent_run(run.id)
    assert completed.status == "succeeded"
    assert completed.output["summary"] == "model output"
    assert completed.prompt_tokens == 12

    monkeypatch.setattr(ai_service, "agent_http_post", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    fallback = main.execute_agent_run(main.enqueue_agent_run(agent.id, symbol="AAPL", test=True).id)
    assert fallback.status == "succeeded"
    assert fallback.error.startswith("LLM fallback:")
    assert fallback.output["evidence"]


def test_model_channel_route_executes_and_records_sanitized_metrics(isolated_app_db, monkeypatch):
    monkeypatch.setenv("APP_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_CREDENTIAL_ASYNC_TESTS", "false")
    channel = main.create_model_channel(main.ModelChannelCreate(
        name="Primary Analysis",
        provider_type="openai_compatible",
        base_url="https://models.example.test/v1",
        api_key_env="MODEL_ROUTE_TEST_KEY",
        api_key="route-secret-value",
        models=["analysis-model"],
    ))
    route = main.create_model_route(main.ModelRouteCreate(
        name="News Analysis Default",
        alias="news-analysis-default",
        targets=[main.ModelRouteTargetInput(channel_id=channel.id, model="analysis-model", priority=100, weight=100)],
    ))

    def fake_call(selected_channel, model, messages, system_prompt, temperature, max_tokens, api_key):
        assert selected_channel.id == channel.id
        assert model == "analysis-model"
        assert api_key == "route-secret-value"
        return main.model_gateway_service.GatewayResponse("model response", 11, 7, "stop")

    monkeypatch.setattr(main.model_gateway_service, "call_model", fake_call)
    response, selected_channel, selected_model = main.run_model_route(
        route.id, [{"role": "user", "content": "Analyze this."}], source="agent"
    )
    assert response.text == "model response"
    assert selected_channel.id == channel.id
    assert selected_model == "analysis-model"
    invocation = main.list_model_invocations(limit=1)[0]
    assert invocation.status == "succeeded"
    assert invocation.prompt_tokens == 11
    assert invocation.completion_tokens == 7
    assert "route-secret-value" not in invocation.model_dump_json()
    assert main.get_model_channel(channel.id).health_status == "healthy"


def test_model_channel_can_save_large_upstream_catalog(isolated_app_db):
    models = [f"vendor/model-{index}" for index in range(222)]
    channel = main.create_model_channel(main.ModelChannelCreate(
        name="Large Catalog",
        provider_type="ollama",
        base_url="http://localhost:11434/v1",
        api_key_env="",
        models=models,
    ))
    assert len(channel.models) == 222

    updated = main.update_model_channel(channel.id, main.ModelChannelUpdate(
        name="Large Catalog Updated",
        models=channel.models,
    ))
    assert updated.name == "Large Catalog Updated"
    assert len(updated.models) == len(models)
    assert set(updated.models) == set(models)


def test_agent_uses_logical_model_route(isolated_app_db, monkeypatch):
    monkeypatch.setenv("APP_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode())
    channel = main.create_model_channel(main.ModelChannelCreate(
        name="Agent Route Channel", provider_type="ollama", base_url="http://localhost:11434/v1",
        api_key_env="", models=["local-analysis"],
    ))
    route = main.create_model_route(main.ModelRouteCreate(
        name="Agent Default", alias="agent-default",
        targets=[main.ModelRouteTargetInput(channel_id=channel.id, model="local-analysis")],
    ))
    agent = main.update_agent("market_monitor", main.AgentUpdate(
        runtime_mode="llm", model_route_id=route.id, fallback_to_rules=False,
    ))

    model_output = '{"summary":"routed output","risk_level":"watch","confidence":83,"symbols":["AAPL"],"evidence":["price"],"actions":["review"],"proposals":[]}'
    monkeypatch.setattr(
        main.model_gateway_service,
        "call_model",
        lambda *args, **kwargs: main.model_gateway_service.GatewayResponse(model_output, 15, 9, "stop"),
    )
    completed = main.execute_agent_run(main.enqueue_agent_run(
        agent.id, symbol="AAPL", context={"research_note": "tune momentum", "api_key": "snapshot-secret"}, test=True,
    ).id)
    assert completed.status == "succeeded"
    assert completed.output["summary"] == "routed output"
    assert completed.model == "local-analysis"
    assert completed.prompt_tokens == 15
    detail = main.get_agent_run_detail(completed.id)
    assert detail.trigger_input["research_note"] == "tune momentum"
    assert detail.trigger_input["api_key"] == "[REDACTED]"
    assert detail.input_snapshot["agent_config"]["model_route_id"] == route.id
    assert detail.input_snapshot["execution"]["model"] == "local-analysis"
    assert "Rule baseline:" in detail.input_snapshot["model_request"]["user_prompt"]
    assert "snapshot-secret" not in detail.model_dump_json()
    summary = main.AgentRunSummary.model_validate(completed.model_dump()).model_dump()
    assert "output" not in summary
    assert "error" not in summary


def test_agent_run_detail_supports_historical_trigger_input(isolated_app_db):
    queued = main.enqueue_agent_run(
        "market_monitor", trigger="manual", symbol="MSFT", context={"note": "historical context"}, test=True,
    )
    detail = main.get_agent_run_detail(queued.id)
    assert detail.trigger_input == {"note": "historical context"}
    assert detail.input_snapshot is None

    from app.api.routes.auth_admin import agent_run_detail as agent_run_detail_api
    now = datetime.now(timezone.utc).isoformat()
    viewer = main.AuthUser(
        id="viewer", username="viewer", display_name="Viewer", role="viewer", enabled=True,
        created_at=now, updated_at=now,
    )
    with pytest.raises(main.HTTPException) as denied:
        agent_run_detail_api(queued.id, SimpleNamespace(state=SimpleNamespace(user=viewer)))
    assert denied.value.status_code == 403


def test_legacy_agent_provider_is_migrated_to_route(isolated_app_db):
    provider = main.create_agent_provider(main.AgentProviderCreate(
        name="Legacy Gateway", base_url="https://legacy.example.test/v1", model="legacy-model",
        api_key_env="LEGACY_MODEL_API_KEY",
    ))
    main.update_agent("news_analyst", main.AgentUpdate(runtime_mode="hybrid", provider_id=provider.id))
    main._INITIALIZED_DB_PATHS.clear()
    main.init_db()
    migrated = main.get_agent("news_analyst")
    assert migrated.model_route_id == f"route-{provider.id}"
    route = main.get_model_route(migrated.model_route_id)
    assert route.targets[0].channel_id == provider.id
    assert route.targets[0].model == "legacy-model"


def test_playground_stream_fails_over_only_before_first_delta(isolated_app_db, monkeypatch):
    first = main.create_model_channel(main.ModelChannelCreate(
        name="Primary Stream", provider_type="ollama", base_url="http://localhost:11434/v1",
        api_key_env="", models=["stream-model"],
    ))
    second = main.create_model_channel(main.ModelChannelCreate(
        name="Backup Stream", provider_type="ollama", base_url="http://localhost:11435/v1",
        api_key_env="", models=["stream-model"],
    ))
    route = main.create_model_route(main.ModelRouteCreate(
        name="Streaming Route", alias="streaming-route", targets=[
            main.ModelRouteTargetInput(channel_id=first.id, model="stream-model", priority=100),
            main.ModelRouteTargetInput(channel_id=second.id, model="stream-model", priority=50),
        ],
    ))
    calls = []

    def pre_delta_failure(channel, *args, **kwargs):
        calls.append(channel.id)
        if channel.id == first.id:
            raise RuntimeError("connect timeout")
        yield {"type": "delta", "text": "backup"}
        yield {"type": "usage", "prompt_tokens": 2, "completion_tokens": 1}

    monkeypatch.setattr(main.model_gateway_service, "stream_model", pre_delta_failure)
    payload = main.PlaygroundChatRequest(route_id=route.id, messages=[{"role": "user", "content": "hello"}])
    events = list(main.stream_playground_chat(payload))
    assert calls == [first.id, second.id]
    assert any(item.get("text") == "backup" for item in events)
    assert events[-1]["type"] == "done"

    calls.clear()

    def post_delta_failure(channel, *args, **kwargs):
        calls.append(channel.id)
        yield {"type": "delta", "text": "partial"}
        raise RuntimeError("stream interrupted")

    monkeypatch.setattr(main.model_gateway_service, "stream_model", post_delta_failure)
    events = list(main.stream_playground_chat(payload))
    assert calls == [first.id]
    assert events[-1]["type"] == "error"


def test_factor_agent_proposal_requires_approval(isolated_app_db):
    agent = main.get_agent("factor_research")
    run = main.execute_agent_run(main.enqueue_agent_run(agent.id, symbol="AAPL").id)
    assert run.status == "succeeded"
    proposals = main.list_agent_proposals(status="pending")
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.resource_type == "factors"

    actor = main.AuthUser(
        id="test-admin", username="admin", display_name="Admin", role="admin", enabled=True,
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    )
    reviewed = main.review_agent_proposal(proposal.id, True, actor)
    assert reviewed.status == "executed"
    assert any(item.name == proposal.payload["name"] for item in main.list_factor_definitions())

def test_portfolio_mark_to_market(isolated_app_db):
    from app.api.routes import charting as chart_routes

    quote = chart_routes.instrument_quote("FINNHUB:AAPL")
    assert quote.price == 212.44
    assert quote.currency == "USD"
    assert quote.source == "Test market provider"
    from app.modules.market import charting
    assert charting.resolve_instrument("INDEX:SPX").positionable is False
    assert charting.resolve_instrument("FINNHUB:SPY").quantity_units[0].label == "份"
    position = main.create_portfolio_position(
        main.PortfolioPositionCreate(
            instrument_id="FINNHUB:AAPL", quantity=10, quantity_unit="share", average_price=None, side="long"
        )
    )
    assert position.instrument_id == "FINNHUB:AAPL"
    assert position.quantity_unit == "share"
    assert position.unit_label == "股"
    assert position.base_quantity == 10
    assert position.average_price == 212.44
    summary = main.get_portfolio_summary()
    assert any(item.id == position.id for item in summary.positions)
    assert summary.long_exposure > 0
    assert summary.total_market_value > 0
    risk = main.portfolio_risk()
    assert risk.gross_exposure > 0
    assert risk.largest_position_symbol == "AAPL"
    assert risk.contributions
    assert risk.actions

    main.delete_portfolio_position(position.id)
    summary_after = main.get_portfolio_summary()
    assert not any(item.id == position.id for item in summary_after.positions)

    with pytest.raises(main.HTTPException) as index_error:
        main.create_portfolio_position(main.PortfolioPositionCreate(
            instrument_id="INDEX:SPX", quantity=1, average_price=5000,
        ))
    assert index_error.value.status_code == 422


def test_portfolio_quantity_unit_conversion(isolated_app_db, monkeypatch):
    from app.modules.market import charting

    instrument = main.MarketInstrument(
        id="TEST:AAPL", symbol="AAPL", provider_symbol="AAPL", name="Apple lot test",
        asset_class="stock", market="TEST", currency="USD", provider="test", timezone="UTC",
        realtime=False, delayed=True, intervals=["1d"], positionable=True,
        quantity_units=[
            main.PositionQuantityUnit(code="share", label="股", multiplier=1, step=1),
            main.PositionQuantityUnit(code="lot", label="手", multiplier=100, step=1),
        ],
    )
    monkeypatch.setattr(charting, "resolve_instrument", lambda _value: instrument)
    position = main.create_portfolio_position(main.PortfolioPositionCreate(
        instrument_id=instrument.id, quantity=2, quantity_unit="lot", average_price=100,
    ))
    assert position.quantity == 2
    assert position.unit_label == "手"
    assert position.unit_multiplier == 100
    assert position.base_quantity == 200
    assert position.market_value == 200 * 212.44


def test_paper_trading_market_order_and_partial_close(isolated_app_db, monkeypatch):
    from app.modules.trading import paper as paper_trading

    monkeypatch.setattr(paper_trading, "latest_instrument_quote", lambda instrument_id: main.InstrumentQuote(
        instrument_id=instrument_id, symbol="AAPL", price=100, currency="USD",
        as_of=datetime.now(timezone.utc).isoformat(), source="Fixture quote", stale=False,
    ))
    account = next(item for item in paper_trading.ensure_accounts("paper-user") if item.currency == "USD")
    opened = paper_trading.create_order("paper-user", main.PaperOrderCreate(
        account_id=account.id, instrument_id="FINNHUB:AAPL", position_side="long",
        effect="open", order_type="market", quantity=10, quantity_unit="share",
        take_profit=115, stop_loss=94,
    ))
    assert opened.status == "filled"
    portfolio = paper_trading.get_portfolio("paper-user", account.id)
    assert len(portfolio.positions) == 1
    assert portfolio.positions[0].take_profit == 115
    assert len(paper_trading.list_orders("paper-user", account.id, "pending")) == 2
    closed = paper_trading.close_position("paper-user", portfolio.positions[0].id, main.PaperClosePosition(quantity=4))
    assert closed.status == "filled"
    remaining = paper_trading.get_portfolio("paper-user", account.id).positions[0]
    assert remaining.quantity == 6
    assert {item.quantity for item in paper_trading.list_orders("paper-user", account.id, "pending")} == {6}
    fills = paper_trading.list_fills("paper-user", account.id)
    assert len(fills) == 2
    assert {item.unit_label for item in fills} == {"股"}
    assert paper_trading.list_ledger("paper-user", account.id)


def test_paper_trading_reserves_and_releases_limit_order_cash(isolated_app_db, monkeypatch):
    from app.modules.trading import paper as paper_trading

    monkeypatch.setattr(paper_trading, "latest_instrument_quote", lambda instrument_id: main.InstrumentQuote(
        instrument_id=instrument_id, symbol="AAPL", price=100, currency="USD",
        as_of=datetime.now(timezone.utc).isoformat(), source="Fixture quote", stale=False,
    ))
    account = next(item for item in paper_trading.ensure_accounts("second-user") if item.currency == "USD")
    order = paper_trading.create_order("second-user", main.PaperOrderCreate(
        account_id=account.id, instrument_id="FINNHUB:AAPL", position_side="long",
        effect="open", order_type="limit", quantity=5, quantity_unit="share", limit_price=95,
    ))
    assert order.status == "pending"
    assert paper_trading.get_portfolio("second-user", account.id).account.reserved_cash > 0
    paper_trading.cancel_order("second-user", order.id)
    assert paper_trading.get_portfolio("second-user", account.id).account.reserved_cash == 0
    with pytest.raises(main.HTTPException):
        paper_trading.get_portfolio("paper-user", account.id)
    matching_task = next(item for item in main.list_scheduled_tasks() if item.id == "automation:paper-matching")
    assert matching_task.enabled is True
    assert matching_task.interval_seconds == 60


def test_system_health_summarizes_operational_components(isolated_app_db):
    health = main.get_system_health()
    component_names = {item.name for item in health.components}

    assert health.status in {"ok", "warn", "error"}
    assert {
        "数据库",
        "行情源",
        "自选观察",
        "自动化盯盘",
        "信息源",
        "预警规则",
        "因子库",
        "通知投递",
        "模拟组合",
        "事件日志",
    }.issubset(component_names)


def test_expanded_indicator_catalog_and_local_series(isolated_app_db):
    import numpy as np
    import pandas as pd
    from app.modules.market import charting

    catalog = charting.indicator_catalog("BINANCE_FUTURES:BTCUSDT")
    assert len(catalog) >= 60
    assert {"hma", "stochrsi", "squeeze", "cvd", "annualized_funding", "fred_dgs10"} <= {
        item.id for item in catalog
    }
    assert next(item for item in catalog if item.id == "fred_dgs10").source_mode == "provider"

    size = 400
    index = np.arange(size, dtype=float)
    frame = pd.DataFrame({
        "time": 1_700_000_000 + index * 3600,
        "open": 100 + index * 0.1,
        "high": 102 + index * 0.1,
        "low": 98 + index * 0.1,
        "close": 100 + index * 0.1 + np.sin(index / 5),
        "volume": 1_000 + index % 20 * 10,
    })
    frame.index = pd.to_datetime(frame["time"], unit="s", utc=True)
    for position, identifier in enumerate(("hma", "stochrsi", "squeeze", "kvo")):
        series = charting._calculate_standard_indicator(main.IndicatorConfig(id=identifier), frame, position)
        assert series and any(item.points for item in series)


def test_finnhub_indicator_resource_normalization(isolated_app_db, monkeypatch):
    from app.modules.market import charting
    from app.modules.market import indicators as indicator_resources
    from app.services import runtime

    profile = SimpleNamespace(
        id="market:finnhub", name="Finnhub 行情", endpoint="https://finnhub.io/api/v1",
        api_key_env="FINNHUB_API_KEY", timeout_seconds=8,
    )
    monkeypatch.setattr(platform_service, "resolve_api_key", lambda *_args: "test-key")

    def fake_json(url, **_kwargs):
        if "/stock/metric" in url:
            return {"metric": {"marketCapitalization": 3000, "peTTM": 24.5, "roeTTM": 31.2}}
        if "/stock/earnings" in url:
            return [{"actual": 2.1, "surprisePercent": 4.2, "period": "2026-06-30"}]
        raise AssertionError(url)

    monkeypatch.setattr(indicator_resources, "_json_request", fake_json)
    items = indicator_resources._finnhub_resources(charting.resolve_instrument("AAPL"), profile)
    assert {"market_cap", "pe_ttm", "roe", "eps_actual", "eps_surprise"} <= {item.id for item in items}
    assert all(item.source_id == "market:finnhub" for item in items)


def test_finnhub_dynamic_symbol_can_be_added_and_restored(isolated_app_db, monkeypatch):
    from app.modules.market import charting
    from app.modules.market import service as runtime

    provider = runtime.MARKET_PROVIDERS["finnhub"]
    provider.supported_symbols.pop("AMZN", None)
    provider._snapshot_cached_at = None
    charting._SYMBOL_SEARCH_CACHE.clear()
    instrument = main.MarketInstrument(
        id="FINNHUB:AMZN", symbol="AMZN", provider_symbol="AMZN", name="Amazon.com Inc.",
        asset_class="stock", market="NASDAQ", currency="USD", provider="finnhub",
        timezone="America/New_York", realtime=False, delayed=True,
        intervals=charting.ALL_INTERVALS, capabilities=["ohlcv", "technical_indicators"],
    )
    monkeypatch.setattr(charting, "_search_finnhub_symbols", lambda _query: [instrument])
    monkeypatch.setattr(
        provider, "_get",
        lambda path, _params: {"c": 180.0, "dp": 1.5} if path == "/quote" else {},
    )
    runtime.set_active_market_provider("finnhub")

    snapshot = runtime.add_watchlist_symbol(main.WatchlistItemCreate(symbol="AMZN"))
    assert snapshot.symbol == "AMZN"
    assert snapshot.name == "Amazon.com Inc."
    assert "AMZN" in runtime.persisted_watchlist_symbols("finnhub")

    provider.supported_symbols.pop("AMZN")
    provider._snapshot_cached_at = None
    restored = {item.symbol: item for item in provider.watchlist()}
    assert restored["AMZN"].price == 180.0
