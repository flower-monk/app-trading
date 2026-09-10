from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.modules.ai.service import agent_worker_loop
from app.modules.infrastructure.database import init_db
from app.modules.news.service import news_source_scheduler_loop
from app.modules.operations.service import (
    automation_loop,
    data_source_health_loop,
    financial_calendar_loop,
    model_channel_health_loop,
)
from app.modules.trading.paper import ensure_schema as ensure_paper_schema
from app.modules.trading.paper import matching_loop


BACKGROUND_JOBS = {
    "automation": automation_loop,
    "agent": agent_worker_loop,
    "data_source_health": data_source_health_loop,
    "news_source": news_source_scheduler_loop,
    "model_health": model_channel_health_loop,
    "financial_calendar": financial_calendar_loop,
    "paper_matching": matching_loop,
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    ensure_paper_schema()
    tasks = {
        name: asyncio.create_task(job(), name=f"open-trading:{name}")
        for name, job in BACKGROUND_JOBS.items()
    }
    application.state.background_tasks = tasks
    try:
        yield
    finally:
        for task in tasks.values():
            task.cancel()
        for task in tasks.values():
            with suppress(asyncio.CancelledError):
                await task

