# -*- coding: utf-8 -*-
"""Bridges evaluation results into bounded action requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.action_request_policy import ActionRequestPolicyDecision
from services.action_request_policy import validate_action_request_for_workflow
from services.action_request_contracts import dump_action_request, parse_action_request
from services.evaluation_result_contracts import (
    EvaluationRubricResult,
    parse_evaluation_result,
)
from services.policy_decision_contracts import (
    build_policy_decision_event_payload,
    dump_policy_decision,
    project_policy_decision,
)
from services.workflow_spec_contracts import WorkflowSpec


@dataclass(frozen=True)
class EvaluationRollbackPolicyResult:
    request: dict[str, Any]
    decision: ActionRequestPolicyDecision

    def to_payload(self) -> dict[str, Any]:
        policy_decision = project_policy_decision(self.decision)
        return {
            "request": dict(self.request),
            "decision": self.decision.to_payload(),
            "policy_decision": dump_policy_decision(policy_decision),
            "policy_decision_event_payload": build_policy_decision_event_payload(
                policy_decision
            ),
        }


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


def build_and_validate_rollback_request_from_evaluation_result(
    *,
    result: EvaluationRubricResult | dict[str, Any],
    request_id: str,
    agent_name: str,
    target_stage_name: str,
    workflow_spec: WorkflowSpec,
    agent_type: str | None = None,
    project_id: int | None = None,
) -> tuple[dict[str, Any], ActionRequestPolicyDecision]:
    """Build rollback intent from evaluation result and validate it against workflow policy."""

    request = build_suggest_rollback_request_from_evaluation_result(
        result=result,
        request_id=request_id,
        agent_name=agent_name,
        agent_type=agent_type,
        target_stage_name=target_stage_name,
    )
    decision = validate_action_request_for_workflow(
        request=request,
        workflow_spec=workflow_spec,
        project_id=project_id,
    )
    return request, decision


def build_rollback_policy_result_from_evaluation_result(
    *,
    result: EvaluationRubricResult | dict[str, Any],
    request_id: str,
    agent_name: str,
    target_stage_name: str,
    workflow_spec: WorkflowSpec,
    agent_type: str | None = None,
    project_id: int | None = None,
) -> EvaluationRollbackPolicyResult:
    """Build rollback intent, validate it, and project the policy decision."""

    request, decision = build_and_validate_rollback_request_from_evaluation_result(
        result=result,
        request_id=request_id,
        agent_name=agent_name,
        target_stage_name=target_stage_name,
        workflow_spec=workflow_spec,
        agent_type=agent_type,
        project_id=project_id,
    )
    return EvaluationRollbackPolicyResult(request=request, decision=decision)


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
