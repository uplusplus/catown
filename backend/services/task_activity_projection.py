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
    scheduler_step_statuses = _scheduler_step_statuses(events)
    steps = [_event_to_step(event, task_run, scheduler_step_statuses) for event in events]
    steps = [step for step in steps if step is not None]
    current_step = _resolve_current_step(steps)
    active_subagent_handle = _active_subagent_handle(summary)
    active_consult_handle = (
        active_subagent_handle
        if isinstance(active_subagent_handle, dict)
        and str(active_subagent_handle.get("dispatch_kind") or "").strip() == "consult"
        else None
    )

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
        "background": _background_projection(summary),
        "active_subagent_handle": active_subagent_handle,
        "active_consult_handle": active_consult_handle,
        "steps": steps,
    }


def _event_to_step(
    event: TaskRunEvent,
    task_run: TaskRun,
    scheduler_step_statuses: dict[str, str],
) -> dict[str, Any] | None:
    payload = _load_payload(getattr(event, "payload_json", None))
    event_type = str(getattr(event, "event_type", "") or "event")
    agent_name = str(getattr(event, "agent_name", "") or payload.get("agent_name") or payload.get("agent_type") or "").strip()
    tool_name = _read_tool_name(payload)
    state = _step_state(event_type, str(getattr(task_run, "status", "") or ""), payload, scheduler_step_statuses)
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


def _step_state(
    event_type: str,
    task_status: str,
    payload: dict[str, Any],
    scheduler_step_statuses: dict[str, str],
) -> str:
    normalized_event = event_type.lower()
    normalized_status = task_status.lower()
    if normalized_status in TERMINAL_TASK_STATUSES:
        return "error" if normalized_status in {"failed", "cancelled"} or _event_is_error(normalized_event) else "done"
    if _event_is_error(normalized_event):
        return "error"
    if normalized_event.startswith("scheduler_step_"):
        step_status = scheduler_step_statuses.get(str(payload.get("step_id") or ""))
        if step_status in {"failed", "cancelled"}:
            return "error"
        if step_status == "completed":
            return "done"
        if step_status == "running":
            return "live"
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
        "### Task Step",
        f"- Event: `{event_type}`",
        f"- Index: `{event.event_index}`",
    ]
    if event.agent_name:
        lines.append(f"- Agent: `{event.agent_name}`")
    if detail:
        lines.append(f"- Summary: {detail}")
    step_state = payload.get("step_state") if isinstance(payload.get("step_state"), dict) else None
    if step_state:
        lines.extend(["", "### Step State", *_dict_lines(step_state)])
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else None
    if runtime:
        lines.extend(["", "### Runtime", f"- Summary: {_runtime_summary(runtime)}", *_dict_lines(runtime)])
    tool_details = _tool_detail_lines(payload)
    if tool_details:
        lines.extend(["", "### Tool Details", *tool_details])
    refs = _step_refs(payload)
    if refs:
        lines.extend(["", "### Refs", f"```json\n{json.dumps(refs, ensure_ascii=False, indent=2)}\n```"])
    return "\n".join(line for line in lines if line)


def _step_refs(payload: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key in (
        "pipeline_run_id",
        "pipeline_stage_id",
        "step_id",
        "tool_call_id",
        "agent_type",
        "dispatch_kind",
        "wait_for_step_id",
        "attached_to_step_id",
    ):
        if payload.get(key) is not None:
            refs[key] = payload.get(key)
    step_state = payload.get("step_state")
    if isinstance(step_state, dict):
        refs["step_state"] = _compact_ref_dict(step_state)
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        refs["runtime"] = _compact_ref_dict(runtime)
    tracked = payload.get("tracked_process")
    if isinstance(tracked, dict):
        refs["tracked_process"] = {
            key: tracked.get(key)
            for key in ("token", "pid", "status", "command", "cwd")
            if tracked.get(key) is not None
        }
    return refs


def _scheduler_step_statuses(events: list[TaskRunEvent]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for event in events:
        payload = _load_payload(getattr(event, "payload_json", None))
        step_id = str(payload.get("step_id") or "").strip()
        if not step_id:
            continue
        event_type = str(getattr(event, "event_type", "") or "").lower()
        step_state = payload.get("step_state") if isinstance(payload.get("step_state"), dict) else {}
        status = str(step_state.get("status") or "").strip().lower()
        if event_type == "scheduler_step_completed":
            status = "completed"
        elif event_type == "scheduler_step_failed":
            status = "failed"
        elif event_type in {"scheduler_step_dispatched", "scheduler_step_resumed"} and not status:
            status = "running"
        if status:
            statuses[step_id] = status
    return statuses


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


def _background_projection(summary: dict[str, Any]) -> dict[str, Any]:
    checkpoint = summary.get("checkpoint_snapshot") if isinstance(summary.get("checkpoint_snapshot"), dict) else {}
    background: dict[str, Any] = {}
    for key in (
        "scheduler_runtime_summary",
        "subagent_lifecycle_summary",
        "subagent_handles_summary",
        "pipeline_inbox_summary",
        "orchestration_handoff_inbox_summary",
    ):
        value = summary.get(key) or checkpoint.get(key)
        if value:
            background[key] = value
    latest_runtime = summary.get("latest_scheduler_runtime") or checkpoint.get("latest_scheduler_runtime")
    if isinstance(latest_runtime, dict):
        background["latest_scheduler_runtime"] = _compact_ref_dict(latest_runtime)
    active_subagent_handle = _active_subagent_handle(summary)
    if isinstance(active_subagent_handle, dict):
        background["active_subagent_handle"] = active_subagent_handle
        if str(active_subagent_handle.get("dispatch_kind") or "").strip() == "consult":
            background["active_consult_handle"] = active_subagent_handle
    return background


def _active_subagent_handle(summary: dict[str, Any]) -> dict[str, Any] | None:
    checkpoint = summary.get("checkpoint_snapshot") if isinstance(summary.get("checkpoint_snapshot"), dict) else {}
    handles = checkpoint.get("subagent_handles") if isinstance(checkpoint.get("subagent_handles"), dict) else {}
    entries = handles.get("entries") if isinstance(handles.get("entries"), list) else []
    lifecycle = checkpoint.get("subagent_lifecycle") if isinstance(checkpoint.get("subagent_lifecycle"), dict) else {}
    lifecycle_entries = lifecycle.get("subagents") if isinstance(lifecycle.get("subagents"), list) else []
    lifecycle_by_step_id = {
        str(entry.get("step_id") or "").strip(): entry
        for entry in lifecycle_entries
        if isinstance(entry, dict) and str(entry.get("step_id") or "").strip()
    }
    latest_subagent_step = (
        checkpoint.get("latest_subagent_step")
        if isinstance(checkpoint.get("latest_subagent_step"), dict)
        else {}
    )
    preferred_step_id = str(latest_subagent_step.get("step_id") or "").strip()

    preferred_handle = None
    if preferred_step_id:
        preferred_handle = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict) and str(entry.get("step_id") or "").strip() == preferred_step_id
            ),
            None,
        )

    candidate = preferred_handle
    if candidate is None:
        candidate = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict) and not bool(entry.get("closed"))
            ),
            None,
        )
    if not isinstance(candidate, dict):
        return None

    step_id = str(candidate.get("step_id") or "").strip()
    lifecycle_entry = lifecycle_by_step_id.get(step_id, {})
    projected = {
        "step_id": candidate.get("step_id"),
        "agent_name": candidate.get("agent_name"),
        "agent_type": candidate.get("agent_type"),
        "dispatch_kind": candidate.get("dispatch_kind"),
        "status": candidate.get("status"),
        "control_state": candidate.get("control_state"),
        "available_actions": candidate.get("available_actions"),
        "dependency_step_id": candidate.get("dependency_step_id"),
        "source": lifecycle_entry.get("source") or latest_subagent_step.get("source"),
        "requested_name": lifecycle_entry.get("requested_name"),
        "closed": candidate.get("closed"),
        "terminal": candidate.get("terminal"),
        "response_preview": (
            latest_subagent_step.get("response_preview")
            if step_id and step_id == preferred_step_id
            else None
        ),
    }
    projected["summary_text"] = _subagent_handle_summary_text(projected)
    return {key: value for key, value in projected.items() if value is not None}


def _subagent_handle_summary_text(handle: dict[str, Any]) -> str | None:
    agent_name = str(
        handle.get("agent_name")
        or handle.get("requested_name")
        or handle.get("agent_type")
        or ""
    ).strip()
    dispatch_kind = str(handle.get("dispatch_kind") or "").strip()
    control_state = str(handle.get("control_state") or handle.get("status") or "").strip().replace("_", " ")
    response_preview = str(handle.get("response_preview") or "").strip()
    dependency_step_id = str(handle.get("dependency_step_id") or "").strip()
    available_actions = (
        handle.get("available_actions")
        if isinstance(handle.get("available_actions"), list)
        else []
    )
    action_labels = [
        str(action).strip()
        for action in available_actions
        if str(action).strip()
    ]
    parts = [
        " ".join(part for part in (agent_name, dispatch_kind, control_state) if part).strip(),
        response_preview,
        f"waiting on {dependency_step_id}" if dependency_step_id else "",
        f"actions: {', '.join(action_labels)}" if action_labels else "",
    ]
    summary = " | ".join(part for part in parts if part)
    return summary or None


def _tool_detail_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    tool_names = payload.get("tool_names") if isinstance(payload.get("tool_names"), list) else []
    if tool_names:
        lines.append(f"- Tools: {', '.join(f'`{name}`' for name in tool_names)}")
    status_counts = payload.get("tool_status_counts")
    if isinstance(status_counts, dict) and status_counts:
        lines.append(f"- Status counts: {json.dumps(status_counts, ensure_ascii=False)}")
    turn_local_state = payload.get("turn_local_state") if isinstance(payload.get("turn_local_state"), dict) else {}
    for key in ("tool_results", "blocked_tools"):
        items = payload.get(key)
        if not isinstance(items, list) and isinstance(turn_local_state.get(key), list):
            items = turn_local_state.get(key)
        if not isinstance(items, list) or not items:
            continue
        lines.append(f"- {key}: {len(items)}")
        for index, item in enumerate(items[:5], start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool_name") or item.get("name") or "tool")
            status = str(item.get("status") or ("succeeded" if item.get("success") else "unknown"))
            result = str(item.get("result") or item.get("blocked_reason") or "").strip()
            preview = f" - {result[:180]}" if result else ""
            lines.append(f"  - {index}. `{name}` `{status}`{preview}")
            metadata = item.get("metadata")
            tracked = metadata.get("tracked_process") if isinstance(metadata, dict) else None
            if isinstance(tracked, dict):
                lines.append(f"    - tracked process: `{tracked.get('token') or tracked.get('pid') or 'attached'}`")
    return lines


def _dict_lines(value: dict[str, Any]) -> list[str]:
    lines = []
    for key in sorted(value):
        item = value.get(key)
        if item is None or isinstance(item, (dict, list)):
            continue
        lines.append(f"- {key}: `{item}`")
    return lines


def _compact_ref_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and not isinstance(item, (list, dict))
    }


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
