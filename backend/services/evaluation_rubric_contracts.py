# -*- coding: utf-8 -*-
"""Schema v1 for bounded evaluation rubrics carried by Catown workflows."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator


class EvaluationRubricAppliesTo(BaseModel):
    workflow_id: str | None = None
    stage_id: str | None = None
    agent_type: str | None = None
    artifact_type: str | None = None


class EvaluationCriterion(BaseModel):
    criterion_id: str
    name: str
    description: str = ""
    scale: Literal["pass_fail", "score_1_5", "score_1_10", "qualitative"] = "qualitative"
    evaluator: Literal["agent", "human", "software", "hybrid"] = "agent"
    required: bool = True
    weight: float = Field(default=1.0, ge=0)
    acceptance_threshold: float | None = None
    guidance: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_threshold_for_scale(self) -> "EvaluationCriterion":
        if self.acceptance_threshold is None:
            return self
        threshold = float(self.acceptance_threshold)
        if self.scale == "pass_fail" and not 0 <= threshold <= 1:
            raise ValueError("pass_fail acceptance_threshold must be between 0 and 1.")
        if self.scale == "score_1_5" and not 1 <= threshold <= 5:
            raise ValueError("score_1_5 acceptance_threshold must be between 1 and 5.")
        if self.scale == "score_1_10" and not 1 <= threshold <= 10:
            raise ValueError("score_1_10 acceptance_threshold must be between 1 and 10.")
        if self.scale == "qualitative":
            raise ValueError("qualitative criteria cannot define a numeric threshold.")
        return self


class EvaluationRubric(BaseModel):
    kind: Literal["evaluation_rubric"] = "evaluation_rubric"
    version: Literal[1] = 1
    rubric_id: str
    name: str
    domain: str = "software_delivery"
    applies_to: EvaluationRubricAppliesTo = Field(default_factory=EvaluationRubricAppliesTo)
    criteria: list[EvaluationCriterion]
    guidance: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_criteria(self) -> "EvaluationRubric":
        if not self.criteria:
            raise ValueError("Evaluation rubric requires at least one criterion.")
        criterion_ids = [criterion.criterion_id.strip() for criterion in self.criteria]
        if any(not criterion_id for criterion_id in criterion_ids):
            raise ValueError("Evaluation rubric criteria require non-empty criterion_id.")
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("Evaluation rubric criteria must have unique criterion_id values.")
        return self


EVALUATION_RUBRIC_ADAPTER = TypeAdapter(EvaluationRubric)


def parse_evaluation_rubric(payload: Any) -> EvaluationRubric:
    """Validate and parse one schema-v1 evaluation rubric payload."""

    return EVALUATION_RUBRIC_ADAPTER.validate_python(payload)


def dump_evaluation_rubric(rubric: EvaluationRubric) -> dict[str, Any]:
    """Return the canonical JSON-compatible payload for one evaluation rubric."""

    return rubric.model_dump(mode="json")
