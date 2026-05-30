# -*- coding: utf-8 -*-
"""User-visible runtime work-unit projection for chat-facing steps."""

from __future__ import annotations

import json
from typing import Any

from models.database import TaskRun, TaskRunEvent


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
ERROR_MARKERS = ("failed", "error", "cancel")
DETAIL_TEXT_LIMIT = 12000
FACT_TEXT_LIMIT = 800
FACT_LIST_PREVIEW_LIMIT = 8


def build_user_visible_runtime_steps(task_run: TaskRun) -> list[dict[str, Any]]:
    """Project task events into user-visible work units.

    This is intentionally not a one-row-per-event projection. Raw events are audit facts;
    these rows explain what work the user should understand as completed or active.
    """

    events = _ordered_events(list(getattr(task_run, "events", []) or []))
    task_status = str(getattr(task_run, "status", "") or "").strip().lower()
    steps: list[dict[str, Any]] = []
    llm_units: dict[str, dict[str, Any]] = {}
    tool_units: dict[str, dict[str, Any]] = {}
    agent_units: dict[str, dict[str, Any]] = {}

    for event in events:
        payload = _payload(event)
        event_type = str(getattr(event, "event_type", "") or "event").strip()
        normalized = event_type.lower()

        if normalized == "user_message_saved":
            steps.append(_base_step(event, task_run, payload, kind="user", phase="message", state="done", summary="User request received"))
            continue

        if normalized == "scheduler_plan_created":
            steps.append(_scheduler_plan_step(event, task_run, payload))
            continue

        if normalized.startswith("scheduler_step_"):
            continue

        if normalized.startswith("llm_"):
            key = _resolve_llm_key(event, payload, llm_units=llm_units, event_type=normalized)
            unit = llm_units.get(key)
            if unit is None:
                unit = _base_step(
                    event,
                    task_run,
                    payload,
                    kind="llm",
                    phase="request",
                    state="live",
                    summary=_llm_summary(event, payload, "request"),
                    step_id=key,
                )
                llm_units[key] = unit
                steps.append(unit)
            else:
                _merge_event_into_step(unit, event, payload)
            if normalized == "llm_response_completed":
                unit["phase"] = "response"
                unit["state"] = "done"
                unit["summary"] = _llm_summary(event, payload, "response")
            elif normalized == "llm_response_started":
                unit["phase"] = "response_started"
                unit["summary"] = _llm_summary(event, payload, "response_started")
            else:
                unit["phase"] = "request"
            continue

        if normalized == "tool_call_started":
            key = _tool_key(event, payload)
            unit = _base_step(
                event,
                task_run,
                payload,
                kind="tool",
                phase="running",
                state="live",
                summary=_tool_summary(event, payload, completed=False),
                step_id=key,
            )
            tool_units[key] = unit
            steps.append(unit)
            continue

        if normalized == "tool_round_recorded":
            matched = False
            for key in _tool_result_keys(event, payload):
                unit = tool_units.get(key)
                if unit is None:
                    continue
                _merge_event_into_step(unit, event, payload)
                unit["phase"] = "completed"
                unit["state"] = "done"
                unit["summary"] = _tool_summary(event, payload, completed=True)
                matched = True
            if not matched:
                steps.append(
                    _base_step(
                        event,
                        task_run,
                        payload,
                        kind="tool",
                        phase="completed",
                        state="done",
                        summary=_tool_summary(event, payload, completed=True),
                    )
                )
            continue

        if normalized == "agent_turn_started":
            key = _agent_key(event, payload)
            unit = _base_step(
                event,
                task_run,
                payload,
                kind="agent",
                phase="started",
                state="live",
                summary=f"{_actor(event, payload) or 'Agent'} started.",
                step_id=key,
            )
            agent_units[key] = unit
            steps.append(unit)
            continue

        if normalized == "agent_turn_completed":
            key = _agent_key(event, payload)
            unit = agent_units.get(key)
            if unit is None:
                unit = _base_step(
                    event,
                    task_run,
                    payload,
                    kind="agent",
                    phase="completed",
                    state="done",
                    summary=f"{_actor(event, payload) or 'Agent'} completed.",
                    step_id=key,
                )
                steps.append(unit)
            else:
                _merge_event_into_step(unit, event, payload)
                unit["phase"] = "completed"
                unit["state"] = "done"
                unit["summary"] = f"{_actor(event, payload) or 'Agent'} completed."
            continue

        if normalized in {"delegated_task_dispatched", "task_run_waiting_for_delegated_work"}:
            steps.append(_delegation_step(event, task_run, payload))
            continue

        if normalized in {"approval_queue_item_created", "approval_queue_item_followup_triggered"}:
            steps.append(_approval_step(event, task_run, payload, resolved=False))
            continue

        if normalized == "approval_queue_item_resolved":
            steps.append(_approval_step(event, task_run, payload, resolved=True))
            continue

        steps.append(_event_fact_step(event, task_run, payload))

    aggregate = _scheduler_aggregate_step(task_run, events)
    if aggregate:
        if aggregate.get("state") == "live":
            _settle_non_terminal_live_steps(steps)
            steps.append(aggregate)
        else:
            _settle_live_steps_through_sequence(steps, int(aggregate.get("sequence") or 0))
            steps.append(aggregate)
            steps.sort(key=lambda step: (int(step.get("sequence") or 0), 1 if step is aggregate else 0))

    if task_status in TERMINAL_TASK_STATUSES:
        terminal_state = "error" if task_status in {"failed", "cancelled"} else "done"
        for step in steps:
            if step.get("state") == "live":
                step["state"] = terminal_state
    else:
        _keep_only_tail_live_step(steps)

    _renumber_steps(steps)
    return steps


def _ordered_events(events: list[TaskRunEvent]) -> list[TaskRunEvent]:
    return sorted(
        events,
        key=lambda event: (
            int(getattr(event, "event_index", 0) or 0),
            _iso_value(getattr(event, "created_at", None)) or "",
            int(getattr(event, "id", 0) or 0),
        ),
    )


def _base_step(
    event: TaskRunEvent,
    task_run: TaskRun,
    payload: dict[str, Any],
    *,
    kind: str,
    phase: str,
    state: str,
    summary: str,
    step_id: str | None = None,
) -> dict[str, Any]:
    event_type = str(getattr(event, "event_type", "") or "event")
    actor = _actor(event, payload)
    resolved_step_id = step_id or _read_step_id(event, payload, actor=actor, kind=kind, phase=phase)
    return {
        "id": f"task-run:{task_run.id}:work:{resolved_step_id}",
        "scope": "task_run",
        "task_run_id": task_run.id,
        "chatroom_id": task_run.chatroom_id,
        "sequence": int(getattr(event, "event_index", 0) or 0),
        "occurred_at": _payload_time(payload, "occurred_at") or _iso_value(getattr(event, "created_at", None)),
        "recorded_at": _payload_time(payload, "recorded_at") or _iso_value(getattr(event, "created_at", None)),
        "event_type": event_type,
        "step_id": resolved_step_id,
        "parent_step_id": _parent_step_id(payload),
        "actor": actor,
        "kind": kind,
        "phase": phase,
        "state": state,
        "facts": _facts(payload),
        "summary": (str(getattr(event, "summary", "") or "").strip() or summary),
        "detail_content": _detail_content(event, payload, str(getattr(event, "summary", "") or "").strip() or summary),
        "event_refs": [_event_ref(event)],
    }


def _merge_event_into_step(step: dict[str, Any], event: TaskRunEvent, payload: dict[str, Any]) -> None:
    step["event_type"] = str(getattr(event, "event_type", "") or step.get("event_type") or "event")
    step["recorded_at"] = _payload_time(payload, "recorded_at") or _iso_value(getattr(event, "created_at", None))
    step["sequence"] = int(getattr(event, "event_index", 0) or step.get("sequence") or 0)
    step["facts"] = {**(step.get("facts") or {}), **_facts(payload)}
    step.setdefault("event_refs", []).append(_event_ref(event))
    step["detail_content"] = _append_detail(step.get("detail_content"), event, payload)


def _scheduler_plan_step(event: TaskRunEvent, task_run: TaskRun, payload: dict[str, Any]) -> dict[str, Any]:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    count = _coerce_int(payload.get("schedule_step_count")) or _coerce_int(runtime.get("step_count"))
    summary = f"Planned {count} work item{'s' if count != 1 else ''}." if count else "Scheduler planned work."
    step = _base_step(event, task_run, payload, kind="scheduler", phase="planned", state="done", summary=summary)
    step["facts"] = {**step["facts"], "runtime": _compact_runtime(runtime)}
    return step


def _scheduler_aggregate_step(task_run: TaskRun, events: list[TaskRunEvent]) -> dict[str, Any] | None:
    latest_runtime: dict[str, Any] | None = None
    latest_event: TaskRunEvent | None = None
    latest_payload: dict[str, Any] = {}
    for event in events:
        payload = _payload(event)
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else None
        if runtime and isinstance(runtime.get("steps"), list):
            latest_runtime = runtime
            latest_event = event
            latest_payload = payload

    if latest_runtime is None or latest_event is None:
        return None

    steps = [step for step in latest_runtime.get("steps", []) if isinstance(step, dict)]
    running = [step for step in steps if str(_runtime_step_status(step)).lower() == "running"]
    waiting = [step for step in steps if str(_runtime_step_status(step)).lower() == "waiting"]
    ready = [step for step in steps if str(_runtime_step_status(step)).lower() == "ready"]
    completed = [step for step in steps if str(_runtime_step_status(step)).lower() == "completed"]
    failed = [
        step
        for step in steps
        if str(_runtime_step_status(step)).lower() in {"failed", "cancelled", "error"}
    ]
    active = running + waiting + ready
    if not active and not failed and not _should_show_completed_scheduler_runtime(latest_runtime, steps, completed):
        return None

    state = "live" if active else ("error" if failed else "done")
    phase = "parallel_running" if active else ("parallel_failed" if failed else "parallel_completed")
    summary = _parallel_summary(
        running=running,
        waiting=waiting,
        ready=ready,
        completed=completed,
        failed=failed,
        state=state,
    )
    step = _base_step(
        latest_event,
        task_run,
        latest_payload,
        kind="scheduler",
        phase=phase,
        state=state,
        summary=summary,
        step_id=f"scheduler:parallel:{task_run.id}",
    )
    step["id"] = f"task-run:{task_run.id}:work:scheduler:parallel"
    step["event_type"] = "scheduler_parallel_runtime"
    step["facts"] = {
        **step.get("facts", {}),
        "children": [_scheduler_child_status(item) for item in steps],
        "running_count": len(running),
        "waiting_count": len(waiting),
        "ready_count": len(ready),
        "completed_count": len(completed),
        "failed_count": len(failed),
    }
    step["detail_content"] = _parallel_detail(step)
    return step


def _delegation_step(event: TaskRunEvent, task_run: TaskRun, payload: dict[str, Any]) -> dict[str, Any]:
    actor = _actor(event, payload) or "Agent"
    target = str(payload.get("to_agent") or payload.get("target_agent_name") or "agent").strip()
    title = str(payload.get("task_title") or "delegated work").strip()
    summary = f"{actor} delegated '{title}' to {target}." if target else f"{actor} delegated '{title}'."
    return _base_step(event, task_run, payload, kind="delegation", phase="dispatched", state="live", summary=summary)


def _approval_step(event: TaskRunEvent, task_run: TaskRun, payload: dict[str, Any], *, resolved: bool) -> dict[str, Any]:
    target = str(payload.get("target_name") or payload.get("tool_name") or "request").strip()
    state = "done" if resolved else "live"
    phase = "resolved" if resolved else "waiting"
    summary = f"Approval resolved for {target}." if resolved else f"Waiting for approval: {target}."
    return _base_step(event, task_run, payload, kind="approval", phase=phase, state=state, summary=summary)


def _event_fact_step(event: TaskRunEvent, task_run: TaskRun, payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(getattr(event, "event_type", "") or "event")
    state = "error" if _is_error_event(event_type.lower()) else "done"
    return _base_step(
        event,
        task_run,
        payload,
        kind=_fallback_kind(event_type),
        phase=_fallback_phase(event_type),
        state=state,
        summary=str(getattr(event, "summary", "") or "").strip() or event_type.replace("_", " "),
    )


def _settle_non_terminal_live_steps(steps: list[dict[str, Any]]) -> None:
    for step in steps:
        if step.get("state") == "live":
            step["state"] = "done"


def _settle_live_steps_through_sequence(steps: list[dict[str, Any]], sequence: int) -> None:
    for step in steps:
        if step.get("state") == "live" and int(step.get("sequence") or 0) <= sequence:
            step["state"] = "done"


def _keep_only_tail_live_step(steps: list[dict[str, Any]]) -> None:
    live_indices = [index for index, step in enumerate(steps) if step.get("state") == "live"]
    if len(live_indices) <= 1:
        return
    for index in live_indices[:-1]:
        steps[index]["state"] = "done"


def _renumber_steps(steps: list[dict[str, Any]]) -> None:
    for index, step in enumerate(steps, start=1):
        step["sequence"] = index


def _resolve_llm_key(
    event: TaskRunEvent,
    payload: dict[str, Any],
    *,
    llm_units: dict[str, dict[str, Any]],
    event_type: str,
) -> str:
    explicit = _llm_key(event, payload)
    if explicit:
        return explicit

    actor = _actor(event, payload) or "agent"
    if event_type in {"llm_response_started", "llm_response_completed"}:
        for key, unit in reversed(list(llm_units.items())):
            if unit.get("kind") == "llm" and unit.get("actor") == actor and unit.get("state") == "live":
                return key
    return f"llm:{actor}:{event.event_index}"


def _llm_key(event: TaskRunEvent, payload: dict[str, Any]) -> str | None:
    actor = _actor(event, payload) or "agent"
    turn = payload.get("turn")
    explicit = str(payload.get("step_id") or "").strip()
    if explicit:
        return explicit
    if turn is not None:
        return f"llm:{actor}:{turn}"
    return None


def _agent_key(event: TaskRunEvent, payload: dict[str, Any]) -> str:
    actor = _actor(event, payload) or "agent"
    turn = payload.get("turn")
    return str(payload.get("step_id") or "").strip() or f"agent:{actor}:{turn if turn is not None else 'turn'}"


def _tool_key(event: TaskRunEvent, payload: dict[str, Any]) -> str:
    actor = _actor(event, payload) or "agent"
    tool = _tool_name(payload) or "tool"
    call_id = str(payload.get("tool_call_id") or "").strip()
    index = payload.get("tool_call_index")
    if call_id:
        return f"tool:{actor}:{call_id}"
    return f"tool:{actor}:{tool}:{index if index is not None else event.event_index}"


def _tool_result_keys(event: TaskRunEvent, payload: dict[str, Any]) -> list[str]:
    actor = _actor(event, payload) or "agent"
    keys = []
    for item in payload.get("tool_results") if isinstance(payload.get("tool_results"), list) else []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool_name") or item.get("name") or payload.get("tool_name") or "tool").strip()
        call_id = str(item.get("tool_call_id") or item.get("id") or "").strip()
        index = item.get("tool_call_index")
        if call_id:
            keys.append(f"tool:{actor}:{call_id}")
        keys.append(f"tool:{actor}:{tool}:{index if index is not None else event.event_index - 1}")
    tool_names = payload.get("tool_names") if isinstance(payload.get("tool_names"), list) else []
    for tool in tool_names:
        keys.append(f"tool:{actor}:{tool}:{event.event_index - 1}")
    return list(dict.fromkeys(keys))


def _llm_summary(event: TaskRunEvent, payload: dict[str, Any], phase: str) -> str:
    actor = _actor(event, payload) or "Agent"
    if phase == "response":
        finish = str(payload.get("finish_reason") or "").strip()
        if finish == "tool_calls":
            return f"{actor} received tool instructions from the model."
        return f"{actor} received a model response."
    if phase == "response_started":
        return f"{actor} is receiving a model response."
    return f"{actor} asked the model."


def _tool_summary(event: TaskRunEvent, payload: dict[str, Any], *, completed: bool) -> str:
    actor = _actor(event, payload) or "Agent"
    tool = _tool_name(payload)
    if not tool and isinstance(payload.get("tool_names"), list) and payload["tool_names"]:
        tool = str(payload["tool_names"][0])
    if completed:
        return f"{actor} ran {tool or 'tool'}."
    return f"{actor} running {tool or 'tool'}."


def _parallel_summary(
    *,
    running: list[dict[str, Any]],
    waiting: list[dict[str, Any]],
    ready: list[dict[str, Any]],
    completed: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    state: str,
) -> str:
    parts = []
    if running:
        parts.append(f"{len(running)} running")
    if waiting:
        parts.append(f"{len(waiting)} waiting")
    if ready:
        parts.append(f"{len(ready)} ready")
    if completed:
        parts.append(f"{len(completed)} completed")
    if failed:
        parts.append(f"{len(failed)} failed")
    if state == "done":
        return f"Parallel agent work completed ({', '.join(parts)})." if parts else "Parallel agent work completed."
    if state == "error":
        return f"Parallel agent work failed ({', '.join(parts)})." if parts else "Parallel agent work failed."
    return f"Parallel agent work running ({', '.join(parts)})." if parts else "Parallel agent work running."


def _should_show_completed_scheduler_runtime(
    runtime: dict[str, Any],
    steps: list[dict[str, Any]],
    completed: list[dict[str, Any]],
) -> bool:
    if not steps or len(completed) != len(steps):
        return False
    mode = str(runtime.get("mode") or "").strip()
    if mode == "blocking_chain_with_sidecars":
        return True
    if _coerce_int(runtime.get("sidecar_step_count")):
        return True
    return any(str(step.get("dispatch_kind") or "").strip() in {"sidecar", "consult"} for step in steps)


def _parallel_detail(step: dict[str, Any]) -> str:
    children = step.get("facts", {}).get("children") if isinstance(step.get("facts"), dict) else []
    lines = ["### Parallel Runtime", step.get("summary") or "Parallel work running."]
    if isinstance(children, list):
        lines.append("")
        lines.append("### Child Status")
        for child in children:
            if not isinstance(child, dict):
                continue
            label = child.get("agent") or child.get("step_id") or "step"
            bits = [str(child.get("status") or "unknown")]
            if child.get("dispatch_kind"):
                bits.append(str(child.get("dispatch_kind")))
            if child.get("wait_for_step_id"):
                bits.append(f"waiting on {child.get('wait_for_step_id')}")
            lines.append(f"- {label}: {', '.join(bits)}")
    return "\n".join(lines)


def _scheduler_child_status(step: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "step_id": step.get("step_id"),
            "agent": step.get("agent") or step.get("agent_type") or step.get("agent_name"),
            "status": _runtime_step_status(step),
            "dispatch_kind": step.get("dispatch_kind"),
            "wait_for_step_id": step.get("wait_for_step_id"),
            "attached_to_step_id": step.get("attached_to_step_id"),
        }.items()
        if value is not None and value != ""
    }


def _runtime_step_status(step: dict[str, Any]) -> str:
    state = step.get("step_state") if isinstance(step.get("step_state"), dict) else step
    return str(state.get("status") or step.get("status") or "").strip().lower()


def _compact_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in runtime.items()
        if value is not None and not isinstance(value, (dict, list))
    }


def _payload(event: TaskRunEvent) -> dict[str, Any]:
    raw = getattr(event, "payload_json", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {"raw": str(raw)}


def _facts(payload: dict[str, Any]) -> dict[str, Any]:
    omitted = {"system_prompt", "prompt_messages", "raw_response"}
    facts: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or key in omitted:
            continue
        compact = _compact_fact_value(value)
        if compact is not None:
            facts[key] = compact
    if not facts.get("prompt_preview") and isinstance(payload.get("prompt_messages"), list):
        prompt_preview = _prompt_messages_preview(payload.get("prompt_messages") or [])
        if prompt_preview:
            facts["prompt_preview"] = prompt_preview
    return facts


def _compact_fact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_text(value, FACT_TEXT_LIMIT)
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            if isinstance(item, (dict, list)):
                if isinstance(item, list):
                    compact[f"{key}_count"] = len(item)
                continue
            compact[str(key)] = _compact_fact_value(item)
        return compact or None
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            items = [_compact_fact_value(item) for item in value[:FACT_LIST_PREVIEW_LIMIT]]
            if len(value) > FACT_LIST_PREVIEW_LIMIT:
                items.append(f"...{len(value) - FACT_LIST_PREVIEW_LIMIT} more")
            return items
        return {"count": len(value)}
    return str(value)


def _detail_content(event: TaskRunEvent, payload: dict[str, Any], summary: str) -> str:
    lines = [
        "### Runtime Step",
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


def _append_detail(current: Any, event: TaskRunEvent, payload: dict[str, Any]) -> str:
    base = str(current or "").strip()
    addition = _detail_content(event, payload, str(getattr(event, "summary", "") or "").strip())
    return f"{base}\n\n---\n\n{addition}" if base else addition


def _event_ref(event: TaskRunEvent) -> dict[str, Any]:
    return {
        "event_index": getattr(event, "event_index", None),
        "event_type": getattr(event, "event_type", None),
        "agent_name": getattr(event, "agent_name", None),
    }


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


def _tool_name(payload: dict[str, Any]) -> str | None:
    for key in ("tool_name", "tool", "target_name"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return None


def _read_step_id(event: TaskRunEvent, payload: dict[str, Any], *, actor: str | None, kind: str, phase: str) -> str:
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


def _fallback_kind(event_type: str) -> str:
    normalized = event_type.lower()
    if normalized.startswith("tool_"):
        return "tool"
    if normalized.startswith("agent_"):
        return "agent"
    if "approval" in normalized:
        return "approval"
    if normalized.startswith("task_"):
        return "task"
    return "event"


def _fallback_phase(event_type: str) -> str:
    normalized = event_type.lower()
    for prefix in ("scheduler_step_", "tool_", "agent_", "task_"):
        if normalized.startswith(prefix):
            return normalized.replace(prefix, "") or "recorded"
    return "recorded"


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


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
