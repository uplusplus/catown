# -*- coding: utf-8 -*-
"""Durable inbox helpers for orchestration handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models.database import OrchestrationHandoffDelivery, TaskRun


@dataclass(frozen=True)
class ClaimedOrchestrationHandoffs:
    messages: list[dict[str, Any]]
    delivery_ids: list[int]
    lease_owner: str | None
    durable_present: bool = False


def create_orchestration_handoff_delivery(
    db: Session,
    *,
    task_run: TaskRun | None,
    from_agent: str,
    to_agent: str,
    from_step_id: str,
    to_step_id: str,
    dispatch_kind: str | None,
    attached_to_step_id: str | None,
    content: str,
) -> OrchestrationHandoffDelivery | None:
    """Create a durable handoff delivery for one target scheduler step."""

    if task_run is None or getattr(task_run, "id", None) is None:
        return None

    delivery = OrchestrationHandoffDelivery(
        task_run_id=task_run.id,
        from_agent=str(from_agent or "").strip() or "agent",
        to_agent=str(to_agent or "").strip() or "agent",
        from_step_id=str(from_step_id or "").strip() or "step",
        to_step_id=str(to_step_id or "").strip() or "step",
        dispatch_kind=(str(dispatch_kind or "").strip() or None),
        attached_to_step_id=(str(attached_to_step_id or "").strip() or None),
        content=str(content or "").strip(),
        status="pending",
    )
    db.add(delivery)
    return delivery


def claim_orchestration_handoffs_for_step(
    db: Session,
    *,
    task_run_id: int | None,
    step_id: str,
    agent_name: str,
    lease_owner: str,
    lease_seconds: int = 300,
) -> ClaimedOrchestrationHandoffs:
    """Claim durable handoffs for one scheduler step under a short lease."""

    resolved_task_run_id = int(task_run_id or 0)
    resolved_step_id = str(step_id or "").strip()
    resolved_agent_name = str(agent_name or "").strip()
    owner = str(lease_owner or "").strip()
    if not resolved_task_run_id or not resolved_step_id or not resolved_agent_name or not owner:
        return ClaimedOrchestrationHandoffs(messages=[], delivery_ids=[], lease_owner=None, durable_present=False)

    base_query = db.query(OrchestrationHandoffDelivery).filter(
        OrchestrationHandoffDelivery.task_run_id == resolved_task_run_id,
        OrchestrationHandoffDelivery.to_step_id == resolved_step_id,
        OrchestrationHandoffDelivery.to_agent == resolved_agent_name,
    )
    durable_present = base_query.first() is not None
    if not durable_present:
        return ClaimedOrchestrationHandoffs(messages=[], delivery_ids=[], lease_owner=None, durable_present=False)

    now = datetime.now()
    lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
    deliveries = (
        base_query
        .filter(
            or_(
                OrchestrationHandoffDelivery.status == "pending",
                (
                    (OrchestrationHandoffDelivery.status == "inflight")
                    & (OrchestrationHandoffDelivery.lease_expires_at.is_not(None))
                    & (OrchestrationHandoffDelivery.lease_expires_at <= now)
                ),
            )
        )
        .order_by(OrchestrationHandoffDelivery.created_at.asc(), OrchestrationHandoffDelivery.id.asc())
        .all()
    )
    if not deliveries:
        return ClaimedOrchestrationHandoffs(messages=[], delivery_ids=[], lease_owner=owner, durable_present=True)

    messages: list[dict[str, Any]] = []
    delivery_ids: list[int] = []
    for delivery in deliveries:
        delivery.status = "inflight"
        delivery.leased_at = now
        delivery.lease_owner = owner
        delivery.lease_expires_at = lease_expires_at
        delivery.attempt_count = int(delivery.attempt_count or 0) + 1
        messages.append(serialize_orchestration_handoff_delivery(delivery))
        delivery_ids.append(delivery.id)
    db.commit()
    return ClaimedOrchestrationHandoffs(
        messages=messages,
        delivery_ids=delivery_ids,
        lease_owner=owner,
        durable_present=True,
    )


def mark_orchestration_handoffs_consumed(
    db: Session,
    *,
    delivery_ids: Iterable[int],
    lease_owner: str | None = None,
) -> int:
    """Ack claimed durable handoffs after successful step completion."""

    rows = _deliveries_by_ids(db, delivery_ids)
    if not rows:
        return 0
    now = datetime.now()
    updated = 0
    for delivery in rows:
        if lease_owner is not None and delivery.lease_owner != lease_owner:
            continue
        if delivery.status == "consumed":
            continue
        delivery.status = "consumed"
        delivery.consumed_at = now
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.last_error = None
        updated += 1
    if updated:
        db.commit()
    return updated


def mark_orchestration_handoffs_failed(
    db: Session,
    *,
    delivery_ids: Iterable[int],
    error: str,
    retry: bool = True,
    lease_owner: str | None = None,
) -> int:
    """Release or dead-letter claimed durable handoffs after a step failure."""

    rows = _deliveries_by_ids(db, delivery_ids)
    if not rows:
        return 0
    now = datetime.now()
    updated = 0
    for delivery in rows:
        if lease_owner is not None and delivery.lease_owner != lease_owner:
            continue
        if delivery.status == "consumed":
            continue
        delivery.status = "pending" if retry else "dead_letter"
        delivery.last_error = str(error or "").strip() or None
        delivery.failed_at = None if retry else now
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        updated += 1
    if updated:
        db.commit()
    return updated


def has_orchestration_handoffs_for_task_run(db: Session, *, task_run_id: int | None) -> bool:
    resolved_task_run_id = int(task_run_id or 0)
    if not resolved_task_run_id:
        return False
    return (
        db.query(OrchestrationHandoffDelivery.id)
        .filter(OrchestrationHandoffDelivery.task_run_id == resolved_task_run_id)
        .first()
        is not None
    )


def serialize_orchestration_handoff_delivery(delivery: OrchestrationHandoffDelivery) -> Dict[str, Any]:
    return {
        "delivery_id": delivery.id,
        "task_run_id": delivery.task_run_id,
        "from_agent": delivery.from_agent,
        "to_agent": delivery.to_agent,
        "from_step_id": delivery.from_step_id,
        "to_step_id": delivery.to_step_id,
        "dispatch_kind": delivery.dispatch_kind,
        "attached_to_step_id": delivery.attached_to_step_id,
        "content": delivery.content,
        "message_type": "handoff",
        "delivery_status": delivery.status,
        "delivery_attempt_count": int(delivery.attempt_count or 0),
        "delivery_lease_owner": delivery.lease_owner,
        "delivery_lease_expires_at": (
            delivery.lease_expires_at.isoformat() if delivery.lease_expires_at else None
        ),
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
    }


def summarize_orchestration_handoff_inbox(task_run: TaskRun) -> Dict[str, Any]:
    deliveries = list(getattr(task_run, "orchestration_handoff_deliveries", []) or [])
    status_counts: dict[str, int] = {}
    step_counts: dict[str, dict[str, int]] = {}
    oldest_pending_at: datetime | None = None
    next_lease_expiry_at: datetime | None = None

    for delivery in deliveries:
        status = str(delivery.status or "unknown").strip() or "unknown"
        step_id = str(delivery.to_step_id or "").strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        by_status = step_counts.setdefault(step_id, {})
        by_status[status] = by_status.get(status, 0) + 1
        if status == "pending" and delivery.created_at is not None:
            if oldest_pending_at is None or delivery.created_at < oldest_pending_at:
                oldest_pending_at = delivery.created_at
        if status == "inflight" and delivery.lease_expires_at is not None:
            if next_lease_expiry_at is None or delivery.lease_expires_at < next_lease_expiry_at:
                next_lease_expiry_at = delivery.lease_expires_at

    steps = [
        {"step_id": step_id, "status_counts": counts}
        for step_id, counts in sorted(step_counts.items())
    ]
    return {
        "task_run_id": task_run.id,
        "delivery_count": len(deliveries),
        "status_counts": status_counts,
        "pending_delivery_count": status_counts.get("pending", 0),
        "inflight_delivery_count": status_counts.get("inflight", 0),
        "dead_letter_delivery_count": status_counts.get("dead_letter", 0),
        "consumed_delivery_count": status_counts.get("consumed", 0),
        "oldest_pending_at": oldest_pending_at.isoformat() if oldest_pending_at else None,
        "next_lease_expiry_at": next_lease_expiry_at.isoformat() if next_lease_expiry_at else None,
        "steps": steps,
    }


def summarize_orchestration_handoff_projection(projection: Any) -> str | None:
    if not isinstance(projection, dict):
        return None
    total = int(projection.get("delivery_count") or 0)
    if total <= 0:
        return None
    pending = int(projection.get("pending_delivery_count") or 0)
    inflight = int(projection.get("inflight_delivery_count") or 0)
    dead = int(projection.get("dead_letter_delivery_count") or 0)
    consumed = int(projection.get("consumed_delivery_count") or 0)
    return (
        f"Orchestration handoffs {total} total / "
        f"{pending} pending / {inflight} inflight / {dead} dead-letter / {consumed} consumed"
    )


def _deliveries_by_ids(db: Session, delivery_ids: Iterable[int]) -> List[OrchestrationHandoffDelivery]:
    ids = [int(delivery_id) for delivery_id in delivery_ids if delivery_id]
    if not ids:
        return []
    return (
        db.query(OrchestrationHandoffDelivery)
        .filter(OrchestrationHandoffDelivery.id.in_(ids))
        .order_by(OrchestrationHandoffDelivery.id.asc())
        .all()
    )
