"""Connectors (Telegram, Gmail): status, set up (verified first), test message, remove.

Credentials arrive in request bodies only. Responses carry the status and a hint the user recognises (the bot's
@name, the Gmail address) and never a token, a password or a chat id."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.connectors.service import ConnectorProblem
from app.core.services import Services
from app.core.suggestions.engine import SuggestionEngine
from app.data.models.connector import ChatDetected, ChatDetectIn, Connector, ConnectorIn, ConnectorKind
from app.endpoints.deps import CurrentUser, current_user, get_services
from app.i18n.errors import from_localized

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


def _view(c: Connector) -> dict:
    return c.model_dump(mode="json", exclude={"org_id", "updated_at"})


def _ready(services: Services, cu: CurrentUser, kind: ConnectorKind) -> None:
    SuggestionEngine(services, cu.org_id, cu.id).connector_ready(kind.value)  # closes "set it up" suggestions


@router.get("")
def list_connectors(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> list[dict]:
    return [_view(c) for c in services.connectors.status(cu.org_id)]


@router.post("/telegram/detect-chat", response_model=ChatDetected)
async def detect_chat(body: ChatDetectIn, cu: CurrentUser = Depends(current_user),
                      services: Services = Depends(get_services)) -> ChatDetected:
    """After the user pressed Start in their new bot: which chat wrote to it."""
    try:
        found = await services.connectors.detect_chat(body.token)
    except ConnectorProblem as exc:
        from_localized(exc)
    return ChatDetected(chat_id=str(found["chat_id"]), label=found.get("label"))


@router.put("/{kind}")
async def configure(kind: ConnectorKind, body: ConnectorIn, cu: CurrentUser = Depends(current_user),
                    services: Services = Depends(get_services)) -> dict:
    try:
        c = await services.connectors.configure(cu.org_id, kind, body)
    except ConnectorProblem as exc:
        from_localized(exc)
    _ready(services, cu, kind)
    return _view(c)


@router.post("/{kind}/test")
async def test_connector(kind: ConnectorKind, cu: CurrentUser = Depends(current_user),
                         services: Services = Depends(get_services)) -> dict:
    try:
        c = await services.connectors.test(cu.org_id, kind, cu.settings.language.value)
    except ConnectorProblem as exc:
        from_localized(exc)
    _ready(services, cu, kind)
    return _view(c)


@router.delete("/{kind}")
def remove(kind: ConnectorKind, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> dict:
    services.connectors.remove(cu.org_id, kind)
    return _view(services.connectors.get(cu.org_id, kind))

