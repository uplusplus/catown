# -*- coding: utf-8 -*-
"""Policy checks for evaluation results against runner governance policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.evaluation_result_contracts import (
    EvaluationRubricResult,
    parse_evaluation_result,
)
from services.runner_policy import RunnerGovernancePolicy, find_stage_policy


@dataclass(frozen=True)
class EvaluationResultPolicyViolation:
    code: str
    message: str
    field: str | None = None
    severity: str = "error"

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class EvaluationResultPolicyDecision:
    result_id: str
    accepted: bool
    stage_name: str | None = None
    violations: list[EvaluationResultPolicyViolation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "accepted": self.accepted,
            "stage_name": self.stage_name,
            "violations": [violation.to_payload() for violation in self.violations],
            "metadata": dict(self.metadata),
        }


def validate_evaluation_result_for_policy(
    *,
    result: EvaluationRubricResult | dict[str, Any],
    policy: RunnerGovernancePolicy,
) -> EvaluationResultPolicyDecision:
    """Validate one evaluation result against compiled runner governance policy."""

    parsed_result = parse_evaluation_result(result) if isinstance(result, dict) else result
    violations: list[EvaluationResultPolicyViolation] = []
    stage_name = _clean_text(parsed_result.target.stage_id) or None
    stage_policy = find_stage_policy(policy, stage_name)

    if stage_name and stage_policy is None:
        violations.append(
            EvaluationResultPolicyViolation(
                code="unknown_result_stage",
                field="target.stage_id",
                message=f"Evaluation target stage '{stage_name}' is not part of the workflow.",
            )
        )
    elif stage_policy is not None:
        _validate_stage_evaluation_policy(
            result=parsed_result,
            stage_policy_payload=stage_policy.to_payload(),
            violations=violations,
        )

    accepted = not any(violation.severity == "error" for violation in violations)
    return EvaluationResultPolicyDecision(
        result_id=parsed_result.result_id,
        accepted=accepted,
        stage_name=stage_name,
        violations=violations,
        metadata={
            "policy_source": policy.source,
            "pipeline_name": policy.pipeline_name,
            "stage_count": policy.stage_count,
        },
    )


def _validate_stage_evaluation_policy(
    *,
    result: EvaluationRubricResult,
    stage_policy_payload: dict[str, Any],
    violations: list[EvaluationResultPolicyViolation],
) -> None:
    evaluation_policy = dict(stage_policy_payload.get("metadata", {}).get("evaluation_policy") or {})
    rubric_refs = [
        _clean_text(item)
        for item in list(evaluation_policy.get("rubric_refs", []) or [])
        if _clean_text(item)
    ]
    if not rubric_refs:
        return

    if result.rubric_id not in rubric_refs:
        violations.append(
            EvaluationResultPolicyViolation(
                code="rubric_not_allowed_for_stage",
                field="rubric_id",
                message=(
                    f"Rubric '{result.rubric_id}' is not allowed for stage "
                    f"'{stage_policy_payload.get('stage_name')}'."
                ),
            )
        )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
