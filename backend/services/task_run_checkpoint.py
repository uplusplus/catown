# -*- coding: utf-8 -*-
"""Periodic checkpoint snapshots for fast task-run recovery.

Instead of replaying every event from the beginning on recovery, the system
can start from the latest checkpoint and only replay events *after* it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session


# How many events between automatic checkpoint saves.
DEFAULT_CHECKPOINT_INTERVAL = 20


def save_checkpoint(
    db: Session,
    *,
    task_run_id: int,
    event_index: int,
    snapshot: dict[str, Any],
) -> Any:
    """Persist a checkpoint snapshot for *task_run_id* at *event_index*."""

    from models.database import TaskRunCheckpoint

    item = TaskRunCheckpoint(
        task_run_id=task_run_id,
        event_index=event_index,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def latest_checkpoint(db: Session, *, task_run_id: int) -> dict[str, Any] | None:
    """Return the most recent checkpoint snapshot for *task_run_id*, or ``None``."""

    from models.database import TaskRunCheckpoint

    row = (
        db.query(TaskRunCheckpoint)
        .filter(TaskRunCheckpoint.task_run_id == task_run_id)
        .order_by(TaskRunCheckpoint.event_index.desc())
        .first()
    )
    if row is None:
        return None
    try:
        return json.loads(row.snapshot_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def checkpoint_event_index(db: Session, *, task_run_id: int) -> int:
    """Return the event_index of the latest checkpoint, or 0 if none exists."""

    from models.database import TaskRunCheckpoint

    idx = (
        db.query(TaskRunCheckpoint.event_index)
        .filter(TaskRunCheckpoint.task_run_id == task_run_id)
        .order_by(TaskRunCheckpoint.event_index.desc())
        .limit(1)
        .scalar()
    )
    return int(idx or 0)


def should_checkpoint(
    db: Session,
    *,
    task_run_id: int,
    current_event_index: int,
    interval: int = DEFAULT_CHECKPOINT_INTERVAL,
) -> bool:
    """Return ``True`` when it is time to write a new checkpoint."""

    if interval <= 0:
        return False
    last_idx = checkpoint_event_index(db, task_run_id=task_run_id)
    return (current_event_index - last_idx) >= interval


def maybe_save_checkpoint(
    db: Session,
    *,
    task_run: Any,
    snapshot_builder: Any,
    interval: int = DEFAULT_CHECKPOINT_INTERVAL,
) -> bool:
    """Conditionally save a checkpoint if enough events have accumulated.

    *snapshot_builder* is a callable that returns the snapshot dict
    (e.g. ``build_task_run_checkpoint_snapshot``).
    """

    from models.database import TaskRunEvent
    from sqlalchemy import func

    task_run_id = getattr(task_run, "id", None)
    if task_run_id is None:
        return False

    max_idx = (
        db.query(func.max(TaskRunEvent.event_index))
        .filter(TaskRunEvent.task_run_id == task_run_id)
        .scalar()
        or 0
    )
    if not should_checkpoint(db, task_run_id=task_run_id, current_event_index=max_idx, interval=interval):
        return False

    snapshot = snapshot_builder(task_run)
    save_checkpoint(db, task_run_id=task_run_id, event_index=max_idx, snapshot=snapshot)
    return True


def delete_checkpoints(db: Session, *, task_run_id: int) -> int:
    """Delete all checkpoints for *task_run_id*. Returns the count deleted."""

    from models.database import TaskRunCheckpoint

    count = (
        db.query(TaskRunCheckpoint)
        .filter(TaskRunCheckpoint.task_run_id == task_run_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return count
