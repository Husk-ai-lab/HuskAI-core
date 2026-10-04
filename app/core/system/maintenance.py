"""Maintenance switch (Neon ops.maintenance via the anonymous Data API, anonymous token).

Polled at startup and every HUSK_MAINTENANCE_POLL_S seconds. A blocking window pauses AI
work and automation runs and shows a full-screen notice; a non-blocking one shows a banner.
Every upcoming window is returned too, so the UI can warn at startup (modal, once per window)
and count down. A desktop notification is sent once, NOTIFY_LEAD_MIN minutes before a
blocking window. The last known status is cached in SQLite so the app still opens offline.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx

from app.data.models.system import MaintenanceStatus, MaintenanceWindow, ReleaseNotice
from app.data.neon.models import Channel
from app.gateways.neon import NeonGateway
from app.gateways.sqlite import Database
from app.helpers.logging import get_logger
from app.helpers.time import utcnow
from app.i18n import localized_value, t

log = get_logger("maintenance")
CACHE_KEY = "maintenance_status"
RELEASE_KEY = "release_status"
NOTIFIED_KEY = "maintenance_notified"
NOTIFY_LEAD_MIN = 10


def _version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for part in v.split("-")[0].split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


class MaintenanceMonitor:
    def __init__(self, neon: NeonGateway, db: Database, version: str, poll_s: int = 60):
        self.neon, self.db, self.version, self.poll_s = neon, db, version, poll_s
        cached = db.kv_get(CACHE_KEY)
        self.status = MaintenanceStatus.model_validate(cached) if cached else MaintenanceStatus()
        if cached:
            self.status.source = "cache"
        rel = db.kv_get(RELEASE_KEY)
        self.release = ReleaseNotice.model_validate(rel) if rel else ReleaseNotice(current=version)
        self.release.current = version
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None  # warn when the failure changes, not every poll

    @property
    def blocking(self) -> bool:
        s = self.status
        if not (s.active and s.blocking):
            return False
        now = utcnow()
        return (s.starts_at is None or s.starts_at <= now) and (s.ends_at is None or now < s.ends_at)

    async def check(self) -> MaintenanceStatus:
        if not self.neon.enabled:
            return self.status
        try:
            windows = await self.neon.active_maintenance()
        except Exception as exc:
            name = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                name = f"{name} {exc.response.status_code}"
            if name != self._last_error:
                log.warning("maintenance check failed (%s); using cached status", name)
                self._last_error = name
            self.status.source = "cache"
            return self.status
        if self._last_error:
            log.info("maintenance check recovered")
            self._last_error = None
        now = utcnow()
        current = [w for w in windows if w.starts_at <= now < w.ends_at]
        upcoming = sorted((w for w in windows if w.starts_at > now), key=lambda w: w.starts_at)
        upcoming_views = [
            MaintenanceWindow(id=str(w.id), message_it=w.message_it, message_en=w.message_en,
                              starts_at=w.starts_at, ends_at=w.ends_at, blocking=w.blocking)
            for w in upcoming
        ]
        pick = sorted(current, key=lambda w: not w.blocking)[0] if current else (upcoming[0] if upcoming else None)
        if pick is None:
            self.status = MaintenanceStatus(source="neon", checked_at=now, upcoming=upcoming_views)
        else:
            self.status = MaintenanceStatus(
                active=pick in current,
                blocking=pick.blocking,
                message_it=pick.message_it,
                message_en=pick.message_en,
                starts_at=pick.starts_at,
                ends_at=pick.ends_at,
                source="neon",
                checked_at=now,
                upcoming=upcoming_views,
            )
        self.db.kv_set(CACHE_KEY, self.status.model_dump(mode="json"))
        await self._check_release()
        return self.status

    def notify_due(self, notify=None, lang: str | None = None) -> list[str]:  # type: ignore[no-untyped-def]
        """Desktop notification, once per window, shortly before a blocking window starts."""
        if notify is None:
            from app.core.automations.actions import notify_desktop as notify
        if lang is None:
            lang = self._last_user_language()
        now = utcnow()
        done: list[str] = self.db.kv_get(NOTIFIED_KEY, [])
        sent = []
        for w in self.status.upcoming:
            if w.blocking and w.id not in done and w.starts_at - now <= timedelta(minutes=NOTIFY_LEAD_MIN):
                minutes = max(1, int((w.starts_at - now).total_seconds() // 60))
                notify(t("notifications.maintenance_soon", lang, count=minutes),
                       localized_value({"en": w.message_en, "it": w.message_it}, lang))
                done.append(w.id)
                sent.append(w.id)
        if sent:
            self.db.kv_set(NOTIFIED_KEY, done[-50:])
        return sent

    def _last_user_language(self) -> str:
        from app.gateways.sqlite.repositories import UserRepo

        uid = self.db.kv_get("last_user_id")
        return UserRepo(self.db).get_settings(uid).language.value if uid else "en"

    async def _check_release(self) -> None:
        try:
            rels = await self.neon.releases("husk")
        except Exception:
            return
        stable = next((r for r in rels if r.channel == Channel.stable), None)
        if not stable:
            return
        cur = _version_tuple(self.version)
        self.release = ReleaseNotice(
            current=self.version,
            latest=stable.version,
            min_version=stable.min_version,
            update_available=_version_tuple(stable.version) > cur,
            update_required=_version_tuple(stable.min_version) > cur,
        )
        self.db.kv_set(RELEASE_KEY, self.release.model_dump(mode="json"))

    def start(self) -> None:
        if self._task is None and self.neon.enabled:
            self._task = asyncio.create_task(self._loop(), name="maintenance-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            await self.check()
            try:
                self.notify_due()
            except Exception as exc:  # a notification must never stop the poller
                log.warning("maintenance notification failed: %s", type(exc).__name__)
            await asyncio.sleep(self.poll_s)
