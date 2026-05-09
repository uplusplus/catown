# -*- coding: utf-8 -*-
"""Schema v1 for evaluation results produced from Catown rubrics."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator


class EvaluationTargetRef(BaseModel):
    artifact_id: str | None = None
    artifact_type: str | None = None
    file_path: str | None = None
    workflow_id: str | None = None
    stage_id: str | None = None
    task_run_id: int | None = None
    pipeline_run_id: int | None = None
    pipeline_stage_id: int | None = None


class EvaluationReviewer(BaseModel):
    name: str | None = None
    agent_type: str | None = None
    owner: Literal["agent", "human", "software", "hybrid"] = "agent"


class EvaluationCriterionResult(BaseModel):
    criterion_id: str
    status: Literal["passed", "failed", "not_applicable", "needs_review"]
    score: float | None = Field(default=None, ge=0)
    rationale: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationRubricResult(BaseModel):
    kind: Literal["evaluation_result"] = "evaluation_result"
    version: Literal[1] = 1
    result_id: str
    rubric_id: str
    target: EvaluationTargetRef = Field(default_factory=EvaluationTargetRef)
    reviewer: EvaluationReviewer = Field(default_factory=EvaluationReviewer)
    overall_status: Literal["passed", "failed", "needs_review"]
    criterion_results: list[EvaluationCriterionResult]
    summary: str = ""
    recommended_action_request_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_criterion_results(self) -> "EvaluationRubricResult":
        if not self.criterion_results:
            raise ValueError("Evaluation result requires at least one criterion result.")
        criterion_ids = [item.criterion_id.strip() for item in self.criterion_results]
        if any(not criterion_id for criterion_id in criterion_ids):
            raise ValueError("Evaluation criterion results require non-empty criterion_id.")
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("Evaluation criterion results must have unique criterion_id values.")
        if self.overall_status == "passed" and any(
            item.status in {"failed", "needs_review"} for item in self.criterion_results
        ):
            raise ValueError("Passed evaluation results cannot include failed or needs_review criteria.")
        return self


EVALUATION_RESULT_ADAPTER = TypeAdapter(EvaluationRubricResult)


def parse_evaluation_result(payload: Any) -> EvaluationRubricResult:
    """Validate and parse one schema-v1 evaluation result payload."""

    return EVALUATION_RESULT_ADAPTER.validate_python(payload)


def dump_evaluation_result(result: EvaluationRubricResult) -> dict[str, Any]:
    """Return the canonical JSON-compatible payload for one evaluation result."""

    return result.model_dump(mode="json")
