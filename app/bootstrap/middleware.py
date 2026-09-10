from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.modules.infrastructure.database import SESSION_COOKIE
from app.modules.platform.service import add_admin_audit, auth_setup_required, lookup_auth_session


async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    public_paths = {"/login", "/setup", "/api/auth/setup-status", "/api/auth/setup", "/api/auth/login", "/favicon.ico"}
    if path.startswith("/static/") or path in public_paths:
        return await call_next(request)

    if auth_setup_required():
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Initial administrator setup required"}, status_code=503)
        return RedirectResponse("/setup", status_code=303)

    session = lookup_auth_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    user, csrf_token, expires_at = session
    request.state.user = user
    request.state.csrf_token = csrf_token
    request.state.session_expires_at = expires_at
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        self_service_paths = {"/api/auth/logout", "/api/auth/preferences"}
        self_service_prefixes = ("/api/paper/",)
        if path not in self_service_paths and not path.startswith(self_service_prefixes) and user.role != "admin":
            return JSONResponse({"detail": "Administrator role required"}, status_code=403)
        supplied_csrf = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied_csrf, csrf_token):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)

    response = await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400 and not path.startswith(("/api/admin/", "/api/auth/")):
        segments = [segment for segment in path.split("/") if segment]
        resource_type = segments[1] if len(segments) > 1 else "system"
        resource_id = segments[-1] if len(segments) > 2 else None
        add_admin_audit(user, request.method.lower(), resource_type, resource_id, f"{request.method} {path}")
    return response

