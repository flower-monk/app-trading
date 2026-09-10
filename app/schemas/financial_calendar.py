from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CalendarEventKind = Literal["macro", "earnings"]
CalendarEventStatus = Literal["scheduled", "awaiting_result", "released", "revised", "tentative", "cancelled"]
CalendarImportance = Literal["low", "medium", "high"]


class FinancialCalendarEvent(BaseModel):
    id: str
    kind: CalendarEventKind
    title: str
    category: str
    scheduled_at: str
    country: str | None = None
    currency: str | None = None
    symbol: str | None = None
    importance: CalendarImportance = "low"
    status: CalendarEventStatus = "scheduled"
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    provider_forecast: str | None = None
    actual_value: float | None = None
    forecast_value: float | None = None
    previous_value: float | None = None
    unit: str | None = None
    reference: str | None = None
    release_session: Literal["bmo", "amc", "dmh", "unknown"] = "unknown"
    source_id: str
    source_name: str
    source_url: str | None = None
    fetched_at: str
    updated_at: str
    stale: bool = False
    surprise_value: float | None = None
    surprise_direction: Literal["above", "below", "inline"] | None = None
    reminder_enabled: bool = False
    reminder_offsets: list[int] = Field(default_factory=list)
    analysis_status: Literal["none", "queued", "ready", "failed"] = "none"
    source_count: int = 1
    provenance: dict[str, str] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)


class FinancialCalendarSourceSnapshot(BaseModel):
    source_id: str
    source_name: str
    source_event_id: str
    fetched_at: str
    scheduled_at: str
    values: dict[str, object] = Field(default_factory=dict)


class FinancialCalendarPage(BaseModel):
    generated_at: str
    start: str
    end: str
    total: int
    items: list[FinancialCalendarEvent]
    sources: list[str] = Field(default_factory=list)
    stale: bool = False
    warning: str | None = None


class CalendarEventAnalysis(BaseModel):
    event_id: str
    status: Literal["queued", "ready", "failed"]
    summary: str = ""
    risk_level: Literal["info", "watch", "risk", "opportunity"] = "info"
    confidence: int = 0
    affected_markets: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    run_id: str | None = None
    model: str | None = None
    created_at: str
    updated_at: str


class FinancialCalendarEventDetail(BaseModel):
    event: FinancialCalendarEvent
    analysis: CalendarEventAnalysis | None = None
    source_snapshots: list[FinancialCalendarSourceSnapshot] = Field(default_factory=list)


class FinancialCalendarSettings(BaseModel):
    countries: list[str] = Field(default_factory=lambda: ["US", "CN", "EU", "GB", "JP", "HK"])
    default_range_days: int = Field(default=7, ge=1, le=90)
    sync_interval_seconds: int = Field(default=900, ge=300, le=86400)
    sync_enabled: bool = True
    reminders_enabled: bool = True
    high_impact_offsets: list[int] = Field(default_factory=lambda: [30, 5])
    earnings_watchlist_offset: int = Field(default=60, ge=5, le=1440)
    notification_channel_ids: list[str] = Field(default_factory=list)


class FinancialCalendarSettingsUpdate(BaseModel):
    countries: list[str] | None = None
    default_range_days: int | None = Field(default=None, ge=1, le=90)
    sync_interval_seconds: int | None = Field(default=None, ge=300, le=86400)
    sync_enabled: bool | None = None
    reminders_enabled: bool | None = None
    high_impact_offsets: list[int] | None = None
    earnings_watchlist_offset: int | None = Field(default=None, ge=5, le=1440)
    notification_channel_ids: list[str] | None = None


class CalendarEventReminderUpdate(BaseModel):
    enabled: bool = True
    offsets: list[int] = Field(default_factory=lambda: [30, 5], max_length=6)


class FinancialCalendarRefreshResult(BaseModel):
    status: Literal["completed", "warning", "failed"]
    fetched: int
    upserted: int
    merged: int = 0
    conflicts: int = 0
    sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: str
    finished_at: str


class FinancialCalendarSourceStatus(BaseModel):
    source_id: str
    last_run_at: str | None = None
    status: Literal["completed", "warning", "failed", "never"] = "never"
    fetched: int = 0
    upserted: int = 0
    merged: int = 0
    conflicts: int = 0
    last_error: str | None = None
