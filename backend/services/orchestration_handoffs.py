# -*- coding: utf-8 -*-
"""Shared handoff helpers for scheduler-driven multi-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.orchestration_inbox import (
    claim_orchestration_handoffs_for_step,
    create_orchestration_handoff_delivery,
    mark_orchestration_handoffs_consumed,
    mark_orchestration_handoffs_failed,
)
from services.run_ledger import append_task_event
from models.enums import EventType


def compact_runtime_text(value: Any, *, limit: int = 600) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def build_orchestration_previous_work(turns: List[Dict[str, str]]) -> str:
    if not turns:
        return ""
    lines = []
    for item in turns:
        agent_label = str(item.get("agent") or "agent")
        preview = compact_runtime_text(item.get("content") or "", limit=280)
        if preview:
            lines.append(f"- {agent_label}: {preview}")
    if not lines:
        return ""
    return "Completed orchestration turns:\n" + "\n".join(lines)


def build_orchestration_handoff(from_agent_name: str, content: str) -> Dict[str, str]:
    return {
        "from_agent": from_agent_name,
        "content": compact_runtime_text(content, limit=1200),
        "message_type": "handoff",
    }


@dataclass(frozen=True)
class OrchestrationStepHandoffState:
    messages: list[dict[str, Any]]
    delivery_ids: list[int]
    lease_owner: str | None
    durable_present: bool = False


def claim_orchestration_step_handoffs(
    db: Session,
    *,
    task_run: TaskRun | None,
    step: Any,
    agent_name: str,
    pending_handoffs: Dict[str, List[Dict[str, str]]],
    lease_seconds: int = 300,
) -> OrchestrationStepHandoffState:
    """Load handoffs for a step from the durable inbox when available, otherwise from the fallback map."""

    if task_run is not None and getattr(task_run, "id", None) is not None:
        claimed = claim_orchestration_handoffs_for_step(
            db,
            task_run_id=task_run.id,
            step_id=step.step_id,
            agent_name=agent_name,
            lease_owner=f"orchestration-handoff:{task_run.id}:{step.step_id}:{uuid4().hex}",
            lease_seconds=lease_seconds,
        )
        if claimed.durable_present:
            pending_handoffs.pop(step.step_id, None)
            return OrchestrationStepHandoffState(
                messages=claimed.messages,
                delivery_ids=claimed.delivery_ids,
                lease_owner=claimed.lease_owner,
                durable_present=True,
            )
    return OrchestrationStepHandoffState(
        messages=list(pending_handoffs.pop(step.step_id, [])),
        delivery_ids=[],
        lease_owner=None,
        durable_present=False,
    )


def acknowledge_orchestration_step_handoffs(
    db: Session,
    handoff_state: OrchestrationStepHandoffState,
) -> int:
    """Ack durable handoffs after a step completes successfully."""

    if not handoff_state.delivery_ids:
        return 0
    return mark_orchestration_handoffs_consumed(
        db,
        delivery_ids=handoff_state.delivery_ids,
        lease_owner=handoff_state.lease_owner,
    )


def fail_orchestration_step_handoffs(
    db: Session,
    handoff_state: OrchestrationStepHandoffState,
    *,
    error: str,
    retry: bool = True,
) -> int:
    """Release or dead-letter durable handoffs after a step fails."""

    if not handoff_state.delivery_ids:
        return 0
    return mark_orchestration_handoffs_failed(
        db,
        delivery_ids=handoff_state.delivery_ids,
        error=error,
        retry=retry,
        lease_owner=handoff_state.lease_owner,
    )


def record_orchestration_handoffs(
    db: Session,
    task_run: TaskRun | None,
    pending_handoffs: Dict[str, List[Dict[str, str]]],
    *,
    from_agent_name: str,
    from_step_id: str,
    content: str,
    ready_steps: list[Any],
    recovered: bool = False,
) -> Dict[str, str] | None:
    """Create a handoff payload, enqueue it for ready steps, and record ledger events."""

    if not content:
        return None

    handoff = build_orchestration_handoff(from_agent_name, content)
    for next_step in ready_steps:
        pending_handoffs.setdefault(next_step.step_id, []).append(handoff)
        create_orchestration_handoff_delivery(
            db,
            task_run=task_run,
            from_agent=from_agent_name,
            to_agent=next_step.agent_name,
            from_step_id=from_step_id,
            to_step_id=next_step.step_id,
            dispatch_kind=next_step.dispatch_kind,
            attached_to_step_id=next_step.attached_to_step_id,
            content=handoff.get("content") or "",
        )
        payload = {
            "from_agent": from_agent_name,
            "to_agent": next_step.agent_name,
            "from_step_id": from_step_id,
            "to_step_id": next_step.step_id,
            "dispatch_kind": next_step.dispatch_kind,
            "attached_to_step_id": next_step.attached_to_step_id,
            "content_preview": handoff.get("content"),
        }
        if recovered:
            payload["recovered"] = True
        append_task_event(
            db,
            task_run,
            EventType.HANDOFF_CREATED,
            agent_name=from_agent_name,
            summary=(
                f"Recovery created a handoff for {next_step.agent_name}."
                if recovered
                else f"Handoff created for {next_step.agent_name}."
            ),
            payload=payload,
        )
    return handoff
