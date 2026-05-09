# -*- coding: utf-8 -*-
"""Execution-readiness diagnostics for canonical workflow specs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.workflow_spec_contracts import WorkflowSpec, WorkflowStageSpec


@dataclass(frozen=True)
class WorkflowSpecDiagnostic:
    code: str
    message: str
    field: str | None = None
    stage_id: str | None = None
    severity: str = "error"

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "stage_id": self.stage_id,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class WorkflowSpecPolicyReport:
    workflow_id: str
    executable: bool
    diagnostics: list[WorkflowSpecDiagnostic] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "executable": self.executable,
            "diagnostics": [diagnostic.to_payload() for diagnostic in self.diagnostics],
            "metadata": dict(self.metadata),
        }


def validate_workflow_spec_for_execution(
    workflow_spec: WorkflowSpec,
) -> WorkflowSpecPolicyReport:
    """Return deterministic diagnostics for workflow specs before execution."""

    diagnostics: list[WorkflowSpecDiagnostic] = []
    stages = list(workflow_spec.stages or [])
    stage_ids = [_clean_text(stage.stage_id) for stage in stages]
    stage_index_by_id = {
        stage_id: index
        for index, stage_id in enumerate(stage_ids)
        if stage_id and stage_ids.count(stage_id) == 1
    }

    if not _clean_text(workflow_spec.workflow_id):
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="workflow_id_missing",
                field="workflow_id",
                message="Workflow spec must declare a workflow_id.",
            )
        )

    if not stages:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="workflow_has_no_stages",
                field="stages",
                message="Workflow spec must declare at least one stage.",
            )
        )

    _validate_stage_identity(stages=stages, stage_ids=stage_ids, diagnostics=diagnostics)
    for index, stage in enumerate(stages):
        _validate_stage(
            stage=stage,
            index=index,
            stage_index_by_id=stage_index_by_id,
            diagnostics=diagnostics,
        )

    executable = not any(diagnostic.severity == "error" for diagnostic in diagnostics)
    return WorkflowSpecPolicyReport(
        workflow_id=_clean_text(workflow_spec.workflow_id),
        executable=executable,
        diagnostics=diagnostics,
        metadata={
            "stage_count": len(stages),
            "warning_count": len([item for item in diagnostics if item.severity == "warning"]),
            "error_count": len([item for item in diagnostics if item.severity == "error"]),
        },
    )


def _validate_stage_identity(
    *,
    stages: list[WorkflowStageSpec],
    stage_ids: list[str],
    diagnostics: list[WorkflowSpecDiagnostic],
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for stage_id in stage_ids:
        if not stage_id:
            continue
        if stage_id in seen:
            duplicates.add(stage_id)
        seen.add(stage_id)

    for index, stage in enumerate(stages):
        stage_id = stage_ids[index]
        if not stage_id:
            diagnostics.append(
                WorkflowSpecDiagnostic(
                    code="stage_id_missing",
                    field=f"stages[{index}].stage_id",
                    message=f"Stage at index {index} must declare a stage_id.",
                )
            )
            continue
        if stage_id in duplicates:
            diagnostics.append(
                WorkflowSpecDiagnostic(
                    code="duplicate_stage_id",
                    field=f"stages[{index}].stage_id",
                    stage_id=stage_id,
                    message=f"Stage id '{stage_id}' is duplicated.",
                )
            )


def _validate_stage(
    *,
    stage: WorkflowStageSpec,
    index: int,
    stage_index_by_id: dict[str, int],
    diagnostics: list[WorkflowSpecDiagnostic],
) -> None:
    stage_id = _clean_text(stage.stage_id)

    if not _clean_text(stage.agent_type):
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="stage_agent_missing",
                field=f"stages[{index}].agent_type",
                stage_id=stage_id or None,
                message=f"Stage '{stage_id or index}' must declare an agent_type.",
            )
        )

    if not _clean_text(stage.display_name):
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="stage_display_name_missing",
                field=f"stages[{index}].display_name",
                stage_id=stage_id or None,
                severity="warning",
                message=f"Stage '{stage_id or index}' has no display_name.",
            )
        )

    if int(stage.timeout_minutes or 0) < 1:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="stage_timeout_invalid",
                field=f"stages[{index}].timeout_minutes",
                stage_id=stage_id or None,
                message=f"Stage '{stage_id or index}' must have timeout_minutes >= 1.",
            )
        )

    if stage.gate == "condition":
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="condition_gate_has_no_expression",
                field=f"stages[{index}].gate",
                stage_id=stage_id or None,
                severity="warning",
                message=(
                    f"Stage '{stage_id or index}' uses gate=condition, but "
                    "workflow_spec v1 has no condition expression schema."
                ),
            )
        )

    _validate_delivery(stage=stage, index=index, diagnostics=diagnostics)
    _validate_rollback(
        stage=stage,
        index=index,
        stage_index_by_id=stage_index_by_id,
        diagnostics=diagnostics,
    )
    _validate_skills(stage=stage, index=index, diagnostics=diagnostics)


def _validate_delivery(
    *,
    stage: WorkflowStageSpec,
    index: int,
    diagnostics: list[WorkflowSpecDiagnostic],
) -> None:
    stage_id = _clean_text(stage.stage_id)
    expected_artifacts = [
        _clean_text(item)
        for item in list(stage.delivery.expected_artifacts or [])
        if _clean_text(item)
    ]
    if stage.delivery.required and not expected_artifacts:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="required_delivery_has_no_artifacts",
                field=f"stages[{index}].delivery.expected_artifacts",
                stage_id=stage_id or None,
                message=(
                    f"Stage '{stage_id or index}' marks delivery as required "
                    "but declares no expected artifacts."
                ),
            )
        )


def _validate_rollback(
    *,
    stage: WorkflowStageSpec,
    index: int,
    stage_index_by_id: dict[str, int],
    diagnostics: list[WorkflowSpecDiagnostic],
) -> None:
    stage_id = _clean_text(stage.stage_id)
    target_stage_name = _clean_text(stage.rollback.target_stage_name)

    if not stage.rollback.enabled:
        if target_stage_name:
            diagnostics.append(
                WorkflowSpecDiagnostic(
                    code="rollback_target_without_policy",
                    field=f"stages[{index}].rollback.target_stage_name",
                    stage_id=stage_id or None,
                    severity="warning",
                    message=(
                        f"Stage '{stage_id or index}' declares a rollback target "
                        "while rollback is disabled."
                    ),
                )
            )
        return

    if int(stage.rollback.max_attempts or 0) < 1:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="rollback_attempts_invalid",
                field=f"stages[{index}].rollback.max_attempts",
                stage_id=stage_id or None,
                message=f"Stage '{stage_id or index}' rollback requires max_attempts >= 1.",
            )
        )

    if not target_stage_name:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="rollback_target_missing",
                field=f"stages[{index}].rollback.target_stage_name",
                stage_id=stage_id or None,
                message=f"Stage '{stage_id or index}' rollback requires a target stage.",
            )
        )
        return

    target_index = stage_index_by_id.get(target_stage_name)
    if target_index is None:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="rollback_target_unknown",
                field=f"stages[{index}].rollback.target_stage_name",
                stage_id=stage_id or None,
                message=f"Rollback target stage '{target_stage_name}' does not exist.",
            )
        )
        return

    if target_stage_name == stage_id:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="rollback_target_self",
                field=f"stages[{index}].rollback.target_stage_name",
                stage_id=stage_id or None,
                message=f"Stage '{stage_id}' cannot roll back to itself.",
            )
        )
        return

    if target_index >= index:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="rollback_target_not_predecessor",
                field=f"stages[{index}].rollback.target_stage_name",
                stage_id=stage_id or None,
                message=(
                    f"Stage '{stage_id}' rollback target '{target_stage_name}' "
                    "must appear before the source stage in workflow_spec v1."
                ),
            )
        )


def _validate_skills(
    *,
    stage: WorkflowStageSpec,
    index: int,
    diagnostics: list[WorkflowSpecDiagnostic],
) -> None:
    stage_id = _clean_text(stage.stage_id)
    active = {_clean_text(item) for item in list(stage.skills.active or []) if _clean_text(item)}
    hint_only = {
        _clean_text(item)
        for item in list(stage.skills.hint_only or [])
        if _clean_text(item)
    }
    overlap = sorted(active & hint_only)
    if overlap:
        diagnostics.append(
            WorkflowSpecDiagnostic(
                code="skill_declared_active_and_hint_only",
                field=f"stages[{index}].skills",
                stage_id=stage_id or None,
                severity="warning",
                message=(
                    f"Stage '{stage_id or index}' declares skills as both active "
                    f"and hint-only: {', '.join(overlap)}."
                ),
            )
        )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
