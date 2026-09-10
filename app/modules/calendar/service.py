from __future__ import annotations

import json
import re
import sqlite3
from html.parser import HTMLParser
from time import perf_counter
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.core.config import settings as app_settings
from app.db.session import connect
from app.schemas import (
    CalendarEventAnalysis,
    CalendarEventReminderUpdate,
    FinancialCalendarEvent,
    FinancialCalendarEventDetail,
    FinancialCalendarPage,
    FinancialCalendarRefreshResult,
    FinancialCalendarSourceSnapshot,
    FinancialCalendarSourceStatus,
    FinancialCalendarSettings,
    FinancialCalendarSettingsUpdate,
)


DATABASE_PATH = app_settings.database_path
DEFAULT_COUNTRIES = ["US", "CN", "EU", "GB", "JP", "HK"]
RESULT_BACKFILL_DAYS = 7
MACRO_RESULT_GRACE = timedelta(minutes=15)
EARNINGS_RESULT_GRACE = timedelta(hours=8)


class CalendarEntitlementError(RuntimeError):
    """The credential is valid enough to reach the provider but lacks this calendar capability."""


def set_database_path(path) -> None:
    global DATABASE_PATH
    DATABASE_PATH = path


def _connect() -> sqlite3.Connection:
    return connect(DATABASE_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_financial_calendar_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS financial_calendar_events (
            id TEXT PRIMARY KEY, source_event_id TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, category TEXT NOT NULL, scheduled_at TEXT NOT NULL,
            country TEXT, currency TEXT, symbol TEXT, importance TEXT NOT NULL,
            status TEXT NOT NULL, actual TEXT, forecast TEXT, previous TEXT,
            provider_forecast TEXT, actual_value REAL, forecast_value REAL,
            previous_value REAL, unit TEXT, reference TEXT, release_session TEXT NOT NULL,
            source_id TEXT NOT NULL, source_name TEXT NOT NULL, source_url TEXT,
            fetched_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(source_id, source_event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_financial_calendar_time
            ON financial_calendar_events(scheduled_at, kind, importance);
        CREATE INDEX IF NOT EXISTS idx_financial_calendar_symbol
            ON financial_calendar_events(symbol, scheduled_at);
        CREATE TABLE IF NOT EXISTS financial_calendar_sync_runs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, fetched INTEGER NOT NULL,
            upserted INTEGER NOT NULL, sources TEXT NOT NULL, errors TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            source_id TEXT, merged INTEGER NOT NULL DEFAULT 0,
            conflicts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS financial_calendar_event_sources (
            canonical_event_id TEXT NOT NULL, source_id TEXT NOT NULL,
            source_name TEXT NOT NULL, source_event_id TEXT NOT NULL,
            canonical_key TEXT NOT NULL, payload TEXT NOT NULL,
            fetched_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(source_id, source_event_id),
            FOREIGN KEY(canonical_event_id) REFERENCES financial_calendar_events(id)
        );
        CREATE INDEX IF NOT EXISTS idx_calendar_source_canonical
            ON financial_calendar_event_sources(canonical_event_id, source_id);
        CREATE TABLE IF NOT EXISTS financial_calendar_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS financial_calendar_reminders (
            event_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, offsets TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES financial_calendar_events(id)
        );
        CREATE TABLE IF NOT EXISTS financial_calendar_reminder_deliveries (
            id TEXT PRIMARY KEY, event_id TEXT NOT NULL, offset_minutes INTEGER NOT NULL,
            delivered_at TEXT NOT NULL, UNIQUE(event_id, offset_minutes)
        );
        CREATE TABLE IF NOT EXISTS financial_calendar_analyses (
            event_id TEXT PRIMARY KEY, status TEXT NOT NULL, summary TEXT NOT NULL,
            risk_level TEXT NOT NULL, confidence INTEGER NOT NULL,
            affected_markets TEXT NOT NULL, symbols TEXT NOT NULL, scenarios TEXT NOT NULL,
            risks TEXT NOT NULL, evidence TEXT NOT NULL, run_id TEXT, model TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES financial_calendar_events(id)
        );
        """
    )
    event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(financial_calendar_events)").fetchall()}
    if "canonical_key" not in event_columns:
        connection.execute("ALTER TABLE financial_calendar_events ADD COLUMN canonical_key TEXT NOT NULL DEFAULT ''")
    if "provenance" not in event_columns:
        connection.execute("ALTER TABLE financial_calendar_events ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}'")
    if "conflicts" not in event_columns:
        connection.execute("ALTER TABLE financial_calendar_events ADD COLUMN conflicts TEXT NOT NULL DEFAULT '[]'")
    sync_columns = {row["name"] for row in connection.execute("PRAGMA table_info(financial_calendar_sync_runs)").fetchall()}
    for name, definition in (
        ("source_id", "TEXT"), ("merged", "INTEGER NOT NULL DEFAULT 0"),
        ("conflicts", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in sync_columns:
            connection.execute(f"ALTER TABLE financial_calendar_sync_runs ADD COLUMN {name} {definition}")
    defaults = FinancialCalendarSettings().model_dump(mode="json")
    connection.execute(
        "INSERT OR IGNORE INTO financial_calendar_settings VALUES (1, ?, ?)",
        (json.dumps(defaults, ensure_ascii=False), _now()),
    )


def initialize() -> None:
    with _connect() as connection:
        ensure_financial_calendar_schema(connection)


def get_settings() -> FinancialCalendarSettings:
    initialize()
    with _connect() as connection:
        row = connection.execute("SELECT payload FROM financial_calendar_settings WHERE id=1").fetchone()
    return FinancialCalendarSettings.model_validate(json.loads(row["payload"]))


def update_settings(payload: FinancialCalendarSettingsUpdate) -> FinancialCalendarSettings:
    current = get_settings().model_dump(mode="json")
    current.update(payload.model_dump(exclude_none=True, mode="json"))
    current["countries"] = list(dict.fromkeys(str(item).strip().upper() for item in current["countries"] if str(item).strip()))[:30]
    current["high_impact_offsets"] = sorted({max(1, min(1440, int(item))) for item in current["high_impact_offsets"]}, reverse=True)[:6]
    saved = FinancialCalendarSettings.model_validate(current)
    with _connect() as connection:
        connection.execute(
            "UPDATE financial_calendar_settings SET payload=?, updated_at=? WHERE id=1",
            (json.dumps(saved.model_dump(mode="json"), ensure_ascii=False), _now()),
        )
    return saved


def _parse_datetime(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("事件缺少发布时间")
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    number = float(match.group())
    suffix = str(value).strip().upper()
    if suffix.endswith("K"):
        number *= 1_000
    elif suffix.endswith("M"):
        number *= 1_000_000
    elif suffix.endswith("B"):
        number *= 1_000_000_000
    return number


def _importance(value: object) -> str:
    if isinstance(value, (int, float)):
        return {3: "high", 2: "medium"}.get(int(value), "low")
    raw = str(value or "").lower()
    return "high" if raw in {"3", "high"} else "medium" if raw in {"2", "medium"} else "low"


def _country_code(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    mapping = {
        "united states": "US", "china": "CN", "euro area": "EU", "european union": "EU",
        "united kingdom": "GB", "japan": "JP", "hong kong": "HK",
    }
    return mapping.get(raw.lower(), raw.upper() if len(raw) <= 3 else raw)


def _event_id(source_id: str, source_event_id: str) -> str:
    return f"calendar-{sha256(f'{source_id}:{source_event_id}'.encode()).hexdigest()[:18]}"


def _request_json(url: str, timeout: int) -> object:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "AppTrading/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in {401, 403}:
            raise CalendarEntitlementError(f"当前 API 套餐不支持该日历能力（HTTP {exc.code}）") from exc
        if exc.code == 429:
            raise RuntimeError("数据源请求频率超限（HTTP 429），请降低刷新周期") from exc
        raise RuntimeError(f"数据源请求失败（HTTP {exc.code}）：{detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"数据源连接或响应解析失败：{exc}") from exc


def _request_text(url: str, timeout: int) -> str:
    request = Request(url, headers={
        "Accept": "text/calendar,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 (compatible; AppTrading/0.1; +https://www.bls.gov/)",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code == 429:
            raise RuntimeError("数据源请求频率超限（HTTP 429），请降低刷新周期") from exc
        raise RuntimeError(f"数据源请求失败（HTTP {exc.code}）：{detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"数据源连接失败：{exc}") from exc


def _unfold_ical(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw.rstrip("\r"))
    return lines


def _ical_datetime(value: str, timezone_name: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if "T" not in raw:
        return datetime.strptime(raw[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    parsed = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
    try:
        from zoneinfo import ZoneInfo
        return parsed.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
    except Exception:
        return parsed.replace(tzinfo=timezone.utc)


def _official_ics_events(profile, _api_key: str, start: date, end: date) -> list[dict[str, object]]:
    text = _request_text(profile.endpoint, profile.timeout_seconds)
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ical(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            blocks.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.split(";", 1)[0].upper()] = value.replace("\\,", ",").replace("\\n", " ").strip()
    if not blocks and "BEGIN:VCALENDAR" not in text:
        raise RuntimeError("官方日历源未返回有效 iCalendar 内容")
    country = str(profile.config.get("country") or (profile.markets[0] if profile.markets else "US")).upper()
    events: list[dict[str, object]] = []
    for item in blocks:
        if not item.get("SUMMARY") or not item.get("DTSTART"):
            continue
        scheduled = _ical_datetime(item["DTSTART"], profile.timezone)
        if not start <= scheduled.date() <= end:
            continue
        title = item["SUMMARY"]
        source_event_id = item.get("UID") or f"{title}:{scheduled.isoformat()}"
        events.append(_official_event(profile, source_event_id, title, scheduled, country, item.get("URL")))
    return events


def _bea_calendar_events(profile, _api_key: str, start: date, end: date) -> list[dict[str, object]]:
    body = _request_json(profile.endpoint, profile.timeout_seconds)
    if not isinstance(body, dict):
        raise RuntimeError("BEA 发布日历返回了无法识别的 JSON 结构")
    events: list[dict[str, object]] = []
    for title, value in body.items():
        if title == "file_last_updated" or not isinstance(value, dict):
            continue
        for raw in value.get("release_dates", []):
            scheduled = _parse_datetime(raw)
            if start <= scheduled.date() <= end:
                events.append(_official_event(
                    profile, f"{title}:{scheduled.isoformat()}", title, scheduled, "US",
                    "https://www.bea.gov/news/schedule/",
                ))
    return events


class _TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"[ \t]+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.text.append(value)
        if self._cell is not None:
            self._cell.append(data)


def _localized_datetime(year: int, month: int, day: int, hour: int, minute: int, timezone_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
    except Exception:
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _nbs_calendar_events(profile, _api_key: str, start: date, end: date) -> list[dict[str, object]]:
    text = _request_text(profile.endpoint, profile.timeout_seconds)
    parser = _TableTextParser()
    parser.feed(text)
    year_match = re.search(r"(20\d{2})年.*?发布日程", text)
    year = int(year_match.group(1)) if year_match else start.year
    events: list[dict[str, object]] = []
    for cells in parser.rows:
        if len(cells) < 3 or not re.match(r"^\d+$", cells[0].strip()):
            continue
        title = re.sub(r"\s+", " ", cells[1]).strip()
        for month, cell in enumerate(cells[2:14], start=1):
            days = [int(value) for value in re.findall(r"(?<!\d)(\d{1,2})\s*/", cell)]
            clocks = [(int(hour), int(minute)) for hour, minute in re.findall(r"(\d{1,2})\s*[：:]\s*(\d{2})", cell)]
            for index, day in enumerate(days):
                hour, minute = clocks[index] if index < len(clocks) else clocks[0] if clocks else (10, 0)
                try:
                    scheduled = _localized_datetime(year, month, day, hour, minute, profile.timezone)
                except ValueError:
                    continue
                if start <= scheduled.date() <= end:
                    events.append(_official_event(
                        profile, f"{title}:{scheduled.isoformat()}", title, scheduled, "CN", profile.endpoint,
                    ))
    if not parser.rows:
        raise RuntimeError("国家统计局发布日历未返回可解析的表格")
    return events


def _ecb_calendar_events(profile, _api_key: str, start: date, end: date) -> list[dict[str, object]]:
    text = _request_text(profile.endpoint, profile.timeout_seconds)
    parser = _TableTextParser()
    parser.feed(text)
    events: list[dict[str, object]] = []
    ignored = {"tentative", "includes press release", "reference period"}
    for index, value in enumerate(parser.text):
        match = re.fullmatch(r"(\d{2})/(\d{2})/(20\d{2})\s+(\d{2}):(\d{2})\s*(?:CET|CEST)?", value, re.I)
        if not match:
            continue
        day, month, year, hour, minute = map(int, match.groups())
        title = next((
            candidate for candidate in parser.text[index + 1:index + 6]
            if candidate.lower() not in ignored and not candidate.lower().startswith("reference period")
            and not re.match(r"^\d{2}/\d{2}/20\d{2}", candidate)
        ), "ECB statistical release")
        scheduled = _localized_datetime(year, month, day, hour, minute, profile.timezone)
        if start <= scheduled.date() <= end:
            events.append(_official_event(
                profile, f"{title}:{scheduled.isoformat()}", title, scheduled, "EU", profile.endpoint,
            ))
    if not parser.text:
        raise RuntimeError("ECB 统计日历未返回可解析内容")
    return events


def _fomc_calendar_events(profile, _api_key: str, start: date, end: date) -> list[dict[str, object]]:
    text = _request_text(profile.endpoint, profile.timeout_seconds)
    parser = _TableTextParser()
    parser.feed(text)
    months = {name: index for index, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), start=1
    )}
    current_year: int | None = None
    events: list[dict[str, object]] = []
    for index, value in enumerate(parser.text[:-1]):
        year_match = re.fullmatch(r"(20\d{2}) FOMC Meetings", value)
        if year_match:
            current_year = int(year_match.group(1))
            continue
        if current_year is None or value not in months:
            continue
        day_match = re.fullmatch(r"(?:\d{1,2}-)?(\d{1,2})\*?", parser.text[index + 1])
        if not day_match:
            continue
        scheduled = _localized_datetime(current_year, months[value], int(day_match.group(1)), 14, 0, profile.timezone)
        if start <= scheduled.date() <= end:
            title = "FOMC Interest Rate Decision"
            events.append(_official_event(
                profile, f"fomc:{scheduled.date().isoformat()}", title, scheduled, "US", profile.endpoint,
            ))
    if not any("FOMC Meetings" in value for value in parser.text):
        raise RuntimeError("FOMC 日历页面未返回可解析的会议计划")
    return events


def _official_event(profile, source_event_id: str, title: str, scheduled: datetime, country: str, url: str | None) -> dict[str, object]:
    importance = "high" if re.search(r"CPI|employment|payroll|GDP|personal income|FOMC|interest rate|unemployment|producer price", title, re.I) else "medium"
    return {
        "id": _event_id(profile.id, source_event_id), "source_event_id": source_event_id,
        "kind": "macro", "title": title, "category": _indicator_code(title),
        "scheduled_at": scheduled.astimezone(timezone.utc).isoformat(), "country": country,
        "currency": {"US": "USD", "CN": "CNY", "EU": "EUR"}.get(country), "symbol": None,
        "importance": importance, "status": "scheduled", "actual": None, "forecast": None,
        "previous": None, "provider_forecast": None, "actual_value": None,
        "forecast_value": None, "previous_value": None, "unit": None, "reference": None,
        "release_session": "unknown", "source_id": profile.id, "source_name": profile.name,
        "source_url": url or profile.endpoint,
    }


def _trading_economics_events(profile, api_key: str, start: date, end: date) -> list[dict[str, object]]:
    endpoint = profile.endpoint.rstrip("/")
    params = urlencode({"c": api_key, "f": "json", "values": "true"})
    body = _request_json(f"{endpoint}/calendar/country/All/{start.isoformat()}/{end.isoformat()}?{params}", profile.timeout_seconds)
    if not isinstance(body, list):
        raise RuntimeError("Trading Economics 返回了无法识别的经济日历结构")
    events: list[dict[str, object]] = []
    for item in body:
        if not isinstance(item, dict) or not item.get("Event"):
            continue
        scheduled = _parse_datetime(item.get("Date"))
        source_event_id = str(item.get("CalendarId") or f"{item.get('Country')}:{item.get('Event')}:{scheduled.isoformat()}")
        actual = item.get("Actual")
        forecast = item.get("Forecast")
        previous = item.get("Previous")
        status = "released" if actual not in {None, ""} else "tentative" if str(item.get("DateSpan")) == "1" else "scheduled"
        events.append({
            "id": _event_id(profile.id, source_event_id), "source_event_id": source_event_id,
            "kind": "macro", "title": str(item["Event"]), "category": str(item.get("Category") or item["Event"]),
            "scheduled_at": scheduled.isoformat(), "country": _country_code(item.get("Country")),
            "currency": str(item.get("Currency") or "") or None, "symbol": None,
            "importance": _importance(item.get("Importance")), "status": status,
            "actual": str(actual) if actual not in {None, ""} else None,
            "forecast": str(forecast) if forecast not in {None, ""} else None,
            "previous": str(previous) if previous not in {None, ""} else None,
            "provider_forecast": str(item.get("TEForecast")) if item.get("TEForecast") not in {None, ""} else None,
            "actual_value": item.get("ActualValue") if item.get("ActualValue") is not None else _number(actual),
            "forecast_value": item.get("ForecastValue") if item.get("ForecastValue") is not None else _number(forecast),
            "previous_value": item.get("PreviousValue") if item.get("PreviousValue") is not None else _number(previous),
            "unit": str(item.get("Unit") or "") or None, "reference": str(item.get("Reference") or "") or None,
            "release_session": "unknown", "source_id": profile.id, "source_name": profile.name,
            "source_url": str(item.get("SourceURL") or "") or None,
        })
    return events


def _finnhub_macro_events(profile, api_key: str, start: date, end: date) -> list[dict[str, object]]:
    params = urlencode({"from": start.isoformat(), "to": end.isoformat(), "token": api_key})
    body = _request_json(f"{profile.endpoint.rstrip('/')}/calendar/economic?{params}", profile.timeout_seconds)
    rows = body.get("economicCalendar", []) if isinstance(body, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError("Finnhub 返回了无法识别的经济日历结构")
    events = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("event"):
            continue
        scheduled = _parse_datetime(item.get("time"))
        source_event_id = f"{item.get('country')}:{item.get('event')}:{scheduled.isoformat()}"
        actual, forecast, previous = item.get("actual"), item.get("estimate"), item.get("prev")
        events.append({
            "id": _event_id(profile.id, source_event_id), "source_event_id": source_event_id,
            "kind": "macro", "title": str(item["event"]), "category": str(item["event"]),
            "scheduled_at": scheduled.isoformat(), "country": _country_code(item.get("country")),
            "currency": None, "symbol": None, "importance": _importance(item.get("impact")),
            "status": "released" if actual not in {None, ""} else "scheduled",
            "actual": str(actual) if actual not in {None, ""} else None,
            "forecast": str(forecast) if forecast not in {None, ""} else None,
            "previous": str(previous) if previous not in {None, ""} else None,
            "provider_forecast": None, "actual_value": _number(actual), "forecast_value": _number(forecast),
            "previous_value": _number(previous), "unit": str(item.get("unit") or "") or None,
            "reference": None, "release_session": "unknown", "source_id": profile.id,
            "source_name": profile.name, "source_url": None,
        })
    return events


def _finnhub_earnings_events(profile, api_key: str, start: date, end: date) -> list[dict[str, object]]:
    include_international = bool(profile.config.get("international", False))
    params = urlencode({"from": start.isoformat(), "to": end.isoformat(), "international": str(include_international).lower(), "token": api_key})
    body = _request_json(f"{profile.endpoint.rstrip('/')}/calendar/earnings?{params}", profile.timeout_seconds)
    rows = body.get("earningsCalendar", []) if isinstance(body, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError("Finnhub 返回了无法识别的财报日历结构")
    events = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("symbol") or not item.get("date"):
            continue
        session = str(item.get("hour") or "unknown").lower()
        session = session if session in {"bmo", "amc", "dmh"} else "unknown"
        clock = time(12, 0) if session == "dmh" else time(13, 0) if session == "bmo" else time(21, 0) if session == "amc" else time(0, 0)
        scheduled = datetime.combine(date.fromisoformat(str(item["date"])), clock, timezone.utc)
        symbol = str(item["symbol"]).upper()
        source_event_id = f"{symbol}:{item.get('year')}:{item.get('quarter')}:{item['date']}"
        actual, forecast = item.get("epsActual"), item.get("epsEstimate")
        events.append({
            "id": _event_id(profile.id, source_event_id), "source_event_id": source_event_id,
            "kind": "earnings", "title": f"{symbol} 财报发布", "category": "Earnings",
            "scheduled_at": scheduled.isoformat(), "country": None, "currency": "USD", "symbol": symbol,
            "importance": "high", "status": "released" if actual is not None else "scheduled",
            "actual": str(actual) if actual is not None else None,
            "forecast": str(forecast) if forecast is not None else None, "previous": None,
            "provider_forecast": str(item.get("revenueEstimate")) if item.get("revenueEstimate") is not None else None,
            "actual_value": _number(actual), "forecast_value": _number(forecast), "previous_value": None,
            "unit": "EPS", "reference": f"{item.get('year')} Q{item.get('quarter')}",
            "release_session": session, "source_id": profile.id, "source_name": profile.name, "source_url": None,
        })
    return events


def _indicator_code(title: str) -> str:
    patterns = (
        (r"consumer price|\bcpi\b|居民消费价格", "CPI"),
        (r"producer price|\bppi\b|工业生产者", "PPI"),
        (r"gross domestic product|\bgdp\b|国内生产总值", "GDP"),
        (r"employment situation|nonfarm|payroll|就业", "EMPLOYMENT"),
        (r"unemployment|失业率", "UNEMPLOYMENT"),
        (r"personal income and outlays|\bpce\b", "PCE"),
        (r"retail sales|零售", "RETAIL_SALES"),
        (r"purchasing managers|\bpmi\b|采购经理", "PMI"),
        (r"fomc|interest rate|利率决议", "RATE_DECISION"),
        (r"international trade|trade balance|进出口|贸易", "TRADE"),
    )
    for pattern, code in patterns:
        if re.search(pattern, title, re.I):
            return code
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", title.lower()).strip("_")
    return normalized[:80] or "ECONOMIC_EVENT"


def _canonical_key(item: dict[str, object]) -> str:
    scheduled = _parse_datetime(item["scheduled_at"])
    country = str(item.get("country") or "GLOBAL").upper()
    if item.get("kind") == "earnings":
        return f"earnings:{str(item.get('symbol') or '').upper()}:{scheduled.date().isoformat()}"
    indicator = _indicator_code(str(item.get("category") or item.get("title") or ""))
    return f"macro:{country}:{indicator}:{scheduled.date().isoformat()}"


_MERGE_FIELDS = (
    "title", "category", "scheduled_at", "country", "currency", "symbol", "importance", "status",
    "actual", "forecast", "previous", "provider_forecast", "actual_value", "forecast_value",
    "previous_value", "unit", "reference", "release_session", "source_url",
)


def _upsert_events(events: list[dict[str, object]], fetched_at: str) -> tuple[int, int, int]:
    if not events:
        return 0, 0, 0
    from app.modules.integrations.service import list_profiles

    profiles = {profile.id: profile for profile in list_profiles()}
    touched: set[str] = set()
    with _connect() as connection:
        for item in events:
            key = _canonical_key(item)
            existing_snapshot = connection.execute(
                "SELECT canonical_event_id FROM financial_calendar_event_sources WHERE source_id=? AND source_event_id=?",
                (item["source_id"], item["source_event_id"]),
            ).fetchone()
            existing_event = connection.execute(
                "SELECT id FROM financial_calendar_events WHERE canonical_key=? ORDER BY created_at LIMIT 1", (key,)
            ).fetchone()
            if existing_snapshot:
                canonical_id = existing_snapshot["canonical_event_id"]
            elif existing_event:
                canonical_id = existing_event["id"]
            else:
                legacy = connection.execute(
                    "SELECT id FROM financial_calendar_events WHERE source_id=? AND source_event_id=?",
                    (item["source_id"], item["source_event_id"]),
                ).fetchone()
                canonical_id = legacy["id"] if legacy else f"calendar-{sha256(key.encode()).hexdigest()[:18]}"
            touched.add(canonical_id)
            connection.execute(
                """INSERT INTO financial_calendar_event_sources
                   (canonical_event_id,source_id,source_name,source_event_id,canonical_key,payload,fetched_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_id,source_event_id) DO UPDATE SET
                     canonical_event_id=excluded.canonical_event_id,source_name=excluded.source_name,
                     canonical_key=excluded.canonical_key,payload=excluded.payload,
                     fetched_at=excluded.fetched_at,updated_at=excluded.updated_at""",
                (canonical_id, item["source_id"], item["source_name"], item["source_event_id"], key,
                 json.dumps(item, ensure_ascii=False), fetched_at, fetched_at),
            )

        merged_total = conflict_total = 0
        for canonical_id in touched:
            snapshots = connection.execute(
                "SELECT * FROM financial_calendar_event_sources WHERE canonical_event_id=?", (canonical_id,)
            ).fetchall()
            ranked = sorted(
                ((row, json.loads(row["payload"])) for row in snapshots),
                key=lambda pair: (
                    profiles.get(pair[0]["source_id"]).priority if profiles.get(pair[0]["source_id"]) else 0,
                    profiles.get(pair[0]["source_id"]).reliability_weight if profiles.get(pair[0]["source_id"]) else 0,
                ), reverse=True,
            )
            merged: dict[str, object] = {}
            provenance: dict[str, str] = {}
            conflicts: list[str] = []
            for field in _MERGE_FIELDS:
                candidates = [(row, payload.get(field)) for row, payload in ranked if payload.get(field) not in {None, ""}]
                if candidates:
                    merged[field] = candidates[0][1]
                    provenance[field] = candidates[0][0]["source_id"]
                distinct = {str(value) for _row, value in candidates}
                if field in {"scheduled_at", "actual", "forecast", "previous"} and len(distinct) > 1:
                    conflicts.append(field)
            primary_row, primary = ranked[0]
            old = connection.execute(
                "SELECT actual,actual_value,status,created_at FROM financial_calendar_events WHERE id=?",
                (canonical_id,),
            ).fetchone()
            actual = merged.get("actual")
            actual_value = merged.get("actual_value")
            if actual in {None, ""} and old and old["actual"] not in {None, ""}:
                actual = old["actual"]
                actual_value = old["actual_value"]
                provenance.setdefault("actual", "persisted_release")
                if actual_value is not None:
                    provenance.setdefault("actual_value", "persisted_release")
            status = str(merged.get("status") or "scheduled")
            if actual not in {None, ""}:
                status = (
                    "revised"
                    if old and old["actual"] not in {None, ""} and actual != old["actual"]
                    else "revised" if status == "revised" or (old and old["status"] == "revised")
                    else "released"
                )
            created_at = old["created_at"] if old else fetched_at
            values = (
                canonical_id, primary["source_event_id"], primary["kind"], merged.get("title") or primary["title"],
                merged.get("category") or primary["category"], merged.get("scheduled_at") or primary["scheduled_at"],
                merged.get("country"), merged.get("currency"), merged.get("symbol"), merged.get("importance") or "low",
                status, actual, merged.get("forecast"), merged.get("previous"),
                merged.get("provider_forecast"), actual_value, merged.get("forecast_value"),
                merged.get("previous_value"), merged.get("unit"), merged.get("reference"),
                merged.get("release_session") or "unknown", primary_row["source_id"], primary_row["source_name"],
                merged.get("source_url"), fetched_at, created_at, fetched_at, primary_row["canonical_key"],
                json.dumps(provenance, ensure_ascii=False), json.dumps(conflicts, ensure_ascii=False),
            )
            connection.execute(
                """INSERT INTO financial_calendar_events (
                   id,source_event_id,kind,title,category,scheduled_at,country,currency,symbol,importance,status,
                   actual,forecast,previous,provider_forecast,actual_value,forecast_value,previous_value,unit,
                   reference,release_session,source_id,source_name,source_url,fetched_at,created_at,updated_at,
                   canonical_key,provenance,conflicts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     source_event_id=excluded.source_event_id,title=excluded.title,category=excluded.category,
                     scheduled_at=excluded.scheduled_at,country=excluded.country,currency=excluded.currency,
                     symbol=excluded.symbol,importance=excluded.importance,status=excluded.status,
                     actual=excluded.actual,forecast=excluded.forecast,previous=excluded.previous,
                     provider_forecast=excluded.provider_forecast,actual_value=excluded.actual_value,
                     forecast_value=excluded.forecast_value,previous_value=excluded.previous_value,
                     unit=excluded.unit,reference=excluded.reference,release_session=excluded.release_session,
                     source_id=excluded.source_id,source_name=excluded.source_name,source_url=excluded.source_url,
                     fetched_at=excluded.fetched_at,updated_at=excluded.updated_at,canonical_key=excluded.canonical_key,
                     provenance=excluded.provenance,conflicts=excluded.conflicts""", values,
            )
            merged_total += max(0, len(snapshots) - 1)
            conflict_total += len(conflicts)
    return len(events), merged_total, conflict_total


def refresh(
    start: date | None = None, end: date | None = None, source_id: str | None = None,
) -> FinancialCalendarRefreshResult:
    initialize()
    started = datetime.now(timezone.utc)
    # Result providers often publish or revise values after the release date. Keep
    # completed events in the sync window long enough for delayed backfills.
    start = start or (started.date() - timedelta(days=RESULT_BACKFILL_DAYS))
    end = end or (started.date() + timedelta(days=30))
    from app.modules.integrations.service import get_profile, get_route, record_health, resolve_candidates
    from app.modules.platform.service import resolve_api_key

    fetched = upserted = merged = conflicts = 0
    sources: list[str] = []
    errors: list[str] = []
    fetched_at = _now()
    for capability in ("economic_calendar", "earnings_calendar"):
        capability_label = "宏观日历" if capability == "economic_calendar" else "财报日历"
        if source_id:
            selected = get_profile(source_id)
            candidates = [selected] if selected.enabled and capability in selected.capabilities else []
        else:
            candidates = resolve_candidates("GLOBAL", capability)
        route = get_route("GLOBAL", capability)
        aggregate = bool(route and route.mode == "aggregate")
        capability_succeeded = False
        for profile in candidates:
            auth_type = getattr(profile, "auth_type", "query" if getattr(profile, "api_key_env", "") else "none")
            key = resolve_api_key("data_source", profile.id, profile.api_key_env) if auth_type != "none" else ""
            if auth_type != "none" and not key:
                errors.append(f"{capability_label} · {profile.name}: 未配置 {profile.api_key_env}")
                continue
            started_source = perf_counter()
            try:
                if capability == "economic_calendar" and profile.adapter == "trading_economics":
                    rows = _trading_economics_events(profile, key, start, end)
                elif capability == "economic_calendar" and profile.adapter == "finnhub":
                    rows = _finnhub_macro_events(profile, key, start, end)
                elif capability == "earnings_calendar" and profile.adapter == "finnhub":
                    rows = _finnhub_earnings_events(profile, key, start, end)
                elif capability == "economic_calendar" and profile.adapter == "calendar_ics":
                    rows = _official_ics_events(profile, key, start, end)
                elif capability == "economic_calendar" and profile.adapter == "bea_calendar":
                    rows = _bea_calendar_events(profile, key, start, end)
                elif capability == "economic_calendar" and profile.adapter == "nbs_calendar":
                    rows = _nbs_calendar_events(profile, key, start, end)
                elif capability == "economic_calendar" and profile.adapter == "ecb_calendar":
                    rows = _ecb_calendar_events(profile, key, start, end)
                elif capability == "economic_calendar" and profile.adapter == "fomc_calendar":
                    rows = _fomc_calendar_events(profile, key, start, end)
                else:
                    continue
                fetched += len(rows)
                written, merged_count, conflict_count = _upsert_events(rows, fetched_at)
                upserted += written
                merged += merged_count
                conflicts += conflict_count
                sources.append(profile.name)
                latency = (perf_counter() - started_source) * 1000
                record_health(profile.id, True, latency, f"日历同步成功，共 {len(rows)} 条")
                capability_succeeded = True
                if not aggregate:
                    break
            except CalendarEntitlementError:
                if capability == "economic_calendar" and profile.adapter == "finnhub":
                    message = "Finnhub 当前套餐不含经济日历，请配置 Trading Economics API Key 或升级 Finnhub Premium"
                elif capability == "earnings_calendar" and profile.adapter == "finnhub":
                    message = "Finnhub 当前套餐不含财报日历"
                else:
                    message = f"{profile.name} 当前套餐不含此日历能力"
                errors.append(f"{capability_label} · {message}")
            except Exception as exc:
                message = str(exc)[:500]
                errors.append(f"{capability_label} · {profile.name}: {message}")
                record_health(profile.id, False, (perf_counter() - started_source) * 1000, message)
        if not capability_succeeded and not candidates and not source_id and not sources:
            errors.append(f"{capability_label} · 没有可用的数据源，请检查凭据和数据源状态")
    finished = datetime.now(timezone.utc)
    status = "completed" if sources and not errors else "warning" if sources else "failed"
    result = FinancialCalendarRefreshResult(
        status=status, fetched=fetched, upserted=upserted, merged=merged, conflicts=conflicts,
        sources=list(dict.fromkeys(sources)), errors=errors,
        started_at=started.isoformat(), finished_at=finished.isoformat(),
    )
    with _connect() as connection:
        run_id = f"calendar-sync-{sha256(result.finished_at.encode()).hexdigest()[:14]}"
        connection.execute(
            """INSERT INTO financial_calendar_sync_runs
               (id,status,fetched,upserted,sources,errors,started_at,finished_at,source_id,merged,conflicts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, result.status, fetched, upserted, json.dumps(result.sources, ensure_ascii=False),
             json.dumps(errors, ensure_ascii=False), result.started_at, result.finished_at, source_id, merged, conflicts),
        )
    return result


def refresh_source(source_id: str, start: date | None = None, end: date | None = None) -> FinancialCalendarRefreshResult:
    from app.modules.integrations.service import get_profile
    profile = get_profile(source_id)
    if "economic_calendar" not in profile.capabilities and "earnings_calendar" not in profile.capabilities:
        raise HTTPException(status_code=409, detail="该数据源不提供财经日历能力")
    return refresh(start=start, end=end, source_id=source_id)


def refresh_all_sources() -> FinancialCalendarRefreshResult:
    started = datetime.now(timezone.utc)
    results = refresh_due_sources(force=True)
    finished = datetime.now(timezone.utc)
    sources = list(dict.fromkeys(source for result in results for source in result.sources))
    errors = list(dict.fromkeys(error for result in results for error in result.errors))
    status = "completed" if sources and not errors else "warning" if sources else "failed"
    return FinancialCalendarRefreshResult(
        status=status,
        fetched=sum(result.fetched for result in results),
        upserted=sum(result.upserted for result in results),
        merged=sum(result.merged for result in results),
        conflicts=sum(result.conflicts for result in results),
        sources=sources,
        errors=errors,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )


def _analysis_from_row(row: sqlite3.Row | None) -> CalendarEventAnalysis | None:
    if row is None:
        return None
    return CalendarEventAnalysis(
        event_id=row["event_id"], status=row["status"], summary=row["summary"], risk_level=row["risk_level"],
        confidence=row["confidence"], affected_markets=json.loads(row["affected_markets"]),
        symbols=json.loads(row["symbols"]), scenarios=json.loads(row["scenarios"]), risks=json.loads(row["risks"]),
        evidence=json.loads(row["evidence"]), run_id=row["run_id"], model=row["model"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _event_from_row(
    row: sqlite3.Row, watchlist: set[str], settings: FinancialCalendarSettings,
    reminder_row: sqlite3.Row | None = None, analysis_status: str | None = None,
) -> FinancialCalendarEvent:
    default_reminder = settings.reminders_enabled and (
        (row["kind"] == "macro" and row["importance"] == "high") or
        (row["kind"] == "earnings" and row["symbol"] in watchlist)
    )
    offsets = settings.high_impact_offsets if row["kind"] == "macro" else [settings.earnings_watchlist_offset]
    if reminder_row:
        default_reminder = bool(reminder_row["enabled"])
        offsets = json.loads(reminder_row["offsets"])
    surprise = None
    direction = None
    if row["actual_value"] is not None and row["forecast_value"] is not None:
        surprise = row["actual_value"] - row["forecast_value"]
        tolerance = max(abs(row["forecast_value"]) * 0.0001, 1e-9)
        direction = "inline" if abs(surprise) <= tolerance else "above" if surprise > 0 else "below"
    status = row["status"]
    if status == "scheduled" and row["actual"] in {None, ""}:
        scheduled = _parse_datetime(row["scheduled_at"])
        grace = EARNINGS_RESULT_GRACE if row["kind"] == "earnings" else MACRO_RESULT_GRACE
        # Some earnings feeds only provide a date and store midnight as a placeholder.
        if row["kind"] == "earnings" and row["release_session"] == "unknown":
            grace = timedelta(days=1)
        if datetime.now(timezone.utc) >= scheduled + grace:
            status = "awaiting_result"
    return FinancialCalendarEvent(
        **{key: row[key] for key in FinancialCalendarEvent.model_fields if key in row.keys() and key not in {
            "status", "stale", "surprise_value", "surprise_direction", "reminder_enabled", "reminder_offsets", "analysis_status",
            "provenance", "conflicts", "source_count",
        }},
        status=status,
        stale=(datetime.now(timezone.utc) - _parse_datetime(row["fetched_at"])) > timedelta(hours=2),
        surprise_value=surprise, surprise_direction=direction, reminder_enabled=default_reminder,
        reminder_offsets=offsets, analysis_status=analysis_status or "none",
        provenance=json.loads(row["provenance"] or "{}") if "provenance" in row.keys() else {},
        conflicts=json.loads(row["conflicts"] or "[]") if "conflicts" in row.keys() else [],
        source_count=int(row["source_count"]) if "source_count" in row.keys() else 1,
    )


def _watchlist_symbols() -> set[str]:
    try:
        from app.modules.market.service import list_watchlist_snapshots
        return {item.symbol for item in list_watchlist_snapshots()}
    except Exception:
        return set()


def list_events(
    start: date, end: date, kind: str | None = None, countries: list[str] | None = None,
    importance: str | None = None, watchlist_only: bool = False, query: str | None = None,
    source_id: str | None = None,
) -> FinancialCalendarPage:
    initialize()
    clauses = ["scheduled_at>=?", "scheduled_at<?"]
    params: list[object] = [datetime.combine(start, time.min, timezone.utc).isoformat(), datetime.combine(end + timedelta(days=1), time.min, timezone.utc).isoformat()]
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    if countries:
        placeholders = ",".join("?" for _ in countries)
        clauses.append(f"UPPER(COALESCE(country,'')) IN ({placeholders})")
        params.extend(item.upper() for item in countries)
    if importance:
        clauses.append("importance=?")
        params.append(importance)
    if query:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(category) LIKE ? OR LOWER(COALESCE(symbol,'')) LIKE ?)")
        needle = f"%{query.strip().lower()}%"
        params.extend([needle, needle, needle])
    if source_id:
        clauses.append("(financial_calendar_events.source_id=? OR EXISTS (SELECT 1 FROM financial_calendar_event_sources es WHERE es.canonical_event_id=financial_calendar_events.id AND es.source_id=?))")
        params.extend([source_id, source_id])
    watchlist = _watchlist_symbols()
    if watchlist_only:
        if not watchlist:
            clauses.append("1=0")
        else:
            placeholders = ",".join("?" for _ in watchlist)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(sorted(watchlist))
    with _connect() as connection:
        rows = connection.execute(
            f"""SELECT financial_calendar_events.*,
                   MAX(1,(SELECT COUNT(*) FROM financial_calendar_event_sources es WHERE es.canonical_event_id=financial_calendar_events.id)) AS source_count
                FROM financial_calendar_events WHERE {' AND '.join(clauses)}
                ORDER BY scheduled_at, importance DESC, title""",
            params,
        ).fetchall()
        latest = connection.execute("SELECT * FROM financial_calendar_sync_runs ORDER BY finished_at DESC LIMIT 1").fetchone()
        reminder_rows = connection.execute("SELECT * FROM financial_calendar_reminders").fetchall()
        analysis_rows = connection.execute("SELECT event_id,status FROM financial_calendar_analyses").fetchall()
    settings = get_settings()
    reminders = {row["event_id"]: row for row in reminder_rows}
    analysis_statuses = {row["event_id"]: row["status"] for row in analysis_rows}
    items = [_event_from_row(row, watchlist, settings, reminders.get(row["id"]), analysis_statuses.get(row["id"])) for row in rows]
    warning = None
    if latest and latest["status"] != "completed":
        errors = json.loads(latest["errors"])
        warning = _calendar_warning(errors, kind)
    elif not latest:
        warning = "尚未同步财经日历，请配置数据源凭据后刷新。"
    awaiting_count = sum(item.status == "awaiting_result" for item in items)
    if awaiting_count:
        backfill_warning = (
            f"{awaiting_count} 条事件已过发布时间但数据源尚未返回实际值；"
            "官方日程源只提供发布时间，请检查实际值数据源的凭据和同步状态。"
        )
        warning = f"{warning}；{backfill_warning}" if warning else backfill_warning
    return FinancialCalendarPage(
        generated_at=_now(), start=start.isoformat(), end=end.isoformat(), total=len(items), items=items,
        sources=sorted({item.source_name for item in items}), stale=any(item.stale for item in items), warning=warning,
    )


def _calendar_warning(errors: list[str], kind: str | None) -> str | None:
    expected = "宏观日历" if kind == "macro" else "财报日历" if kind == "earnings" else None
    normalized: list[tuple[str | None, str]] = []
    for raw in errors:
        if raw.startswith("宏观日历 ·"):
            normalized.append(("宏观日历", raw))
        elif raw.startswith("财报日历 ·"):
            normalized.append(("财报日历", raw))
        elif "International market is only available" in raw or "HTTP 401" in raw:
            normalized.append(("财报日历", "财报日历 · Finnhub 普通套餐仅支持美国市场，已切换为美国市场模式，请重新刷新"))
        elif "HTTP 403" in raw or "don't have access to this resource" in raw:
            normalized.append(("宏观日历", "宏观日历 · Finnhub 当前套餐不含经济日历，请配置 Trading Economics API Key 或升级 Finnhub Premium"))
        else:
            normalized.append((None, raw))
    visible = [message for category, message in normalized if expected is None or category in {None, expected}]
    return "；".join(dict.fromkeys(visible[:2])) or None


def get_event(event_id: str) -> FinancialCalendarEventDetail:
    initialize()
    with _connect() as connection:
        row = connection.execute(
            """SELECT financial_calendar_events.*,
               MAX(1,(SELECT COUNT(*) FROM financial_calendar_event_sources es WHERE es.canonical_event_id=financial_calendar_events.id)) AS source_count
               FROM financial_calendar_events WHERE id=?""", (event_id,),
        ).fetchone()
        analysis = connection.execute("SELECT * FROM financial_calendar_analyses WHERE event_id=?", (event_id,)).fetchone()
        reminder = connection.execute("SELECT * FROM financial_calendar_reminders WHERE event_id=?", (event_id,)).fetchone()
        snapshots = connection.execute(
            "SELECT * FROM financial_calendar_event_sources WHERE canonical_event_id=? ORDER BY source_name", (event_id,)
        ).fetchall()
    if row is None:
        raise HTTPException(status_code=404, detail="财经事件不存在")
    return FinancialCalendarEventDetail(
        event=_event_from_row(row, _watchlist_symbols(), get_settings(), reminder, analysis["status"] if analysis else None),
        analysis=_analysis_from_row(analysis),
        source_snapshots=[FinancialCalendarSourceSnapshot(
            source_id=item["source_id"], source_name=item["source_name"], source_event_id=item["source_event_id"],
            fetched_at=item["fetched_at"], scheduled_at=json.loads(item["payload"])["scheduled_at"],
            values={key: value for key, value in json.loads(item["payload"]).items() if key in _MERGE_FIELDS},
        ) for item in snapshots],
    )


def update_event_reminder(event_id: str, payload: CalendarEventReminderUpdate) -> FinancialCalendarEvent:
    get_event(event_id)
    offsets = sorted({max(1, min(1440, int(item))) for item in payload.offsets}, reverse=True)
    with _connect() as connection:
        connection.execute(
            """INSERT INTO financial_calendar_reminders VALUES (?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET enabled=excluded.enabled,offsets=excluded.offsets,updated_at=excluded.updated_at""",
            (event_id, int(payload.enabled), json.dumps(offsets), _now()),
        )
    return get_event(event_id).event


def delete_event_reminder(event_id: str) -> FinancialCalendarEvent:
    get_event(event_id)
    with _connect() as connection:
        connection.execute("DELETE FROM financial_calendar_reminders WHERE event_id=?", (event_id,))
    return get_event(event_id).event


def queue_analysis(event_id: str, trigger: str = "calendar_event_upcoming") -> CalendarEventAnalysis:
    detail = get_event(event_id)
    from app.modules.ai.service import enqueue_agent_run
    run = enqueue_agent_run("economic_event_analyst", trigger=trigger, symbol=detail.event.symbol, context={"event_id": event_id})
    now = _now()
    with _connect() as connection:
        connection.execute(
            """INSERT INTO financial_calendar_analyses VALUES (?, 'queued', '', 'info', 0, '[]', '[]', '[]', '[]', '[]', ?, NULL, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET status='queued',run_id=excluded.run_id,updated_at=excluded.updated_at""",
            (event_id, run.id, now, now),
        )
    return get_event(event_id).analysis


def persist_analysis(event_id: str, output: dict[str, object], run_id: str, model: str | None) -> None:
    now = _now()
    existing = None
    with _connect() as connection:
        existing = connection.execute("SELECT created_at FROM financial_calendar_analyses WHERE event_id=?", (event_id,)).fetchone()
        connection.execute(
            """INSERT OR REPLACE INTO financial_calendar_analyses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, "ready", str(output.get("summary", ""))[:2000], str(output.get("risk_level", "info")),
             int(output.get("confidence", 0)), json.dumps(output.get("affected_markets", []), ensure_ascii=False),
             json.dumps(output.get("symbols", []), ensure_ascii=False), json.dumps(output.get("scenarios", []), ensure_ascii=False),
             json.dumps(output.get("risks", []), ensure_ascii=False), json.dumps(output.get("evidence", []), ensure_ascii=False),
             run_id, model, existing["created_at"] if existing else now, now),
        )


def latest_sync_finished_at() -> str | None:
    initialize()
    with _connect() as connection:
        row = connection.execute("SELECT finished_at FROM financial_calendar_sync_runs ORDER BY finished_at DESC LIMIT 1").fetchone()
    return row["finished_at"] if row else None


def latest_source_sync(source_id: str) -> sqlite3.Row | None:
    initialize()
    with _connect() as connection:
        return connection.execute(
            "SELECT * FROM financial_calendar_sync_runs WHERE source_id=? ORDER BY finished_at DESC LIMIT 1",
            (source_id,),
        ).fetchone()


def source_statuses() -> list[FinancialCalendarSourceStatus]:
    from app.modules.integrations.service import list_profiles
    statuses: list[FinancialCalendarSourceStatus] = []
    for profile in list_profiles():
        if not {"economic_calendar", "earnings_calendar"}.intersection(profile.capabilities):
            continue
        latest = latest_source_sync(profile.id)
        errors = json.loads(latest["errors"]) if latest else []
        statuses.append(FinancialCalendarSourceStatus(
            source_id=profile.id, last_run_at=latest["finished_at"] if latest else None,
            status=latest["status"] if latest else "never", fetched=latest["fetched"] if latest else 0,
            upserted=latest["upserted"] if latest else 0, merged=latest["merged"] if latest else 0,
            conflicts=latest["conflicts"] if latest else 0, last_error=errors[0] if errors else None,
        ))
    return statuses


def due_source_ids(now: datetime | None = None) -> list[str]:
    from app.modules.integrations.service import list_profiles
    now = now or datetime.now(timezone.utc)
    due: list[str] = []
    for profile in list_profiles():
        if not profile.enabled or not {"economic_calendar", "earnings_calendar"}.intersection(profile.capabilities):
            continue
        latest = latest_source_sync(profile.id)
        if not latest or (now - _parse_datetime(latest["finished_at"])).total_seconds() >= profile.sync_interval_seconds:
            due.append(profile.id)
    return due


def refresh_due_sources(force: bool = False) -> list[FinancialCalendarRefreshResult]:
    from app.modules.integrations.service import list_profiles
    profiles = [
        profile for profile in list_profiles()
        if profile.enabled and {"economic_calendar", "earnings_calendar"}.intersection(profile.capabilities)
    ]
    due = {profile.id for profile in profiles} if force else set(due_source_ids())
    return [refresh_source(profile.id) for profile in profiles if profile.id in due]


def due_for_sync() -> bool:
    settings = get_settings()
    if not settings.sync_enabled:
        return False
    latest = latest_sync_finished_at()
    return latest is None or datetime.now(timezone.utc) >= _parse_datetime(latest) + timedelta(seconds=settings.sync_interval_seconds)


def due_reminders(now: datetime | None = None) -> list[tuple[FinancialCalendarEvent, int]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = get_settings()
    if not settings.reminders_enabled:
        return []
    start = now.date()
    end = (now + timedelta(days=1)).date()
    page = list_events(start, end)
    due: list[tuple[FinancialCalendarEvent, int]] = []
    with _connect() as connection:
        delivered = {
            (row["event_id"], row["offset_minutes"])
            for row in connection.execute(
                "SELECT event_id,offset_minutes FROM financial_calendar_reminder_deliveries WHERE delivered_at>=?",
                ((now - timedelta(days=2)).isoformat(),),
            ).fetchall()
        }
    for event in page.items:
        if not event.reminder_enabled or event.status in {"cancelled", "released", "revised"}:
            continue
        scheduled = _parse_datetime(event.scheduled_at)
        for offset in event.reminder_offsets:
            remind_at = scheduled - timedelta(minutes=offset)
            if remind_at <= now < remind_at + timedelta(minutes=2) and (event.id, offset) not in delivered:
                due.append((event, offset))
    return due


def mark_reminder_delivered(event_id: str, offset_minutes: int, delivered_at: str | None = None) -> None:
    delivered_at = delivered_at or _now()
    delivery_id = f"calendar-reminder-{sha256(f'{event_id}:{offset_minutes}'.encode()).hexdigest()[:16]}"
    with _connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO financial_calendar_reminder_deliveries VALUES (?,?,?,?)",
            (delivery_id, event_id, offset_minutes, delivered_at),
        )


def due_analysis_events(now: datetime | None = None) -> list[tuple[str, str]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with _connect() as connection:
        rows = connection.execute(
            """SELECT e.*,a.status AS analysis_status,a.updated_at AS analysis_updated_at
               FROM financial_calendar_events e
               LEFT JOIN financial_calendar_analyses a ON a.event_id=e.id
               WHERE e.importance='high' AND e.scheduled_at>=? AND e.scheduled_at<=?
               ORDER BY e.scheduled_at LIMIT 20""",
            ((now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()),
        ).fetchall()
    result: list[tuple[str, str]] = []
    for row in rows:
        scheduled = _parse_datetime(row["scheduled_at"])
        if row["status"] in {"released", "revised"}:
            if row["analysis_status"] != "queued" and (
                not row["analysis_updated_at"] or _parse_datetime(row["analysis_updated_at"]) < _parse_datetime(row["updated_at"])
            ):
                result.append((row["id"], "calendar_event_released"))
        elif scheduled - timedelta(minutes=60) <= now and row["analysis_status"] not in {"queued", "ready"}:
            result.append((row["id"], "calendar_event_upcoming"))
    return result


def mark_analysis_failed(event_id: str, message: str) -> None:
    now = _now()
    with _connect() as connection:
        connection.execute(
            """INSERT INTO financial_calendar_analyses VALUES (?, 'failed', ?, 'watch', 0, '[]', '[]', '[]', '[]', '[]', NULL, NULL, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET status='failed',summary=excluded.summary,updated_at=excluded.updated_at""",
            (event_id, message[:1000], now, now),
        )
