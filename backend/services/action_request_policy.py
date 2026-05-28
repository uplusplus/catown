# -*- coding: utf-8 -*-
"""Workflow-aware policy checks for schema-v1 action requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.action_request_contracts import (
    ActionRequest,
    dump_action_request,
    parse_action_request,
)
from services.artifact_output_path_policy import validate_artifact_output_path
from services.policy_decision_contracts import (
    build_policy_decision_event_payload,
    build_policy_decision_gate_result,
    dump_policy_decision,
    project_policy_decision,
)
from services.runner_policy import (
    RunnerGovernancePolicy,
    StageRunnerPolicy,
    compile_workflow_run_policy,
    find_stage_policy,
)
from services.workflow_spec_contracts import WorkflowSpec


@dataclass(frozen=True)
class ActionRequestPolicyViolation:
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
class ActionRequestPolicyDecision:
    request_id: str
    request_type: str
    accepted: bool
    stage_name: str | None = None
    violations: list[ActionRequestPolicyViolation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request_type": self.request_type,
            "accepted": self.accepted,
            "stage_name": self.stage_name,
            "violations": [violation.to_payload() for violation in self.violations],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ActionRequestPolicyResult:
    request: ActionRequest
    decision: ActionRequestPolicyDecision

    def to_payload(self) -> dict[str, Any]:
        policy_decision = project_policy_decision(self.decision)
        return {
            "request": dump_action_request(self.request),
            "decision": self.decision.to_payload(),
            "policy_decision": dump_policy_decision(policy_decision),
            "policy_decision_event_payload": build_policy_decision_event_payload(
                policy_decision
            ),
            "policy_decision_gate_result": build_policy_decision_gate_result(policy_decision),
        }


def validate_action_request_for_workflow(
    *,
    request: ActionRequest | dict[str, Any],
    workflow_spec: WorkflowSpec,
    project_id: int | None = None,
    stage_tool_packs: dict[str, dict[str, Any]] | None = None,
) -> ActionRequestPolicyDecision:
    """Validate one action request against a canonical workflow spec."""

    parsed_request = _ensure_action_request(request)
    policy = compile_workflow_run_policy(
        workflow_spec=workflow_spec,
        project_id=project_id,
        stage_tool_packs=stage_tool_packs,
    )
    return validate_action_request_for_policy(request=parsed_request, policy=policy)


def validate_and_project_action_request_for_workflow(
    *,
    request: ActionRequest | dict[str, Any],
    workflow_spec: WorkflowSpec,
    project_id: int | None = None,
    stage_tool_packs: dict[str, dict[str, Any]] | None = None,
) -> ActionRequestPolicyResult:
    """Validate an action request and project the policy decision."""

    parsed_request = _ensure_action_request(request)
    decision = validate_action_request_for_workflow(
        request=parsed_request,
        workflow_spec=workflow_spec,
        project_id=project_id,
        stage_tool_packs=stage_tool_packs,
    )
    return ActionRequestPolicyResult(request=parsed_request, decision=decision)


def validate_action_request_for_policy(
    *,
    request: ActionRequest | dict[str, Any],
    policy: RunnerGovernancePolicy,
) -> ActionRequestPolicyDecision:
    """Validate one action request against compiled runner governance policy."""

    parsed_request = _ensure_action_request(request)
    violations: list[ActionRequestPolicyViolation] = []
    source_stage_name = _clean_text(parsed_request.source.stage_name) or None
    source_stage = find_stage_policy(policy, source_stage_name)

    if source_stage_name and source_stage is None:
        violations.append(
            ActionRequestPolicyViolation(
                code="unknown_source_stage",
                field="source.stage_name",
                message=f"Source stage '{source_stage_name}' is not part of the workflow.",
            )
        )

    source_agent_type = _clean_text(parsed_request.source.agent_type)
    if (
        source_stage is not None
        and source_agent_type
        and source_agent_type != source_stage.agent_name
    ):
        violations.append(
            ActionRequestPolicyViolation(
                code="source_agent_mismatch",
                field="source.agent_type",
                message=(
                    f"Stage '{source_stage.stage_name}' is owned by agent "
                    f"'{source_stage.agent_name}', not '{source_agent_type}'."
                ),
            )
        )

    request_type = _clean_text(parsed_request.type)
    if request_type == "request_approval":
        _validate_request_approval(
            request=parsed_request,
            policy=policy,
            source_stage_name=source_stage_name,
            violations=violations,
        )
    elif request_type == "suggest_rollback":
        _validate_suggest_rollback(
            request=parsed_request,
            policy=policy,
            source_stage=source_stage,
            source_stage_name=source_stage_name,
            violations=violations,
        )
    elif request_type == "publish_artifact":
        _validate_publish_artifact(
            request=parsed_request,
            source_stage=source_stage,
            violations=violations,
        )
    elif request_type == "report_blocker":
        _validate_report_blocker(
            request=parsed_request,
            source_stage=source_stage,
            violations=violations,
        )
    elif request_type == "ask_agent":
        _validate_ask_agent(
            request=parsed_request,
            policy=policy,
            violations=violations,
        )

    accepted = not any(violation.severity == "error" for violation in violations)
    return ActionRequestPolicyDecision(
        request_id=_clean_text(parsed_request.request_id),
        request_type=request_type,
        accepted=accepted,
        stage_name=source_stage_name,
        violations=violations,
        metadata={
            "policy_source": policy.source,
            "pipeline_name": policy.pipeline_name,
            "stage_count": policy.stage_count,
        },
    )


def validate_and_project_action_request_for_policy(
    *,
    request: ActionRequest | dict[str, Any],
    policy: RunnerGovernancePolicy,
) -> ActionRequestPolicyResult:
    """Validate an action request against runner policy and project the decision."""

    parsed_request = _ensure_action_request(request)
    decision = validate_action_request_for_policy(
        request=parsed_request,
        policy=policy,
    )
    return ActionRequestPolicyResult(request=parsed_request, decision=decision)


def _validate_request_approval(
    *,
    request: ActionRequest,
    policy: RunnerGovernancePolicy,
    source_stage_name: str | None,
    violations: list[ActionRequestPolicyViolation],
) -> None:
    payload = request.payload
    target_kind = _clean_text(getattr(payload, "target_kind", None))
    if target_kind not in {"pipeline_gate", "stage_gate"}:
        return

    target_stage_name = _clean_text(getattr(payload, "target_name", None)) or source_stage_name
    if not target_stage_name:
        violations.append(
            ActionRequestPolicyViolation(
                code="missing_gate_stage",
                field="payload.target_name",
                message="Stage-gate approval requests must identify a workflow stage.",
            )
        )
        return

    target_stage = find_stage_policy(policy, target_stage_name)
    if target_stage is None:
        violations.append(
            ActionRequestPolicyViolation(
                code="unknown_gate_stage",
                field="payload.target_name",
                message=f"Gate target stage '{target_stage_name}' is not part of the workflow.",
            )
        )
        return

    if target_stage.approval.kind not in {"manual", "condition"}:
        violations.append(
            ActionRequestPolicyViolation(
                code="stage_gate_not_configured",
                field="payload.target_name",
                message=(
                    f"Stage '{target_stage.stage_name}' has gate "
                    f"'{target_stage.approval.kind}', so it does not require gate approval."
                ),
            )
        )


def _validate_suggest_rollback(
    *,
    request: ActionRequest,
    policy: RunnerGovernancePolicy,
    source_stage: StageRunnerPolicy | None,
    source_stage_name: str | None,
    violations: list[ActionRequestPolicyViolation],
) -> None:
    payload = request.payload
    target_stage_name = _clean_text(getattr(payload, "target_stage_name", None))
    if not source_stage_name:
        violations.append(
            ActionRequestPolicyViolation(
                code="missing_source_stage",
                field="source.stage_name",
                message="Rollback suggestions must identify the source workflow stage.",
            )
        )
    if source_stage is None:
        return

    if not source_stage.rollback.enabled:
        violations.append(
            ActionRequestPolicyViolation(
                code="rollback_not_enabled",
                field="source.stage_name",
                message=f"Stage '{source_stage.stage_name}' has no rollback policy enabled.",
            )
        )

    target_stage = find_stage_policy(policy, target_stage_name)
    if target_stage is None:
        violations.append(
            ActionRequestPolicyViolation(
                code="unknown_rollback_target",
                field="payload.target_stage_name",
                message=f"Rollback target stage '{target_stage_name}' is not part of the workflow.",
            )
        )
        return

    configured_target = _clean_text(source_stage.rollback.target_stage)
    if configured_target and configured_target != target_stage.stage_name:
        violations.append(
            ActionRequestPolicyViolation(
                code="rollback_target_mismatch",
                field="payload.target_stage_name",
                message=(
                    f"Stage '{source_stage.stage_name}' may roll back to "
                    f"'{configured_target}', not '{target_stage.stage_name}'."
                ),
            )
        )


def _validate_publish_artifact(
    *,
    request: ActionRequest,
    source_stage: StageRunnerPolicy | None,
    violations: list[ActionRequestPolicyViolation],
) -> None:
    if source_stage is None:
        return

    expected_artifacts = list(source_stage.delivery.expected_artifacts or [])
    if not expected_artifacts:
        return

    payload = request.payload
    file_path = _clean_text(getattr(payload, "file_path", None))
    if not file_path:
        violations.append(
            ActionRequestPolicyViolation(
                code="artifact_path_missing",
                field="payload.file_path",
                severity="warning",
                message=(
                    f"Stage '{source_stage.stage_name}' declares expected artifacts, "
                    "but this artifact request does not include a file path."
                ),
            )
        )
        return

    if not _matches_expected_artifact(file_path, expected_artifacts):
        violations.append(
            ActionRequestPolicyViolation(
                code="artifact_not_expected",
                field="payload.file_path",
                message=(
                    f"Artifact path '{file_path}' does not match expected artifacts "
                    f"for stage '{source_stage.stage_name}'."
                ),
            )
        )
        return

    _append_artifact_path_policy_violations(
        file_path=file_path,
        violations=violations,
    )


def _validate_report_blocker(
    *,
    request: ActionRequest,
    source_stage: StageRunnerPolicy | None,
    violations: list[ActionRequestPolicyViolation],
) -> None:
    payload = request.payload
    if _clean_text(getattr(payload, "severity", None)) != "blocker":
        return
    if source_stage is not None and not source_stage.rollback.enabled:
        violations.append(
            ActionRequestPolicyViolation(
                code="blocker_without_rollback_policy",
                field="source.stage_name",
                severity="warning",
                message=(
                    f"Stage '{source_stage.stage_name}' can report blockers, "
                    "but the workflow has no rollback policy for that stage."
                ),
            )
        )


def _validate_ask_agent(
    *,
    request: ActionRequest,
    policy: RunnerGovernancePolicy,
    violations: list[ActionRequestPolicyViolation],
) -> None:
    payload = request.payload
    target_agent = _clean_text(getattr(payload, "target_agent_name", None)).lower()
    if not target_agent:
        return

    known_agents = {
        _clean_text(stage.agent_name).lower()
        for stage in policy.stages
        if _clean_text(stage.agent_name)
    }
    if target_agent not in known_agents:
        violations.append(
            ActionRequestPolicyViolation(
                code="unknown_target_agent",
                field="payload.target_agent_name",
                severity="warning",
                message=f"Target agent '{target_agent}' is not a stage owner in this workflow.",
            )
        )


def _append_artifact_path_policy_violations(
    *,
    file_path: str,
    violations: list[ActionRequestPolicyViolation],
) -> None:
    decision = validate_artifact_output_path(file_path)
    if decision.accepted:
        return
    for violation in decision.violations:
        violations.append(
            ActionRequestPolicyViolation(
                code=violation.code,
                field=f"payload.{violation.field}" if violation.field else "payload.file_path",
                message=violation.message,
            )
        )


def _ensure_action_request(request: ActionRequest | dict[str, Any]) -> ActionRequest:
    if isinstance(request, dict):
        return parse_action_request(request)
    return request


def _matches_expected_artifact(file_path: str, expected_artifacts: list[str]) -> bool:
    normalized_file_path = _normalize_path(file_path)
    file_basename = _basename(file_path)
    for expected_artifact in expected_artifacts:
        normalized_expected = _normalize_path(expected_artifact)
        if not normalized_expected:
            continue
        if normalized_file_path == normalized_expected:
            return True
        if file_basename and file_basename == _basename(normalized_expected):
            return True
        if normalized_expected.endswith("/") and normalized_file_path.startswith(
            normalized_expected
        ):
            return True
    return False


def _normalize_path(value: Any) -> str:
    return _clean_text(value).replace("\\", "/")


def _basename(value: Any) -> str:
    normalized = _normalize_path(value)
    return normalized.rsplit("/", 1)[-1] if normalized else ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
