"""Shared helpers for blocked-tool approval queue replay semantics."""

from __future__ import annotations

import hashlib
from typing import Any, Dict


def blocked_tool_queue_kind(blocked_kind: Any) -> str:
    return "escalation" if str(blocked_kind or "").strip().lower() == "sandbox" else "approval"


def blocked_tool_queue_title(tool_name: Any, *, queue_kind: str) -> str:
    normalized_tool_name = str(tool_name or "tool").strip() or "tool"
    if queue_kind == "escalation":
        return f"Escalation needed for {normalized_tool_name}"
    return f"Approval needed for {normalized_tool_name}"


def blocked_tool_resume_supported(*, blocked_kind: Any, blocked_reason: Any) -> bool:
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
    return {
        "turn": int(turn),
        "tool_name": blocked_tool.get("tool_name"),
        "arguments": blocked_tool.get("arguments"),
        "status": blocked_tool.get("status"),
        "blocked_kind": blocked_tool.get("blocked_kind"),
        "blocked_reason": blocked_tool.get("blocked_reason"),
        "resume_supported": bool(resume_supported),
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


def replay_result_is_actionable(replay_result: Any) -> bool:
    """A replay can continue execution only after a successful, unblocked tool result."""

    return bool(getattr(replay_result, "success", False)) and not bool(getattr(replay_result, "blocked", False))


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
            "replay_success": bool(getattr(replay_result, "success", False)),
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
