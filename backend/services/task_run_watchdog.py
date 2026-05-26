# -*- coding: utf-8 -*-
"""Periodic watchdog sweep for stale running TaskRuns and orphaned approvals.

Finds TaskRuns stuck in ``running`` with no recent event activity and
transitions them to ``failed``.  Also expires stale approvals and fails
their blocked TaskRuns.

Usage
-----
Call ``run_watchdog_sweep()`` from a cron job, heartbeat, or background
task at a regular interval (recommended: every 5–10 minutes).

Or use the individual functions:
- ``sweep_stale_task_runs()`` — stale running TaskRuns
- ``sweep_expired_approvals()`` — expired pending approvals
- ``sweep_stale_paused_task_runs()`` — paused TaskRuns with expired blockers
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models.database import TaskRun, TaskRunEvent, ApprovalQueueItem
from models.enums import EventType
from services.task_run_lifecycle import terminalize_task_run


logger = logging.getLogger("catown.task_run_watchdog")

# TaskRuns with no event newer than this threshold are considered stale.
DEFAULT_STALE_THRESHOLD_MINUTES = 30

# Paused TaskRuns whose approval expired more than this long ago are swept.
DEFAULT_PAUSED_STALE_THRESHOLD_MINUTES = 60


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
        logger.info("[Watchdog] Swept %d stale running task run(s).", len(swept))
    return swept


def sweep_stale_paused_task_runs(
    db: Session,
    *,
    threshold_minutes: int = DEFAULT_PAUSED_STALE_THRESHOLD_MINUTES,
    now: datetime | None = None,
) -> list[dict]:
    """Find paused TaskRuns whose approval expired a long time ago and fail them.

    This catches cases where ``expire_stale_approvals()`` didn't properly
    terminalize the TaskRun (e.g. due to a race or crash).

    Returns a list of dicts describing the TaskRuns that were swept.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=threshold_minutes)

    stale_paused = (
        db.query(TaskRun)
        .filter(
            TaskRun.status == "paused",
            TaskRun.updated_at < cutoff,
        )
        .all()
    )

    swept: list[dict] = []
    for task_run in stale_paused:
        # Check if the blocking approval is still pending (shouldn't be after TTL).
        blocker_id = getattr(task_run, "blocked_by_queue_item_id", None)
        blocker_resolved = True
        if blocker_id:
            blocker = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.id == blocker_id).first()
            if blocker and blocker.status == "pending":
                blocker_resolved = False  # Still pending — don't sweep yet.

        if not blocker_resolved:
            continue

        idle_minutes = int((now - (task_run.updated_at or task_run.created_at or now)).total_seconds() / 60)

        logger.warning(
            "[Watchdog] Sweeping stale paused task_run id=%s blocker_id=%s idle=%dmin",
            task_run.id,
            blocker_id,
            idle_minutes,
        )

        terminalize_task_run(
            db,
            task_run,
            status="failed",
            summary=f"Watchdog: paused with resolved/expired approval, no activity for {idle_minutes} minutes.",
            event_type=EventType.TASK_RUN_INTERRUPTED,
            payload={
                "sweep_reason": "watchdog_stale_paused",
                "idle_minutes": idle_minutes,
                "blocked_by_queue_item_id": blocker_id,
                "threshold_minutes": threshold_minutes,
            },
        )

        swept.append({
            "task_run_id": task_run.id,
            "chatroom_id": task_run.chatroom_id,
            "idle_minutes": idle_minutes,
            "blocked_by_queue_item_id": blocker_id,
        })

    if swept:
        logger.info("[Watchdog] Swept %d stale paused task run(s).", len(swept))
    return swept


def run_watchdog_sweep(
    db: Session,
    *,
    stale_running_threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES,
    stale_paused_threshold_minutes: int = DEFAULT_PAUSED_STALE_THRESHOLD_MINUTES,
    now: datetime | None = None,
) -> dict:
    """Run all watchdog sweeps and return a summary.

    Call this from a cron job or heartbeat.  Returns::

        {
            "stale_running_swept": [...],
            "stale_paused_swept": [...],
            "total_swept": int,
        }
    """
    now = now or datetime.now()

    stale_running = sweep_stale_task_runs(
        db, threshold_minutes=stale_running_threshold_minutes, now=now,
    )
    stale_paused = sweep_stale_paused_task_runs(
        db, threshold_minutes=stale_paused_threshold_minutes, now=now,
    )

    total = len(stale_running) + len(stale_paused)
    if total:
        logger.info("[Watchdog] Total swept: %d (running=%d, paused=%d)", total, len(stale_running), len(stale_paused))

    return {
        "stale_running_swept": stale_running,
        "stale_paused_swept": stale_paused,
        "total_swept": total,
    }
