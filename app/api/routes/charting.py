from __future__ import annotations

from app.modules.infrastructure.database import SESSION_COOKIE

import asyncio
from bisect import bisect_left
from datetime import datetime, timezone
from time import perf_counter
from typing import Literal

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from app.schemas import (
    ChartAssetClass,
    ChartBundle,
    ChartEventMarker,
    ChartEventPreview,
    ChartInterval,
    ChartLayout,
    ChartLayoutCreate,
    ChartLayoutUpdate,
    ChartNewsMode,
    ChartQuery,
    ChartSeriesBundle,
    ChartSeriesQuery,
    IndicatorDefinition,
    IndicatorSnapshotBundle,
    InstrumentQuote,
    Literal,
    MarketInstrument,
    NewsEvent,
)
from app.modules.market.charting import (
    MINIMUM_RANGE_BY_INTERVAL,
    build_chart_bundle,
    build_chart_series,
    create_chart_layout,
    delete_chart_layout,
    indicator_catalog,
    list_chart_layouts,
    list_instruments,
    latest_instrument_quote,
    resolve_instrument,
    update_chart_layout,
)
from app.modules.market.indicators import indicator_snapshots
from app.modules.market.service import get_market_provider
from app.modules.news.service import source_articles_to_events_filtered
from app.modules.platform.service import current_request_user, lookup_auth_session


router = APIRouter(tags=["Professional charting"])


def news_events(symbol: str, limit: int = 200) -> list[NewsEvent]:
    """Domain-facing news lookup kept patchable for chart marker tests."""

    return source_articles_to_events_filtered(symbol, limit, [])


@router.get("/api/instruments", response_model=list[MarketInstrument])
def instruments(
    query: str = Query(default="", max_length=80),
    asset_class: ChartAssetClass | None = None,
) -> list[MarketInstrument]:
    return list_instruments(query=query, asset_class=asset_class)


@router.get("/api/instruments/{instrument_id}/quote", response_model=InstrumentQuote)
def instrument_quote(instrument_id: str) -> InstrumentQuote:
    instrument = resolve_instrument(instrument_id)
    if not instrument.positionable:
        raise HTTPException(status_code=422, detail="该标的不可用于模拟持仓")
    provider = get_market_provider()
    aliases = {instrument.symbol.upper(), instrument.symbol.upper().replace("USDT", "USD")}
    snapshot = next((item for item in provider.watchlist() if item.symbol.upper() in aliases), None)
    if snapshot and snapshot.price > 0:
        provider_status = provider.status(is_active=True)
        return InstrumentQuote(
            instrument_id=instrument.id,
            symbol=instrument.symbol,
            price=snapshot.price,
            currency=instrument.currency,
            as_of=datetime.now(timezone.utc).isoformat(),
            source=provider_status.display_name,
            stale=provider_status.is_delayed,
        )
    return latest_instrument_quote(instrument.id)


@router.get("/api/chart/indicators", response_model=list[IndicatorDefinition])
def chart_indicators(instrument_id: str | None = None) -> list[IndicatorDefinition]:
    return indicator_catalog(instrument_id)


@router.get("/api/indicator-snapshots/{instrument_id}", response_model=IndicatorSnapshotBundle)
def external_indicator_snapshots(instrument_id: str) -> IndicatorSnapshotBundle:
    return indicator_snapshots(instrument_id)


INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
    "1w": 604800, "1mo": 2678400,
}


EventMappingReason = Literal["exact", "within_candle", "next_trading_candle"]


def _event_timestamp(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def _event_candle_mapping(
    candle_times: list[int], event_time: int, interval: ChartInterval,
) -> tuple[int, EventMappingReason] | None:
    insertion = bisect_left(candle_times, event_time)
    if insertion < len(candle_times) and candle_times[insertion] == event_time:
        return insertion, "exact"
    if insertion == 0:
        return None
    expected_end = candle_times[insertion - 1] + INTERVAL_SECONDS[interval]
    if event_time < expected_end:
        return insertion - 1, "within_candle"
    if insertion < len(candle_times):
        return insertion, "next_trading_candle"
    return None


def _aggregate_sentiment(events: list[NewsEvent]) -> Literal["bullish", "bearish", "neutral", "mixed"]:
    bullish = 0.0
    bearish = 0.0
    for event in events:
        weight = float(event.impact_score)
        if event.sentiment == "bullish":
            bullish += weight
        elif event.sentiment == "bearish":
            bearish += weight
        elif event.sentiment == "mixed":
            bullish += weight / 2
            bearish += weight / 2
    directional = bullish + bearish
    if directional == 0:
        return "neutral"
    if bullish / directional >= 0.3 and bearish / directional >= 0.3:
        return "mixed"
    return "bullish" if bullish > bearish else "bearish"


def _attach_news_markers(bundle: ChartBundle, news_mode: ChartNewsMode = "key") -> ChartBundle:
    if not bundle.candles or news_mode == "off":
        return bundle
    candle_times = [item.time for item in bundle.candles]
    grouped: dict[int, list[tuple[NewsEvent, EventMappingReason]]] = {}
    minimum_impact = 60 if news_mode == "key" else 0
    for event in news_events(bundle.instrument.symbol, limit=200):
        if event.impact_score < minimum_impact and not event.manual_association:
            continue
        mapping = _event_candle_mapping(candle_times, _event_timestamp(event.published_at), bundle.interval)
        if mapping is not None:
            index, reason = mapping
            grouped.setdefault(index, []).append((event, reason))
    markers: list[ChartEventMarker] = []
    for index, mapped_events in grouped.items():
        events = [event for event, _reason in mapped_events]
        reasons = {event.id: reason for event, reason in mapped_events}
        primary = sorted(events, key=lambda item: item.impact_score, reverse=True)[0]
        aggregate_sentiment = _aggregate_sentiment(events)
        previews = [
            ChartEventPreview(
                id=event.id,
                title=event.title,
                summary=event.summary,
                source=event.source,
                published_at=event.published_at,
                event_type=event.event_type,
                sentiment=event.sentiment,
                impact_score=event.impact_score,
                price_reaction=event.price_reaction,
                mapping_reason=reasons[event.id],
            )
            for event in sorted(events, key=lambda item: (item.impact_score, item.published_at), reverse=True)
        ]
        marker_id = f"news:{bundle.instrument.id}:{bundle.interval}:{candle_times[index]}"
        markers.append(
            ChartEventMarker(
                id=marker_id,
                time=candle_times[index],
                candle_time=candle_times[index],
                title=primary.title,
                sentiment=aggregate_sentiment,
                aggregate_sentiment=aggregate_sentiment,
                impact_score=primary.impact_score,
                count=len(events),
                mapping_reason=reasons[primary.id],
                events=previews,
            )
        )
    bundle.markers = sorted(markers, key=lambda item: item.time)
    return bundle


def _server_timing(timings: dict[str, float]) -> str:
    return ", ".join(f"{name};dur={duration:.1f}" for name, duration in timings.items())


@router.post("/api/chart/query", response_model=ChartBundle)
def chart_query(payload: ChartQuery, response: Response) -> ChartBundle:
    timings: dict[str, float] = {}
    bundle = build_chart_bundle(payload, timings=timings)
    news_started = perf_counter()
    bundle = _attach_news_markers(bundle, payload.news_mode)
    timings["news"] = (perf_counter() - news_started) * 1000
    response.headers["Server-Timing"] = _server_timing(timings)
    return bundle


@router.post("/api/chart/series/query", response_model=ChartSeriesBundle)
def chart_series_query(payload: ChartSeriesQuery, response: Response) -> ChartSeriesBundle:
    timings: dict[str, float] = {}
    bundle = build_chart_series(payload, timings=timings)
    response.headers["Server-Timing"] = _server_timing(timings)
    return bundle


@router.get("/api/chart/layouts", response_model=list[ChartLayout])
def chart_layouts(request: Request, asset_class: ChartAssetClass | None = None) -> list[ChartLayout]:
    user = current_request_user(request)
    return list_chart_layouts(user.id, asset_class=asset_class)


@router.post("/api/chart/layouts", response_model=ChartLayout)
def add_chart_layout(payload: ChartLayoutCreate, request: Request) -> ChartLayout:
    user = current_request_user(request)
    return create_chart_layout(user.id, payload)


@router.patch("/api/chart/layouts/{layout_id}", response_model=ChartLayout)
def patch_chart_layout(layout_id: str, payload: ChartLayoutUpdate, request: Request) -> ChartLayout:
    user = current_request_user(request)
    return update_chart_layout(user.id, layout_id, payload)


@router.delete("/api/chart/layouts/{layout_id}")
def remove_chart_layout(layout_id: str, request: Request) -> dict[str, bool]:
    user = current_request_user(request)
    return delete_chart_layout(user.id, layout_id)


@router.websocket("/api/stream/chart")
async def chart_stream(
    websocket: WebSocket,
    instrument_id: str,
    interval: ChartInterval = "15m",
) -> None:
    session = lookup_auth_session(websocket.cookies.get(SESSION_COOKIE))
    if session is None:
        await websocket.close(code=4401)
        return
    instrument = resolve_instrument(instrument_id)
    await websocket.accept()
    delay = 5 if instrument.realtime else 30
    try:
        while True:
            bundle = build_chart_bundle(
                ChartQuery(
                    instrument_id=instrument.id,
                    interval=interval,
                    range=MINIMUM_RANGE_BY_INTERVAL[interval],
                    indicators=[],
                    limit=100,
                )
            )
            await websocket.send_json(
                {
                    "type": "chart_update",
                    "instrument_id": instrument.id,
                    "interval": interval,
                    "candle": bundle.candles[-1].model_dump(mode="json"),
                    "status": bundle.status.model_dump(mode="json"),
                }
            )
            await asyncio.sleep(delay)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except Exception as exc:
        await websocket.send_json({"type": "chart_error", "message": str(exc)[:240]})
        await websocket.close(code=1011)
