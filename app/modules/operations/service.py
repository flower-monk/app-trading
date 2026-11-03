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
from app.schemas import (
    AdminActivityItem,
    AdminAttentionItem,
    AdminOverview,
    AdminOverviewMetric,
    AgentUpdate,
    AutomationSettings,
    DashboardMetric,
    DashboardOverview,
    DataSourceProfileUpdate,
    FinancialCalendarSettingsUpdate,
    HealthComponent,
    Literal,
    ScheduledTask,
    ScheduledTaskBulkResult,
    ScheduledTaskBulkUpdate,
    ScheduledTaskRunResult,
    ScheduledTaskUpdate,
    SystemHealth,
    agents,
    credentials,
    data_sources,
    financial_calendar,
    paper_trading,
)
from app.modules.integrations.service import ensure_data_source_schema, set_database_path as set_data_source_database_path
from app.modules.ai import gateway as model_gateway_service

from app.modules._dependencies import service_call
get_market_provider_status = service_call('app.modules.market.service', 'get_market_provider_status')
from app.modules.market.service import MARKET_PROVIDERS
add_event_log = service_call('app.modules.trading.service', 'add_event_log')
db_connect = service_call('app.modules.infrastructure.database', 'db_connect')
deliver_event = service_call('app.modules.trading.service', 'deliver_event')
enqueue_agent_run = service_call('app.modules.ai.service', 'enqueue_agent_run')
get_active_market_provider_name = service_call('app.modules.market.service', 'get_active_market_provider_name')
get_agent = service_call('app.modules.ai.service', 'get_agent')
get_automation_settings = service_call('app.modules.platform.service', 'get_automation_settings')
get_config_value = service_call('app.modules.platform.service', 'get_config_value')
information_source_is_ingestible = service_call('app.modules.news.service', 'information_source_is_ingestible')
init_db = service_call('app.modules.infrastructure.database', 'init_db')
list_agent_runs = service_call('app.modules.ai.service', 'list_agent_runs')
list_agents = service_call('app.modules.ai.service', 'list_agents')
list_automation_runs = service_call('app.modules.trading.service', 'list_automation_runs')
list_event_logs = service_call('app.modules.trading.service', 'list_event_logs')
list_model_channels = service_call('app.modules.ai.service', 'list_model_channels')
list_watchlist_snapshots = service_call('app.modules.market.service', 'list_watchlist_snapshots')
news_source_monitoring = service_call('app.modules.news.service', 'news_source_monitoring')
redact_model_error = service_call('app.modules.ai.service', 'redact_model_error')
refresh_source = service_call('app.modules.news.service', 'refresh_source')
row_to_agent_config = service_call('app.modules.ai.service', 'row_to_agent_config')
row_to_source = service_call('app.modules.news.service', 'row_to_source')
run_automation_cycle = service_call('app.modules.trading.service', 'run_automation_cycle')
run_due_data_source_health_checks = service_call('app.modules.market.service', 'run_due_data_source_health_checks')
set_automation_settings = service_call('app.modules.platform.service', 'set_automation_settings')
set_config_value = service_call('app.modules.platform.service', 'set_config_value')
test_model_channel = service_call('app.modules.ai.service', 'test_model_channel')
update_agent = service_call('app.modules.ai.service', 'update_agent')


async def automation_loop() -> None:
    while True:
        settings = get_automation_settings()
        if not settings.enabled:
            await asyncio.sleep(5)
            continue
        try:
            await asyncio.to_thread(run_automation_cycle)
        except Exception as exc:  # noqa: BLE001 - keep scheduler alive and record failures
            add_event_log(
                event_type="system",
                severity="error",
                title="自动化盯盘失败",
                message=str(exc)[:240],
            )
        await asyncio.sleep(settings.interval_seconds)


async def data_source_health_loop() -> None:
    await asyncio.sleep(10)
    while True:
        enabled = get_config_value("data_source_health_enabled", "true") == "true"
        interval = max(60, min(86400, int(get_config_value("data_source_health_interval", "60"))))
        if enabled:
            try:
                await asyncio.to_thread(run_due_data_source_health_checks)
            except Exception as exc:  # noqa: BLE001 - health checks must not stop the scheduler
                add_event_log("data_source_health", "error", "数据源健康检查失败", str(exc)[:240])
        await asyncio.sleep(interval if enabled else 5)


def run_model_channel_health_checks() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for channel in list_model_channels():
        if not channel.enabled or not channel.models:
            continue
        try:
            results.append(test_model_channel(channel.id, channel.models[0]))
        except HTTPException as exc:
            results.append({"ok": False, "channel_id": channel.id, "error": str(exc.detail)})
    return results


async def model_channel_health_loop() -> None:
        await asyncio.sleep(15)


RETENTION_TASK_ID = "maintenance:agent-data-retention"


def agent_data_retention_settings() -> dict[str, int | bool]:
    def configured_int(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(get_config_value(key, str(default)))
        except ValueError:
            value = default
        return max(minimum, min(maximum, value))

    return {
        "enabled": get_config_value("agent_retention_enabled", "true") == "true",
        "interval_seconds": configured_int("agent_retention_interval_seconds", 86400, 3600, 604800),
        "checkpoint_retention_days": configured_int("agent_checkpoint_retention_days", 14, 1, 3650),
        "trace_retention_days": configured_int("agent_trace_retention_days", 90, 1, 3650),
        "artifact_retention_days": configured_int("agent_artifact_retention_days", 180, 1, 3650),
        "vacuum_after_cleanup": get_config_value("agent_retention_vacuum", "true") == "true",
    }


def run_agent_data_retention() -> dict[str, int | bool]:
    from app.modules.ai.workflow import cleanup_agent_data

    config = agent_data_retention_settings()
    result = cleanup_agent_data(
        checkpoint_retention_days=int(config["checkpoint_retention_days"]),
        trace_retention_days=int(config["trace_retention_days"]),
        artifact_retention_days=int(config["artifact_retention_days"]),
        vacuum_after_cleanup=bool(config["vacuum_after_cleanup"]),
    )
    removed = sum(int(result[key]) for key in ("checkpoints", "checkpoint_writes", "spans", "approvals", "runs", "artifacts"))
    set_config_value("agent_retention_last_run_at", datetime.now(timezone.utc).isoformat())
    set_config_value("agent_retention_last_status", "completed")
    set_config_value("agent_retention_last_removed", str(removed))
    add_event_log(
        "system", "info", "Agent 数据保留清理完成",
        f"删除 {removed} 条过期数据，释放 {int(result['reclaimed_bytes'])} 字节。",
    )
    return result


async def agent_data_retention_loop() -> None:
    await asyncio.sleep(20)
    while True:
        config = agent_data_retention_settings()
        if not config["enabled"]:
            await asyncio.sleep(60)
            continue
        last_run_at = get_config_value("agent_retention_last_run_at", "")
        due = not last_run_at
        if last_run_at:
            try:
                last_run = datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
                due = datetime.now(timezone.utc) >= last_run + timedelta(seconds=int(config["interval_seconds"]))
            except ValueError:
                due = True
        if due:
            try:
                await asyncio.to_thread(run_agent_data_retention)
            except Exception as exc:  # noqa: BLE001 - maintenance failure must not stop other jobs
                set_config_value("agent_retention_last_run_at", datetime.now(timezone.utc).isoformat())
                set_config_value("agent_retention_last_status", "failed")
                add_event_log("system", "error", "Agent 数据保留清理失败", str(exc)[:500])
        await asyncio.sleep(60)


def run_financial_calendar_cycle(force_sync: bool = False) -> dict[str, object]:
    from app.modules.calendar import service as financial_calendar

    sync_result = None
    sync_results = []
    if force_sync:
        sync_results = [financial_calendar.refresh_all_sources()]
    elif financial_calendar.get_settings().sync_enabled:
        sync_results = financial_calendar.refresh_due_sources()
    for current_sync in sync_results:
        sync_result = current_sync
        add_event_log(
            event_type="system", severity="info" if current_sync.status == "completed" else "watch",
            title="财经日历同步完成",
            message=f"获取 {current_sync.fetched} 条，写入 {current_sync.upserted} 条。" + (f" {'；'.join(current_sync.errors[:2])}" if current_sync.errors else ""),
        )
    reminders = 0
    calendar_settings = financial_calendar.get_settings()
    for event, offset in financial_calendar.due_reminders():
        log = add_event_log(
            event_type="system", severity="risk" if event.importance == "high" else "watch",
            title=f"财经事件提醒：{event.title}",
            message=f"事件将在 {offset} 分钟后公布。预期 {event.forecast or '--'}，前值 {event.previous or '--'}。",
            symbol=event.symbol,
        )
        deliver_event(log, calendar_settings.notification_channel_ids or None)
        financial_calendar.mark_reminder_delivered(event.id, offset)
        reminders += 1
    analyses = 0
    for event_id, trigger in financial_calendar.due_analysis_events():
        try:
            financial_calendar.queue_analysis(event_id, trigger)
            analyses += 1
        except Exception as exc:  # noqa: BLE001 - one event must not stop the calendar scheduler
            financial_calendar.mark_analysis_failed(event_id, str(exc))
    return {"sync": sync_result, "sync_results": sync_results, "reminders": reminders, "analyses": analyses}


async def financial_calendar_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_financial_calendar_cycle)
        except Exception as exc:  # noqa: BLE001 - keep the scheduler alive and expose the failure
            add_event_log("system", "error", "财经日历任务失败", str(exc)[:500])
        await asyncio.sleep(30)
    while True:
        enabled = get_config_value("model_channel_health_enabled", "false") == "true"
        interval = max(300, min(86400, int(get_config_value("model_channel_health_interval", "900"))))
        if enabled:
            try:
                await asyncio.to_thread(run_model_channel_health_checks)
            except Exception as exc:
                add_event_log("model_gateway", "error", "模型渠道健康检查失败", redact_model_error(exc))
        await asyncio.sleep(interval if enabled else 10)


def schedule_time_after(value: str | None, interval_seconds: int) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (parsed + timedelta(seconds=interval_seconds)).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def list_scheduled_tasks() -> list[ScheduledTask]:
    init_db()
    automation = get_automation_settings()
    automation_runs = list_automation_runs(limit=1)
    latest_automation = automation_runs[0] if automation_runs else None
    tasks = [
        ScheduledTask(
            id="automation:market-monitor",
            task_type="automation",
            name="平台自动化盯盘",
            description="刷新到期新闻源、评估预警，并触发相关 Agent 事件。",
            owner_id="market-monitor",
            owner_name="平台运行时",
            enabled=automation.enabled,
            effective_enabled=automation.enabled,
            interval_seconds=automation.interval_seconds,
            minimum_interval_seconds=60,
            status=(
                "paused" if not automation.enabled
                else "warning" if latest_automation and latest_automation.status == "warning"
                else "failed" if latest_automation and latest_automation.status == "failed"
                else "scheduled"
            ),
            last_run_at=latest_automation.finished_at if latest_automation else None,
            next_run_at=(
                schedule_time_after(latest_automation.finished_at if latest_automation else None, automation.interval_seconds)
                if automation.enabled else None
            ),
            last_status=latest_automation.status if latest_automation else None,
        )
    ]
    health_enabled = get_config_value("data_source_health_enabled", "true") == "true"
    health_interval = max(60, min(86400, int(get_config_value("data_source_health_interval", "60"))))
    from app.modules.integrations.service import list_health_checks
    latest_health_checks = list_health_checks(limit=1)
    latest_health = latest_health_checks[0] if latest_health_checks else None
    tasks.append(ScheduledTask(
        id="data_source:health-check", task_type="data_source", name="数据源健康检查",
        description="检测行情与指数来源，维护熔断、降级和自动恢复状态。",
        owner_id="health-check", owner_name="数据源控制面", enabled=health_enabled,
        effective_enabled=health_enabled, interval_seconds=health_interval,
        minimum_interval_seconds=60, status="scheduled" if health_enabled else "paused",
        last_run_at=latest_health.checked_at if latest_health else None,
        next_run_at=schedule_time_after(latest_health.checked_at if latest_health else None, health_interval) if health_enabled else None,
        last_status=latest_health.status if latest_health else None,
    ))
    from app.modules.calendar import service as financial_calendar
    calendar_settings = financial_calendar.get_settings()
    from app.modules.integrations.service import list_profiles as list_data_source_profiles
    for profile in list_data_source_profiles():
        if not {"economic_calendar", "earnings_calendar"}.intersection(profile.capabilities):
            continue
        latest = financial_calendar.latest_source_sync(profile.id)
        credential_ready = profile.auth_type == "none" or profile.credential_configured
        effective = calendar_settings.sync_enabled and profile.enabled and credential_ready
        status = (
            "paused" if not profile.enabled or not calendar_settings.sync_enabled
            else "blocked" if not credential_ready
            else "failed" if profile.health_status == "offline"
            else "warning" if profile.health_status == "degraded"
            else "scheduled"
        )
        tasks.append(ScheduledTask(
            id=f"calendar_source:{profile.id}", task_type="data_source", name=f"{profile.name} 同步",
            description=f"{profile.provider} · {' / '.join(profile.markets) or 'GLOBAL'} · 财经日历。",
            owner_id=profile.id, owner_name=profile.provider,
            enabled=profile.enabled, effective_enabled=effective,
            interval_seconds=profile.sync_interval_seconds, minimum_interval_seconds=300,
            status=status, dependency=None if credential_ready else f"缺少凭据 {profile.api_key_env}",
            last_run_at=latest["finished_at"] if latest else None,
            next_run_at=schedule_time_after(latest["finished_at"] if latest else None, profile.sync_interval_seconds) if effective else None,
            last_status=latest["status"] if latest else None,
        ))
    tasks.append(ScheduledTask(
        id="automation:financial-calendar-reminders", task_type="automation", name="财经事件提醒检查",
        description="检查高影响宏观事件和自选股财报，并触发站内及 Webhook 提醒。",
        owner_id="financial-calendar-reminders", owner_name="财经日历控制面",
        enabled=calendar_settings.reminders_enabled, effective_enabled=calendar_settings.reminders_enabled,
        interval_seconds=60, minimum_interval_seconds=60,
        status="scheduled" if calendar_settings.reminders_enabled else "paused",
        next_run_at=schedule_time_after(None, 60) if calendar_settings.reminders_enabled else None,
    ))
    from app.modules.trading import paper as paper_trading
    paper_enabled, paper_interval = paper_trading.matching_settings()
    tasks.append(ScheduledTask(
        id="automation:paper-matching", task_type="automation", name="模拟委托撮合",
        description="使用最新报价与提交后的 K 线检查限价、止损及 OCO 模拟委托。",
        owner_id="paper-matching", owner_name="模拟交易引擎", enabled=paper_enabled,
        effective_enabled=paper_enabled, interval_seconds=paper_interval, minimum_interval_seconds=60,
        status="scheduled" if paper_enabled else "paused",
        next_run_at=schedule_time_after(None, paper_interval) if paper_enabled else None,
    ))
    model_health_enabled = get_config_value("model_channel_health_enabled", "false") == "true"
    model_health_interval = max(300, min(86400, int(get_config_value("model_channel_health_interval", "900"))))
    with db_connect() as connection:
        latest_model_health = connection.execute(
            "SELECT status, created_at FROM model_invocations WHERE source='health_test' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    tasks.append(ScheduledTask(
        id="model_gateway:health-check", task_type="model_gateway", name="模型渠道健康检测",
        description="检测启用渠道的首个模型，更新响应耗时、降级和熔断状态。",
        owner_id="health-check", owner_name="模型控制面", enabled=model_health_enabled,
        effective_enabled=model_health_enabled, interval_seconds=model_health_interval,
        minimum_interval_seconds=300, status="scheduled" if model_health_enabled else "paused",
        last_run_at=latest_model_health["created_at"] if latest_model_health else None,
        next_run_at=schedule_time_after(latest_model_health["created_at"] if latest_model_health else None, model_health_interval) if model_health_enabled else None,
        last_status=latest_model_health["status"] if latest_model_health else None,
    ))
    retention = agent_data_retention_settings()
    retention_last_run = get_config_value("agent_retention_last_run_at", "") or None
    retention_last_status = get_config_value("agent_retention_last_status", "") or None
    retention_enabled = bool(retention["enabled"])
    retention_interval = int(retention["interval_seconds"])
    tasks.append(ScheduledTask(
        id=RETENTION_TASK_ID, task_type="maintenance", name="Agent 数据保留清理",
        description="清理过期 checkpoint、运行轨迹、已处理审批和投资产物；不触碰运行中或待审批实例。",
        owner_id="agent-runtime-retention", owner_name="Agent 运行时",
        enabled=retention_enabled, effective_enabled=retention_enabled,
        interval_seconds=retention_interval, minimum_interval_seconds=3600,
        maximum_interval_seconds=604800,
        status="paused" if not retention_enabled else "failed" if retention_last_status == "failed" else "scheduled",
        last_run_at=retention_last_run,
        next_run_at=(
            schedule_time_after(retention_last_run, retention_interval)
            if retention_last_run else (datetime.now(timezone.utc) + timedelta(seconds=retention_interval)).isoformat()
        ) if retention_enabled else None,
        last_status=retention_last_status,
        configuration={
            "checkpoint_retention_days": int(retention["checkpoint_retention_days"]),
            "trace_retention_days": int(retention["trace_retention_days"]),
            "artifact_retention_days": int(retention["artifact_retention_days"]),
            "vacuum_after_cleanup": bool(retention["vacuum_after_cleanup"]),
        },
    ))

    with db_connect() as connection:
        source_rows = connection.execute(
            "SELECT * FROM news_sources ORDER BY priority DESC, created_at DESC"
        ).fetchall()
        agent_rows = connection.execute(
            "SELECT * FROM agent_configs ORDER BY builtin DESC, template_type, name"
        ).fetchall()
        active_agent_rows = connection.execute(
            """SELECT agent_id, status FROM agent_runs
               WHERE status IN ('queued', 'running') ORDER BY created_at DESC"""
        ).fetchall()
        latest_agent_rows = connection.execute(
            """SELECT runs.agent_id, runs.status, runs.finished_at
               FROM agent_runs AS runs
               INNER JOIN (
                   SELECT agent_id, MAX(created_at) AS latest_created_at
                   FROM agent_runs GROUP BY agent_id
               ) latest
               ON latest.agent_id = runs.agent_id AND latest.latest_created_at = runs.created_at"""
        ).fetchall()

    active_agents = {row["agent_id"]: row["status"] for row in active_agent_rows}
    latest_agents = {row["agent_id"]: row for row in latest_agent_rows}
    source_monitors = {item.source_id: item for item in news_source_monitoring()}
    for row in source_rows:
        source = row_to_source(row)
        if not information_source_is_ingestible(source):
            continue
        source_enabled = bool(source.enabled)
        monitor = source_monitors[source.id]
        effective_enabled = source_enabled and source.api_key_configured
        source_status: Literal["scheduled", "paused", "blocked", "running", "warning", "failed"]
        if not source_enabled:
            source_status = "paused"
        elif not source.api_key_configured:
            source_status = "blocked"
        elif monitor.status == "running":
            source_status = "running"
        elif source.last_error:
            source_status = "failed"
        else:
            source_status = "scheduled"
        tasks.append(ScheduledTask(
            id=f"news_source:{source.id}",
            task_type="news_source",
            name=f"{source.name} 轮询",
            description=f"通过 {source.adapter} 采集 {source.category} 内容。",
            owner_id=str(source.id),
            owner_name=source.provider,
            enabled=source_enabled,
            effective_enabled=effective_enabled,
            interval_seconds=source.poll_interval_seconds,
            minimum_interval_seconds=60,
            status=source_status,
            dependency=None if source.api_key_configured else f"缺少凭据 {source.api_key_env}",
            last_run_at=monitor.last_run_at,
            next_run_at=(
                schedule_time_after(source.last_fetched_at, source.poll_interval_seconds)
                if effective_enabled else None
            ),
            last_status=monitor.last_status,
        ))

    for row in agent_rows:
        agent = row_to_agent_config(row)
        latest = latest_agents.get(agent.id)
        active_status = active_agents.get(agent.id)
        effective_enabled = agent.enabled and agent.schedule_enabled
        if not agent.schedule_enabled:
            agent_status: Literal["scheduled", "paused", "blocked", "running", "warning", "failed"] = "paused"
        elif not agent.enabled:
            agent_status = "blocked"
        elif active_status:
            agent_status = "running"
        elif latest and latest["status"] == "failed":
            agent_status = "failed"
        else:
            agent_status = "scheduled"
        tasks.append(ScheduledTask(
            id=f"agent:{agent.id}",
            task_type="agent",
            name=f"{agent.name} 调度",
            description=f"{agent.template_type} · {agent.runtime_mode} · {agent.symbol_scope}",
            owner_id=agent.id,
            owner_name=agent.name,
            enabled=agent.schedule_enabled,
            effective_enabled=effective_enabled,
            interval_seconds=agent.interval_seconds,
            minimum_interval_seconds=30,
            status=agent_status,
            dependency=None if agent.enabled else "Agent 实例已停用",
            last_run_at=agent.last_run_at or (latest["finished_at"] if latest else None),
            next_run_at=agent.next_run_at if effective_enabled else None,
            last_status=latest["status"] if latest else None,
        ))
    return tasks


def get_scheduled_task(task_id: str) -> ScheduledTask:
    task = next((item for item in list_scheduled_tasks() if item.id == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return task


def update_scheduled_task(task_id: str, payload: ScheduledTaskUpdate) -> ScheduledTask:
    current = get_scheduled_task(task_id)
    interval = current.interval_seconds if payload.interval_seconds is None else payload.interval_seconds
    if interval < current.minimum_interval_seconds or interval > current.maximum_interval_seconds:
        raise HTTPException(
            status_code=422,
            detail=f"Interval must be between {current.minimum_interval_seconds} and {current.maximum_interval_seconds} seconds",
        )
    enabled = current.enabled if payload.enabled is None else payload.enabled
    if task_id == RETENTION_TASK_ID:
        set_config_value("agent_retention_enabled", "true" if enabled else "false")
        set_config_value("agent_retention_interval_seconds", str(interval))
        retention_updates = {
            "agent_checkpoint_retention_days": payload.checkpoint_retention_days,
            "agent_trace_retention_days": payload.trace_retention_days,
            "agent_artifact_retention_days": payload.artifact_retention_days,
        }
        for key, value in retention_updates.items():
            if value is not None:
                set_config_value(key, str(value))
        if payload.vacuum_after_cleanup is not None:
            set_config_value("agent_retention_vacuum", "true" if payload.vacuum_after_cleanup else "false")
    elif task_id.startswith("calendar_source:"):
        from app.modules.integrations.service import update_profile as update_data_source_profile
        update_data_source_profile(current.owner_id, DataSourceProfileUpdate(
            enabled=enabled, sync_interval_seconds=interval,
        ))
    elif task_id == "data_source:financial-calendar-sync":
        from app.modules.calendar import service as financial_calendar
        financial_calendar.update_settings(FinancialCalendarSettingsUpdate(sync_enabled=enabled, sync_interval_seconds=interval))
    elif task_id == "automation:financial-calendar-reminders":
        from app.modules.calendar import service as financial_calendar
        financial_calendar.update_settings(FinancialCalendarSettingsUpdate(reminders_enabled=enabled))
    elif task_id == "automation:paper-matching":
        from app.modules.trading import paper as paper_trading
        paper_trading.set_matching_settings(enabled, interval)
    elif current.task_type == "automation":
        set_automation_settings(AutomationSettings(enabled=enabled, interval_seconds=interval))
    elif current.task_type == "data_source":
        set_config_value("data_source_health_enabled", "true" if enabled else "false")
        set_config_value("data_source_health_interval", str(interval))
    elif current.task_type == "news_source":
        with db_connect() as connection:
            connection.execute(
                "UPDATE news_sources SET enabled = ?, poll_interval_seconds = ? WHERE id = ?",
                (int(enabled), interval, int(current.owner_id)),
            )
    elif current.task_type == "model_gateway":
        set_config_value("model_channel_health_enabled", "true" if enabled else "false")
        set_config_value("model_channel_health_interval", str(interval))
    else:
        update_agent(
            current.owner_id,
            AgentUpdate(schedule_enabled=enabled, interval_seconds=interval),
        )
    return get_scheduled_task(task_id)


def bulk_update_scheduled_tasks(payload: ScheduledTaskBulkUpdate) -> ScheduledTaskBulkResult:
    updated = 0
    for task_id in dict.fromkeys(payload.ids):
        update_scheduled_task(task_id, ScheduledTaskUpdate(enabled=payload.enabled))
        updated += 1
    return ScheduledTaskBulkResult(updated=updated, enabled=payload.enabled)


def run_scheduled_task_now(task_id: str) -> ScheduledTaskRunResult:
    task = get_scheduled_task(task_id)
    if task_id == RETENTION_TASK_ID:
        result = run_agent_data_retention()
        removed = sum(int(result[key]) for key in ("checkpoints", "checkpoint_writes", "spans", "approvals", "runs", "artifacts"))
        return ScheduledTaskRunResult(
            task_id=task.id, status="completed",
            message=f"清理 {removed} 条过期数据，释放 {int(result['reclaimed_bytes'])} 字节；空间整理{'完成' if result['vacuumed'] else '已跳过'}。",
        )
    if task_id.startswith("calendar_source:"):
        from app.modules.calendar import service as financial_calendar
        sync = financial_calendar.refresh_source(task.owner_id)
        return ScheduledTaskRunResult(
            task_id=task.id, status="warning" if sync.status != "completed" else "completed",
            message=f"获取 {sync.fetched} 条，合并 {sync.merged} 条，冲突 {sync.conflicts} 项。" + (f" {'；'.join(sync.errors[:2])}" if sync.errors else ""),
        )
    if task_id == "data_source:financial-calendar-sync":
        result = run_financial_calendar_cycle(force_sync=True)
        sync = result["sync"]
        return ScheduledTaskRunResult(
            task_id=task.id, status="warning" if sync.status != "completed" else "completed",
            message=f"获取 {sync.fetched} 条财经事件，写入 {sync.upserted} 条。" + (f" {'；'.join(sync.errors[:2])}" if sync.errors else ""),
        )
    if task_id == "automation:financial-calendar-reminders":
        result = run_financial_calendar_cycle(force_sync=False)
        return ScheduledTaskRunResult(
            task_id=task.id, status="completed",
            message=f"发送 {result['reminders']} 条提醒，触发 {result['analyses']} 个事件分析。",
        )
    if task_id == "automation:paper-matching":
        from app.modules.trading import paper as paper_trading
        result = paper_trading.run_matching()
        return ScheduledTaskRunResult(
            task_id=task.id, status="warning" if result.errors else "completed",
            message=f"检查 {result.checked} 笔委托，成交 {result.filled} 笔，过期 {result.expired} 笔。",
        )
    if task.task_type == "automation":
        result = run_automation_cycle()
        return ScheduledTaskRunResult(
            task_id=task.id,
            status="warning" if result.triggered_alerts else "completed",
            message=f"刷新 {len(result.news_results)} 个新闻源，新增 {result.inserted_news} 条新闻，触发 {result.triggered_alerts} 条预警。",
            run_id=result.id,
        )
    if task.task_type == "news_source":
        result = refresh_source(int(task.owner_id))
        return ScheduledTaskRunResult(
            task_id=task.id,
            status="failed" if result.error else "completed",
            message=result.error or f"抓取 {result.fetched} 条，新增 {result.inserted} 条。",
        )
    if task.task_type == "data_source":
        results = run_due_data_source_health_checks()
        return ScheduledTaskRunResult(
            task_id=task.id, status="completed",
            message=f"完成 {len(results)} 个到期数据源的健康检查。",
        )
    if task.task_type == "model_gateway":
        results = run_model_channel_health_checks()
        failed = sum(not item.get("ok", False) for item in results)
        return ScheduledTaskRunResult(
            task_id=task.id, status="warning" if failed else "completed",
            message=f"完成 {len(results)} 个模型渠道检测，失败 {failed} 个。",
        )
    agent = get_agent(task.owner_id)
    if not agent.enabled:
        raise HTTPException(status_code=409, detail="Enable the Agent instance before running this task")
    run = enqueue_agent_run(agent.id, trigger="manual")
    return ScheduledTaskRunResult(
        task_id=task.id,
        status="queued",
        message=f"{agent.name} 已进入运行队列。",
        run_id=run.id,
    )


def get_system_health() -> SystemHealth:
    init_db()
    generated_at = datetime.now(timezone.utc).isoformat()
    components: list[HealthComponent] = []
    provider_name = get_active_market_provider_name()

    try:
        with db_connect() as connection:
            connection.execute("SELECT 1").fetchone()
            source_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled,
                    COALESCE(SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END), 0) AS errors
                FROM news_sources
                """
            ).fetchone()
            alert_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled
                FROM alert_rules
                """
            ).fetchone()
            factor_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled
                FROM factor_definitions
                """
            ).fetchone()
            notification_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(enabled), 0) AS enabled,
                    COALESCE(SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END), 0) AS errors
                FROM notification_channels
                """
            ).fetchone()
            failed_delivery_row = connection.execute(
                "SELECT COUNT(*) AS failed FROM notification_deliveries WHERE success = 0"
            ).fetchone()
            portfolio_row = connection.execute("SELECT COUNT(*) AS total FROM portfolio_positions").fetchone()
            watchlist_row = connection.execute(
                "SELECT COUNT(*) AS total FROM watchlist_items WHERE provider = ?",
                (provider_name,),
            ).fetchone()
            event_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END), 0) AS errors,
                    COALESCE(SUM(CASE WHEN severity = 'risk' THEN 1 ELSE 0 END), 0) AS risks
                FROM event_logs
                WHERE created_at >= ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
            ).fetchone()
        components.append(HealthComponent(name="数据库", status="ok", message="SQLite 可读写，核心表已初始化。"))
    except sqlite3.Error as exc:
        return SystemHealth(
            status="error",
            generated_at=generated_at,
            components=[
                HealthComponent(
                    name="数据库",
                    status="error",
                    message=f"SQLite 检查失败：{str(exc)[:120]}",
                )
            ],
        )

    provider = get_market_provider_status()
    provider_status: Literal["ok", "warn", "error"] = "ok"
    if provider.status == "degraded":
        provider_status = "warn"
    if provider.status == "offline":
        provider_status = "error"
    components.append(
        HealthComponent(
            name="行情源",
            status=provider_status,
            message=f"{provider.display_name}：{provider.message}",
            count=len(provider.coverage),
        )
    )

    watchlist_total = int(watchlist_row["total"])
    provider_symbol_total = len(MARKET_PROVIDERS[provider_name].list_symbols())
    components.append(
        HealthComponent(
            name="自选观察",
            status="ok" if watchlist_total else "warn",
            message=(
                f"当前自选 {watchlist_total} 个标的。"
                if watchlist_total
                else f"未定制自选列表，默认展示 {provider_symbol_total} 个可用标的。"
            ),
            count=watchlist_total or provider_symbol_total,
        )
    )

    settings = get_automation_settings()
    components.append(
        HealthComponent(
            name="自动化盯盘",
            status="ok" if settings.enabled else "warn",
            message=f"{'已开启' if settings.enabled else '未开启'}，间隔 {settings.interval_seconds} 秒。",
        )
    )

    source_total = int(source_row["total"])
    source_enabled = int(source_row["enabled"])
    source_errors = int(source_row["errors"])
    source_status: Literal["ok", "warn", "error"] = "ok"
    source_message = f"{source_enabled}/{source_total} 个信息源启用。"
    if source_total == 0 or source_enabled == 0:
        source_status = "warn"
        source_message = "尚未启用信息源，新闻与事件数据当前不可用。"
    if source_errors:
        source_status = "error"
        source_message = f"{source_errors} 个信息源最近拉取失败。"
    components.append(HealthComponent(name="信息源", status=source_status, message=source_message, count=source_total))

    alert_total = int(alert_row["total"])
    alert_enabled = int(alert_row["enabled"])
    components.append(
        HealthComponent(
            name="预警规则",
            status="ok" if alert_enabled else "warn",
            message=f"{alert_enabled}/{alert_total} 条规则启用。",
            count=alert_total,
        )
    )

    factor_total = int(factor_row["total"])
    factor_enabled = int(factor_row["enabled"])
    components.append(
        HealthComponent(
            name="因子库",
            status="ok" if factor_enabled else "warn",
            message=f"{factor_enabled}/{factor_total} 个因子启用。",
            count=factor_total,
        )
    )

    channel_total = int(notification_row["total"])
    channel_enabled = int(notification_row["enabled"])
    channel_errors = int(notification_row["errors"])
    failed_deliveries = int(failed_delivery_row["failed"])
    notification_status: Literal["ok", "warn", "error"] = "ok"
    notification_message = f"{channel_enabled}/{channel_total} 个通知通道启用。"
    if channel_total == 0 or channel_enabled == 0:
        notification_status = "warn"
        notification_message = "尚未配置通知通道，预警只会写入事件日志。"
    if channel_errors or failed_deliveries:
        notification_status = "warn"
        notification_message = f"{channel_errors} 个通道异常，累计 {failed_deliveries} 次投递失败。"
    components.append(
        HealthComponent(
            name="通知投递",
            status=notification_status,
            message=notification_message,
            count=channel_total,
        )
    )

    position_total = int(portfolio_row["total"])
    components.append(
        HealthComponent(
            name="模拟组合",
            status="ok" if position_total else "warn",
            message=f"当前跟踪 {position_total} 个持仓。",
            count=position_total,
        )
    )

    event_total = int(event_row["total"])
    event_errors = int(event_row["errors"])
    event_risks = int(event_row["risks"])
    event_status: Literal["ok", "warn", "error"] = "ok"
    event_message = f"近 24 小时 {event_total} 条事件。"
    if event_risks:
        event_status = "warn"
        event_message = f"近 24 小时 {event_risks} 条风险事件。"
    if event_errors:
        event_status = "error"
        event_message = f"近 24 小时 {event_errors} 条错误事件。"
    components.append(HealthComponent(name="事件日志", status=event_status, message=event_message, count=event_total))

    overall: Literal["ok", "warn", "error"] = "ok"
    if any(item.status == "error" for item in components):
        overall = "error"
    elif any(item.status == "warn" for item in components):
        overall = "warn"

    return SystemHealth(status=overall, generated_at=generated_at, components=components)


def get_dashboard_overview() -> DashboardOverview:
    health = get_system_health()
    components = {item.name: item for item in health.components}
    agents = list_agents()
    enabled_agents = sum(item.enabled for item in agents)
    agent_runs = list_agent_runs(limit=8)
    failed_agent_runs = sum(item.status == "failed" for item in agent_runs)
    automation = get_automation_settings()
    automation_runs = list_automation_runs(limit=8)

    def component_metric(
        metric_id: str,
        label: str,
        component_name: str,
        href: str,
    ) -> DashboardMetric:
        component = components.get(component_name)
        if component is None:
            return DashboardMetric(
                id=metric_id, label=label, value="--", status="warn", detail="暂无状态数据。", href=href
            )
        return DashboardMetric(
            id=metric_id,
            label=label,
            value=str(component.count) if component.count is not None else component.status.upper(),
            status=component.status,
            detail=component.message,
            href=href,
        )

    agent_status: Literal["ok", "warn", "error"] = "ok"
    if not enabled_agents:
        agent_status = "warn"
    if failed_agent_runs:
        agent_status = "error"
    metrics = [
        DashboardMetric(
            id="platform",
            label="平台状态",
            value={"ok": "正常", "warn": "关注", "error": "异常"}[health.status],
            status=health.status,
            detail=f"{len(health.components)} 个组件已完成检查。",
            href="/admin",
        ),
        component_metric("market-provider", "行情源", "行情源", "/market"),
        DashboardMetric(
            id="agents",
            label="Agent",
            value=f"{enabled_agents}/{len(agents)}",
            status=agent_status,
            detail=f"最近 {len(agent_runs)} 次运行，失败 {failed_agent_runs} 次。",
            href="/agents",
        ),
        DashboardMetric(
            id="automation",
            label="自动化",
            value="运行中" if automation.enabled else "已暂停",
            status="ok" if automation.enabled else "warn",
            detail=f"执行间隔 {automation.interval_seconds} 秒，保留最近 {len(automation_runs)} 次运行。",
            href="/alerts",
        ),
        component_metric("news", "信息源", "信息源", "/news"),
        component_metric("notifications", "通知", "通知投递", "/alerts"),
    ]
    return DashboardOverview(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=health.status,
        metrics=metrics,
        health=health.components,
        agent_runs=agent_runs,
        automation_runs=automation_runs,
        events=list_event_logs(limit=10),
        watchlist=list_watchlist_snapshots()[:6],
    )


def get_admin_overview() -> AdminOverview:
    """Return a database-only operations snapshot for the management landing page."""
    init_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with db_connect() as connection:
        data_sources = connection.execute(
            "SELECT COUNT(*) total, SUM(enabled) enabled, SUM(CASE WHEN enabled=1 AND health_status IN ('degraded','unavailable') THEN 1 ELSE 0 END) issues FROM data_source_profiles"
        ).fetchone()
        news_sources = connection.execute(
            "SELECT COUNT(*) total, SUM(enabled) enabled, SUM(CASE WHEN enabled=1 AND last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END) issues FROM news_sources"
        ).fetchone()
        agents = connection.execute(
            "SELECT COUNT(*) total, SUM(enabled) enabled FROM agent_configs"
        ).fetchone()
        failed_agents = int(connection.execute(
            "SELECT COUNT(*) total FROM agent_runs WHERE status='failed' AND created_at >= ?", (since,)
        ).fetchone()["total"])
        models = connection.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN enabled=1 AND health_status='healthy' THEN 1 ELSE 0 END) healthy, SUM(CASE WHEN enabled=1 AND health_status IN ('degraded','unavailable') THEN 1 ELSE 0 END) issues FROM model_channels"
        ).fetchone()
        pending = int(connection.execute("SELECT COUNT(*) total FROM agent_proposals WHERE status='pending'").fetchone()["total"])
        credentials = connection.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN encrypted_value IS NOT NULL THEN 1 ELSE 0 END) configured, SUM(CASE WHEN test_status='failed' THEN 1 ELSE 0 END) issues FROM api_credentials"
        ).fetchone()
        recent_audit = connection.execute(
            "SELECT id, username, action, resource_type, created_at FROM admin_audit_logs ORDER BY created_at DESC LIMIT 8"
        ).fetchall()

    def count(row: sqlite3.Row, key: str) -> int:
        return int(row[key] or 0)

    attention: list[AdminAttentionItem] = []
    issue_specs = [
        (count(data_sources, "issues"), "data-source", "数据源需要处理", "存在降级或不可用的数据源。", "/admin/data-sources"),
        (count(news_sources, "issues"), "news-source", "新闻源采集异常", "存在最近刷新失败的新闻源。", "/admin/news-sources"),
        (failed_agents, "agent-run", "Agent 运行失败", "近 24 小时存在失败运行。", "/admin/agents?section=runs"),
        (count(models, "issues"), "model-channel", "模型渠道不可用", "存在降级或不可用的启用渠道。", "/admin/models"),
        (count(credentials, "issues"), "credential", "凭据检测失败", "存在连接检测失败的凭据。", "/admin/credentials"),
        (pending, "proposal", "Agent 建议待审批", "存在尚未处理的 Agent 资源建议。", "/admin/agents?section=proposals"),
    ]
    for amount, item_id, title, detail, href in issue_specs:
        if amount:
            attention.append(AdminAttentionItem(id=item_id, severity="error" if item_id in {"model-channel", "credential"} else "warn", title=f"{title} · {amount}", detail=detail, href=href))

    metrics = [
        AdminOverviewMetric(id="data", label="数据源", value=f"{count(data_sources, 'enabled')}/{count(data_sources, 'total')}", status="warn" if count(data_sources, "issues") else "ok", detail=f"异常 {count(data_sources, 'issues')} 个", href="/admin/data-sources"),
        AdminOverviewMetric(id="news", label="新闻源", value=f"{count(news_sources, 'enabled')}/{count(news_sources, 'total')}", status="warn" if count(news_sources, "issues") else "ok", detail=f"异常 {count(news_sources, 'issues')} 个", href="/admin/news-sources"),
        AdminOverviewMetric(id="agents", label="Agent", value=f"{count(agents, 'enabled')}/{count(agents, 'total')}", status="error" if failed_agents else "ok", detail=f"24h 失败 {failed_agents} 次", href="/admin/agents"),
        AdminOverviewMetric(id="models", label="模型渠道", value=f"{count(models, 'healthy')}/{count(models, 'total')}", status="error" if count(models, "issues") else "ok", detail=f"异常 {count(models, 'issues')} 个", href="/admin/models"),
        AdminOverviewMetric(id="credentials", label="凭据", value=f"{count(credentials, 'configured')}/{count(credentials, 'total')}", status="error" if count(credentials, "issues") else "ok", detail=f"检测失败 {count(credentials, 'issues')} 个", href="/admin/credentials"),
        AdminOverviewMetric(id="proposals", label="待审批", value=str(pending), status="warn" if pending else "ok", detail="Agent 资源变更建议", href="/admin/agents?section=proposals"),
    ]
    overall: Literal["ok", "warn", "error"] = "error" if any(item.status == "error" for item in metrics) else "warn" if attention else "ok"
    return AdminOverview(
        generated_at=datetime.now(timezone.utc).isoformat(), status=overall, metrics=metrics,
        attention=attention,
        activity=[AdminActivityItem(id=str(row["id"]), actor=str(row["username"]), action=str(row["action"]), resource=str(row["resource_type"]), created_at=str(row["created_at"])) for row in recent_audit],
    )
