"""Helpers for shared runtime-event emission semantics."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional


def build_runtime_event_payload(
    *,
    client_turn_id: str | None = None,
    stage_policy: Any = None,
    extra_payload: Optional[Dict[str, Any]] = None,
    **fields: Any,
) -> Dict[str, Any]:
    """Build a compact event payload while omitting absent optional fields."""

    payload: Dict[str, Any] = {}
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    if client_turn_id is not None:
        payload["client_turn_id"] = client_turn_id
    if stage_policy is not None:
        payload["stage_policy"] = (
            stage_policy.to_payload()
            if hasattr(stage_policy, "to_payload")
            else stage_policy
        )
    if isinstance(extra_payload, dict):
        payload.update({key: value for key, value in extra_payload.items() if value is not None})
    return payload


def build_context_compaction_callback(
    *,
    emit_event: Callable[[str, str, Dict[str, Any]], Any],
    agent_name: str,
    extra_payload: Optional[Dict[str, Any]] = None,
    summary_noun: str = "context",
) -> Callable[[Dict[str, Any]], None]:
    """Build a de-duplicating compaction callback around an event emitter."""

    seen_signatures: set[str] = set()

    def _callback(diagnostics: Dict[str, Any]) -> None:
        if not isinstance(diagnostics, dict) or not diagnostics.get("compacted"):
            return
        signature = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        if signature in seen_signatures:
            return
        seen_signatures.add(signature)

        summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
        payload: Dict[str, Any] = {
            "selector_diagnostics": diagnostics,
            "compacted": True,
        }
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)

        emit_event(
            "context_compaction",
            f"{agent_name} compacted {summary_noun} "
            f"(dropped={summary.get('dropped_count', 0)}, truncated={summary.get('truncated_count', 0)}).",
            payload,
        )

    return _callback
