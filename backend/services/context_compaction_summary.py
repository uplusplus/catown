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
    role_budgets = selector.get("max_tokens_by_role") if isinstance(selector.get("max_tokens_by_role"), dict) else {}
    scope_budgets = selector.get("max_tokens_by_scope") if isinstance(selector.get("max_tokens_by_scope"), dict) else {}
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
        "max_tokens_by_role": role_budgets,
        "max_tokens_by_scope": scope_budgets,
        "scope_usage": scope_usage,
        "budget_summary": " | ".join(budget_summary_parts),
        "scope_usage_summary": scope_usage_summary,
        "detail_summary": " | ".join(part for part in detail_summary_parts if part),
        "summary_text": fallback_summary if isinstance(fallback_summary, str) and fallback_summary.strip() else None,
    }
