"""Shared helpers for approved blocked-tool replay continuation semantics."""

from __future__ import annotations

from typing import Any, Dict


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
