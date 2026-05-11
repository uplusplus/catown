"""Shared helpers for blocked-tool approval queue replay semantics."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from services.action_request_contracts import dump_action_request, parse_action_request
from services.turn_state import build_tool_result_record


def blocked_tool_queue_kind(blocked_kind: Any) -> str:
    return "escalation" if str(blocked_kind or "").strip().lower() == "sandbox" else "approval"


def blocked_tool_queue_title(tool_name: Any, *, queue_kind: str, blocked_kind: Any = None) -> str:
    normalized_tool_name = str(tool_name or "tool").strip() or "tool"
    if str(blocked_kind or "").strip().lower() == "timeout":
        return f"Continue waiting for {normalized_tool_name}"
    if queue_kind == "escalation":
        return f"Escalation needed for {normalized_tool_name}"
    return f"Approval needed for {normalized_tool_name}"


def blocked_tool_resume_supported(*, blocked_kind: Any, blocked_reason: Any) -> bool:
    if str(blocked_kind or "").strip().lower() == "timeout":
        return True
    if str(blocked_kind or "").strip().lower() != "approval":
        return False
    reason = str(blocked_reason or "").strip().lower()
    if not reason:
        return True
    return "not authorized to use tool" not in reason and "unauthorized tool" not in reason


def build_blocked_tool_request_key(
    *,
    task_run_id: Any,
    agent_name: Any,
    blocked_tool: Dict[str, Any],
) -> str:
    return hashlib.sha1(
        "|".join(
            [
                str(task_run_id or ""),
                str(agent_name or ""),
                str(blocked_tool.get("status") or ""),
                str(blocked_tool.get("tool_name") or ""),
                str(blocked_tool.get("arguments") or ""),
                str(blocked_tool.get("blocked_reason") or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()


def build_blocked_tool_request_payload(
    *,
    turn: int,
    blocked_tool: Dict[str, Any],
    resume_supported: bool,
    runtime_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    runtime_payload = runtime_payload if isinstance(runtime_payload, dict) else {}
    payload = {
        "turn": int(turn),
        "tool_name": blocked_tool.get("tool_name"),
        "arguments": blocked_tool.get("arguments"),
        "status": blocked_tool.get("status"),
        "blocked_kind": blocked_tool.get("blocked_kind"),
        "blocked_reason": blocked_tool.get("blocked_reason"),
        "resume_supported": bool(resume_supported),
        "metadata": blocked_tool.get("metadata") if isinstance(blocked_tool.get("metadata"), dict) else {},
        "pipeline_run_id": runtime_payload.get("pipeline_run_id"),
        "pipeline_stage_id": (
            runtime_payload.get("pipeline_stage_id")
            if runtime_payload.get("pipeline_stage_id") is not None
            else runtime_payload.get("stage_id")
        ),
        "pipeline_id": runtime_payload.get("pipeline_id"),
        "stage_name": runtime_payload.get("stage_name"),
        "display_name": runtime_payload.get("display_name"),
    }
    tool_call_id = blocked_tool.get("tool_call_id")
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    return payload


def build_blocked_tool_action_request(
    *,
    request_id: str,
    agent_name: Any,
    agent_type: Any = None,
    turn: int,
    blocked_tool: Dict[str, Any],
    resume_supported: bool,
    runtime_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Compile one blocked-tool payload into action_request schema v1."""

    runtime_payload = runtime_payload if isinstance(runtime_payload, dict) else {}
    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": request_id,
            "type": "request_approval",
            "source": {
                "agent_name": str(agent_name or "").strip() or "agent",
                "agent_type": (str(agent_type or "").strip() or None),
                "stage_name": runtime_payload.get("stage_name"),
                "task_run_id": runtime_payload.get("task_run_id"),
                "pipeline_run_id": runtime_payload.get("pipeline_run_id"),
                "pipeline_stage_id": (
                    runtime_payload.get("pipeline_stage_id")
                    if runtime_payload.get("pipeline_stage_id") is not None
                    else runtime_payload.get("stage_id")
                ),
                "turn_index": int(turn),
            },
            "summary": str(blocked_tool.get("blocked_reason") or "").strip() or None,
            "payload": {
                "queue_kind": blocked_tool_queue_kind(blocked_tool.get("blocked_kind")),
                "target_kind": "tool",
                "target_name": blocked_tool.get("tool_name"),
                "reason": blocked_tool.get("blocked_reason") or "",
                "resume_supported": bool(resume_supported),
                "request_payload": build_blocked_tool_request_payload(
                    turn=turn,
                    blocked_tool=blocked_tool,
                    resume_supported=resume_supported,
                    runtime_payload=runtime_payload,
                ),
            },
        }
    )
    return dump_action_request(request)


def build_pipeline_gate_request_key(*, pipeline_run_id: Any, stage_name: Any) -> str:
    return f"pipeline_gate:{int(pipeline_run_id or 0)}:{str(stage_name or '').strip()}"


def build_pipeline_gate_request_payload(
    *,
    pipeline_id: Any,
    pipeline_run_id: Any,
    pipeline_stage_id: Any,
    stage_name: Any,
    display_name: Any,
    stage_policy: Any = None,
) -> Dict[str, Any]:
    return {
        "pipeline_id": pipeline_id,
        "pipeline_run_id": pipeline_run_id,
        "pipeline_stage_id": pipeline_stage_id,
        "stage_name": stage_name,
        "display_name": display_name,
        "resume_supported": True,
        "stage_policy": stage_policy.to_payload() if stage_policy is not None else None,
    }


def build_pipeline_gate_action_request(
    *,
    request_id: str,
    agent_name: Any,
    agent_type: Any = None,
    pipeline_id: Any,
    pipeline_run_id: Any,
    pipeline_stage_id: Any,
    stage_name: Any,
    display_name: Any,
    stage_policy: Any = None,
) -> Dict[str, Any]:
    """Compile one pipeline-gate payload into action_request schema v1."""

    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": request_id,
            "type": "request_approval",
            "source": {
                "agent_name": str(agent_name or "").strip() or "agent",
                "agent_type": (str(agent_type or "").strip() or None),
                "stage_name": (str(stage_name or "").strip() or None),
                "pipeline_run_id": pipeline_run_id,
                "pipeline_stage_id": pipeline_stage_id,
            },
            "summary": f"Approval required for pipeline gate {str(display_name or stage_name or 'gate').strip()}",
            "payload": {
                "queue_kind": "approval",
                "target_kind": "pipeline_gate",
                "target_name": str(stage_name or "").strip() or None,
                "reason": f"Pipeline gate {str(display_name or stage_name or 'gate').strip()} requires approval.",
                "resume_supported": True,
                "request_payload": build_pipeline_gate_request_payload(
                    pipeline_id=pipeline_id,
                    pipeline_run_id=pipeline_run_id,
                    pipeline_stage_id=pipeline_stage_id,
                    stage_name=stage_name,
                    display_name=display_name,
                    stage_policy=stage_policy,
                ),
            },
        }
    )
    return dump_action_request(request)


def build_pipeline_gate_resolution_payload(
    *,
    pipeline_run_id: Any,
    pipeline_stage_id: Any,
    stage_name: Any,
    display_name: Any,
) -> Dict[str, Any]:
    return {
        "pipeline_run_id": pipeline_run_id,
        "pipeline_stage_id": pipeline_stage_id,
        "stage_name": stage_name,
        "display_name": display_name,
    }


def build_queue_rejection_resolution_payload(
    *,
    request_payload: Dict[str, Any],
    rollback_to: Any = None,
) -> Dict[str, Any]:
    payload = {
        "request_payload": request_payload,
        "resume_supported": bool(request_payload.get("resume_supported")),
        "action_taken": "queue_resolved_only",
    }
    if rollback_to is not None:
        payload["rollback_to"] = rollback_to
    return payload


def load_approval_queue_request_payload(raw_payload: Any) -> Dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    if not raw_payload:
        return {}
    try:
        loaded = json.loads(str(raw_payload))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def replay_tool_call_id(item: Any, tool_name: Any = None) -> str:
    fallback = tool_name if tool_name is not None else getattr(item, "target_name", "tool")
    return f"queue-replay-{getattr(item, 'id', fallback or 'tool')}"


def resolve_replay_tool_call_id(item: Any, request_payload: Dict[str, Any] | None = None, tool_name: Any = None) -> str:
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    original_tool_call_id = str(request_payload.get("tool_call_id") or "").strip()
    if original_tool_call_id:
        return original_tool_call_id
    return replay_tool_call_id(item, tool_name)


def build_replay_tool_result_record(
    item: Any,
    *,
    tool_name: Any,
    arguments: Any,
    result: Any,
    success: bool,
    request_payload: Dict[str, Any] | None = None,
) -> Any:
    return build_tool_result_record(
        tool_call_id=resolve_replay_tool_call_id(item, request_payload, tool_name),
        tool_name=str(tool_name or getattr(item, "target_name", None) or "tool"),
        arguments=arguments,
        result=result,
        success=success,
    )


def resolve_replay_tool_name(item: Any, request_payload: Dict[str, Any]) -> str:
    return str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip()


def resolve_replay_arguments_text(request_payload: Dict[str, Any]) -> str:
    return str(request_payload.get("arguments") or "{}")


def parse_replay_arguments(arguments_text: Any) -> tuple[Dict[str, Any] | None, str | None]:
    try:
        loaded_arguments = json.loads(str(arguments_text or "{}"))
        if not isinstance(loaded_arguments, dict):
            raise ValueError("Tool arguments must be a JSON object.")
    except Exception as exc:
        return None, str(exc)
    return loaded_arguments, None


def resolve_pipeline_replay_run_id(item: Any, request_payload: Dict[str, Any]) -> Any:
    return getattr(item, "pipeline_run_id", None) or request_payload.get("pipeline_run_id")


def resolve_pipeline_replay_stage_id(item: Any, request_payload: Dict[str, Any]) -> Any:
    return getattr(item, "pipeline_stage_id", None) or request_payload.get("pipeline_stage_id")


def approval_queue_item_has_pipeline_cursor(item: Any, request_payload: Dict[str, Any] | None = None) -> bool:
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    return bool(resolve_pipeline_replay_run_id(item, request_payload))


def approval_queue_resume_strategy(item: Any, request_payload: Dict[str, Any] | None = None) -> str:
    if approval_queue_item_has_pipeline_cursor(item, request_payload):
        return "resume_pipeline_stage"
    return "resume_original_tool_call"


def build_pending_approval_continuation_cursor(
    item: Any,
    *,
    request_payload: Dict[str, Any] | None = None,
    blocked_payload: Dict[str, Any] | None = None,
    tool_round_payload: Dict[str, Any] | None = None,
    source_event_type: Any = None,
    source_event_at: Any = None,
) -> Dict[str, Any]:
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    blocked_payload = blocked_payload if isinstance(blocked_payload, dict) else {}
    tool_round_payload = tool_round_payload if isinstance(tool_round_payload, dict) else {}
    return {
        "next_action": "await_approval",
        "resume_strategy": approval_queue_resume_strategy(item, request_payload),
        "source_event_type": source_event_type,
        "source_event_at": source_event_at,
        "turn": request_payload.get("turn") or blocked_payload.get("turn") or tool_round_payload.get("turn"),
        "tool_name": getattr(item, "target_name", None) or blocked_payload.get("tool_name"),
        "blocked_kind": request_payload.get("blocked_kind") or blocked_payload.get("blocked_kind"),
        "queue_item_id": getattr(item, "id", None),
        "resume_token": getattr(item, "resume_token", None),
        "resolution_owner": getattr(item, "resolution_owner", None),
        "resolution_lease_expires_at": (
            getattr(item, "resolution_lease_expires_at", None).isoformat()
            if getattr(item, "resolution_lease_expires_at", None) is not None
            else None
        ),
        "pipeline_run_id": resolve_pipeline_replay_run_id(item, request_payload),
        "pipeline_stage_id": resolve_pipeline_replay_stage_id(item, request_payload),
    }


def build_approval_queue_item_created_event_payload(item: Any) -> Dict[str, Any]:
    return {
        "queue_item_id": getattr(item, "id", None),
        "queue_kind": getattr(item, "queue_kind", None),
        "target_kind": getattr(item, "target_kind", None),
        "target_name": getattr(item, "target_name", None),
        "status": getattr(item, "status", None),
        "source": getattr(item, "source", None),
    }


def build_approval_queue_item_resolved_event_payload(
    item: Any,
    *,
    status: str,
    resolved_by: Any = None,
    request_payload: Dict[str, Any] | None = None,
    resolution_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    resolution_payload = resolution_payload if isinstance(resolution_payload, dict) else {}
    payload = {
        "queue_item_id": getattr(item, "id", None),
        "queue_kind": getattr(item, "queue_kind", None),
        "target_kind": getattr(item, "target_kind", None),
        "target_name": getattr(item, "target_name", None),
        "status": status,
        "resolved_by": resolved_by if resolved_by is not None else getattr(item, "resolved_by", None),
    }
    if request_payload:
        payload["resume_supported"] = bool(request_payload.get("resume_supported"))
    for key in (
        "action_taken",
        "replay_status",
        "replay_success",
        "replay_blocked",
        "replay_blocked_kind",
        "followup_attempted",
        "followup_status",
        "followup_reason",
        "followup_error",
        "followup_message_id",
    ):
        if resolution_payload.get(key) is not None:
            payload[key] = resolution_payload.get(key)
    return payload


def replay_result_is_actionable(replay_result: Any) -> bool:
    """A replay can continue execution after any non-blocked tool result.

    Failed command/test output is actionable context for the agent: the next
    turn should diagnose it or choose a corrected command. Blocked results are
    not actionable because they still require an external decision.
    """

    return not bool(getattr(replay_result, "blocked", False))


def build_followup_skipped_payload(reason: str) -> Dict[str, Any]:
    return {
        "followup_attempted": False,
        "followup_status": "skipped",
        "followup_reason": reason,
    }


def build_followup_failed_payload(error: Any, **fields: Any) -> Dict[str, Any]:
    payload = {
        "followup_attempted": True,
        "followup_status": "failed",
        "followup_error": str(error),
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    return payload


def build_followup_continued_payload(**fields: Any) -> Dict[str, Any]:
    payload = {
        "followup_attempted": True,
        "followup_status": "continued",
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    return payload


def build_followup_triggered_event_payload(
    item: Any,
    replay_result: Any,
    *,
    message_id: Any = None,
    pipeline: Any = None,
    pipeline_run: Any = None,
    request_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    payload = {
        "queue_item_id": getattr(item, "id", None),
        "tool_name": getattr(replay_result, "tool_name", None),
        "tool_call_id": getattr(replay_result, "tool_call_id", None),
    }
    if message_id is not None:
        payload["message_id"] = message_id
    if pipeline is not None:
        payload["pipeline_id"] = getattr(pipeline, "id", None)
    if pipeline_run is not None:
        payload["pipeline_run_id"] = getattr(pipeline_run, "id", None)
    pipeline_stage_id = resolve_pipeline_replay_stage_id(item, request_payload)
    if pipeline_stage_id is not None:
        payload["pipeline_stage_id"] = pipeline_stage_id
    if request_payload.get("stage_name") is not None:
        payload["stage_name"] = request_payload.get("stage_name")
    return payload


def build_followup_failed_event_payload(
    item: Any,
    replay_result: Any,
    error: Any,
    *,
    pipeline: Any = None,
    pipeline_run: Any = None,
) -> Dict[str, Any]:
    payload = {
        "queue_item_id": getattr(item, "id", None),
        "tool_name": getattr(replay_result, "tool_name", None),
        "error": str(error),
    }
    if pipeline is not None:
        payload["pipeline_id"] = getattr(pipeline, "id", None)
    if pipeline_run is not None:
        payload["pipeline_run_id"] = getattr(pipeline_run, "id", None)
    return payload


def build_tool_replay_followup_context(item: Any, replay_result: Any, *, result_preview_limit: int = 400) -> str:
    tool_name = (
        str(getattr(replay_result, "tool_name", None) or getattr(item, "target_name", None) or "tool").strip()
        or "tool"
    )
    result_preview = _compact_text(getattr(replay_result, "result", ""), limit=result_preview_limit)
    success = _replay_result_success(replay_result)
    next_step = (
        "Continue from this successful result. Do not rerun the same tool call unless the user explicitly asks "
        "or the result shows it did not complete."
        if success
        else "The approved tool ran but failed. Treat the failure output as diagnostic evidence, explain the cause, "
        "and choose a corrected next step instead of stopping at the tool failure."
    )
    return (
        "Approved tool continuation completed.\n"
        f"- Tool: {tool_name}\n"
        f"- Status: {getattr(replay_result, 'status', 'unknown')}\n"
        f"- Success: {str(success).lower()}\n"
        f"- Result: {result_preview}\n"
        f"{next_step}"
    )


def build_queue_replay_resolution_payload(
    *,
    request_payload: Dict[str, Any],
    replay_result: Any = None,
    action_taken: str = "queue_resolved_only",
    result_preview_limit: int = 280,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "request_payload": request_payload,
        "resume_supported": bool(request_payload.get("resume_supported")),
        "action_taken": action_taken,
    }
    if replay_result is None:
        return payload

    payload.update(
        {
            "replay_attempted": True,
            "replay_status": getattr(replay_result, "status", None),
            "replay_success": _replay_result_success(replay_result),
            "replay_blocked": bool(getattr(replay_result, "blocked", False)),
            "replay_blocked_kind": getattr(replay_result, "blocked_kind", None),
            "replay_result_preview": _compact_text(
                getattr(replay_result, "result", ""),
                limit=result_preview_limit,
            ),
        }
    )
    return payload


def build_approval_queue_replay_round_payload(item: Any, request_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "replay": True,
        "replay_of_queue_item_id": getattr(item, "id", None),
    }
    for key in ("pipeline_id", "pipeline_run_id", "pipeline_stage_id", "stage_name", "display_name"):
        if request_payload.get(key) is not None:
            payload[key] = request_payload.get(key)
    return payload


def _compact_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "..."
    return text


def _replay_result_success(replay_result: Any) -> bool:
    if hasattr(replay_result, "success"):
        return bool(getattr(replay_result, "success", False))
    status = str(getattr(replay_result, "status", "") or "").strip().lower()
    return status in {"success", "succeeded", "completed", "done"}
