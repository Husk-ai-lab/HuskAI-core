"""Public system status: maintenance (Neon Data API, anonymous), release notice, Studio, configuration flags.

No login required: the maintenance screen must show even before sign-in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.services import Services
from app.data.models.system import SystemStatus
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
        llm_configured=services.llm.configured,
        llm_model=services.llm.model_name,
        zitadel_configured=s.zitadel_configured,
        neon_configured=services.neon.enabled,
        maintenance=services.maintenance.status,
        release=services.maintenance.release,
        studio=services.studio.status,
        data_dir=str(services.paths.root),
    )


@router.get("/internal/studio-token", include_in_schema=False)
async def studio_token(services: Services = Depends(get_services), x_husk_launch_secret: str = Header("")) -> dict:
    """Token endpoint for Husk Studio (technical plan: STUDIO_TOKEN_URL). The launch-secret
    check happens in LocalGuard; here we mint a fresh access token for the signed-in user."""
    uid = services.db.kv_get("last_user_id")
    if not uid:
        raise HTTPException(404, "no signed-in user")
    token = services.tokens.get_access(uid)
    if not token:
        refresh = services.tokens.load_refresh(uid)
        if not refresh:
            raise HTTPException(401, "sign-in required")
        ts = await services.zitadel.refresh(refresh)
        services.tokens.save_refresh(uid, ts.refresh_token)
        services.tokens.set_access(uid, ts.access_token, ts.expires_at, ts.id_token or None)
        token = ts.access_token
    return {"access_token": token, "token_type": "Bearer"}
