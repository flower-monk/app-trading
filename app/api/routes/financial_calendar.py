from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query, Request

from app.schemas import (
    CalendarEventAnalysis,
    CalendarEventReminderUpdate,
    FinancialCalendarEvent,
    FinancialCalendarEventDetail,
    FinancialCalendarPage,
    FinancialCalendarRefreshResult,
    FinancialCalendarSettings,
    FinancialCalendarSettingsUpdate,
    FinancialCalendarSourceStatus,
    Literal,
)
from app.modules.calendar import service as financial_calendar
from app.modules.platform.service import current_request_user


router = APIRouter(tags=["Financial calendar"])


@router.get("/api/financial-calendar", response_model=FinancialCalendarPage)
def calendar_events(
    start: date | None = None,
    end: date | None = None,
    kind: Literal["macro", "earnings"] | None = None,
    country: list[str] = Query(default=[]),
    importance: Literal["low", "medium", "high"] | None = None,
    watchlist_only: bool = False,
    query: str | None = Query(default=None, max_length=100),
    source_id: str | None = Query(default=None, max_length=120),
) -> FinancialCalendarPage:
    settings = financial_calendar.get_settings()
    start = start or date.today()
    end = end or (start + timedelta(days=settings.default_range_days - 1))
    if end < start:
        start, end = end, start
    if (end - start).days > 90:
        end = start + timedelta(days=90)
    return financial_calendar.list_events(start, end, kind, country, importance, watchlist_only, query, source_id)


@router.get("/api/financial-calendar/settings", response_model=FinancialCalendarSettings)
def calendar_settings() -> FinancialCalendarSettings:
    return financial_calendar.get_settings()


@router.patch("/api/financial-calendar/settings", response_model=FinancialCalendarSettings)
def patch_calendar_settings(payload: FinancialCalendarSettingsUpdate, request: Request) -> FinancialCalendarSettings:
    current_request_user(request)
    return financial_calendar.update_settings(payload)


@router.post("/api/financial-calendar/refresh", response_model=FinancialCalendarRefreshResult)
def refresh_calendar(request: Request) -> FinancialCalendarRefreshResult:
    current_request_user(request)
    return financial_calendar.refresh_all_sources()


@router.get("/api/financial-calendar/sources/status", response_model=list[FinancialCalendarSourceStatus])
def calendar_source_statuses() -> list[FinancialCalendarSourceStatus]:
    return financial_calendar.source_statuses()


@router.get("/api/financial-calendar/{event_id}", response_model=FinancialCalendarEventDetail)
def calendar_event_detail(event_id: str) -> FinancialCalendarEventDetail:
    return financial_calendar.get_event(event_id)


@router.put("/api/financial-calendar/{event_id}/reminder", response_model=FinancialCalendarEvent)
def put_calendar_reminder(event_id: str, payload: CalendarEventReminderUpdate, request: Request) -> FinancialCalendarEvent:
    current_request_user(request)
    return financial_calendar.update_event_reminder(event_id, payload)


@router.delete("/api/financial-calendar/{event_id}/reminder", response_model=FinancialCalendarEvent)
def remove_calendar_reminder(event_id: str, request: Request) -> FinancialCalendarEvent:
    current_request_user(request)
    return financial_calendar.delete_event_reminder(event_id)


@router.post("/api/financial-calendar/{event_id}/analyze", response_model=CalendarEventAnalysis)
def analyze_calendar_event(event_id: str, request: Request) -> CalendarEventAnalysis:
    current_request_user(request)
    return financial_calendar.queue_analysis(event_id)
