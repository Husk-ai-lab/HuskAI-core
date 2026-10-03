"""Local scheduler: runs active automations on this computer while Husk is running.

Schedule triggers use cron (Europe/Rome); file triggers watch a folder for new files.
Paused automatically during a blocking maintenance window.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Awaitable, Callable
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.data.models.automation import Automation, AutomationStatus, RunTrigger, TriggerType
from app.helpers.logging import get_logger

log = get_logger("scheduler")
TZ = "Europe/Rome"

RunFn = Callable[[Automation, RunTrigger, dict], Awaitable[None]]


class LocalScheduler:
    def __init__(self, run_fn: RunFn, is_blocked: Callable[[], bool]):
        self.run_fn = run_fn
        self.is_blocked = is_blocked
        self.scheduler = AsyncIOScheduler(timezone=TZ)
        self._watchers: dict[str, asyncio.Task] = {}
        self._stop_events: dict[str, asyncio.Event] = {}

    def start(self, automations: list[Automation]) -> None:
        self.scheduler.start()
        for a in automations:
            self.sync(a)

    def shutdown(self) -> None:
        for ev in self._stop_events.values():
            ev.set()
        for t in self._watchers.values():
            t.cancel()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def sync(self, a: Automation) -> None:
        """Make the scheduler match the automation's status and trigger."""
        self.remove(a.id)
        if a.status != AutomationStatus.attiva or not a.graph.nodes:
            return
        trig = a.graph.trigger()
        if trig.trigger == TriggerType.schedule and trig.cron:
            try:
                self.scheduler.add_job(
                    self._fire, CronTrigger.from_crontab(trig.cron, timezone=TZ), args=[a, RunTrigger.schedule, {}],
                    id=a.id, replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=600,
                )
            except ValueError as exc:
                log.warning("invalid cron for %s: %s", a.id, exc)
        elif trig.trigger == TriggerType.file and trig.folder:
            stop = asyncio.Event()
            self._stop_events[a.id] = stop
            self._watchers[a.id] = asyncio.create_task(self._watch(a, Path(trig.folder).expanduser(), trig.pattern, stop))

    def remove(self, automation_id: str) -> None:
        if self.scheduler.running and self.scheduler.get_job(automation_id):
            self.scheduler.remove_job(automation_id)
        ev = self._stop_events.pop(automation_id, None)
        if ev:
            ev.set()
        t = self._watchers.pop(automation_id, None)
        if t:
            t.cancel()

    def next_run(self, automation_id: str) -> str | None:
        job = self.scheduler.get_job(automation_id) if self.scheduler.running else None
        return job.next_run_time.isoformat() if job and job.next_run_time else None

    async def _fire(self, a: Automation, trigger: RunTrigger, payload: dict) -> None:
        if self.is_blocked():
            log.info("maintenance: skipping scheduled run of %s", a.id)
            return
        await self.run_fn(a, trigger, payload)

    async def _watch(self, a: Automation, folder: Path, pattern: str | None, stop: asyncio.Event) -> None:
        from watchfiles import Change, awatch

        if not folder.exists():
            log.warning("watched folder missing for %s", a.id)
            return
        async for changes in awatch(folder, stop_event=stop, recursive=False):
            for change, path in changes:
                if change != Change.added:
                    continue
                if pattern and not fnmatch.fnmatch(Path(path).name, pattern):
                    continue
                await self._fire(a, RunTrigger.file, {"path": path, "name": Path(path).name})
