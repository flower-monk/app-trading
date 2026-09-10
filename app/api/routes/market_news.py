from __future__ import annotations

from app.modules.infrastructure.database import SESSION_COOKIE, init_db
from app.modules.market.service import MARKET_PROVIDERS
from fastapi import HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
import asyncio

from fastapi import APIRouter

from app.core.config import settings
from app.schemas import (
    AppSettings,
    AppSettingsUpdate,
    Candle,
    DashboardOverview,
    FilterLibraryScoreReport,
    FilterStrategyCoverageReport,
    IndicatorSummary,
    InformationSourceHealth,
    InformationSourceSummary,
    Literal,
    MarketBreadth,
    MarketProviderConfig,
    MarketProviderStatus,
    MarketSymbol,
    NewsDetail,
    NewsEvent,
    NewsFeedPage,
    NewsImpactDiagnosticPage,
    NewsImpactGraphEdge,
    NewsImpactGraphEdgeCreate,
    NewsImpactGraphEdgeReview,
    NewsImpactGraphView,
    NewsImpactLink,
    NewsImpactOverrideUpdate,
    NewsRefreshResult,
    NewsSource,
    NewsSourceCreate,
    NewsSourceMonitor,
    NewsSourceOverview,
    NewsSourceRun,
    NewsSourceTestResult,
    NewsSourceUpdate,
    NewsSymbolOverrideUpdate,
    NewsTaggingBackfillRequest,
    NewsTaggingBackfillResult,
    NewsTaggingStatus,
    ScreenerPreset,
    ScreenerPresetCreate,
    ScreenerPresetUpdate,
    ScreenerPresetValidation,
    ScreenerResult,
    StrategyCandidateReport,
    SymbolSnapshot,
    SystemHealth,
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
)

from app.modules.market.service import (
    add_watchlist_symbol,
    bulk_update_watchlist,
    create_screener_preset,
    create_watchlist_group,
    create_watchlist_resource,
    delete_screener_preset,
    delete_watchlist_group,
    delete_watchlist_symbol,
    list_managed_market_snapshots,
    list_screener_presets,
    list_watchlist_groups,
    list_watchlist_resources,
    strategy_candidate_report,
    update_screener_preset,
    update_watchlist_group,
    update_watchlist_resource,
    validate_screener_presets,
)
from app.modules.market.service import (
    get_market_candles as get_market_candles_service,
    get_market_indicators as get_market_indicators_service,
    get_market_provider_status as get_market_provider_status_service,
    search_market_symbols as search_market_symbols_service,
)
from app.modules.news.service import (
    IMPACT_DIAGNOSTIC_REASONS,
    clear_news_symbol_override,
    create_news_impact_graph_edge,
    enqueue_news_tagging,
    get_news_detail,
    information_source_health,
    information_source_summary,
    latest_market_news,
    list_news_impact_diagnostics,
    list_news_impact_graph_edges,
    list_news_impact_links,
    list_news_source_runs,
    news_feed_page,
    news_impact_graph_view,
    news_source_overview,
    news_tagging_status,
    queue_news_tagging_backfill,
    review_news_impact_graph_edge,
    test_news_source,
    update_news_impact_override,
    update_news_symbol_override,
)
from app.modules.news.service import (
    create_news_source as create_news_source_service,
    list_news_events as list_news_events_service,
    list_news_sources as list_news_sources_service,
    update_news_source as update_news_source_service,
)
from app.modules.operations.service import get_dashboard_overview, news_source_monitoring, refresh_source
from app.modules.platform.service import (
    current_admin_user,
    get_app_settings,
    lookup_auth_session,
    set_active_market_provider,
    update_app_settings,
)
from app.modules.research.service import (
    export_dataset_csv,
    filter_library_score_report,
    filter_strategy_coverage_report,
    get_active_market_provider_name,
    get_system_health,
    run_screener,
)
from app.modules.trading.service import (
    get_market_breadth,
    list_watchlist_snapshots,
    market_stream_payload,
    refresh_due_news_sources,
)


router = APIRouter(tags=["Market data and news"])


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/dashboard", include_in_schema=False)
@router.get("/market", include_in_schema=False)
@router.get("/market-tools", include_in_schema=False)
@router.get("/watchlist", include_in_schema=False)
@router.get("/news", include_in_schema=False)
@router.get("/ai", include_in_schema=False)
@router.get("/factors", include_in_schema=False)
@router.get("/backtest", include_in_schema=False)
@router.get("/alerts", include_in_schema=False)
def workspace_page() -> FileResponse:
    return FileResponse(settings.page_dir / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/models", include_in_schema=False)
def models_workspace_page(request: Request) -> RedirectResponse:
    current_admin_user(request)
    query = request.url.query
    return RedirectResponse(f"/admin/models?{query}" if query else "/admin/models", status_code=303)


@router.websocket("/api/stream/market")
async def market_stream(websocket: WebSocket, interval: int = 5) -> None:
    session = lookup_auth_session(websocket.cookies.get(SESSION_COOKIE))
    if session is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    delay = max(2, min(60, interval))
    try:
        while True:
            await websocket.send_json(market_stream_payload())
            await asyncio.sleep(delay)
    except WebSocketDisconnect:
        return


@router.get("/api/health", response_model=SystemHealth)
def system_health() -> SystemHealth:
    return get_system_health()


@router.get("/api/dashboard/overview", response_model=DashboardOverview)
def dashboard_overview() -> DashboardOverview:
    return get_dashboard_overview()


@router.get("/api/settings", response_model=AppSettings)
def app_settings() -> AppSettings:
    return get_app_settings()


@router.patch("/api/settings", response_model=AppSettings)
def patch_app_settings(payload: AppSettingsUpdate) -> AppSettings:
    return update_app_settings(payload)


@router.get("/api/export/{dataset}", response_class=Response)
def export_dataset(dataset: str, symbol: str | None = None) -> Response:
    return export_dataset_csv(dataset, symbol=symbol)


@router.get("/api/market-provider", response_model=MarketProviderStatus)
def market_provider_status() -> MarketProviderStatus:
    return get_market_provider_status_service()


@router.get("/api/market-provider/options", response_model=list[MarketProviderStatus])
def market_provider_options() -> list[MarketProviderStatus]:
    active_name = get_active_market_provider_name()
    return [provider.status(is_active=name == active_name) for name, provider in MARKET_PROVIDERS.items()]


@router.post("/api/market-provider", response_model=MarketProviderStatus)
def update_market_provider(payload: MarketProviderConfig) -> MarketProviderStatus:
    return set_active_market_provider(payload.provider)


@router.get("/api/symbols", response_model=list[MarketSymbol])
def symbols(query: str = "") -> list[MarketSymbol]:
    return search_market_symbols_service(query)


@router.get("/api/watchlist", response_model=list[SymbolSnapshot])
def watchlist() -> list[SymbolSnapshot]:
    return list_watchlist_snapshots()


@router.post("/api/watchlist", response_model=SymbolSnapshot)
def add_watchlist_item(payload: WatchlistItemCreate) -> SymbolSnapshot:
    return add_watchlist_symbol(payload)


@router.delete("/api/watchlist/{symbol}", deprecated=True)
def remove_watchlist_item(symbol: str) -> dict[str, bool]:
    return delete_watchlist_symbol(symbol)


@router.get("/api/watchlist/groups", response_model=list[WatchlistGroup])
def watchlist_groups() -> list[WatchlistGroup]:
    return list_watchlist_groups()


@router.post("/api/watchlist/groups", response_model=WatchlistGroup)
def add_watchlist_group(payload: WatchlistGroupCreate) -> WatchlistGroup:
    return create_watchlist_group(payload)


@router.patch("/api/watchlist/groups/{group_id}", response_model=WatchlistGroup)
def patch_watchlist_group(group_id: str, payload: WatchlistGroupUpdate) -> WatchlistGroup:
    return update_watchlist_group(group_id, payload)


@router.delete("/api/watchlist/groups/{group_id}")
def remove_watchlist_group(group_id: str) -> dict[str, int | bool]:
    return delete_watchlist_group(group_id)


@router.get("/api/watchlist/items", response_model=WatchlistResourcePage)
def watchlist_items(
    status: Literal["active", "disabled", "all"] = "active",
    group_id: str | None = None,
    market: str | None = None,
    signal: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    sort_by: Literal["symbol", "price", "change", "rsi", "macd", "signal", "updated_at"] = "updated_at",
    direction: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> WatchlistResourcePage:
    return list_watchlist_resources(
        status=status,
        group_id=group_id,
        market=market,
        signal=signal,
        tag=tag,
        query=query,
        sort_by=sort_by,
        direction=direction,
        page=page,
        page_size=page_size,
    )


@router.post("/api/watchlist/items", response_model=WatchlistResourceItem)
def add_watchlist_resource(payload: WatchlistResourceCreate) -> WatchlistResourceItem:
    return create_watchlist_resource(payload)


@router.patch("/api/watchlist/items/{symbol}", response_model=WatchlistResourceItem)
def patch_watchlist_resource(symbol: str, payload: WatchlistResourceUpdate) -> WatchlistResourceItem:
    return update_watchlist_resource(symbol, payload)


@router.post("/api/watchlist/items/bulk-actions", response_model=WatchlistBulkResult)
def watchlist_bulk_actions(payload: WatchlistBulkAction) -> WatchlistBulkResult:
    return bulk_update_watchlist(payload)


@router.get("/api/market-breadth", response_model=MarketBreadth)
def market_breadth() -> MarketBreadth:
    snapshots, provenance = list_managed_market_snapshots()
    return get_market_breadth(snapshots, provenance)


@router.get("/api/screener", response_model=ScreenerResult)
def screener(
    min_change: float | None = None,
    max_change: float | None = None,
    min_rsi: float | None = Query(default=None, ge=0, le=100),
    max_rsi: float | None = Query(default=None, ge=0, le=100),
    min_trend: int | None = Query(default=None, ge=0, le=100),
    signal: str | None = None,
    market: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> ScreenerResult:
    snapshots, provenance = list_managed_market_snapshots()
    return run_screener(
        min_change=min_change,
        max_change=max_change,
        min_rsi=min_rsi,
        max_rsi=max_rsi,
        min_trend=min_trend,
        signal=signal,
        market=market,
        limit=limit,
        snapshots=snapshots,
        provenance=provenance,
    )


@router.get("/api/strategy-candidates", response_model=StrategyCandidateReport)
def strategy_candidates(
    min_change: float | None = None,
    max_change: float | None = None,
    min_rsi: float | None = None,
    max_rsi: float | None = None,
    min_trend: int | None = None,
    signal: str | None = None,
    market: str | None = None,
    factor_id: str | None = None,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = Query(default=6, ge=1, le=12),
) -> StrategyCandidateReport:
    return strategy_candidate_report(
        min_change=min_change,
        max_change=max_change,
        min_rsi=min_rsi,
        max_rsi=max_rsi,
        min_trend=min_trend,
        signal=signal,
        market=market,
        factor_id=factor_id,
        interval=interval,
        limit=limit,
    )


@router.get("/api/screener/presets", response_model=list[ScreenerPreset])
def screener_presets() -> list[ScreenerPreset]:
    return list_screener_presets()


@router.get("/api/screener/preset-validations", response_model=list[ScreenerPresetValidation])
def screener_preset_validations() -> list[ScreenerPresetValidation]:
    return validate_screener_presets()


@router.get("/api/filters/library-score", response_model=FilterLibraryScoreReport)
def filter_library_score() -> FilterLibraryScoreReport:
    return filter_library_score_report()


@router.get("/api/filters/strategy-coverage", response_model=FilterStrategyCoverageReport)
def filter_strategy_coverage() -> FilterStrategyCoverageReport:
    return filter_strategy_coverage_report()


@router.post("/api/screener/presets", response_model=ScreenerPreset)
def add_screener_preset(payload: ScreenerPresetCreate) -> ScreenerPreset:
    return create_screener_preset(payload)


@router.patch("/api/screener/presets/{preset_id}", response_model=ScreenerPreset)
def patch_screener_preset(preset_id: str, payload: ScreenerPresetUpdate) -> ScreenerPreset:
    return update_screener_preset(preset_id, payload)


@router.delete("/api/screener/presets/{preset_id}", deprecated=True)
def remove_screener_preset(preset_id: str) -> dict[str, bool]:
    return delete_screener_preset(preset_id)


@router.get("/api/candles/{symbol}", response_model=list[Candle])
def candles(
    symbol: str,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = Query(default=120, ge=40, le=240),
) -> list[Candle]:
    return get_market_candles_service(symbol, interval=interval, limit=limit)


@router.get("/api/indicators/{symbol}", response_model=list[IndicatorSummary])
def indicators(symbol: str) -> list[IndicatorSummary]:
    return get_market_indicators_service(symbol)


@router.get("/api/information-sources", response_model=list[NewsSource])
@router.get("/api/news-sources", response_model=list[NewsSource])
def list_news_sources() -> list[NewsSource]:
    return list_news_sources_service()


@router.get("/api/information-sources/summary", response_model=InformationSourceSummary)
def source_summary() -> InformationSourceSummary:
    return information_source_summary()


@router.get("/api/information-sources/health", response_model=list[InformationSourceHealth])
def source_health() -> list[InformationSourceHealth]:
    return information_source_health()


@router.post("/api/information-sources", response_model=NewsSource)
@router.post("/api/news-sources", response_model=NewsSource)
def create_news_source(payload: NewsSourceCreate) -> NewsSource:
    return create_news_source_service(payload)


@router.patch("/api/information-sources/{source_id}", response_model=NewsSource)
@router.patch("/api/news-sources/{source_id}", response_model=NewsSource)
def update_news_source(source_id: int, payload: NewsSourceUpdate) -> NewsSource:
    return update_news_source_service(source_id, payload)


@router.post("/api/information-sources/refresh", response_model=list[NewsRefreshResult])
@router.post("/api/news-sources/refresh", response_model=list[NewsRefreshResult])
def refresh_all_news_sources(
    force: bool = False,
    source_id: list[int] = Query(default=[]),
) -> list[NewsRefreshResult]:
    init_db()
    selected_sources = source_id if isinstance(source_id, list) else []
    return refresh_due_news_sources(force=force, trigger="manual", source_ids=selected_sources)


@router.post("/api/information-sources/{source_id}/refresh", response_model=NewsRefreshResult)
@router.post("/api/news-sources/{source_id}/refresh", response_model=NewsRefreshResult)
def refresh_news_source(source_id: int) -> NewsRefreshResult:
    init_db()
    return refresh_source(source_id, trigger="manual")


@router.post("/api/news-sources/{source_id}/test", response_model=NewsSourceTestResult)
def test_news_source_connection(source_id: int) -> NewsSourceTestResult:
    return test_news_source(source_id)


@router.get("/api/news-sources/monitoring", response_model=list[NewsSourceMonitor])
def monitor_news_sources() -> list[NewsSourceMonitor]:
    return news_source_monitoring()


@router.get("/api/news-sources/overview", response_model=NewsSourceOverview)
def news_sources_overview() -> NewsSourceOverview:
    return news_source_overview()


@router.get("/api/news-sources/{source_id}/runs", response_model=list[NewsSourceRun])
def news_source_runs(source_id: int, limit: int = Query(default=20, ge=1, le=200)) -> list[NewsSourceRun]:
    return list_news_source_runs(source_id, limit)


@router.get("/api/news", response_model=NewsFeedPage)
def all_news_events(
    source_id: list[int] = Query(default=[]),
    symbol: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NewsFeedPage:
    selected_sources = source_id if isinstance(source_id, list) else []
    return news_feed_page(selected_sources, symbol=symbol, limit=limit, offset=offset)


@router.get("/api/news/{symbol}", response_model=list[NewsEvent])
def news_events(
    symbol: str,
    limit: int = 7,
    source_id: list[int] = Query(default=[]),
) -> list[NewsEvent]:
    selected_sources = source_id if isinstance(source_id, list) else []
    return list_news_events_service(symbol, limit=limit, source_ids=selected_sources)


@router.get("/api/market-news", response_model=list[NewsEvent])
def market_news(limit: int = Query(default=10, ge=1, le=50)) -> list[NewsEvent]:
    return latest_market_news(limit)


@router.get("/api/news-events/{news_id}", response_model=NewsDetail)
def news_detail(news_id: str) -> NewsDetail:
    return get_news_detail(news_id)


@router.get("/api/news-events/{news_id}/impacts", response_model=list[NewsImpactLink])
def news_impacts(news_id: str, include_rejected: bool = False) -> list[NewsImpactLink]:
    get_news_detail(news_id)
    return list_news_impact_links(news_id, include_rejected)


@router.put("/api/news-events/{news_id}/impacts", response_model=NewsDetail)
def save_news_impacts(news_id: str, payload: NewsImpactOverrideUpdate, request: Request) -> NewsDetail:
    return update_news_impact_override(news_id, payload, current_admin_user(request))


@router.get("/api/news-impact-graph/view", response_model=NewsImpactGraphView)
def impact_graph_view(
    request: Request,
    status: list[str] | None = Query(default=None),
    query: str = "",
) -> NewsImpactGraphView:
    current_admin_user(request)
    allowed = {"approved", "pending", "failed", "rejected", "disabled"}
    selected = [value for value in (status or []) if value in allowed]
    return news_impact_graph_view(selected or None, query)


@router.get("/api/news-impact-graph/edges", response_model=list[NewsImpactGraphEdge])
def impact_graph_edges(request: Request, status: str | None = None, query: str = "") -> list[NewsImpactGraphEdge]:
    current_admin_user(request)
    return list_news_impact_graph_edges(status, query)


@router.get("/api/news-impact-graph/diagnostics", response_model=NewsImpactDiagnosticPage)
def impact_graph_diagnostics(
    request: Request,
    reason: str = "",
    query: str = "",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NewsImpactDiagnosticPage:
    current_admin_user(request)
    allowed_reasons = set(IMPACT_DIAGNOSTIC_REASONS)
    selected_reason = reason if reason in allowed_reasons else ""
    return list_news_impact_diagnostics(selected_reason, query, limit, offset)


@router.post("/api/news-impact-graph/edges", response_model=NewsImpactGraphEdge)
def add_impact_graph_edge(payload: NewsImpactGraphEdgeCreate, request: Request) -> NewsImpactGraphEdge:
    return create_news_impact_graph_edge(payload, current_admin_user(request))


@router.patch("/api/news-impact-graph/edges/{edge_id}", response_model=NewsImpactGraphEdge)
def patch_impact_graph_edge(edge_id: str, payload: NewsImpactGraphEdgeReview, request: Request) -> NewsImpactGraphEdge:
    return review_news_impact_graph_edge(edge_id, payload, current_admin_user(request))


@router.put("/api/news-events/{news_id}/symbols", response_model=NewsDetail)
def save_news_symbols(news_id: str, payload: NewsSymbolOverrideUpdate, request: Request) -> NewsDetail:
    actor = current_admin_user(request)
    return update_news_symbol_override(news_id, payload, actor)


@router.delete("/api/news-events/{news_id}/symbols/override", response_model=NewsDetail)
def restore_news_symbol_detection(news_id: str, request: Request) -> NewsDetail:
    current_admin_user(request)
    return clear_news_symbol_override(news_id)


@router.get("/api/news-tagging/status", response_model=NewsTaggingStatus)
def tagging_status() -> NewsTaggingStatus:
    return news_tagging_status()


@router.post("/api/news-tagging/backfill", response_model=NewsTaggingBackfillResult)
def backfill_news_tags(payload: NewsTaggingBackfillRequest, request: Request) -> NewsTaggingBackfillResult:
    current_admin_user(request)
    status = news_tagging_status()
    if payload.engine == "agent" and (status.agent_mode not in {"llm", "hybrid"} or not status.provider_ready):
        raise HTTPException(status_code=409, detail="请先为 News Tagger 配置并启用可用的模型服务")
    return queue_news_tagging_backfill(
        payload.days, payload.batch_size, scope=payload.scope, engine=payload.engine,
    )


@router.post("/api/news-events/{news_id}/retag", response_model=NewsTaggingBackfillResult)
def retag_news(news_id: str, request: Request) -> NewsTaggingBackfillResult:
    current_admin_user(request)
    queued = enqueue_news_tagging([news_id], trigger="manual", force=True)
    if not queued:
        raise HTTPException(status_code=404, detail="News event not found")
    return NewsTaggingBackfillResult(days=0, matched=1, queued=queued, skipped=0)
