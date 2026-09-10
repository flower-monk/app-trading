"""Temporary compatibility facade for pre-modular internal imports.

Production routes and background jobs use ``app.modules`` directly. This facade
keeps older extensions and tests working while callers migrate to domain APIs.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from app.schemas import *
from app.modules.ai.service import *
from app.modules.infrastructure.database import *
from app.modules.market.service import *
from app.modules.news.service import *
from app.modules.operations.service import *
from app.modules.platform.service import *
from app.modules.research.service import *
from app.modules.trading.service import *
from app.bootstrap.application import app
from app.bootstrap.middleware import authentication_middleware
from app.modules.infrastructure import database as _database
from app.web.router import (
    admin_agents_page,
    admin_impact_graph_page,
    admin_page,
    admin_resources_page,
    agents_page,
    login_page,
    model_management_page,
    model_playground_page,
    setup_page,
)

_INITIALIZED_DB_PATHS = _database._INITIALIZED_DB_PATHS


def init_db() -> None:
    """Synchronize legacy DB_PATH overrides before delegating to the DB bootstrap."""

    from app.modules.infrastructure import database

    database.DB_PATH = DB_PATH
    database.init_db()


def db_connect():
    from app.modules.infrastructure import database

    database.DB_PATH = DB_PATH
    return database.db_connect()

# Explicit endpoint re-exports preserve the legacy test and extension surface.

from app.api.routes.auth_admin import (
    auth_setup_status,
    auth_setup,
    auth_login,
    auth_me,
    auth_preferences,
    patch_auth_preferences,
    auth_logout,
    schedules,
    schedule_detail,
    patch_schedule,
    schedule_bulk_status,
    run_schedule,
    agent_providers,
    add_agent_provider,
    patch_agent_provider,
    test_agent_provider_connection,
    credential_status,
    credentials,
    credential_detail,
    add_credential,
    patch_credential,
    replace_credential_secret,
    clear_credential_secret,
    test_credential_connection,
    replace_market_provider_credential,
    agents,
    add_agent,
    all_agent_runs,
    agent_run_detail,
    agent_detail,
    patch_agent,
    clone_agent_api,
    run_agent_api,
    test_agent_api,
    agent_run_history,
    agent_proposals,
    approve_agent_proposal,
    reject_agent_proposal,
    admin_users,
    admin_create_user,
    admin_update_user,
    admin_reset_user_password,
    admin_revoke_user_sessions,
    admin_resources,
    admin_overview,
    admin_resource_create,
    admin_resource_list,
    admin_resource_export,
    admin_resource_detail,
    admin_resource_edit,
    admin_resource_status,
    admin_resource_bulk_status,
    admin_resource_rerun,
    admin_resource_action,
)

from app.api.routes.market_news import (
    index,
    workspace_page,
    models_workspace_page,
    market_stream,
    system_health,
    dashboard_overview,
    app_settings,
    patch_app_settings,
    export_dataset,
    market_provider_status,
    market_provider_options,
    update_market_provider,
    symbols,
    watchlist,
    add_watchlist_item,
    remove_watchlist_item,
    watchlist_groups,
    add_watchlist_group,
    patch_watchlist_group,
    remove_watchlist_group,
    watchlist_items,
    add_watchlist_resource,
    patch_watchlist_resource,
    watchlist_bulk_actions,
    market_breadth,
    screener,
    strategy_candidates,
    screener_presets,
    screener_preset_validations,
    filter_library_score,
    filter_strategy_coverage,
    add_screener_preset,
    patch_screener_preset,
    remove_screener_preset,
    candles,
    indicators,
    list_news_sources,
    source_summary,
    source_health,
    create_news_source,
    update_news_source,
    refresh_all_news_sources,
    refresh_news_source,
    test_news_source_connection,
    monitor_news_sources,
    news_sources_overview,
    news_source_runs,
    all_news_events,
    news_events,
    market_news,
    news_detail,
    news_impacts,
    save_news_impacts,
    impact_graph_view,
    impact_graph_edges,
    impact_graph_diagnostics,
    add_impact_graph_edge,
    patch_impact_graph_edge,
    save_news_symbols,
    restore_news_symbol_detection,
    tagging_status,
    backfill_news_tags,
    retag_news,
)

from app.api.routes.trading_ops import (
    alerts,
    create_alert,
    update_alert,
    remove_alert,
    evaluate_alerts,
    events,
    automation_settings,
    update_automation_settings,
    automation_run,
    automation_runs,
    notification_channels,
    add_notification_channel,
    patch_notification_channel,
    remove_notification_channel,
    notification_deliveries,
    portfolio,
    portfolio_risk,
    add_portfolio_position,
    patch_portfolio_position,
    remove_portfolio_position,
    chart_events,
    ai_briefing,
    ai_insights,
    factor_ideas,
)

from app.api.routes.quant_research import (
    factor_definitions,
    data_catalog,
    data_quality,
    data_freshness,
    market_regime,
    market_regime_history,
    market_regime_transition,
    regime_scenario_risk,
    research_readiness,
    research_coverage,
    factor_coverage,
    factor_lifecycle,
    data_lineage,
    research_run_audit,
    factor_coverage_backfill,
    research_coverage_backfill,
    research_actions,
    research_dashboard,
    research_health,
    filter_validation,
    filter_contribution,
    filter_admission,
    filter_stress_test,
    filter_regime_templates,
    regime_playbook,
    strategy_templates,
    add_strategy_template,
    patch_strategy_template,
    remove_strategy_template,
    validate_strategy_template,
    strategy_promotion_gate_list,
    strategy_promotion_gate,
    strategy_validations,
    strategy_validation_decay,
    strategy_risk_controls,
    market_state_controls,
    strategy_operating_plan,
    strategy_evidence_packs,
    add_factor_definition,
    patch_factor_definition,
    create_backtest_run,
    backtest_benchmark,
    backtest_forward_consistency,
    create_forward_test,
    forward_tests,
    forward_leaderboard,
    strategy_backtest_monitor,
    strategy_forward_monitor,
    strategy_sensitivity,
    strategy_revalidation_plan,
    strategy_validation_coverage,
    create_walk_forward_test,
    walk_forward_tests,
    walk_forward_leaderboard,
    create_stress_test,
    stress_tests,
    stress_leaderboard,
    robustness,
    strategy_drift,
    strategy_allocation,
    trade_readiness,
    regime_risk_gate,
    create_research_pipeline,
    create_factor_admission,
    factor_admission_queue,
    revalidation_queue,
    create_sensitivity_test,
    create_parameter_optimization,
    factor_leaderboard,
    factor_correlation,
    regime_exposure,
    regime_attribution,
    regime_factor_fit,
    strategy_leaderboard,
    strategy_regime_fit,
    strategy_regime_matrix,
    strategy_crowding,
    strategy_lifecycle,
    list_backtest_runs,
    backtest_summaries,
)

from app.api.routes.charting import (
    instruments,
    instrument_quote,
    chart_indicators,
    external_indicator_snapshots,
    chart_query,
    chart_series_query,
    chart_layouts,
    add_chart_layout,
    patch_chart_layout,
    remove_chart_layout,
    chart_stream,
)

from app.api.routes.data_sources import (
    data_sources,
    data_source_summary,
    data_source_health_checks,
    data_source,
    add_data_source,
    patch_data_source,
    test_data_source,
    refresh_data_source,
    data_source_routes,
    put_data_source_route,
    validate_data_source_config,
    publish_data_source_config,
    data_source_revisions,
    rollback_data_source_revision,
)

from app.api.routes.model_gateway import (
    provider_presets,
    gateway_summary,
    model_channels,
    add_model_channel,
    patch_model_channel,
    remove_model_channel,
    test_channel,
    sync_channel_models,
    batch_channel_status,
    model_routes,
    add_model_route,
    patch_model_route,
    remove_model_route,
    model_invocations,
    model_playground_chat,
)

from app.api.routes.financial_calendar import (
    calendar_events,
    calendar_settings,
    patch_calendar_settings,
    refresh_calendar,
    calendar_source_statuses,
    calendar_event_detail,
    put_calendar_reminder,
    remove_calendar_reminder,
    analyze_calendar_event,
)

from app.api.routes.paper_trading import (
    paper_accounts,
    patch_account,
    paper_portfolio,
    paper_orders,
    create_order,
    cancel_order,
    close_position,
    paper_fills,
    cash_ledger,
    run_matching,
)
