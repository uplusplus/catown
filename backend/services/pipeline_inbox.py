"""Durable inbox helpers for pipeline inter-agent messages."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models.database import PipelineMessage, PipelineMessageDelivery


def enqueue_message_delivery(db: Session, message: PipelineMessage) -> Optional[PipelineMessageDelivery]:
    """Create a durable inbox entry for a direct pipeline message."""

    recipient = str(message.to_agent or "").strip()
    if not recipient:
        return None

    delivery = PipelineMessageDelivery(
        message_id=message.id,
        run_id=message.run_id,
        to_agent=recipient,
        status="pending",
    )
    db.add(delivery)
    return delivery


def claim_messages_for_agent(
    db: Session,
    *,
    run_id: int,
    agent_name: str,
    lease_owner: str,
    lease_seconds: int = 300,
    message_type: str | None = None,
    exclude_message_type: str | None = None,
) -> List[Dict[str, Any]]:
    """Claim pending inbox messages under a short lease for replay-safe processing."""

    owner = str(lease_owner or "").strip()
    if not owner:
        raise ValueError("lease_owner is required")

    now = datetime.now()
    deliveries = _claimable_deliveries_for_agent(
        db,
        run_id=run_id,
        agent_name=agent_name,
        now=now,
        message_type=message_type,
        exclude_message_type=exclude_message_type,
    )
    if not deliveries:
        return []

    lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
    messages: List[Dict[str, Any]] = []
    for delivery in deliveries:
        message = delivery.message
        if message is None:
            continue
        delivery.status = "inflight"
        delivery.lease_owner = owner
        delivery.leased_at = now
        delivery.lease_expires_at = lease_expires_at
        delivery.attempt_count = int(delivery.attempt_count or 0) + 1
        messages.append(serialize_delivery_message(delivery))

    db.commit()
    return messages


def mark_delivery_consumed(db: Session, *, delivery_id: int, lease_owner: str | None = None) -> bool:
    """Ack an inflight delivery after successful processing."""

    delivery = db.query(PipelineMessageDelivery).filter(PipelineMessageDelivery.id == delivery_id).first()
    if delivery is None or delivery.status == "consumed":
        return False
    if lease_owner is not None and delivery.lease_owner != lease_owner:
        return False

    delivery.status = "consumed"
    delivery.consumed_at = datetime.now()
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    delivery.last_error = None
    db.commit()
    return True


def mark_delivery_failed(
    db: Session,
    *,
    delivery_id: int,
    error: str,
    retry: bool = True,
    lease_owner: str | None = None,
) -> bool:
    """Release or dead-letter an inflight delivery after a processing failure."""

    delivery = db.query(PipelineMessageDelivery).filter(PipelineMessageDelivery.id == delivery_id).first()
    if delivery is None or delivery.status == "consumed":
        return False
    if lease_owner is not None and delivery.lease_owner != lease_owner:
        return False

    delivery.status = "pending" if retry else "dead_letter"
    delivery.last_error = str(error or "").strip() or None
    delivery.failed_at = None if retry else datetime.now()
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    db.commit()
    return True


def pop_messages_for_agent(
    db: Session,
    *,
    run_id: int,
    agent_name: str,
    message_type: str | None = None,
    exclude_message_type: str | None = None,
) -> List[Dict[str, Any]]:
    """Claim and consume pending inbox messages for compatibility with stage loops."""

    lease_owner = f"pipeline-pop:{run_id}:{agent_name}"
    messages = claim_messages_for_agent(
        db,
        run_id=run_id,
        agent_name=agent_name,
        lease_owner=lease_owner,
        message_type=message_type,
        exclude_message_type=exclude_message_type,
    )
    for message in messages:
        delivery_id = message.get("delivery_id")
        if delivery_id is not None:
            mark_delivery_consumed(db, delivery_id=int(delivery_id), lease_owner=lease_owner)
    return messages


def pop_instruction_texts_for_agent(db: Session, *, run_id: int, agent_name: str) -> List[str]:
    """Claim and consume pending BOSS instructions for an agent from durable storage."""

    messages = pop_messages_for_agent(
        db,
        run_id=run_id,
        agent_name=agent_name,
        message_type="HUMAN_INSTRUCT",
    )
    return [str(message.get("content") or "").strip() for message in messages if str(message.get("content") or "").strip()]


def consume_legacy_instruction_texts_for_agent(db: Session, *, run_id: int, agent_name: str) -> List[str]:
    """Backfill consumed deliveries for legacy HUMAN_INSTRUCT rows without deliveries."""

    legacy_messages = (
        db.query(PipelineMessage)
        .filter(
            PipelineMessage.run_id == run_id,
            PipelineMessage.message_type == "HUMAN_INSTRUCT",
            PipelineMessage.to_agent == agent_name,
            ~PipelineMessage.deliveries.any(),
        )
        .order_by(PipelineMessage.created_at)
        .all()
    )
    if not legacy_messages:
        return []

    now = datetime.now()
    results: List[str] = []
    for message in legacy_messages:
        delivery = PipelineMessageDelivery(
            message_id=message.id,
            run_id=message.run_id,
            to_agent=agent_name,
            status="consumed",
            consumed_at=now,
        )
        db.add(delivery)
        text = str(message.content or "").strip()
        if text:
            results.append(text)
    db.commit()
    return results


def serialize_pipeline_message(message: PipelineMessage) -> Dict[str, Any]:
    return {
        "message_id": message.id,
        "run_id": message.run_id,
        "stage_id": message.stage_id,
        "from_agent": message.from_agent,
        "to_agent": message.to_agent,
        "content": message.content,
        "message_type": message.message_type,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def serialize_delivery_message(delivery: PipelineMessageDelivery) -> Dict[str, Any]:
    payload = serialize_pipeline_message(delivery.message)
    payload.update(
        {
            "delivery_id": delivery.id,
            "delivery_status": delivery.status,
            "delivery_attempt_count": delivery.attempt_count or 0,
            "delivery_lease_owner": delivery.lease_owner,
            "delivery_lease_expires_at": (
                delivery.lease_expires_at.isoformat() if delivery.lease_expires_at else None
            ),
        }
    )
    return payload


def _claimable_deliveries_for_agent(
    db: Session,
    *,
    run_id: int,
    agent_name: str,
    now: datetime,
    message_type: str | None = None,
    exclude_message_type: str | None = None,
) -> List[PipelineMessageDelivery]:
    query = (
        db.query(PipelineMessageDelivery)
        .join(PipelineMessage, PipelineMessage.id == PipelineMessageDelivery.message_id)
        .filter(
            PipelineMessageDelivery.run_id == run_id,
            PipelineMessageDelivery.to_agent == agent_name,
            or_(
                PipelineMessageDelivery.status == "pending",
                (
                    (PipelineMessageDelivery.status == "inflight")
                    & (PipelineMessageDelivery.lease_expires_at.isnot(None))
                    & (PipelineMessageDelivery.lease_expires_at < now)
                ),
            ),
        )
    )
    if message_type:
        query = query.filter(PipelineMessage.message_type == message_type)
    if exclude_message_type:
        query = query.filter(PipelineMessage.message_type != exclude_message_type)
    return query.order_by(PipelineMessage.created_at.asc(), PipelineMessageDelivery.id.asc()).all()
