"""Public system status: maintenance (Neon Data API, anonymous), release notice, Studio, configuration flags.

No login required: the maintenance screen must show even before sign-in.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app import config
from app.core.services import Services
from app.data.models.system import ShellStatus, SystemStatus
from app.endpoints.deps import get_services

router = APIRouter(tags=["system"])


@router.get("/health")
def health(services: Services = Depends(get_services)) -> dict:
    return {"status": "ok", "version": services.version}


@router.get("/api/v1/system/status", response_model=SystemStatus)
def system_status(services: Services = Depends(get_services)) -> SystemStatus:
    s = services.settings
    return SystemStatus(
        version=services.version,
        llm_model=services.llm.model_name,
        login_configured=s.clerk_configured,
        neon_configured=services.neon.enabled,
        maintenance=services.maintenance.status,
        release=services.maintenance.release,
        studio=services.studio.status,
        shell=ShellStatus(attached=services.shell.attached),
        data_dir=str(services.paths.root),
    )


@router.get("/internal/studio-token", include_in_schema=False)
async def studio_token(services: Services = Depends(get_services), x_husk_launch_secret: str = Header("")) -> dict:
    """Token endpoint for Husk Studio (technical plan: STUDIO_TOKEN_URL). The launch-secret
    check happens in LocalGuard; here we mint a fresh access token for the signed-in user."""
    uid = services.db.kv_get("last_user_id")
    if not uid:
        raise HTTPException(404, "no signed-in user")
    try:
        token = await services.access.get(uid)
    except Exception:
        raise HTTPException(401, "sign-in required") from None
    return {"access_token": token, "token_type": "Bearer"}


# --- the desktop shell --------------------------------------------------------------------------
class OpenManagerIn(BaseModel):
    tab: Literal["components", "updates", "logs"] = "components"
    component: Literal["husk", "studio"] | None = None


class OpenManagerOut(BaseModel):
    queued: bool  # False: no desktop shell is attached, the caller should open `fallback_url` itself
    id: int | None = None
    shell_attached: bool
    fallback_url: str


@router.post("/api/v1/system/shell/open-manager", response_model=OpenManagerOut)
async def open_manager(body: OpenManagerIn, services: Services = Depends(get_services)) -> OpenManagerOut:
    """"Install Husk Studio" and friends: ask the desktop shell to show its installation manager. Carries no data;
    the origin check of LocalGuard applies like for every POST."""
    req = await services.shell.post("open_manager", body.tab, body.component)
    return OpenManagerOut(queued=req is not None, id=req.id if req else None,
                          shell_attached=services.shell.attached, fallback_url=config.HUSK_STUDIO_INSTALL_URL)


@router.get("/internal/shell/requests", include_in_schema=False)
async def shell_requests(after: int = Query(0, ge=0), wait: float = Query(0, ge=0, le=25),
                         services: Services = Depends(get_services)) -> dict:
    """The shell's long poll. The launch-secret check happens in LocalGuard. `after` is the last id the shell has
    seen: it is the acknowledgement. Answers at once when something is newer, else after `wait` seconds."""
    found = await services.shell.wait(after, wait)
    return {"latest": services.shell.latest, "requests": [r.public() for r in found]}
