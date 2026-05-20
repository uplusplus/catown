# -*- coding: utf-8 -*-
"""Shared recovery lease helpers for interrupted orchestration runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RecoveryLeaseClaimResult:
    task_run: Any | None
    reason: str
    status: str | None = None
    detail: str | None = None
    owner: str | None = None
    lease_expires_at: datetime | None = None


class RecoveryLeaseLostError(RuntimeError):
    """Raised when recovery loses its lease during execution."""

    def __init__(self, task_run_id: int | None):
        self.task_run_id = task_run_id
        super().__init__(f"Recovery lease lost for task run {task_run_id}.")


def claim_recovery_lease(
    db: Session,
    *,
    task_run_id: int,
    recoverable_run_kinds: set[str],
    owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> RecoveryLeaseClaimResult:
    from models.database import TaskRun

    current_time = now or datetime.now()
    lease_expires_at = current_time + timedelta(seconds=lease_seconds)
    updated = (
        db.query(TaskRun)
        .filter(
            TaskRun.id == task_run_id,
            TaskRun.status == "running",
            TaskRun.run_kind.in_(sorted(recoverable_run_kinds)),
            or_(
                TaskRun.recovery_owner.is_(None),
                TaskRun.recovery_lease_expires_at.is_(None),
                TaskRun.recovery_lease_expires_at < current_time,
            ),
        )
        .update(
            {
                TaskRun.recovery_owner: owner,
                TaskRun.recovery_claimed_at: current_time,
                TaskRun.recovery_lease_expires_at: lease_expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
    if updated:
        return RecoveryLeaseClaimResult(
            task_run=task_run,
            reason="claimed",
            status=(task_run.status if task_run is not None else None),
            owner=owner,
            lease_expires_at=lease_expires_at,
        )
    if task_run is None:
        return RecoveryLeaseClaimResult(
            task_run=None,
            reason="not_found",
            status=None,
            detail="Task run not found.",
        )
    run_kind = (task_run.run_kind or "").strip()
    status = (task_run.status or "").strip()
    if status != "running":
        return RecoveryLeaseClaimResult(
            task_run=task_run,
            reason="not_running",
            status=status or None,
            detail=f"Task run is already {status or 'not running'}.",
        )
    if run_kind not in recoverable_run_kinds:
        return RecoveryLeaseClaimResult(
            task_run=task_run,
            reason="not_recoverable",
            status=status or None,
            detail=f"Task run kind '{run_kind or 'unknown'}' is not recoverable.",
        )
    return RecoveryLeaseClaimResult(
        task_run=task_run,
        reason="leased",
        status=status or None,
        detail=_format_recovery_lease_detail(
            getattr(task_run, "recovery_owner", None),
            getattr(task_run, "recovery_lease_expires_at", None),
        ),
        owner=getattr(task_run, "recovery_owner", None),
        lease_expires_at=getattr(task_run, "recovery_lease_expires_at", None),
    )


def renew_recovery_lease(
    db: Session,
    *,
    task_run_id: int,
    owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> datetime | None:
    from models.database import TaskRun

    current_time = now or datetime.now()
    lease_expires_at = current_time + timedelta(seconds=lease_seconds)
    updated = (
        db.query(TaskRun)
        .filter(
            TaskRun.id == task_run_id,
            TaskRun.recovery_owner == owner,
        )
        .update(
            {
                TaskRun.recovery_claimed_at: current_time,
                TaskRun.recovery_lease_expires_at: lease_expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated:
        return lease_expires_at
    return None


def ensure_recovery_lease(
    db: Session,
    *,
    task_run_id: int,
    owner: str,
    lease_seconds: int,
) -> datetime:
    lease_expires_at = renew_recovery_lease(
        db,
        task_run_id=task_run_id,
        owner=owner,
        lease_seconds=lease_seconds,
    )
    if lease_expires_at is None:
        raise RecoveryLeaseLostError(task_run_id)
    return lease_expires_at


def _format_recovery_lease_detail(owner: Optional[str], lease_expires_at: Optional[datetime]) -> str:
    if owner and lease_expires_at:
        return f"Task run is already being recovered by {owner} until {lease_expires_at.isoformat()}."
    if owner:
        return f"Task run is already being recovered by {owner}."
    return "Task run is already being recovered by another Catown instance."
