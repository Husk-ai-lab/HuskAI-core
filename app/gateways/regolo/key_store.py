"""Per-user AI keys, entered after login as ONE string (simplified BYOK for the MVP):
the Regolo.ai key followed by the Venice AI key (`…VENICE_INFERENCE_KEY_…`, for the Jev decision model).

Husk AI gives each customer a key; the user pastes it once. It is saved in the user's local
data (`user_ai_keys` in husk.db) only after Regolo.ai has accepted it. It is never returned by
the API, never logged, and stripped from backup exports.

Temporary: when husk-gateway exists the app stops holding any Regolo key (rule 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app import config
from app.gateways.sqlite import Database
from app.helpers.time import iso, parse_iso


def mask(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) >= 8 else "••••"


_COMBINED = re.compile(rf"^(?P<regolo>.+?)(?P<venice>{re.escape(config.VENICE_KEY_PREFIX)}[A-Za-z0-9_\-]+)$")


def split_keys(raw: str) -> tuple[str, str | None]:
    """`<regolo><VENICE_INFERENCE_KEY_…>` → (regolo, venice). No Venice part → (raw, None)."""
    text = "".join(raw.split())  # pasted keys may wrap or carry spaces
    m = _COMBINED.match(text)
    if m:
        return m.group("regolo"), m.group("venice")
    if text.startswith(config.VENICE_KEY_PREFIX):
        return "", text
    return text, None


@dataclass
class StoredKey:
    user_id: str
    org_id: str
    api_key: str
    key_hint: str
    model: str | None
    verified_at: str
    decision_key: str | None = None
    decision_hint: str | None = None


class UserAiKeyStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, user_id: str | None) -> StoredKey | None:
        if not user_id:
            return None
        with self.db.read() as c:
            r = c.execute(
                "SELECT user_id, org_id, api_key, key_hint, model, verified_at, decision_key, decision_hint "
                "FROM user_ai_keys WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return StoredKey(**dict(r)) if r else None

    def set(self, user_id: str, org_id: str, api_key: str, model: str | None,
            decision_key: str | None = None) -> StoredKey:
        now = iso()
        dhint = mask(decision_key) if decision_key else None
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO user_ai_keys(user_id, org_id, api_key, key_hint, model, verified_at, updated_at, "
                "decision_key, decision_hint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "org_id = excluded.org_id, api_key = excluded.api_key, key_hint = excluded.key_hint, "
                "model = excluded.model, verified_at = excluded.verified_at, updated_at = excluded.updated_at, "
                "decision_key = excluded.decision_key, decision_hint = excluded.decision_hint",
                (user_id, org_id, api_key, mask(api_key), model, now, now, decision_key, dhint),
            )
        return StoredKey(user_id, org_id, api_key, mask(api_key), model, now, decision_key, dhint)

    def delete(self, user_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM user_ai_keys WHERE user_id = ?", (user_id,))

    @staticmethod
    def verified_at(k: StoredKey) -> object:
        return parse_iso(k.verified_at)
