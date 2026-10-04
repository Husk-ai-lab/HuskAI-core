"""Shared FastAPI dependencies: services, current user (login required), maintenance gate."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request, status

from app.core.services import Services
from app.data.models.user import User, UserSettings
from app.gateways.sqlite.repositories import SessionRepo, UserRepo
from app.i18n.errors import api_error

SESSION_COOKIE = "husk_session"


def get_services(request: Request) -> Services:
    return request.app.state.services


@dataclass
class CurrentUser:
    user: User
    settings: UserSettings
    session_id: str

    @property
    def org_id(self) -> str:
        return self.user.org_id

    @property
    def id(self) -> str:
        return self.user.id


def current_user(request: Request, services: Services = Depends(get_services)) -> CurrentUser:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        api_error(status.HTTP_401_UNAUTHORIZED, "errors.auth_required")
    user_id = SessionRepo(services.db).resolve(sid)
    if not user_id:
        api_error(status.HTTP_401_UNAUTHORIZED, "errors.session_expired")
    repo = UserRepo(services.db)
    user = repo.get(user_id)
    if not user:
        api_error(status.HTTP_401_UNAUTHORIZED, "errors.auth_required")
    return CurrentUser(user=user, settings=repo.get_settings(user_id), session_id=sid)


def not_in_maintenance(services: Services = Depends(get_services)) -> None:
    if services.maintenance.blocking:
        s = services.maintenance.status
        api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "errors.maintenance",
                  message={"en": s.message_en or s.message_it or "", "it": s.message_it or s.message_en or ""})


async def llm_ready(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> None:
    """The caller has a verified AI key and a model is selected."""
    if not services.llm.configured_for(cu.id):
        api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "errors.ai_key_missing")
    await services.llm.resolve_model(services.llm.key_for(cu.id))  # no-op once verified
    if not services.llm.model_name:
        api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "errors.ai_model_unreachable")


def decisions_ready(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> None:
    """Discovery also needs the decision model (the Venice part of the combined key)."""
    if not services.decisions.configured_for(cu.id):
        api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "errors.ai_key_venice_missing")
