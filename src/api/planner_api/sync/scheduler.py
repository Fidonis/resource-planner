"""Cron schedule per integration (FR-INT-02)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner_api.errors import ErrorCode, ProblemError
from planner_api.models import IntegrationConfig
from planner_api.sync.engine import SyncEngine

log = logging.getLogger("planner_api.sync")
JOB_PREFIX = "integration:"


def validate_schedule(expression: str) -> None:
    """Raises ``ValueError`` with a message for the administrator when it is no cron line."""
    try:
        CronTrigger.from_crontab(expression.strip(), timezone=UTC)
    except ValueError as exc:
        raise ValueError(f"is not a cron line of five fields ({exc})") from exc


class SyncScheduler:
    def __init__(self, engine: SyncEngine, factory: async_sessionmaker[AsyncSession]) -> None:
        self.engine = engine
        self.factory = factory
        self._scheduler: AsyncIOScheduler | None = None

    async def start(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._scheduler.start()
        await self.refresh()

    async def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    async def refresh(self) -> None:
        """Make the jobs match the enabled integrations that have a schedule."""
        scheduler = self._scheduler
        if scheduler is None:
            return
        async with self.factory() as session:
            rows = (
                await session.execute(
                    select(IntegrationConfig.id, IntegrationConfig.schedule).where(
                        IntegrationConfig.enabled.is_(True), IntegrationConfig.schedule.is_not(None)
                    )
                )
            ).all()
        wanted = {f"{JOB_PREFIX}{i}": (i, s) for i, s in rows if s}
        for job in scheduler.get_jobs():
            if job.id.startswith(JOB_PREFIX) and job.id not in wanted:
                job.remove()
        for job_id, (integration_id, expression) in wanted.items():
            try:
                trigger = CronTrigger.from_crontab(expression.strip(), timezone=UTC)
            except ValueError:
                log.warning("integration %s has an invalid schedule", integration_id)
                continue
            scheduler.add_job(
                self._run,
                trigger,
                args=[integration_id],
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )

    def job_ids(self) -> list[str]:
        return sorted(j.id for j in self._scheduler.get_jobs()) if self._scheduler else []

    async def _run(self, integration_id: uuid.UUID) -> None:
        try:
            await self.engine.run(integration_id, triggered_by="schedule")
        except ProblemError as exc:
            level = logging.INFO if exc.code == ErrorCode.SYNC_RUNNING else logging.WARNING
            log.log(level, "scheduled sync of %s skipped: %s", integration_id, exc.detail)
        except Exception:
            log.exception("scheduled sync of %s failed", integration_id)
