# -*- coding: utf-8 -*-
"""Task activity projection for chat-facing task steps."""

from __future__ import annotations

import json
from typing import Any

from models.database import TaskRun, TaskRunEvent
from services.run_ledger import serialize_task_run_summary


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
ERROR_EVENT_MARKERS = ("failed", "error", "cancel")
LIVE_EVENT_TYPES = {
    "agent_turn_started",
    "tool_call_started",
    "scheduler_step_dispatched",
    "scheduler_step_resumed",
    "approval_queue_item_followup_triggered",
}


def build_task_activity_projection(task_run: TaskRun) -> dict[str, Any]:
    """Build the canonical foreground activity projection for one task run."""

    events = list(getattr(task_run, "events", []) or [])
    latest_event_index = max((int(getattr(event, "event_index", 0) or 0) for event in events), default=0)
    summary = serialize_task_run_summary(task_run)
    steps = [_event_to_step(event, task_run) for event in events]
    steps = [step for step in steps if step is not None]
    current_step = _resolve_current_step(steps)

    return {
        "task_run_id": task_run.id,
        "status": task_run.status,
        "title": task_run.title,
        "run_kind": task_run.run_kind,
        "version": latest_event_index,
        "latest_event_index": latest_event_index,
        "updated_at": task_run.updated_at.isoformat() if task_run.updated_at else None,
        "current_step_id": current_step.get("id") if current_step else None,
        "summary": summary.get("summary") or summary.get("latest_continuation_event_summary") or task_run.summary,
        "steps": steps,
    }


def _event_to_step(event: TaskRunEvent, task_run: TaskRun) -> dict[str, Any] | None:
    payload = _load_payload(getattr(event, "payload_json", None))
    event_type = str(getattr(event, "event_type", "") or "event")
    agent_name = str(getattr(event, "agent_name", "") or payload.get("agent_name") or payload.get("agent_type") or "").strip()
    tool_name = _read_tool_name(payload)
    state = _step_state(event_type, str(getattr(task_run, "status", "") or ""))
    label = _step_label(event_type, agent_name=agent_name, tool_name=tool_name, payload=payload)
    detail = _step_detail(event, payload, event_type)
    detail_content = _step_detail_content(event, payload, event_type, detail)

    return {
        "id": f"event-{event.event_index}",
        "event_index": event.event_index,
        "event_type": event_type,
        "label": label,
        "state": state,
        "agent": agent_name or None,
        "tool": tool_name,
        "summary": detail,
        "detail": detail,
        "detail_content": detail_content,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "refs": _step_refs(payload),
    }


def _step_state(event_type: str, task_status: str) -> str:
    normalized_event = event_type.lower()
    normalized_status = task_status.lower()
    if normalized_status in TERMINAL_TASK_STATUSES:
        return "error" if normalized_status in {"failed", "cancelled"} or _event_is_error(normalized_event) else "done"
    if _event_is_error(normalized_event):
        return "error"
    if normalized_event in LIVE_EVENT_TYPES:
        return "live"
    if normalized_event.endswith("_completed") or normalized_event in {"agent_turn_completed", "tool_round_recorded"}:
        return "done"
    return "live" if normalized_status == "running" else "done"


def _resolve_current_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in reversed(steps):
        if step.get("state") == "live":
            return step
    return steps[-1] if steps else None


def _step_label(event_type: str, *, agent_name: str, tool_name: str | None, payload: dict[str, Any]) -> str:
    if event_type == "scheduler_plan_created":
        mode = str(payload.get("mode") or payload.get("runner_policy", {}).get("mode") or "schedule")
        return f"Plan {mode}"
    if event_type in {"scheduler_step_dispatched", "scheduler_step_resumed", "scheduler_step_completed", "scheduler_step_failed"}:
        agent = agent_name or str(payload.get("agent_type") or "agent")
        return f"{agent} {event_type.replace('scheduler_step_', '').replace('_', ' ')}"
    if event_type == "agent_turn_started":
        return f"{agent_name or 'Agent'} starts"
    if event_type == "agent_turn_completed":
        return f"{agent_name or 'Agent'} responds"
    if event_type == "tool_call_started":
        return f"{agent_name or 'Agent'} calls {tool_name or 'tool'}"
    if event_type == "tool_round_recorded":
        return f"{agent_name or 'Agent'} records tool output"
    if event_type == "approval_queue_item_created":
        return "Approval requested"
    if event_type == "approval_queue_item_resolved":
        return "Approval resolved"
    return event_type.replace("_", " ").title()


def _step_detail(event: TaskRunEvent, payload: dict[str, Any], event_type: str) -> str:
    summary = str(getattr(event, "summary", "") or "").strip()
    if summary:
        return summary
    if event_type == "scheduler_plan_created":
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        return _runtime_summary(runtime) or "Scheduler plan created."
    if event_type.startswith("scheduler_step_"):
        step_state = payload.get("step_state") if isinstance(payload.get("step_state"), dict) else {}
        return str(step_state.get("summary") or step_state.get("status") or "").strip() or event_type.replace("_", " ")
    if event_type == "tool_round_recorded":
        tool_names = payload.get("tool_names") if isinstance(payload.get("tool_names"), list) else []
        return ", ".join(str(name) for name in tool_names if str(name).strip()) or "Tool output recorded."
    return event_type.replace("_", " ")


def _step_detail_content(event: TaskRunEvent, payload: dict[str, Any], event_type: str, detail: str) -> str:
    lines = [
        f"Event: {event_type}",
        f"Index: {event.event_index}",
    ]
    if event.agent_name:
        lines.append(f"Agent: {event.agent_name}")
    if detail:
        lines.append(f"Summary: {detail}")
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else None
    if runtime:
        lines.append(f"Runtime: {_runtime_summary(runtime)}")
    refs = _step_refs(payload)
    if refs:
        lines.append(f"Refs: {json.dumps(refs, ensure_ascii=False)}")
    return "\n".join(line for line in lines if line)


def _step_refs(payload: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key in ("pipeline_run_id", "pipeline_stage_id", "step_id", "tool_call_id"):
        if payload.get(key) is not None:
            refs[key] = payload.get(key)
    tracked = payload.get("tracked_process")
    if isinstance(tracked, dict):
        refs["tracked_process"] = {
            key: tracked.get(key)
            for key in ("token", "pid", "status", "command", "cwd")
            if tracked.get(key) is not None
        }
    return refs


def _read_tool_name(payload: dict[str, Any]) -> str | None:
    if isinstance(payload.get("tool_names"), list) and payload["tool_names"]:
        return str(payload["tool_names"][0] or "").strip() or None
    for key in ("tool_name", "target_name"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return None


def _runtime_summary(runtime: dict[str, Any]) -> str:
    bits = []
    for key, label in (
        ("completed_step_count", "completed"),
        ("running_step_count", "running"),
        ("waiting_step_count", "waiting"),
        ("ready_step_count", "ready"),
    ):
        if runtime.get(key) is not None:
            bits.append(f"{runtime.get(key)} {label}")
    return " · ".join(bits)


def _event_is_error(event_type: str) -> bool:
    return any(marker in event_type for marker in ERROR_EVENT_MARKERS)


def _load_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
