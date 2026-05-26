# -*- coding: utf-8 -*-
"""Periodic watchdog sweep for stale running TaskRuns.

Finds TaskRuns stuck in ``running`` with no recent event activity and
transitions them to ``failed`` so that the UI does not show a perpetual
"agent working" spinner.

Usage
-----
Call ``sweep_stale_task_runs()`` from a cron job, heartbeat, or background
task at a regular interval (recommended: every 5–10 minutes).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models.database import TaskRun, TaskRunEvent
from models.enums import EventType
from services.task_run_lifecycle import terminalize_task_run


logger = logging.getLogger("catown.task_run_watchdog")

# TaskRuns with no event newer than this threshold are considered stale.
DEFAULT_STALE_THRESHOLD_MINUTES = 30


def sweep_stale_task_runs(
    db: Session,
    *,
    threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES,
    now: datetime | None = None,
) -> list[dict]:
    """Find and terminalize stale running TaskRuns.

    Returns a list of dicts describing the TaskRuns that were swept.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=threshold_minutes)

    stale_runs = (
        db.query(TaskRun)
        .filter(
            TaskRun.status == "running",
            TaskRun.updated_at < cutoff,
        )
        .all()
    )

    swept: list[dict] = []
    for task_run in stale_runs:
        # Double-check: is there a recent event that updated_at didn't reflect?
        latest_event = (
            db.query(TaskRunEvent)
            .filter(TaskRunEvent.task_run_id == task_run.id)
            .order_by(TaskRunEvent.event_index.desc())
            .first()
        )
        if latest_event and latest_event.created_at and latest_event.created_at > cutoff:
            # Has recent activity — skip.
            continue

        last_event_type = latest_event.event_type if latest_event else "none"
        last_event_at = latest_event.created_at.isoformat() if latest_event and latest_event.created_at else "none"
        idle_minutes = int((now - (latest_event.created_at if latest_event and latest_event.created_at else task_run.updated_at or task_run.created_at or now)).total_seconds() / 60)

        logger.warning(
            "[Watchdog] Sweeping stale task_run id=%s status=%s last_event=%s last_event_at=%s idle=%dmin",
            task_run.id,
            task_run.status,
            last_event_type,
            last_event_at,
            idle_minutes,
        )

        terminalize_task_run(
            db,
            task_run,
            status="failed",
            summary=f"Watchdog: no activity for {idle_minutes} minutes (last event: {last_event_type}).",
            event_type=EventType.TASK_RUN_INTERRUPTED,
            payload={
                "sweep_reason": "watchdog_stale",
                "idle_minutes": idle_minutes,
                "last_event_type": last_event_type,
                "last_event_at": last_event_at,
                "threshold_minutes": threshold_minutes,
            },
        )

        swept.append({
            "task_run_id": task_run.id,
            "chatroom_id": task_run.chatroom_id,
            "idle_minutes": idle_minutes,
            "last_event_type": last_event_type,
        })

    if swept:
        logger.info("[Watchdog] Swept %d stale task run(s).", len(swept))
    return swept
