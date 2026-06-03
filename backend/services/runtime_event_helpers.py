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
        if not isinstance(diagnostics, dict):
            return
        event_kind = _context_diagnostics_event_kind(diagnostics)
        semantic_compaction = event_kind == "semantic_compaction"
        if (
            not semantic_compaction
            and not diagnostics.get("selection_changed")
            and event_kind not in {"tool_output_budget", "tool_schema_budget"}
        ):
            return
        signature = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        if signature in seen_signatures:
            return
        seen_signatures.add(signature)

        summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
        payload: Dict[str, Any] = {
            "selector_diagnostics": diagnostics,
            "semantic_compaction": semantic_compaction,
            "event_kind": event_kind,
            "context_pressure_kind": diagnostics.get("context_pressure_kind"),
        }
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)

        emit_event(
            "context_compaction" if semantic_compaction else "context_budget_event",
            _context_event_summary(
                agent_name=agent_name,
                summary_noun=summary_noun,
                event_kind=event_kind,
                semantic_compaction=semantic_compaction,
                dropped_count=summary.get("dropped_count", 0),
                truncated_count=summary.get("truncated_count", 0),
            ),
            payload,
        )

    return _callback


def should_emit_context_budget_event(diagnostics: Dict[str, Any]) -> bool:
    if not isinstance(diagnostics, dict):
        return False
    if diagnostics.get("selection_changed") or diagnostics.get("semantic_compaction"):
        return True
    return _context_diagnostics_event_kind(diagnostics) in {"tool_output_budget", "tool_schema_budget"}


def _context_diagnostics_event_kind(diagnostics: Dict[str, Any]) -> str:
    event_kind = diagnostics.get("event_kind")
    if isinstance(event_kind, str) and event_kind.strip():
        return event_kind.strip()
    if diagnostics.get("semantic_compaction"):
        return "semantic_compaction"
    if diagnostics.get("selection_changed"):
        return "selection_truncation"
    prompt = diagnostics.get("prompt") if isinstance(diagnostics.get("prompt"), dict) else {}
    tool_budget = prompt.get("tool_output_budget") if isinstance(prompt.get("tool_output_budget"), dict) else {}
    if int(tool_budget.get("estimated_saved_tokens") or 0) > 0:
        return "tool_output_budget"
    tool_schema_budget = prompt.get("tool_schema_budget") if isinstance(prompt.get("tool_schema_budget"), dict) else {}
    if (
        int(tool_schema_budget.get("tokens") or 0) > 0
        or int(tool_schema_budget.get("estimated_saved_tokens") or 0) > 0
    ):
        return "tool_schema_budget"
    return "selection_pass"


def _context_event_summary(
    *,
    agent_name: str,
    summary_noun: str,
    event_kind: str,
    semantic_compaction: bool,
    dropped_count: Any,
    truncated_count: Any,
) -> str:
    if semantic_compaction or event_kind == "semantic_compaction":
        return (
            f"{agent_name} semantically compacted {summary_noun} "
            f"(dropped={dropped_count}, truncated={truncated_count})."
        )
    if event_kind == "tool_output_budget":
        return f"{agent_name} summarized tool output for {summary_noun} budget."
    if event_kind == "tool_schema_budget":
        return f"{agent_name} recorded tool schema cost for {summary_noun} budget."
    target = "" if summary_noun == "context" else f" for {summary_noun}"
    return (
        f"{agent_name} adjusted context budget{target} "
        f"(dropped={dropped_count}, truncated={truncated_count})."
    )
