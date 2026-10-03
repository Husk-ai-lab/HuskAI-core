"""Account: ZITADEL login (login only — no guest, no sign-up), profile, settings, usage,
and the single-file backup (export everything / import)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.services import Services
from app.data.models.user import Me, User, UserSettings, UserSettingsPatch
from app.endpoints.deps import SESSION_COOKIE, CurrentUser, current_user, get_services
from app.gateways.regolo.key_store import mask
from app.gateways.regolo.provider import KeyRejected
from app.gateways.sqlite.backup import BackupError, export_backup, import_backup
from app.gateways.sqlite.repositories import SessionRepo, UsageRepo, UserRepo
from app.gateways.zitadel.oidc import ZitadelError, identity_from_claims
from app.helpers.ids import new_secret
from app.helpers.logging import get_logger
from app.helpers.time import utcnow
from app.i18n.errors import api_error, from_localized

log = get_logger("auth")

router = APIRouter(prefix="/api/v1/user", tags=["user"])
callback_router = APIRouter(tags=["auth"])  # mounted at root: /auth/callback (plan contract)

SESSION_DAYS = 30


class LoginUrl(BaseModel):
    url: str


class AuthConfig(BaseModel):
    configured: bool
    issuer: str | None


class LogoutResult(BaseModel):
    end_session_url: str | None = None


@router.get("/auth/config", response_model=AuthConfig)
def auth_config(services: Services = Depends(get_services)) -> AuthConfig:
    s = services.settings
    return AuthConfig(configured=s.zitadel_configured, issuer=s.zitadel_issuer)


@router.get("/auth/login", response_model=LoginUrl)
async def login(login_hint: str | None = None, services: Services = Depends(get_services)) -> LoginUrl:
    try:
        url, _state = await services.zitadel.authorize_url(login_hint=login_hint)
    except ZitadelError as exc:
        from_localized(exc)
    except Exception as exc:
        log.warning("zitadel discovery failed: %s", type(exc).__name__)
        api_error(503, "errors.login_unreachable")
    return LoginUrl(url=url)


def _frontend(services: Services, path: str = "/") -> str:
    base = services.settings.frontend_dev_url
    return (base.rstrip("/") + path) if base else path


@callback_router.get("/auth/callback", include_in_schema=False)
async def auth_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
    services: Services = Depends(get_services),
) -> RedirectResponse:
    if error or not code or not state:
        return RedirectResponse(_frontend(services, "/accesso?error=cancelled"))
    try:
        tokens = await services.zitadel.exchange(code, state)
        ident = identity_from_claims(tokens.claims)
    except ZitadelError as exc:
        log.warning("login failed: %s", exc)
        return RedirectResponse(_frontend(services, "/accesso?error=failed"))
    user = User(
        id=ident.user_id, org_id=ident.org_id, org_name=ident.org_name, email=ident.email,
        display_name=ident.name, roles=ident.roles,
    )
    UserRepo(services.db).upsert(user)
    services.tokens.save_refresh(user.id, tokens.refresh_token)
    services.tokens.set_access(user.id, tokens.access_token, tokens.expires_at, tokens.id_token)
    services.db.kv_set("last_user_id", user.id)
    sid = new_secret()
    SessionRepo(services.db).create(sid, user, utcnow() + timedelta(days=SESSION_DAYS))
    resp = RedirectResponse(_frontend(services, "/"))
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=False,
                    max_age=SESSION_DAYS * 86400, path="/")
    return resp


@router.post("/auth/logout", response_model=LogoutResult)
async def logout(request: Request, services: Services = Depends(get_services)) -> LogoutResult:
    sid = request.cookies.get(SESSION_COOKIE)
    end_url = None
    if sid:
        repo = SessionRepo(services.db)
        uid = repo.resolve(sid)
        repo.delete(sid)
        if uid:
            id_token = services.tokens.id_token(uid)
            services.tokens.delete(uid)
            if services.settings.zitadel_configured:
                try:
                    end_url = await services.zitadel.end_session_url(
                        id_token, f"http://127.0.0.1:{services.settings.port}/accesso")
                except Exception:
                    end_url = None
    return LogoutResult(end_session_url=end_url)


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
    own_key = services.ai_keys.get(cu.id)  # read before the db is replaced
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "import.db"
        with target.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        try:
            res = await asyncio.to_thread(import_backup, services.db, services.paths, target, cu.org_id)
        except BackupError as exc:
            from_localized(exc)
    # Keep the current user signed in and their AI key (neither travels in backups).
    UserRepo(services.db).upsert(cu.user)
    SessionRepo(services.db).create(cu.session_id, cu.user, utcnow() + timedelta(days=SESSION_DAYS))
    if own_key:
        services.ai_keys.set(cu.id, cu.org_id, own_key.api_key, own_key.model)
    services.index.drop()
    for org in res.org_ids:
        await asyncio.to_thread(services.knowledge(org).reindex)
    if services.scheduler is not None:
        from app.gateways.sqlite.repositories import AutomationRepo

        for a in AutomationRepo(services.db, cu.org_id).list():
            services.scheduler.sync(a)
    return ImportResponse(documents=res.documents, files=res.files, safety_copy=res.safety_copy)


# --- AI key (simplified BYOK for the MVP: the key is provided by Husk AI) --------------------
class AiKeyStatus(BaseModel):
    configured: bool
    source: str  # user | env | none
    hint: str | None = None  # last 4 characters only; the key itself is never returned
    model: str | None = None
    verified_at: str | None = None


class AiKeyIn(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=512)


def _ai_key_status(services: Services, user_id: str) -> AiKeyStatus:
    own = services.ai_keys.get(user_id)
    if own:
        return AiKeyStatus(configured=True, source="user", hint=own.key_hint, model=own.model or services.llm.model_name,
                           verified_at=own.verified_at)
    if services.settings.regolo_api_key:  # developer fallback from .env
        return AiKeyStatus(configured=True, source="env", hint=mask(services.settings.regolo_api_key),
                           model=services.llm.model_name)
    return AiKeyStatus(configured=False, source="none")


@router.get("/ai-key", response_model=AiKeyStatus)
def ai_key_status(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> AiKeyStatus:
    return _ai_key_status(services, cu.id)


@router.put("/ai-key", response_model=AiKeyStatus)
async def save_ai_key(body: AiKeyIn, cu: CurrentUser = Depends(current_user),
                      services: Services = Depends(get_services)) -> AiKeyStatus:
    """Saved ONLY after Regolo.ai accepts it. Rejected or unverifiable keys are not stored."""
    key = body.api_key.strip()
    if any(c.isspace() for c in key):
        api_error(400, "errors.ai_key_spaces")
    try:
        models = await services.llm.list_models(key)
    except KeyRejected:
        api_error(400, "errors.ai_key_rejected")
    except Exception:
        api_error(503, "errors.ai_key_unverifiable")
    if not models:
        api_error(400, "errors.ai_key_no_models")
    await services.llm.resolve_model(key)
    services.ai_keys.set(cu.id, cu.org_id, key, services.llm.model_name)
    log.info("ai key verified and saved user=%s", cu.id)
    return _ai_key_status(services, cu.id)


@router.delete("/ai-key", response_model=AiKeyStatus)
def delete_ai_key(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> AiKeyStatus:
    services.ai_keys.delete(cu.id)
    return _ai_key_status(services, cu.id)
