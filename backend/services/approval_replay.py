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
