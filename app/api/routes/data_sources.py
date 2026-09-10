from __future__ import annotations

from app.modules.infrastructure.database import init_db

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import (
    DataSourceConfigPublishRequest,
    DataSourceControlSummary,
    DataSourceHealthCheck,
    DataSourceProfile,
    DataSourceProfileCreate,
    DataSourceProfileUpdate,
    DataSourceRevision,
    DataSourceRoute,
    DataSourceRouteUpdate,
)
from app.modules.integrations.service import (
    control_summary,
    create_profile,
    get_profile,
    list_health_checks,
    list_profiles,
    list_revisions,
    list_routes,
    publish_route,
    rollback_revision,
    update_profile,
)
from app.modules.market.service import test_managed_data_source
from app.modules.news.service import create_news_source, refresh_source
from app.modules.platform.service import add_admin_audit, current_request_user, ensure_credential_binding


router = APIRouter(tags=["Data source control plane"])


@router.get("/api/data-sources", response_model=list[DataSourceProfile])
def data_sources(domain: str | None = None, query: str = "") -> list[DataSourceProfile]:
    init_db()
    return list_profiles(domain=domain, query=query)


@router.get("/api/data-sources/summary", response_model=DataSourceControlSummary)
def data_source_summary() -> DataSourceControlSummary:
    init_db()
    return control_summary()


@router.get("/api/data-sources/health-checks", response_model=list[DataSourceHealthCheck])
def data_source_health_checks(source_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> list[DataSourceHealthCheck]:
    init_db()
    return list_health_checks(source_id=source_id, limit=limit)


@router.get("/api/data-sources/{source_id}", response_model=DataSourceProfile)
def data_source(source_id: str) -> DataSourceProfile:
    init_db()
    return get_profile(source_id)


@router.post("/api/data-sources", response_model=DataSourceProfile)
def add_data_source(payload: DataSourceProfileCreate, request: Request) -> DataSourceProfile:
    actor = current_request_user(request)
    if payload.domain == "content":
        content_adapters = {"rss", "finnhub", "adanos", "gdelt", "cryptocompare", "alphavantage", "fred", "widget", "reference"}
        if payload.adapter not in content_adapters:
            raise HTTPException(status_code=422, detail="Adapter is not available for content ingestion")
        from app.schemas import NewsSourceCreate
        legacy = create_news_source(NewsSourceCreate(
            name=payload.name, url=payload.endpoint, type="rss" if payload.adapter == "rss" else "rest_api",
            category=payload.category, adapter=payload.adapter, provider=payload.provider,
            region=payload.markets[0] if payload.markets else "global", auth_type=payload.auth_type,
            api_key_env=payload.api_key_env, capabilities=payload.capabilities, config=payload.config,
            priority=payload.priority, reliability_weight=payload.reliability_weight, enabled=payload.enabled,
        ))
        init_db()
        source = get_profile(f"content:{legacy.id}")
    else:
        market_adapters = {
            "finnhub", "binance", "coinbase", "public_index", "alphavantage", "fred", "adanos",
            "trading_economics", "calendar_ics", "bea_calendar", "nbs_calendar", "ecb_calendar", "fomc_calendar",
        }
        if payload.adapter not in market_adapters:
            raise HTTPException(status_code=422, detail="Adapter is not available for market data")
        source = create_profile(payload)
        ensure_credential_binding("data_source", source.id, source.name, source.api_key_env)
        source = get_profile(source.id)
    add_admin_audit(actor, "create", "data_sources", source.id, f"Created data source {source.name}")
    return source


@router.patch("/api/data-sources/{source_id}", response_model=DataSourceProfile)
def patch_data_source(source_id: str, payload: DataSourceProfileUpdate, request: Request) -> DataSourceProfile:
    actor = current_request_user(request)
    source = update_profile(source_id, payload)
    ensure_credential_binding("data_source", source.id, source.name, source.api_key_env)
    source = get_profile(source.id)
    add_admin_audit(actor, "update", "data_sources", source_id, f"Updated data source {source.name}")
    return source


@router.post("/api/data-sources/{source_id}/test", response_model=DataSourceProfile)
def test_data_source(source_id: str, request: Request) -> DataSourceProfile:
    actor = current_request_user(request)
    source = test_managed_data_source(source_id)
    add_admin_audit(actor, "test", "data_sources", source_id, source.last_error or "Connection test passed")
    return source


@router.post("/api/data-sources/{source_id}/refresh")
def refresh_data_source(source_id: str, request: Request):
    actor = current_request_user(request)
    source = get_profile(source_id)
    if {"economic_calendar", "earnings_calendar"}.intersection(source.capabilities):
        from app.modules.calendar import service as financial_calendar
        result = financial_calendar.refresh_source(source_id)
        add_admin_audit(
            actor, "refresh", "data_sources", source_id,
            f"Fetched {result.fetched}; merged {result.merged}; conflicts {result.conflicts}",
        )
        return result
    if source.legacy_source_id is None:
        tested = test_managed_data_source(source_id)
        add_admin_audit(actor, "refresh", "data_sources", source_id, "Market source refreshed through connection test")
        return {"source_id": source_id, "status": tested.health_status, "message": tested.last_error or "连接正常"}
    result = refresh_source(source.legacy_source_id)
    add_admin_audit(actor, "refresh", "data_sources", source_id, f"Fetched {result.fetched}; inserted {result.inserted}")
    return result


@router.get("/api/data-source-routes", response_model=list[DataSourceRoute])
def data_source_routes() -> list[DataSourceRoute]:
    init_db()
    return list_routes()


@router.put("/api/data-source-routes/{market}/{capability}", response_model=DataSourceRoute)
def put_data_source_route(
    market: str, capability: str, payload: DataSourceRouteUpdate, request: Request
) -> DataSourceRoute:
    actor = current_request_user(request)
    return _validate_and_publish_route(market, capability, payload, actor)


def _validate_route_sources(source_ids: list[str]) -> list[dict[str, object]]:
    validation: list[dict[str, object]] = []
    for source_id in source_ids:
        result = test_managed_data_source(source_id)
        passed = result.health_status == "healthy"
        validation.append({
            "source_id": source_id, "source_name": result.name,
            "status": "pass" if passed else "fail",
            "latency_ms": result.last_latency_ms or 0,
            "message": result.last_error or "连接及能力检测通过",
        })
    failures = [item for item in validation if item["status"] == "fail"]
    if failures:
        raise HTTPException(status_code=409, detail={"message": "数据源检测失败，旧路由保持有效", "validation": validation})
    return validation


def _validate_and_publish_route(market: str, capability: str, payload: DataSourceRouteUpdate, actor) -> DataSourceRoute:
    validation = _validate_route_sources(payload.source_ids)
    route = publish_route(
        market, capability, payload.source_ids, payload.mode,
        payload.expected_version, actor.username, validation,
    )
    add_admin_audit(actor, "publish", "data_source_routes", route.id, f"Published route version {route.version}")
    return route


@router.post("/api/data-source-config/validate")
def validate_data_source_config(payload: DataSourceConfigPublishRequest, request: Request):
    current_request_user(request)
    validation = _validate_route_sources(payload.source_ids)
    return {"valid": True, "market": payload.market.upper(), "capability": payload.capability, "validation": validation}


@router.post("/api/data-source-config/publish", response_model=DataSourceRoute)
def publish_data_source_config(payload: DataSourceConfigPublishRequest, request: Request) -> DataSourceRoute:
    actor = current_request_user(request)
    return _validate_and_publish_route(payload.market, payload.capability, payload, actor)


@router.get("/api/data-source-config/revisions", response_model=list[DataSourceRevision])
def data_source_revisions(limit: int = Query(default=100, ge=1, le=500)) -> list[DataSourceRevision]:
    init_db()
    return list_revisions(limit=limit)


@router.post("/api/data-source-config/revisions/{revision_id}/rollback", response_model=DataSourceRoute)
def rollback_data_source_revision(revision_id: str, request: Request) -> DataSourceRoute:
    actor = current_request_user(request)
    route = rollback_revision(revision_id, actor.username)
    add_admin_audit(actor, "rollback", "data_source_routes", route.id, f"Rolled back from {revision_id}")
    return route
