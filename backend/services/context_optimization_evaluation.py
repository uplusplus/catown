# -*- coding: utf-8 -*-
"""ADR-035 context/token optimization evaluation helpers."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


PHASE5_TARGETS = {
    "false_early_semantic_compactions": {"max": 0},
    "inline_tool_output_token_reduction_ratio": {"min": 0.60},
    "average_input_token_reduction_ratio": {"min": 0.30},
    "time_to_first_token_improvement_ratio": {"min": 0.0},
    "task_success_regression_count": {"max": 0},
}


def build_context_optimization_metrics(raw: Mapping[str, Any]) -> dict[str, float | int | None]:
    """Derive ADR-035 Phase 5 metrics from baseline/current observations."""

    return {
        "false_early_semantic_compactions": _int_value(raw.get("false_early_semantic_compactions")),
        "inline_tool_output_token_reduction_ratio": _reduction_ratio(
            raw.get("inline_tool_output_tokens_before"),
            raw.get("inline_tool_output_tokens_after"),
            raw.get("inline_tool_output_token_reduction_ratio"),
        ),
        "average_input_token_reduction_ratio": _reduction_ratio(
            raw.get("average_input_tokens_before"),
            raw.get("average_input_tokens_after"),
            raw.get("average_input_token_reduction_ratio"),
        ),
        "time_to_first_token_improvement_ratio": _reduction_ratio(
            raw.get("time_to_first_token_ms_before"),
            raw.get("time_to_first_token_ms_after"),
            raw.get("time_to_first_token_improvement_ratio"),
        ),
        "task_success_regression_count": _int_value(raw.get("task_success_regression_count")),
    }


def build_observation_from_context_budget_events(
    events: Iterable[Any],
    *,
    time_to_first_token_ms_before: Any = None,
    time_to_first_token_ms_after: Any = None,
    task_success_regression_count: Any = 0,
) -> dict[str, Any]:
    """Build Phase 5 evaluation input from context-budget event payloads or Monitor items."""

    inline_before = 0
    inline_after = 0
    input_before_values: list[int] = []
    input_after_values: list[int] = []
    false_early_semantic_compactions = 0
    event_count = 0
    tool_heavy_event_count = 0

    for event in events or []:
        diagnostics = _event_selector_diagnostics(event)
        if not diagnostics:
            continue
        event_count += 1
        if _is_false_early_semantic_compaction(diagnostics):
            false_early_semantic_compactions += 1

        prompt = diagnostics.get("prompt") if isinstance(diagnostics.get("prompt"), Mapping) else {}
        prompt_total = prompt.get("total") if isinstance(prompt.get("total"), Mapping) else {}
        prompt_tokens = _int_value(prompt_total.get("tokens"))
        tool_output_budget = (
            prompt.get("tool_output_budget")
            if isinstance(prompt.get("tool_output_budget"), Mapping)
            else {}
        )
        tool_schema_budget = (
            prompt.get("tool_schema_budget")
            if isinstance(prompt.get("tool_schema_budget"), Mapping)
            else {}
        )
        original_tool_tokens = _int_value(tool_output_budget.get("estimated_original_tokens"))
        prompt_visible_tool_tokens = _int_value(tool_output_budget.get("prompt_visible_tokens"))
        saved_tool_tokens = _int_value(tool_output_budget.get("estimated_saved_tokens")) or 0
        if original_tool_tokens is None and prompt_visible_tool_tokens is not None:
            original_tool_tokens = prompt_visible_tool_tokens + saved_tool_tokens
        if prompt_visible_tool_tokens is None and original_tool_tokens is not None:
            prompt_visible_tool_tokens = max(original_tool_tokens - saved_tool_tokens, 0)

        if original_tool_tokens is not None and prompt_visible_tool_tokens is not None:
            inline_before += original_tool_tokens
            inline_after += prompt_visible_tool_tokens
            if original_tool_tokens > prompt_visible_tool_tokens:
                tool_heavy_event_count += 1

        schema_saved_tokens = _int_value(tool_schema_budget.get("estimated_saved_tokens")) or 0
        total_saved_tokens = saved_tool_tokens + schema_saved_tokens
        if prompt_tokens is not None:
            input_after_values.append(prompt_tokens)
            input_before_values.append(prompt_tokens + total_saved_tokens)

    observation = {
        "event_count": event_count,
        "tool_heavy_event_count": tool_heavy_event_count,
        "false_early_semantic_compactions": false_early_semantic_compactions,
        "inline_tool_output_tokens_before": inline_before or None,
        "inline_tool_output_tokens_after": inline_after or None,
        "average_input_tokens_before": _average(input_before_values),
        "average_input_tokens_after": _average(input_after_values),
        "time_to_first_token_ms_before": time_to_first_token_ms_before,
        "time_to_first_token_ms_after": time_to_first_token_ms_after,
        "task_success_regression_count": task_success_regression_count,
    }
    return observation


def evaluate_context_optimization_metrics(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate ADR-035 Phase 5 observations against the rollout thresholds."""

    metrics = build_context_optimization_metrics(raw)
    results = {
        metric_name: _evaluate_metric(metric_name, value)
        for metric_name, value in metrics.items()
    }
    statuses = {item["status"] for item in results.values()}
    if "missing" in statuses:
        overall = "needs_data"
    elif "fail" in statuses:
        overall = "fail"
    elif "needs_review" in statuses:
        overall = "needs_review"
    else:
        overall = "pass"
    payload = {
        "kind": "context_optimization_evaluation",
        "overall_status": overall,
        "metrics": results,
    }
    observation = {
        key: raw.get(key)
        for key in ("event_count", "tool_heavy_event_count")
        if key in raw
    }
    if observation:
        payload["observation"] = observation
    return payload


def evaluate_context_budget_event_observations(
    events: Iterable[Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Evaluate ADR-035 Phase 5 metrics directly from context-budget events."""

    return evaluate_context_optimization_metrics(
        build_observation_from_context_budget_events(events, **kwargs)
    )


def _evaluate_metric(metric_name: str, value: float | int | None) -> dict[str, Any]:
    target = PHASE5_TARGETS[metric_name]
    if value is None:
        return {
            "value": None,
            "status": "missing",
            "target": target,
        }
    if "min" in target:
        status = "pass" if float(value) >= float(target["min"]) else "fail"
    else:
        status = "pass" if float(value) <= float(target["max"]) else "fail"
    return {
        "value": value,
        "status": status,
        "target": target,
    }


def _reduction_ratio(before: Any, after: Any, explicit: Any = None) -> float | None:
    explicit_value = _float_value(explicit)
    if explicit_value is not None:
        return explicit_value
    before_value = _float_value(before)
    after_value = _float_value(after)
    if before_value is None or after_value is None or before_value <= 0:
        return None
    return round(max((before_value - after_value) / before_value, -1.0), 6)


def _event_selector_diagnostics(event: Any) -> Mapping[str, Any]:
    payload = _event_payload(event)
    diagnostics = payload.get("selector_diagnostics") if isinstance(payload.get("selector_diagnostics"), Mapping) else {}
    if diagnostics:
        return diagnostics
    return event if isinstance(event, Mapping) and event.get("prompt") else {}


def _event_payload(event: Any) -> Mapping[str, Any]:
    if isinstance(event, Mapping):
        if isinstance(event.get("payload"), Mapping):
            return event["payload"]
        if isinstance(event.get("selector_diagnostics"), Mapping):
            return event
        raw_payload = event.get("payload_json")
        if raw_payload:
            return _json_mapping(raw_payload)
        return event
    raw_payload = getattr(event, "payload_json", None)
    if raw_payload:
        return _json_mapping(raw_payload)
    return {}


def _json_mapping(raw_payload: Any) -> Mapping[str, Any]:
    try:
        payload = json.loads(str(raw_payload or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _is_false_early_semantic_compaction(diagnostics: Mapping[str, Any]) -> bool:
    if not bool(diagnostics.get("semantic_compaction")):
        return False
    pressure_kind = str(diagnostics.get("context_pressure_kind") or "").strip()
    return pressure_kind != "model_window_pressure"


def _average(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
