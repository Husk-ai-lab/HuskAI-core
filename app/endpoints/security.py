"""Loopback hardening: a website open in the user's browser must not drive the backend.

State-changing requests must come from an allowed Origin (or carry none, e.g. curl from
this machine). /internal/* additionally requires the shell's per-launch secret.
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.helpers.settings import Settings

SAFE = {"GET", "HEAD", "OPTIONS"}


def allowed_origins(settings: Settings) -> set[str]:
    p = settings.port
    origins = {f"http://127.0.0.1:{p}", f"http://localhost:{p}"}
    if settings.frontend_dev_url:
        origins.add(settings.frontend_dev_url.rstrip("/"))
    if settings.env == "dev":
        origins |= {"http://localhost:5173", "http://127.0.0.1:5173"}
    return origins


class LocalGuard(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings
        self.origins = allowed_origins(settings)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/internal/"):
            secret = self.settings.launch_secret
            given = request.headers.get("x-husk-launch-secret", "")
            if not secret or not hmac.compare_digest(secret, given):
                return JSONResponse({"detail": "forbidden"}, status_code=403)
        if request.method not in SAFE:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in self.origins:
                return JSONResponse({"detail": "origin not allowed", "code": "errors.origin_not_allowed"}, status_code=403)
        return await call_next(request)
