# -*- coding: utf-8 -*-
"""Schema v1 for bounded workflow specs interpreted by Catown executors."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkflowDeliverySpec(BaseModel):
    expected_artifacts: list[str] = Field(default_factory=list)
    required: bool = False


class WorkflowRollbackSpec(BaseModel):
    enabled: bool = False
    max_attempts: int = 0
    target_stage_name: str | None = None


class WorkflowSkillSpec(BaseModel):
    active: list[str] = Field(default_factory=list)
    hint_only: list[str] = Field(default_factory=list)


class WorkflowEvaluationSpec(BaseModel):
    rubric_refs: list[str] = Field(default_factory=list)
    required: bool = False


class WorkflowStageSpec(BaseModel):
    stage_id: str
    display_name: str
    agent_type: str
    gate: Literal["auto", "manual", "condition"] = "auto"
    timeout_minutes: int = 30
    context_prompt: str = ""
    delivery: WorkflowDeliverySpec = Field(default_factory=WorkflowDeliverySpec)
    rollback: WorkflowRollbackSpec = Field(default_factory=WorkflowRollbackSpec)
    skills: WorkflowSkillSpec = Field(default_factory=WorkflowSkillSpec)
    evaluation: WorkflowEvaluationSpec = Field(default_factory=WorkflowEvaluationSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowSpec(BaseModel):
    kind: Literal["workflow_spec"] = "workflow_spec"
    version: Literal[1] = 1
    workflow_id: str
    name: str
    description: str = ""
    domain: str = "software_delivery"
    stages: list[WorkflowStageSpec]
    metadata: dict[str, Any] = Field(default_factory=dict)


def compile_pipeline_template_to_workflow_spec(template_name: str, payload: dict[str, Any]) -> WorkflowSpec:
    """Compile the current pipelines.json template shape into workflow_spec v1."""

    stages = []
    for stage in list(payload.get("stages", []) or []):
        if not isinstance(stage, dict):
            continue
        expected_artifacts = [
            str(item).strip()
            for item in list(stage.get("expected_artifacts", []) or [])
            if str(item).strip()
        ]
        rubric_refs = [
            str(item).strip()
            for item in list(stage.get("evaluation_rubrics", []) or [])
            if str(item).strip()
        ]
        stages.append(
            WorkflowStageSpec(
                stage_id=str(stage.get("name") or "").strip(),
                display_name=str(stage.get("display_name") or "").strip(),
                agent_type=str(stage.get("agent") or "").strip(),
                gate=str(stage.get("gate") or "auto").strip() or "auto",
                timeout_minutes=int(stage.get("timeout_minutes") or 30),
                context_prompt=str(stage.get("context_prompt") or ""),
                delivery=WorkflowDeliverySpec(
                    expected_artifacts=expected_artifacts,
                    required=bool(expected_artifacts),
                ),
                rollback=WorkflowRollbackSpec(
                    enabled=bool(stage.get("rollback_on_blocker", False)),
                    max_attempts=int(stage.get("max_rollback_count") or 0),
                    target_stage_name=(
                        str(stage.get("rollback_target") or "").strip() or None
                    ),
                ),
                skills=WorkflowSkillSpec(
                    active=[
                        str(item).strip()
                        for item in list(stage.get("active_skills", []) or [])
                        if str(item).strip()
                    ],
                    hint_only=[
                        str(item).strip()
                        for item in list(stage.get("hint_only_skills", []) or [])
                        if str(item).strip()
                    ],
                ),
                evaluation=WorkflowEvaluationSpec(
                    rubric_refs=rubric_refs,
                    required=bool(rubric_refs),
                ),
            )
        )

    return WorkflowSpec(
        workflow_id=str(template_name or "").strip() or "default",
        name=str(payload.get("name") or template_name or "workflow").strip(),
        description=str(payload.get("description") or ""),
        stages=stages,
    )
