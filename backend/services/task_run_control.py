# -*- coding: utf-8 -*-
"""Task-run control primitives shared by executor loops."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models.database import TaskRun


class TaskRunCancelledError(RuntimeError):
    """Raised when executor logic observes a cancelled task run."""

    def __init__(self, task_run_id: int | None, context: str = ""):
        self.task_run_id = task_run_id
        self.context = str(context or "").strip()
        detail = f"Task run {task_run_id} was cancelled." if task_run_id is not None else "Task run was cancelled."
        if self.context:
            detail = f"{detail} Context: {self.context}."
        super().__init__(detail)


def task_run_status(db: Session, task_run: TaskRun | int | None) -> str | None:
    """Read the latest persisted task-run status."""

    task_run_id = _task_run_id(task_run)
    if task_run_id is None:
        return None
    status = db.query(TaskRun.status).filter(TaskRun.id == task_run_id).scalar()
    return str(status or "").strip().lower() or None


def raise_if_task_run_cancelled(
    db: Session,
    task_run: TaskRun | int | None,
    *,
    context: str = "",
) -> None:
    """Raise a shared executor cancellation error when the task run is cancelled."""

    if task_run_status(db, task_run) == "cancelled":
        raise TaskRunCancelledError(_task_run_id(task_run), context=context)


def _task_run_id(task_run: TaskRun | int | None) -> int | None:
    if isinstance(task_run, int):
        return task_run if task_run > 0 else None
    raw_id = getattr(task_run, "id", None)
    return int(raw_id) if raw_id else None
