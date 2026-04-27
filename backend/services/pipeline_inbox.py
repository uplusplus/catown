"""Durable inbox helpers for pipeline inter-agent messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

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


def pop_messages_for_agent(
    db: Session,
    *,
    run_id: int,
    agent_name: str,
    message_type: str | None = None,
    exclude_message_type: str | None = None,
) -> List[Dict[str, Any]]:
    """Claim and consume pending inbox messages for an agent from durable storage."""

    deliveries = _pending_deliveries_for_agent(
        db,
        run_id=run_id,
        agent_name=agent_name,
        message_type=message_type,
        exclude_message_type=exclude_message_type,
    )
    if not deliveries:
        return []

    now = datetime.now()
    messages: List[Dict[str, Any]] = []
    for delivery in deliveries:
        message = delivery.message
        if message is None:
            continue
        delivery.status = "consumed"
        delivery.consumed_at = now
        messages.append(serialize_pipeline_message(message))

    db.commit()
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


def _pending_deliveries_for_agent(
    db: Session,
    *,
    run_id: int,
    agent_name: str,
    message_type: str | None = None,
    exclude_message_type: str | None = None,
) -> List[PipelineMessageDelivery]:
    query = (
        db.query(PipelineMessageDelivery)
        .join(PipelineMessage, PipelineMessage.id == PipelineMessageDelivery.message_id)
        .filter(
            PipelineMessageDelivery.run_id == run_id,
            PipelineMessageDelivery.to_agent == agent_name,
            PipelineMessageDelivery.status == "pending",
        )
    )
    if message_type:
        query = query.filter(PipelineMessage.message_type == message_type)
    if exclude_message_type:
        query = query.filter(PipelineMessage.message_type != exclude_message_type)
    return query.order_by(PipelineMessage.created_at.asc(), PipelineMessageDelivery.id.asc()).all()
