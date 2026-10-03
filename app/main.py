"""Husk — FastAPI entry point. Serves the React UI and the v1 API on 127.0.0.1 only."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.automations.scheduler import LocalScheduler
from app.core.automations.service import run_automation
from app.core.events import EventHub
from app.core.knowledge.index import KnowledgeIndex
from app.core.services import Services
from app.core.system.maintenance import MaintenanceMonitor
from app.data.models.discovery import RunStatus
from app.endpoints.security import LocalGuard
from app.endpoints.v1 import system
from app.endpoints.v1.agent.router import router as agent_router
from app.endpoints.v1.automations.router import router as automations_router
from app.endpoints.v1.discovery.router import router as discovery_router
from app.endpoints.v1.documents.router import router as documents_router
from app.endpoints.v1.suggestions.router import router as suggestions_router
from app.endpoints.v1.user.router import callback_router
from app.endpoints.v1.user.router import router as user_router
from app.gateways.neon import build_neon
from app.gateways.regolo.key_store import UserAiKeyStore
from app.gateways.regolo.provider import RegoloProvider
from app.gateways.sqlite import Database
from app.gateways.sqlite.repositories import AutomationRepo, SessionRepo, UsageRepo
from app.gateways.studio.client import StudioClient
from app.gateways.studio.tracing import init_tracing
from app.gateways.zitadel.oidc import ZitadelClient
from app.gateways.zitadel.token_store import TokenStore
from app.helpers.logging import configure_logging, get_logger
from app.helpers.paths import resolve_data_paths
from app.helpers.settings import Settings, get_settings, resource_root

log = get_logger("main")
FRONTEND_DIST = resource_root() / "app" / "frontend" / "dist"


def build_services(settings: Settings) -> Services:
    paths = resolve_data_paths(settings)
    configure_logging(paths.logs)
    db = Database(paths.db)
    db.migrate()
    neon = build_neon(settings)
    ai_keys = UserAiKeyStore(db)
    return Services(
        settings=settings,
        paths=paths,
        db=db,
        index=KnowledgeIndex(paths.bm25),
        llm=RegoloProvider(settings, UsageRepo(db), ai_keys),
        ai_keys=ai_keys,
        neon=neon,
        studio=StudioClient(settings),
        zitadel=ZitadelClient(settings),
        tokens=TokenStore(use_keyring=settings.env != "test"),
        events=EventHub(),
        maintenance=MaintenanceMonitor(neon, db, __version__, settings.maintenance_poll_s),
        version=__version__,
    )


def _mark_interrupted_runs(services: Services) -> None:
    """Runs that were mid-way when Husk stopped become 'paused' and can be resumed."""
    with services.db.tx() as c:
        c.execute(
            "UPDATE discovery_runs SET status = ? WHERE status IN (?, ?, ?)",
            (RunStatus.paused.value, RunStatus.running.value, RunStatus.pending.value, RunStatus.waiting_network.value),
        )


async def _studio_loop(services: Services) -> None:
    while True:
        await services.studio.probe()
        await asyncio.sleep(services.settings.studio_probe_s)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        svc = services or build_services(settings)
        app.state.services = svc
        init_tracing(__version__)
        _mark_interrupted_runs(svc)
        SessionRepo(svc.db).purge_expired()
        background: list[asyncio.Task] = []
        if settings.env != "test":
            await svc.llm.resolve_model()  # type: ignore[attr-defined]
            await svc.neon.start()
            await svc.maintenance.check()
            svc.maintenance.start()
            background.append(asyncio.create_task(_studio_loop(svc), name="studio-probe"))

        async def scheduled_run(a, trigger, payload):  # type: ignore[no-untyped-def]
            await run_automation(svc, a, trigger, payload, a.activated_by or "scheduler")

        scheduler = LocalScheduler(scheduled_run, lambda: svc.maintenance.blocking)
        scheduler.start(AutomationRepo(svc.db, "*").all_active())
        svc.scheduler = scheduler
        log.info("Husk %s ready on http://%s:%s (data: %s)", __version__, settings.host, settings.port, svc.paths.root)
        try:
            yield
        finally:
            scheduler.shutdown()
            for t in background + list(svc.tasks.values()):
                t.cancel()
            await svc.maintenance.stop()
            await svc.neon.close()

    app = FastAPI(title="Husk", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if settings.env != "prod" else None, openapi_url="/api/openapi.json")
    app.add_middleware(LocalGuard, settings=settings)
    if settings.env == "dev":
        from fastapi.middleware.cors import CORSMiddleware

        from app.endpoints.security import allowed_origins

        app.add_middleware(CORSMiddleware, allow_origins=sorted(allowed_origins(settings)), allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    _install_error_handlers(app)
    for r in (system.router, callback_router, user_router, documents_router, discovery_router,
              automations_router, agent_router, suggestions_router):
        app.include_router(r)

    _mount_frontend(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    """Errors carry message keys; render them in the caller's language (Accept-Language)."""
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.i18n.errors import LocalizedError, render

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            body = render(request, exc.detail["code"], exc.detail.get("params"))
            return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))
        return await http_exception_handler(request, exc)

    @app.exception_handler(LocalizedError)
    async def _localized(request: Request, exc: LocalizedError):  # type: ignore[no-untyped-def]
        return JSONResponse(render(request, exc.key, exc.params), status_code=exc.status)


def _mount_frontend(app: FastAPI) -> None:
    dist: Path = FRONTEND_DIST
    if not (dist / "index.html").exists():
        @app.get("/", include_in_schema=False)
        def _no_ui() -> JSONResponse:
            return JSONResponse({"detail": "UI not built: run `npm run build` in app/frontend"})
        return
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request) -> FileResponse:
        if full_path.startswith(("api/", "internal/")):
            from fastapi import HTTPException

            raise HTTPException(404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
