from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import Settings


Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_application(config: Settings, lifespan: Lifespan) -> FastAPI:
    application = FastAPI(
        title=config.app_name,
        version=config.app_version,
        lifespan=lifespan,
    )
    application.add_middleware(GZipMiddleware, minimum_size=1000)
    application.mount("/static", StaticFiles(directory=config.static_dir), name="static")
    return application
