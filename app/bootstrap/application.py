from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.auth_admin import router as auth_admin_router
from app.api.routes.charting import router as charting_router
from app.api.routes.data_sources import router as data_sources_router
from app.api.routes.financial_calendar import router as financial_calendar_router
from app.api.routes.market_news import router as market_news_router
from app.api.routes.model_gateway import router as model_gateway_router
from app.api.routes.paper_trading import router as paper_trading_router
from app.api.routes.quant_research import router as quant_research_router
from app.api.routes.trading_ops import router as trading_ops_router
from app.bootstrap.lifecycle import lifespan
from app.bootstrap.middleware import authentication_middleware
from app.core.application import create_application
from app.core.config import settings
from app.web.router import router as web_router


ROUTERS = (
    auth_admin_router,
    market_news_router,
    trading_ops_router,
    quant_research_router,
    charting_router,
    data_sources_router,
    model_gateway_router,
    financial_calendar_router,
    paper_trading_router,
)


def create_app() -> FastAPI:
    application = create_application(settings, lifespan)
    application.middleware("http")(authentication_middleware)
    application.include_router(web_router)
    for router in ROUTERS:
        application.include_router(router)
    return application


app = create_app()
