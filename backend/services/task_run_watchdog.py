# -*- coding: utf-8 -*-
"""Periodic watchdog sweep for stale running TaskRuns and orphaned approvals.

ADR-030: Uses event-sourced state derivation. The watchdog checks whether
the last event in a TaskRun's stream is terminal; if not and the TaskRun
has been idle too long, it emits a ``task_run_interrupted`` event which
automatically derives the status to "failed".

Usage
-----
Call ``run_watchdog_sweep()`` from a cron job, heartbeat, or background
task at a regular interval (recommended: every 5–10 minutes).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models.database import TaskRun, TaskRunEvent, ApprovalQueueItem
from models.enums import EventType
from services.delegated_task_guard import has_incomplete_delegated_child_runs
from services.task_run_state import derive_task_run_status, is_terminal_status


logger = logging.getLogger("catown.task_run_watchdog")

DEFAULT_STALE_THRESHOLD_MINUTES = 30
DEFAULT_PAUSED_STALE_THRESHOLD_MINUTES = 60


def _latest_event(db: Session, task_run_id: int) -> TaskRunEvent | None:
    return (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.task_run_id == task_run_id)
        .order_by(TaskRunEvent.event_index.desc())
        .first()
    )


def _is_stale(db: Session, task_run: TaskRun, cutoff: datetime) -> bool:
    """Check if a TaskRun is stale (non-terminal and no recent activity)."""
    latest = _latest_event(db, task_run.id)
    if latest and latest.created_at and latest.created_at > cutoff:
        return False  # Has recent activity.
    # Check if already terminal via derivation.
    if latest:
        all_events = (
            db.query(TaskRunEvent)
            .filter(TaskRunEvent.task_run_id == task_run.id)
            .order_by(TaskRunEvent.event_index.asc())
            .all()
        )
        derived = derive_task_run_status(all_events)
        if is_terminal_status(derived):
            return False  # Already terminal.
    return True


def sweep_stale_task_runs(
    db: Session,
    *,
    threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES,
    now: datetime | None = None,
) -> list[dict]:
    """Find stale running TaskRuns and emit interrupted events."""
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
        if not _is_stale(db, task_run, cutoff):
            continue
        if has_incomplete_delegated_child_runs(db, task_run):
            continue

        latest = _latest_event(db, task_run.id)
        last_event_type = latest.event_type if latest else "none"
        last_event_at = latest.created_at.isoformat() if latest and latest.created_at else "none"
        idle_minutes = int((now - (latest.created_at if latest and latest.created_at else task_run.updated_at or task_run.created_at or now)).total_seconds() / 60)

        logger.warning(
            "[Watchdog] Sweeping stale task_run id=%s last_event=%s idle=%dmin",
            task_run.id, last_event_type, idle_minutes,
        )

        # ADR-030: Emit event — status auto-derives to "failed".
        from services.run_ledger import append_task_event
        append_task_event(
            db, task_run,
            EventType.TASK_RUN_INTERRUPTED,
            summary=f"Watchdog: no activity for {idle_minutes} minutes (last event: {last_event_type}).",
            payload={
                "sweep_reason": "watchdog_stale",
                "idle_minutes": idle_minutes,
                "last_event_type": last_event_type,
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
    """Find paused TaskRuns whose approval expired long ago and fail them."""
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
        blocker_id = getattr(task_run, "blocked_by_queue_item_id", None)
        if blocker_id:
            blocker = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.id == blocker_id).first()
            if blocker and blocker.status == "pending":
                continue  # Still pending — don't sweep.

        idle_minutes = int((now - (task_run.updated_at or task_run.created_at or now)).total_seconds() / 60)

        logger.warning(
            "[Watchdog] Sweeping stale paused task_run id=%s blocker_id=%s idle=%dmin",
            task_run.id, blocker_id, idle_minutes,
        )

        from services.run_ledger import append_task_event
        append_task_event(
            db, task_run,
            EventType.TASK_RUN_INTERRUPTED,
            summary=f"Watchdog: paused with resolved/expired approval, no activity for {idle_minutes} minutes.",
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
    """Run all watchdog sweeps and return a summary."""
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
