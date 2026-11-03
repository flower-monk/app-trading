from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.core.config import settings
from app.modules.platform.service import auth_setup_required, current_admin_user


router = APIRouter(include_in_schema=False)
PAGES = settings.page_dir


@router.get("/setup")
def setup_page() -> Response:
    if not auth_setup_required():
        return RedirectResponse("/login", status_code=303)
    return FileResponse(PAGES / "auth.html")


@router.get("/login")
def login_page() -> FileResponse:
    return FileResponse(PAGES / "auth.html")


def _admin_file(request: Request, filename: str) -> FileResponse:
    current_admin_user(request)
    return FileResponse(PAGES / filename, headers={"Cache-Control": "no-cache"})


def _admin_redirect(request: Request, target: str) -> RedirectResponse:
    query = request.url.query
    return RedirectResponse(f"{target}?{query}" if query else target, status_code=303)


@router.get("/admin")
def admin_page(request: Request) -> FileResponse:
    return _admin_file(request, "admin-overview.html")


@router.get("/admin/resources")
def admin_resources_page(request: Request) -> FileResponse:
    return _admin_file(request, "admin.html")


@router.get("/agents")
def agents_page(request: Request) -> RedirectResponse:
    current_admin_user(request)
    return _admin_redirect(request, "/admin/agents")


@router.get("/admin/agents")
def admin_agents_page(request: Request) -> FileResponse:
    return _admin_file(request, "agents.html")


@router.get("/admin/agent-studio")
def admin_agent_studio_page(request: Request) -> FileResponse:
    return _admin_file(request, "agent-studio.html")


@router.get("/admin/impact-graph")
def admin_impact_graph_page(request: Request) -> FileResponse:
    return _admin_file(request, "impact-graph.html")


@router.get("/model-management")
def model_management_page(request: Request) -> RedirectResponse:
    current_admin_user(request)
    params = [("tab", "channels"), *request.query_params.multi_items()]
    return RedirectResponse(f"/admin/models?{urlencode(params)}", status_code=303)


@router.get("/model-playground")
def model_playground_page(request: Request) -> RedirectResponse:
    current_admin_user(request)
    params = [("tab", "playground"), *request.query_params.multi_items()]
    return RedirectResponse(f"/admin/models?{urlencode(params)}", status_code=303)


for legacy_path, target in (("/schedules", "/admin/schedules"), ("/credentials", "/admin/credentials"), ("/data-sources", "/admin/data-sources"), ("/news-sources", "/admin/news-sources")):
    async def redirect_page(request: Request, destination: str = target) -> RedirectResponse:
        current_admin_user(request)
        return _admin_redirect(request, destination)

    router.add_api_route(legacy_path, redirect_page, methods=["GET"])


for page_path, filename in (("/admin/schedules", "schedules.html"), ("/admin/credentials", "credentials.html"), ("/admin/data-sources", "data-sources.html"), ("/admin/news-sources", "news-sources.html"), ("/admin/models", "models.html")):
    async def admin_file(request: Request, page: str = filename) -> FileResponse:
        return _admin_file(request, page)

    router.add_api_route(page_path, admin_file, methods=["GET"])
