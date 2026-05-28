# -*- coding: utf-8 -*-
"""Runner policy checks for schema-v1 artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.artifact_contracts import ArtifactContract, parse_artifact_contract
from services.artifact_output_path_policy import validate_artifact_output_path
from services.output_header_policy import validate_output_header
from services.runner_policy import (
    RunnerGovernancePolicy,
    StageRunnerPolicy,
    compile_workflow_run_policy,
    find_stage_policy,
)
from services.workflow_spec_contracts import WorkflowSpec


@dataclass(frozen=True)
class ArtifactContractPolicyViolation:
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
class ArtifactContractPolicyDecision:
    artifact_id: str
    artifact_type: str
    accepted: bool
    stage_name: str | None = None
    violations: list[ArtifactContractPolicyViolation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "accepted": self.accepted,
            "stage_name": self.stage_name,
            "violations": [violation.to_payload() for violation in self.violations],
            "metadata": dict(self.metadata),
        }


def validate_artifact_contract_for_workflow(
    *,
    contract: ArtifactContract | dict[str, Any],
    workflow_spec: WorkflowSpec,
    project_id: int | None = None,
    stage_name: str | None = None,
    stage_tool_packs: dict[str, dict[str, Any]] | None = None,
) -> ArtifactContractPolicyDecision:
    """Validate one artifact contract against a canonical workflow spec."""

    policy = compile_workflow_run_policy(
        workflow_spec=workflow_spec,
        project_id=project_id,
        stage_tool_packs=stage_tool_packs,
    )
    return validate_artifact_contract_for_policy(
        contract=contract,
        policy=policy,
        stage_name=stage_name,
    )


def validate_artifact_contract_for_policy(
    *,
    contract: ArtifactContract | dict[str, Any],
    policy: RunnerGovernancePolicy,
    stage_name: str | None = None,
) -> ArtifactContractPolicyDecision:
    """Validate one artifact contract against compiled runner governance policy."""

    parsed_contract = _ensure_artifact_contract(contract)
    violations: list[ArtifactContractPolicyViolation] = []
    target_stage_name = _clean_text(stage_name) or _clean_text(parsed_contract.producer.stage_name)
    target_stage = find_stage_policy(policy, target_stage_name)

    if not target_stage_name:
        violations.append(
            ArtifactContractPolicyViolation(
                code="missing_artifact_stage",
                field="producer.stage_name",
                message="Artifact contract validation requires a producer stage.",
            )
        )
    elif target_stage is None:
        violations.append(
            ArtifactContractPolicyViolation(
                code="unknown_artifact_stage",
                field="producer.stage_name",
                message=f"Artifact producer stage '{target_stage_name}' is not part of the workflow.",
            )
        )
    else:
        _validate_stage_delivery(
            contract=parsed_contract,
            stage=target_stage,
            violations=violations,
        )
        _validate_artifact_output_path(
            contract=parsed_contract,
            violations=violations,
            skip_when_delivery_failed=bool(violations),
        )
        _validate_required_output_header(
            contract=parsed_contract,
            violations=violations,
        )

    accepted = not any(violation.severity == "error" for violation in violations)
    return ArtifactContractPolicyDecision(
        artifact_id=_clean_text(parsed_contract.artifact_id),
        artifact_type=_clean_text(parsed_contract.artifact_type),
        accepted=accepted,
        stage_name=target_stage_name or None,
        violations=violations,
        metadata={
            "artifact_mode": _clean_text(parsed_contract.mode),
            "policy_source": policy.source,
            "pipeline_name": policy.pipeline_name,
            "stage_count": policy.stage_count,
        },
    )


def _validate_stage_delivery(
    *,
    contract: ArtifactContract,
    stage: StageRunnerPolicy,
    violations: list[ArtifactContractPolicyViolation],
) -> None:
    expected_artifacts = list(stage.delivery.expected_artifacts or [])
    if not expected_artifacts:
        return

    artifact_path = _artifact_path(contract)
    if not artifact_path:
        violations.append(
            ArtifactContractPolicyViolation(
                code="artifact_path_missing",
                field="file_path",
                message=(
                    f"Stage '{stage.stage_name}' declares expected artifacts, "
                    "but this artifact contract has no workspace path."
                ),
            )
        )
        return

    if not _matches_expected_artifact(artifact_path, expected_artifacts):
        violations.append(
            ArtifactContractPolicyViolation(
                code="artifact_not_expected",
                field="file_path",
                message=(
                    f"Artifact path '{artifact_path}' does not match expected artifacts "
                    f"for stage '{stage.stage_name}'."
                ),
            )
        )


def _artifact_path(contract: ArtifactContract) -> str:
    mode = _clean_text(contract.mode)
    if mode == "workspace_file":
        return _normalize_path(getattr(contract, "file_path", None))
    if mode == "workspace_directory":
        return _normalize_path(getattr(contract, "directory_path", None))
    if mode == "document":
        return _normalize_path(getattr(contract, "file_path", None))
    if mode == "structured_asset":
        return _normalize_path(getattr(contract, "storage_path", None))
    return ""


def _validate_required_output_header(
    *,
    contract: ArtifactContract,
    violations: list[ArtifactContractPolicyViolation],
) -> None:
    artifact_path = _artifact_path(contract)
    if not artifact_path:
        return

    content = None
    if hasattr(contract, "content_markdown"):
        content = getattr(contract, "content_markdown", None)
    if content is None:
        return
    decision = validate_output_header(path=artifact_path, content=content)
    if not decision.required:
        return
    if decision.accepted:
        return
    for violation in decision.violations:
        violations.append(
            ArtifactContractPolicyViolation(
                code=violation.code,
                field=violation.field,
                message=violation.message,
            )
        )


def _validate_artifact_output_path(
    *,
    contract: ArtifactContract,
    violations: list[ArtifactContractPolicyViolation],
    skip_when_delivery_failed: bool = False,
) -> None:
    if skip_when_delivery_failed:
        return

    artifact_path = _artifact_path(contract)
    if not artifact_path:
        return

    decision = validate_artifact_output_path(artifact_path)
    if decision.accepted:
        return

    for violation in decision.violations:
        violations.append(
            ArtifactContractPolicyViolation(
                code=violation.code,
                field=violation.field,
                message=violation.message,
            )
        )


def _ensure_artifact_contract(contract: ArtifactContract | dict[str, Any]) -> ArtifactContract:
    if isinstance(contract, dict):
        return parse_artifact_contract(contract)
    return contract


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
