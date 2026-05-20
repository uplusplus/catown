# -*- coding: utf-8 -*-
"""Runtime guards for parent task runs with delegated child work."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def find_incomplete_delegated_child_runs(
    db: Session,
    task_run: Any | None,
) -> list[dict[str, Any]]:
    """Return delegated child runs that still prevent the parent from completing."""

    if task_run is None:
        return []

    task_run_model = task_run.__class__
    pending: list[dict[str, Any]] = []
    seen_client_turn_ids: set[str] = set()
    for event in list(getattr(task_run, "events", []) or []):
        if getattr(event, "event_type", None) != "delegated_task_dispatched":
            continue
        payload = _load_payload(getattr(event, "payload_json", None))
        if not isinstance(payload, dict):
            continue
        child_client_turn_id = str(payload.get("child_client_turn_id") or "").strip()
        if not child_client_turn_id or child_client_turn_id in seen_client_turn_ids:
            continue
        seen_client_turn_ids.add(child_client_turn_id)
        child_run = (
            db.query(task_run_model)
            .filter(
                task_run_model.chatroom_id == task_run.chatroom_id,
                task_run_model.client_turn_id == child_client_turn_id,
            )
            .order_by(task_run_model.created_at.desc(), task_run_model.id.desc())
            .first()
        )
        child_status = str(getattr(child_run, "status", "") or "").strip().lower()
        if child_run is not None and child_status in TERMINAL_STATUSES:
            continue
        pending.append(
            {
                "task_id": payload.get("task_id"),
                "task_title": payload.get("task_title"),
                "target_agent_name": payload.get("target_agent_name") or payload.get("to_agent"),
                "child_client_turn_id": child_client_turn_id,
                "child_task_run_id": getattr(child_run, "id", None),
                "child_status": child_status or ("not_started" if child_run is None else None),
            }
        )

    return pending


def build_pending_delegated_work_summary(pending: list[dict[str, Any]]) -> str:
    """Render a compact factual summary for pending delegated child work."""

    if not pending:
        return ""
    first = pending[0]
    title = str(first.get("task_title") or "delegated task").strip()
    target = str(first.get("target_agent_name") or "agent").strip()
    suffix = f" (+{len(pending) - 1} more)" if len(pending) > 1 else ""
    return f"Waiting for delegated work: '{title}' assigned to {target}{suffix}."


def _load_payload(payload_json: str | None) -> Any:
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return {"raw": payload_json}
