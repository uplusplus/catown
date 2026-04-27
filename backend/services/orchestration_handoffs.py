# -*- coding: utf-8 -*-
"""Shared handoff helpers for scheduler-driven multi-agent orchestration."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.run_ledger import append_task_event


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
            "handoff_created",
            agent_name=from_agent_name,
            summary=(
                f"Recovery created a handoff for {next_step.agent_name}."
                if recovered
                else f"Handoff created for {next_step.agent_name}."
            ),
            payload=payload,
        )
    return handoff
