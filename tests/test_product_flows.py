from pathlib import Path
import asyncio

import pytest
from types import SimpleNamespace

from app import main


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
    assert main.lookup_auth_session(token)[0].id == admin.id

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
    assert {"users", "news-sources", "factors", "strategies", "backtests", "events", "admin-audit"} <= resource_ids
    factor_page = main.query_admin_resource("factors", query="momentum")
    assert factor_page.total >= 1
    assert factor_page.items[0]["_resource_id"]
    updated = main.admin_resource_status(
        "factors",
        "momentum-news",
        main.ResourceStatusUpdate(enabled=False),
        request,
    )
    assert updated["enabled"] is False
    assert main.get_factor_definition("momentum-news").enabled is False
    audit = main.query_admin_resource("admin-audit")
    assert audit.total >= 2
    status_audit = next(item for item in audit.items if item["resource_id"] == "momentum-news")
    assert status_audit["before_state"]["enabled"] is True
    assert status_audit["after_state"]["enabled"] is False


@pytest.fixture()
def isolated_app_db(tmp_path: Path):
    original_db = main.DB_PATH
    main.DB_PATH = tmp_path / "test_app_data.db"
    main.init_db()
    main.set_automation_settings(main.AutomationSettings(enabled=False, interval_seconds=300))
    main.update_market_provider(main.MarketProviderConfig(provider="mock"))
    yield
    main.DB_PATH = original_db


def test_market_provider_and_core_market_data(isolated_app_db):
    options = main.market_provider_options()
    assert {item.name for item in options} >= {"mock", "binance", "coinbase"}
    assert main.market_provider_status().name == "mock"
    assert len(main.symbols()) == 8
    assert len(main.watchlist()) == 8
    assert len(main.candles("AAPL", interval="15m", limit=40)) == 40
    assert len(main.indicators("AAPL")) == 4


def test_app_settings_persist_filters_and_automation(isolated_app_db):
    settings = main.app_settings()
    assert settings.active_market_provider == "mock"
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
    assert payload["provider"]["name"] == "mock"
    assert len(payload["watchlist"]) == len(main.watchlist())
    assert payload["breadth"]["total"] == len(payload["watchlist"])


def test_ai_briefing_summarizes_watchlist_risk_context(isolated_app_db):
    briefing = main.ai_briefing()

    assert briefing.title == "AI 全局盯盘简报"
    assert briefing.risk_level in {"info", "watch", "risk", "opportunity"}
    assert briefing.opportunities
    assert briefing.risks
    assert briefing.actions
    assert any("市场广度" in item for item in briefing.evidence)


def test_news_events_ai_factors_and_backtest(isolated_app_db):
    events = main.news_events("AAPL")
    assert len(events) > 0
    detail = main.news_detail(events[0].id)
    assert detail.id == events[0].id
    assert detail.ai_summary
    assert detail.key_points
    assert len(main.chart_events("AAPL")) > 0
    assert len(main.ai_insights("AAPL")) == 3
    assert len(main.factor_ideas("AAPL")) == 4

    run = main.run_backtest(
        main.BacktestRunRequest(symbol="AAPL", factor_id="momentum-news", interval="1h", limit=100)
    )
    assert run.symbol == "AAPL"
    assert len(run.equity_curve) >= 60
    summaries = main.backtest_summaries("AAPL")
    assert summaries[0].id == run.id


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
        "market_monitor", "news_analyst", "risk_sentinel", "factor_research", "briefing"
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

    monkeypatch.setattr(main, "agent_http_post", fake_post)
    run = main.enqueue_agent_run(agent.id, symbol="AAPL", test=True)
    completed = main.execute_agent_run(run.id)
    assert completed.status == "succeeded"
    assert completed.output["summary"] == "model output"
    assert completed.prompt_tokens == 12

    monkeypatch.setattr(main, "agent_http_post", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    fallback = main.execute_agent_run(main.enqueue_agent_run(agent.id, symbol="AAPL", test=True).id)
    assert fallback.status == "succeeded"
    assert fallback.error.startswith("LLM fallback:")
    assert fallback.output["evidence"]


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
    position = main.create_portfolio_position(
        main.PortfolioPositionCreate(symbol="AAPL", quantity=10, average_price=200, side="long")
    )
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


def test_system_health_summarizes_operational_components(isolated_app_db):
    health = main.get_system_health()
    component_names = {item.name for item in health.components}

    assert health.status in {"ok", "warn", "error"}
    assert {
        "数据库",
        "行情源",
        "自选观察",
        "自动化盯盘",
        "新闻源",
        "预警规则",
        "因子库",
        "通知投递",
        "模拟组合",
        "事件日志",
    }.issubset(component_names)
