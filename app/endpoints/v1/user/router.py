"""Account: Clerk login (login only — no guest, no sign-up), profile, settings, usage,
and the single-file backup (export everything / import)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import webbrowser
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.services import Services
from app.data.models.user import Me, User, UserSettings, UserSettingsPatch
from app.endpoints.deps import SESSION_COOKIE, CurrentUser, current_user, get_services
from app.gateways.clerk.oidc import ClerkError
from app.gateways.gateway.errors import GatewayError
from app.gateways.sqlite.backup import BackupError, export_backup, import_backup
from app.gateways.sqlite.repositories import SessionRepo, UsageRepo, UserRepo
from app.gateways.sqlite.wipe import WipeError, wipe_data
from app.helpers.ids import new_secret
from app.helpers.logging import get_logger
from app.helpers.time import utcnow
from app.i18n.errors import api_error, from_localized

log = get_logger("auth")

router = APIRouter(prefix="/api/v1/user", tags=["user"])
callback_router = APIRouter(tags=["auth"])  # mounted at root: /callback (the OAuth redirect URI)

SESSION_DAYS = 30


class AuthConfig(BaseModel):
    configured: bool


class LoginStart(BaseModel):
    url: str  # the Clerk page, also shown in the UI in case the browser did not open
    poll_id: str  # secret held by the app window only; redeemed once by /auth/poll


class PollIn(BaseModel):
    poll_id: str = Field(..., min_length=16, max_length=200)


class PollOut(BaseModel):
    status: Literal["pending", "ok", "error"]
    reason: str | None = None


@router.get("/auth/config", response_model=AuthConfig)
def auth_config(services: Services = Depends(get_services)) -> AuthConfig:
    return AuthConfig(configured=services.settings.clerk_configured)


@router.post("/auth/login", response_model=LoginStart)
async def login(services: Services = Depends(get_services)) -> LoginStart:
    """Start the Clerk login in the SYSTEM browser (the UI may be a native webview). The browser finishes at
    /callback; this window then redeems `poll_id` at /auth/poll to get its own session cookie."""
    try:
        url, state = await services.clerk.authorize_url()
    except ClerkError as exc:
        from_localized(exc)
    except Exception as exc:
        log.warning("clerk discovery failed: %s", type(exc).__name__)
        api_error(503, "errors.login_unreachable")
    poll_id = services.logins.begin(state)
    if services.settings.open_browser:
        try:
            await asyncio.to_thread(webbrowser.open, url)
        except Exception as exc:  # the UI shows the link as a fallback
            log.warning("could not open the browser: %s", type(exc).__name__)
    return LoginStart(url=url, poll_id=poll_id)


def _frontend(services: Services, path: str = "/") -> str:
    base = services.settings.frontend_dev_url
    return (base.rstrip("/") + path) if base else path


def _done(services: Services, status: str, reason: str | None = None) -> RedirectResponse:
    q = f"?status={status}" + (f"&reason={reason}" if reason else "")
    return RedirectResponse(_frontend(services, "/auth/done" + q))


@callback_router.get("/callback", include_in_schema=False)
async def auth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None,
    services: Services = Depends(get_services),
) -> RedirectResponse:
    """Runs in the system browser. Sets NO cookie: it records the result for the app window and shows
    the "you can close this tab" page."""
    if error or not code or not state:
        services.logins.fail(state, "cancelled")
        return _done(services, "error", "cancelled")
    try:
        tokens = await services.clerk.exchange(code, state)
        ident = await services.clerk.identity(tokens)
    except ClerkError as exc:
        reason = "no_org" if exc.key == "errors.login_no_org" else "unreachable" if exc.status == 503 else "failed"
        log.warning("login failed: %s", reason)
        services.logins.fail(state, reason)
        return _done(services, "error", reason)
    user = User(id=ident.user_id, org_id=ident.org_id, email=ident.email, display_name=ident.name)
    UserRepo(services.db).upsert(user)
    services.tokens.save_refresh(user.id, tokens.refresh_token)
    services.tokens.set_access(user.id, tokens.access_token, tokens.expires_at)
    services.db.kv_set("last_user_id", user.id)
    try:
        await services.gateway.me(user.id)  # the gateway registers the user and organisation in Neon
    except Exception as exc:  # not needed to sign in: it is retried by the gateway on the first protected call
        log.warning("gateway registration failed: %s", type(exc).__name__)
    services.logins.complete(state, user.id)
    return _done(services, "ok")


@router.post("/auth/poll", response_model=PollOut)
def auth_poll(body: PollIn, response: Response, services: Services = Depends(get_services)) -> PollOut:
    res = services.logins.poll(body.poll_id)
    if res.status == "ok":
        user = UserRepo(services.db).get(res.user_id or "")
        if not user:
            return PollOut(status="error", reason="failed")
        sid = new_secret()
        SessionRepo(services.db).create(sid, user, utcnow() + timedelta(days=SESSION_DAYS))
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=False,
                            max_age=SESSION_DAYS * 86400, path="/")
    return PollOut(status=res.status, reason=res.reason)  # type: ignore[arg-type]


@router.post("/auth/logout")
async def logout(request: Request, services: Services = Depends(get_services)) -> dict:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        repo = SessionRepo(services.db)
        uid = repo.resolve(sid)
        repo.delete(sid)
        if uid:
            refresh = services.tokens.load_refresh(uid)
            services.tokens.delete(uid)
            if refresh:
                await services.clerk.revoke(refresh)
    return {"ok": True}


class EntitlementOut(BaseModel):
    allowed: bool | None  # None = unknown (the gateway cannot be reached and nothing was seen before)
    status: str
    source: Literal["gateway", "cache", "unknown"]


@router.get("/entitlement", response_model=EntitlementOut)
async def entitlement(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> EntitlementOut:
    """Has this organisation paid? The gateway decides (billing state); the last answer is kept so Husk still opens
    offline. An unreachable gateway never locks anyone out of their local data."""
    key = f"entitlement:{cu.org_id}"
    try:
        ent = await services.gateway.entitlement(cu.id)
    except GatewayError as exc:
        if exc.key == "errors.session_expired":
            SessionRepo(services.db).delete(cu.session_id)
            api_error(401, exc.key)
        cached = services.db.kv_get(key)
        if cached:
            return EntitlementOut(allowed=cached["allowed"], status=cached["status"], source="cache")
        return EntitlementOut(allowed=None, status="unknown", source="unknown")
    services.db.kv_set(key, {"allowed": ent.allowed, "status": ent.status})
    return EntitlementOut(allowed=ent.allowed, status=ent.status, source="gateway")


@router.get("/me", response_model=Me)
def me(cu: CurrentUser = Depends(current_user)) -> Me:
    return Me(user=cu.user, settings=cu.settings)


@router.patch("/settings", response_model=UserSettings)
def update_settings(patch: UserSettingsPatch, cu: CurrentUser = Depends(current_user),
                    services: Services = Depends(get_services)) -> UserSettings:
    merged = cu.settings.model_copy(update=patch.model_dump(exclude_none=True))
    return UserRepo(services.db).save_settings(cu.id, merged)


@router.get("/usage")
def usage(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> dict:
    rows = UsageRepo(services.db).summary(cu.org_id, cu.id)
    return {
        "rows": rows,
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "calls": sum(r["calls"] for r in rows),
    }


# --- backup -----------------------------------------------------------------------------------
@router.get("/data/export")
async def export_all(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> FileResponse:
    path = await asyncio.to_thread(export_backup, services.db, services.paths, services.version)
    return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")


class ImportResponse(BaseModel):
    documents: int
    files: int
    safety_copy: str


@router.post("/data/import", response_model=ImportResponse)
async def import_all(
    file: UploadFile = File(...), confirm: bool = Form(False),
    cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
) -> ImportResponse:
    if not confirm:
        api_error(400, "errors.import_confirm_required")
    if any(not t.done() for k, t in services.tasks.items() if k.startswith("discovery:")):
        api_error(409, "errors.import_discovery_running")
    services.suggestions.cancel_all()  # pending suggestion jobs refer to documents about to be replaced
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "import.db"
        with target.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        try:
            res = await asyncio.to_thread(import_backup, services.db, services.paths, target, cu.org_id)
        except BackupError as exc:
            from_localized(exc)
    # Keep the current user signed in (sessions do not travel in backups).
    UserRepo(services.db).upsert(cu.user)
    SessionRepo(services.db).create(cu.session_id, cu.user, utcnow() + timedelta(days=SESSION_DAYS))
    services.index.drop()
    for org in res.org_ids:
        await asyncio.to_thread(services.knowledge(org).reindex)
    if services.scheduler is not None:
        from app.gateways.sqlite.repositories import AutomationRepo

        for a in AutomationRepo(services.db, cu.org_id).list():
            services.scheduler.sync(a)
    return ImportResponse(documents=res.documents, files=res.files, safety_copy=res.safety_copy)


# --- delete everything --------------------------------------------------------------------------
class WipeRequest(BaseModel):
    confirm: bool = False


class WipeResponse(BaseModel):
    documents: int
    automations: int
    bytes_freed: int


@router.delete("/data", response_model=WipeResponse)
async def delete_all_data(
    body: WipeRequest, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
) -> WipeResponse:
    """Empties the data folder (database, documents, indexes, runs, outputs, old backups). The signed-in user stays
    signed in with their settings; keychain items (login, Telegram/Gmail secrets) are not in the folder and stay."""
    if not body.confirm:
        api_error(400, "errors.wipe_confirm_required")
    if any(not t.done() for k, t in services.tasks.items() if k.startswith("discovery:")):
        api_error(409, "errors.wipe_discovery_running")
    services.suggestions.cancel_all()
    if services.scheduler is not None:
        from app.gateways.sqlite.repositories import AutomationRepo

        for a in AutomationRepo(services.db, "*").all_active():
            services.scheduler.remove(a.id)
    try:
        res = await asyncio.to_thread(wipe_data, services.db, services.paths)
    except WipeError as exc:
        from_localized(exc)
    services.index.drop()
    services.events.reset()
    # Keep the current user signed in, with their settings (language, personality): sessions are in the database.
    UserRepo(services.db).upsert(cu.user)
    UserRepo(services.db).save_settings(cu.id, cu.settings)
    SessionRepo(services.db).create(cu.session_id, cu.user, utcnow() + timedelta(days=SESSION_DAYS))
    return WipeResponse(documents=res.documents, automations=res.automations, bytes_freed=res.bytes_freed)
