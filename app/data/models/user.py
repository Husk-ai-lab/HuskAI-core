from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class Theme(StrEnum):
    light = "light"
    dark = "dark"


class Language(StrEnum):
    """Must mirror app.config.SUPPORTED_LANGUAGES."""

    en = "en"
    it = "it"


class PersonalityId(StrEnum):
    bilanciato = "bilanciato"
    ultra_prudente = "ultra_prudente"
    esplicativo = "esplicativo"
    conciso = "conciso"


class Role(StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


class User(OrgScoped):
    """Local user record. Names and emails stay on this computer."""

    id: str  # ZITADEL user id (sub)
    email: str | None = None
    display_name: str | None = None
    org_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime = Field(default_factory=utcnow)

    @property
    def primary_role(self) -> str:
        for r in (Role.owner, Role.admin):
            if r.value in self.roles:
                return r.value
        return Role.member.value

    @property
    def initials(self) -> str:
        base = self.display_name or self.email or "?"
        return base.strip()[:1].upper()


class UserSettings(HuskModel):
    theme: Theme = Theme.light
    language: Language = Language.en
    personality: PersonalityId = PersonalityId.bilanciato


class UserSettingsPatch(HuskModel):
    theme: Theme | None = None
    language: Language | None = None
    personality: PersonalityId | None = None


class Me(HuskModel):
    user: User
    settings: UserSettings
