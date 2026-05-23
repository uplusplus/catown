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

    # SELECT … FOR UPDATE — locks the row so two processes cannot both
    # claim the same lease in a race window.
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .with_for_update(nowait=False)
        .first()
    )
    if task_run is None:
        return RecoveryLeaseClaimResult(
            task_run=None,
            reason="not_found",
            status=None,
            detail="Task run not found.",
        )

    status = (task_run.status or "").strip()
    run_kind = (task_run.run_kind or "").strip()

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

    # Check if lease is already held and still valid
    recovery_owner = getattr(task_run, "recovery_owner", None)
    recovery_lease_expires_at = getattr(task_run, "recovery_lease_expires_at", None)
    lease_held = (
        recovery_owner is not None
        and recovery_lease_expires_at is not None
        and recovery_lease_expires_at >= current_time
    )
    if lease_held and recovery_owner != owner:
        return RecoveryLeaseClaimResult(
            task_run=task_run,
            reason="leased",
            status=status or None,
            detail=_format_recovery_lease_detail(recovery_owner, recovery_lease_expires_at),
            owner=recovery_owner,
            lease_expires_at=recovery_lease_expires_at,
        )

    # Claim or re-claim the lease
    task_run.recovery_owner = owner
    task_run.recovery_claimed_at = current_time
    task_run.recovery_lease_expires_at = lease_expires_at
    db.add(task_run)
    db.commit()
    db.refresh(task_run)

    return RecoveryLeaseClaimResult(
        task_run=task_run,
        reason="claimed",
        status=status,
        owner=owner,
        lease_expires_at=lease_expires_at,
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

    # SELECT … FOR UPDATE to prevent concurrent renewal races
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .with_for_update(nowait=False)
        .first()
    )
    if task_run is None:
        return None

    if (task_run.recovery_owner or "") != owner:
        return None

    task_run.recovery_claimed_at = current_time
    task_run.recovery_lease_expires_at = lease_expires_at
    db.add(task_run)
    db.commit()
    return lease_expires_at


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
