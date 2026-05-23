# -*- coding: utf-8 -*-
"""Shared compaction diagnostics summarization helpers."""

from __future__ import annotations

from typing import Any


def build_context_compaction_projection(
    diagnostics: dict[str, Any] | None,
    *,
    fallback_summary: str | None = None,
) -> dict[str, Any]:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    selector = diagnostics.get("selector") if isinstance(diagnostics.get("selector"), dict) else {}
    prompt = diagnostics.get("prompt") if isinstance(diagnostics.get("prompt"), dict) else {}
    prompt_total = prompt.get("total") if isinstance(prompt.get("total"), dict) else {}
    prompt_components = prompt.get("components") if isinstance(prompt.get("components"), dict) else {}
    prompt_fragments = prompt.get("fragments") if isinstance(prompt.get("fragments"), list) else []
    reasons = diagnostics.get("reasons") if isinstance(diagnostics.get("reasons"), list) else []
    role_budgets = selector.get("max_tokens_by_role") if isinstance(selector.get("max_tokens_by_role"), dict) else {}
    scope_budgets = selector.get("max_tokens_by_scope") if isinstance(selector.get("max_tokens_by_scope"), dict) else {}
    usage_band = selector.get("usage_band") if isinstance(selector.get("usage_band"), dict) else {}
    scope_usage = summary.get("by_scope") if isinstance(summary.get("by_scope"), dict) else {}

    budget_summary_parts: list[str] = []
    if role_budgets:
        budget_summary_parts.append(
            "roles " + " / ".join(f"{role} {value}" for role, value in role_budgets.items())
        )
    if scope_budgets:
        budget_summary_parts.append(
            "scopes " + " / ".join(f"{scope} {value}" for scope, value in scope_budgets.items())
        )

    scope_usage_summary = " / ".join(
        (
            f"{scope} "
            f"{scope_report.get('selected_count') or 0}/{scope_report.get('candidate_count') or 0} fragments, "
            f"{scope_report.get('selected_tokens') or 0}/{scope_report.get('candidate_tokens') or 0} tokens"
        )
        for scope, scope_report in scope_usage.items()
        if isinstance(scope_report, dict)
    )

    detail_summary_parts = [
        (
            f"Candidates {int(summary.get('candidate_count') or 0)} -> "
            f"selected {int(summary.get('selected_count') or 0)}"
        ),
        (
            f"tokens {int(summary.get('candidate_tokens') or 0)} -> "
            f"{int(summary.get('selected_tokens') or 0)}"
        ),
    ]
    if budget_summary_parts:
        detail_summary_parts.append(" | ".join(budget_summary_parts))
    if scope_usage_summary:
        detail_summary_parts.append(f"usage {scope_usage_summary}")
    reason_summary = _format_reason_summary(reasons)
    if reason_summary:
        detail_summary_parts.append(f"reasons {reason_summary}")

    return {
        "compacted": bool(diagnostics.get("compacted")),
        "dropped_count": int(summary.get("dropped_count") or 0),
        "truncated_count": int(summary.get("truncated_count") or 0),
        "candidate_count": int(summary.get("candidate_count") or 0),
        "selected_count": int(summary.get("selected_count") or 0),
        "candidate_tokens": int(summary.get("candidate_tokens") or 0),
        "selected_tokens": int(summary.get("selected_tokens") or 0),
        "max_fragments": selector.get("max_fragments"),
        "max_tokens": selector.get("max_tokens"),
        "context_window": selector.get("context_window"),
        "input_window": selector.get("input_window"),
        "reserved_completion_tokens": selector.get("reserved_completion_tokens"),
        "static_tokens": selector.get("static_tokens"),
        "usage_band": usage_band,
        "max_tokens_by_role": role_budgets,
        "max_tokens_by_scope": scope_budgets,
        "scope_usage": scope_usage,
        "budget_summary": " | ".join(budget_summary_parts),
        "scope_usage_summary": scope_usage_summary,
        "detail_summary": " | ".join(part for part in detail_summary_parts if part),
        "reasons": reasons,
        "reason_summary": reason_summary,
        "prompt_total": prompt_total,
        "prompt_components": prompt_components,
        "prompt_fragments": prompt_fragments,
        "summary_text": fallback_summary if isinstance(fallback_summary, str) and fallback_summary.strip() else None,
    }


def _format_reason_summary(reasons: list[Any]) -> str:
    parts: list[str] = []
    for reason in reasons:
        if not isinstance(reason, dict):
            continue
        kind = str(reason.get("kind") or "").strip()
        if kind == "max_fragments":
            parts.append(
                f"fragment cap {reason.get('candidate') or 0}>{reason.get('limit') or 0}"
            )
        elif kind == "max_tokens":
            parts.append(
                f"total tokens {reason.get('candidate') or 0}>{reason.get('limit') or 0}"
            )
        elif kind == "role_tokens":
            parts.append(
                f"{reason.get('role') or 'role'} tokens {reason.get('candidate') or 0}>{reason.get('limit') or 0}"
            )
        elif kind == "scope_tokens":
            parts.append(
                f"{reason.get('scope') or 'scope'} tokens {reason.get('candidate') or 0}>{reason.get('limit') or 0}"
            )
        elif kind == "truncated":
            parts.append(f"truncated {reason.get('count') or 0}")
        elif kind == "dropped":
            parts.append(f"dropped {reason.get('count') or 0}")
    return " / ".join(parts)
