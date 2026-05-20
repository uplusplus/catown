# -*- coding: utf-8 -*-
"""Backend-owned canonical timeline projection for chat-facing execution traces."""

from __future__ import annotations

import json
from typing import Any

from models.database import TaskRun, TaskRunEvent


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
ERROR_MARKERS = ("failed", "error", "cancel")
DETAIL_TEXT_LIMIT = 12000


EVENT_KIND_PHASE: dict[str, tuple[str, str]] = {
    "user_message_saved": ("user", "message"),
    "target_agent_selected": ("agent", "selected"),
    "target_agents_selected": ("agent", "selected"),
    "llm_request_created": ("llm", "request"),
    "llm_response_started": ("llm", "response_started"),
    "llm_response_completed": ("llm", "response"),
    "agent_turn_started": ("agent", "started"),
    "agent_turn_completed": ("agent", "completed"),
    "tool_call_started": ("tool", "started"),
    "tool_round_recorded": ("tool", "completed"),
    "delegated_task_dispatched": ("delegation", "dispatched"),
    "task_run_waiting_for_delegated_work": ("delegation", "waiting"),
    "scheduler_plan_created": ("scheduler", "planned"),
    "scheduler_step_dispatched": ("scheduler", "dispatched"),
    "scheduler_step_resumed": ("scheduler", "resumed"),
    "scheduler_step_completed": ("scheduler", "completed"),
    "scheduler_step_failed": ("scheduler", "failed"),
    "approval_queue_item_created": ("approval", "requested"),
    "approval_queue_item_resolved": ("approval", "resolved"),
    "approval_queue_item_followup_triggered": ("approval", "continued"),
    "handoff_created": ("handoff", "created"),
    "task_run_failed": ("task", "failed"),
}

LIVE_EVENT_TYPES = {
    "llm_request_created",
    "llm_response_started",
    "agent_turn_started",
    "tool_call_started",
    "delegated_task_dispatched",
    "task_run_waiting_for_delegated_work",
    "scheduler_step_dispatched",
    "scheduler_step_resumed",
    "approval_queue_item_created",
    "approval_queue_item_followup_triggered",
}


def build_task_run_timeline_projection(task_run: TaskRun) -> dict[str, Any]:
    """Return the canonical timeline projection for one task run."""

    events = _ordered_events(list(getattr(task_run, "events", []) or []))
    scheduler_statuses = _scheduler_step_statuses(events)
    steps = [
        _event_to_step(event, task_run=task_run, scheduler_statuses=scheduler_statuses)
        for event in events
    ]
    steps = [step for step in steps if step is not None]
    current_step = _current_step(steps)
    version = max((int(step.get("sequence") or 0) for step in steps), default=0)

    return {
        "scope": "task_run",
        "task_run_id": task_run.id,
        "chatroom_id": task_run.chatroom_id,
        "version": version,
        "current_step_id": current_step.get("id") if current_step else None,
        "steps": steps,
    }


def build_chatroom_timeline_projection(task_runs: list[TaskRun], *, chatroom_id: int) -> dict[str, Any]:
    """Return the canonical timeline projection for a chatroom across task runs."""

    task_timelines = [build_task_run_timeline_projection(task_run) for task_run in task_runs]
    steps = [
        {**step, "task_run_sequence": step.get("sequence")}
        for timeline in task_timelines
        for step in timeline.get("steps", [])
    ]
    steps.sort(key=_chat_step_sort_key)
    for index, step in enumerate(steps, start=1):
        step["sequence"] = index
        step["scope_sequence"] = step.pop("task_run_sequence", None)
        step["scope"] = "chatroom"

    current_step = _current_step(steps)
    version = max(
        (
            int(timeline.get("version") or 0)
            for timeline in task_timelines
        ),
        default=0,
    )

    return {
        "scope": "chatroom",
        "chatroom_id": chatroom_id,
        "version": version,
        "current_step_id": current_step.get("id") if current_step else None,
        "steps": steps,
    }


def _ordered_events(events: list[TaskRunEvent]) -> list[TaskRunEvent]:
    return sorted(
        events,
        key=lambda event: (
            int(getattr(event, "event_index", 0) or 0),
            _iso_value(getattr(event, "created_at", None)),
            int(getattr(event, "id", 0) or 0),
        ),
    )


def _event_to_step(
    event: TaskRunEvent,
    *,
    task_run: TaskRun,
    scheduler_statuses: dict[str, str],
) -> dict[str, Any] | None:
    payload = _load_payload(getattr(event, "payload_json", None))
    if not isinstance(payload, dict):
        payload = {"value": payload}

    event_type = str(getattr(event, "event_type", "") or "event").strip()
    normalized_event = event_type.lower()
    kind, phase = EVENT_KIND_PHASE.get(normalized_event, _fallback_kind_phase(normalized_event))
    actor = _actor(event, payload)
    step_id = _step_id(event, payload, actor=actor, kind=kind, phase=phase)
    occurred_at = _payload_time(payload, "occurred_at") or _iso_value(getattr(event, "created_at", None))
    recorded_at = _payload_time(payload, "recorded_at") or _iso_value(getattr(event, "created_at", None))
    state = _step_state(normalized_event, str(getattr(task_run, "status", "") or ""), payload, scheduler_statuses)
    summary = _summary(event, payload, normalized_event, actor=actor, kind=kind, phase=phase)

    return {
        "id": f"task-run:{task_run.id}:event:{event.event_index}",
        "scope": "task_run",
        "task_run_id": task_run.id,
        "chatroom_id": task_run.chatroom_id,
        "sequence": int(getattr(event, "event_index", 0) or 0),
        "occurred_at": occurred_at,
        "recorded_at": recorded_at,
        "event_type": event_type,
        "step_id": step_id,
        "parent_step_id": _parent_step_id(payload),
        "actor": actor,
        "kind": kind,
        "phase": phase,
        "state": state,
        "facts": _facts(payload),
        "summary": summary,
        "detail_content": _detail_content(event, payload, summary),
    }


def _fallback_kind_phase(event_type: str) -> tuple[str, str]:
    if event_type.startswith("scheduler_step_"):
        return "scheduler", event_type.replace("scheduler_step_", "") or "event"
    if event_type.startswith("tool_"):
        return "tool", event_type.replace("tool_", "") or "event"
    if event_type.startswith("agent_"):
        return "agent", event_type.replace("agent_", "") or "event"
    if "approval" in event_type:
        return "approval", "event"
    return "event", "recorded"


def _step_state(
    event_type: str,
    task_status: str,
    payload: dict[str, Any],
    scheduler_statuses: dict[str, str],
) -> str:
    normalized_status = task_status.lower()
    if _is_error_event(event_type):
        return "error"
    if event_type.startswith("scheduler_step_"):
        status = scheduler_statuses.get(str(payload.get("step_id") or ""))
        if status in {"failed", "cancelled"}:
            return "error"
        if status == "completed":
            return "done"
        if status == "running":
            return "live"
    if event_type.endswith("_completed") or event_type in {"agent_turn_completed", "tool_round_recorded", "approval_queue_item_resolved"}:
        return "done"
    if normalized_status in TERMINAL_TASK_STATUSES:
        return "error" if normalized_status in {"failed", "cancelled"} else "done"
    if event_type in LIVE_EVENT_TYPES:
        return "live"
    return "done"


def _scheduler_step_statuses(events: list[TaskRunEvent]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for event in events:
        payload = _load_payload(getattr(event, "payload_json", None))
        if not isinstance(payload, dict):
            continue
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


def _actor(event: TaskRunEvent, payload: dict[str, Any]) -> str | None:
    for value in (
        getattr(event, "agent_name", None),
        payload.get("agent_name"),
        payload.get("agent"),
        payload.get("agent_type"),
        payload.get("from_agent"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return None


def _step_id(event: TaskRunEvent, payload: dict[str, Any], *, actor: str | None, kind: str, phase: str) -> str:
    for key in ("canonical_step_id", "step_id", "tool_call_id", "message_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    turn = payload.get("turn")
    if turn is not None and actor:
        return f"{kind}:{actor}:{turn}"
    return f"event:{event.event_index}:{kind}:{phase}"


def _parent_step_id(payload: dict[str, Any]) -> str | None:
    for key in ("parent_step_id", "attached_to_step_id", "wait_for_step_id", "from_step_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return None


def _summary(event: TaskRunEvent, payload: dict[str, Any], event_type: str, *, actor: str | None, kind: str, phase: str) -> str:
    explicit = str(getattr(event, "summary", "") or payload.get("summary") or "").strip()
    if explicit:
        return explicit
    if event_type == "delegated_task_dispatched":
        from_agent = str(payload.get("from_agent") or actor or "Agent").strip()
        to_agent = str(payload.get("to_agent") or payload.get("target_agent_name") or "agent").strip()
        title = str(payload.get("task_title") or "").strip()
        if title:
            return f"{from_agent} delegated '{title}' to {to_agent}."
        return f"{from_agent} delegated a task to {to_agent}."
    if event_type == "task_run_waiting_for_delegated_work":
        pending = payload.get("pending_delegated_work") if isinstance(payload.get("pending_delegated_work"), list) else []
        if pending:
            first = pending[0] if isinstance(pending[0], dict) else {}
            title = str(first.get("task_title") or "delegated task").strip()
            target = str(first.get("target_agent_name") or "agent").strip()
            return f"Waiting for '{title}' from {target}."
        return "Waiting for delegated work."
    return ""


def _detail_content(event: TaskRunEvent, payload: dict[str, Any], summary: str) -> str:
    lines = [
        "### Timeline Fact",
        f"- Event: `{event.event_type}`",
        f"- Sequence: `{event.event_index}`",
    ]
    if event.agent_name:
        lines.append(f"- Actor: `{event.agent_name}`")
    if summary:
        lines.append(f"- Summary: {summary}")
    if event.event_type == "llm_request_created":
        system_prompt = str(payload.get("system_prompt") or "")
        prompt_messages = payload.get("prompt_messages")
        if system_prompt:
            lines.extend(["", "### System Prompt", f"```text\n{_compact_text(system_prompt)}\n```"])
        if prompt_messages is not None:
            lines.extend(["", "### Prompt Messages", f"```json\n{_json_block(prompt_messages)}\n```"])
    facts = _facts(payload)
    if facts:
        lines.extend(["", "### Facts", f"```json\n{json.dumps(facts, ensure_ascii=False, indent=2)}\n```"])
    return "\n".join(lines)


def _facts(payload: dict[str, Any]) -> dict[str, Any]:
    omitted = {"system_prompt", "prompt_messages", "raw_response"}
    facts = {
        key: value
        for key, value in payload.items()
        if value is not None and key not in omitted
    }
    if not facts.get("prompt_preview") and isinstance(payload.get("prompt_messages"), list):
        prompt_preview = _prompt_messages_preview(payload.get("prompt_messages") or [])
        if prompt_preview:
            facts["prompt_preview"] = prompt_preview
    return facts


def _json_block(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(value)
    return _compact_text(text)


def _compact_text(value: str, limit: int = DETAIL_TEXT_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"


def _prompt_messages_preview(messages: list[Any], limit: int = 280) -> str:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role == "system":
            continue
        text = _message_content_preview(item.get("content"))
        if text:
            return text[:limit]
    return ""


def _message_content_preview(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value:
                    parts.append(str(value))
            elif item is not None:
                parts.append(str(item))
        return " ".join(" ".join(parts).split())
    if content is None:
        return ""
    return " ".join(str(content).split())


def _current_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in reversed(steps):
        if step.get("state") == "live":
            return step
    return steps[-1] if steps else None


def _chat_step_sort_key(step: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(step.get("recorded_at") or step.get("occurred_at") or ""),
        int(step.get("task_run_id") or 0),
        int(step.get("sequence") or 0),
    )


def _payload_time(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _iso_value(value: Any) -> str | None:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _is_error_event(event_type: str) -> bool:
    return any(marker in event_type for marker in ERROR_MARKERS)


def _load_payload(payload_json: str | None) -> Any:
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return {"raw": payload_json}
