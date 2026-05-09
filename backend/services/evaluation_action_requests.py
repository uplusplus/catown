# -*- coding: utf-8 -*-
"""Bridges evaluation results into bounded action requests."""

from __future__ import annotations

from typing import Any

from services.action_request_contracts import dump_action_request, parse_action_request
from services.evaluation_result_contracts import (
    EvaluationRubricResult,
    parse_evaluation_result,
)


def build_report_blocker_request_from_evaluation_result(
    *,
    result: EvaluationRubricResult | dict[str, Any],
    request_id: str,
    agent_name: str,
    agent_type: str | None = None,
) -> dict[str, Any]:
    """Compile a failed/needs-review evaluation result into report_blocker intent."""

    parsed_result = _ensure_result(result)
    _ensure_non_passed_result(parsed_result)
    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": request_id,
            "type": "report_blocker",
            "source": _source_payload(
                result=parsed_result,
                agent_name=agent_name,
                agent_type=agent_type,
            ),
            "summary": parsed_result.summary or "Evaluation result requires follow-up.",
            "metadata": _metadata_payload(parsed_result),
            "payload": {
                "severity": "blocker" if parsed_result.overall_status == "failed" else "major",
                "reason": _reason(parsed_result),
                "blocker_code": parsed_result.result_id,
                "suggested_resolution": "Review failed evaluation criteria and decide next action.",
            },
        }
    )
    return dump_action_request(request)


def build_suggest_rollback_request_from_evaluation_result(
    *,
    result: EvaluationRubricResult | dict[str, Any],
    request_id: str,
    agent_name: str,
    target_stage_name: str,
    agent_type: str | None = None,
) -> dict[str, Any]:
    """Compile a failed evaluation result into suggest_rollback intent."""

    parsed_result = _ensure_result(result)
    if parsed_result.overall_status != "failed":
        raise ValueError("Only failed evaluation results can suggest rollback.")
    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": request_id,
            "type": "suggest_rollback",
            "source": _source_payload(
                result=parsed_result,
                agent_name=agent_name,
                agent_type=agent_type,
            ),
            "summary": parsed_result.summary or "Evaluation result suggests rollback.",
            "metadata": _metadata_payload(parsed_result),
            "payload": {
                "target_stage_name": target_stage_name,
                "reason": _reason(parsed_result),
                "blocker_code": parsed_result.result_id,
            },
        }
    )
    return dump_action_request(request)


def _ensure_result(result: EvaluationRubricResult | dict[str, Any]) -> EvaluationRubricResult:
    if isinstance(result, dict):
        return parse_evaluation_result(result)
    return result


def _ensure_non_passed_result(result: EvaluationRubricResult) -> None:
    if result.overall_status == "passed":
        raise ValueError("Passed evaluation results do not produce blocker requests.")


def _source_payload(
    *,
    result: EvaluationRubricResult,
    agent_name: str,
    agent_type: str | None,
) -> dict[str, Any]:
    return {
        "agent_name": agent_name,
        "agent_type": agent_type or result.reviewer.agent_type,
        "stage_name": result.target.stage_id,
        "task_run_id": result.target.task_run_id,
        "pipeline_run_id": result.target.pipeline_run_id,
        "pipeline_stage_id": result.target.pipeline_stage_id,
    }


def _metadata_payload(result: EvaluationRubricResult) -> dict[str, Any]:
    return {
        "evaluation_result_id": result.result_id,
        "rubric_id": result.rubric_id,
        "overall_status": result.overall_status,
        "target": result.target.model_dump(mode="json"),
    }


def _reason(result: EvaluationRubricResult) -> str:
    failed = [
        item
        for item in result.criterion_results
        if item.status in {"failed", "needs_review"}
    ]
    reasons = [
        f"{item.criterion_id}: {item.rationale}".strip()
        for item in failed
        if item.rationale.strip()
    ]
    if reasons:
        return "; ".join(reasons)
    return result.summary or f"Evaluation result {result.result_id} requires follow-up."
