# -*- coding: utf-8 -*-
"""Shared governance policy objects for runner-style execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApprovalPolicy:
    kind: str = "auto"
    required: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "required": self.required,
        }


@dataclass(frozen=True)
class DeliveryContract:
    expected_artifacts: list[str] = field(default_factory=list)
    required: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "expected_artifacts": list(self.expected_artifacts),
            "required": self.required,
        }


@dataclass(frozen=True)
class RollbackPolicy:
    enabled: bool = False
    max_attempts: int = 0
    target_stage: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_attempts": self.max_attempts,
            "target_stage": self.target_stage,
        }


@dataclass(frozen=True)
class StageRunnerPolicy:
    stage_name: str
    display_name: str
    agent_name: str
    stage_order: int
    stage_count: int
    is_terminal_stage: bool
    timeout_minutes: int
    context_prompt: str = ""
    active_skills: list[str] = field(default_factory=list)
    hint_only_skills: list[str] = field(default_factory=list)
    approval: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    delivery: DeliveryContract = field(default_factory=DeliveryContract)
    rollback: RollbackPolicy = field(default_factory=RollbackPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "display_name": self.display_name,
            "agent_name": self.agent_name,
            "stage_order": self.stage_order,
            "stage_count": self.stage_count,
            "is_terminal_stage": self.is_terminal_stage,
            "timeout_minutes": self.timeout_minutes,
            "context_prompt_preview": _trim(self.context_prompt, limit=500),
            "active_skills": list(self.active_skills),
            "hint_only_skills": list(self.hint_only_skills),
            "approval": self.approval.to_payload(),
            "delivery": self.delivery.to_payload(),
            "rollback": self.rollback.to_payload(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RunnerGovernancePolicy:
    mode: str
    source: str
    pipeline_name: str | None = None
    project_id: int | None = None
    stage_count: int = 0
    stages: list[StageRunnerPolicy] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "pipeline_name": self.pipeline_name,
            "project_id": self.project_id,
            "stage_count": self.stage_count,
            "stages": [stage.to_payload() for stage in self.stages],
            "metadata": dict(self.metadata),
        }


def compile_pipeline_stage_policy(
    *,
    stage_cfg: Any,
    stage_order: int,
    stage_count: int,
    tool_policy_pack: dict[str, Any] | None = None,
) -> StageRunnerPolicy:
    gate = _clean_text(getattr(stage_cfg, "gate", None)) or "auto"
    expected_artifacts = [str(item).strip() for item in list(getattr(stage_cfg, "expected_artifacts", []) or []) if str(item).strip()]
    rollback_on_blocker = bool(getattr(stage_cfg, "rollback_on_blocker", False))
    rollback_target = _clean_text(getattr(stage_cfg, "rollback_target", None)) or None

    return StageRunnerPolicy(
        stage_name=_clean_text(getattr(stage_cfg, "name", None)),
        display_name=_clean_text(getattr(stage_cfg, "display_name", None)) or _clean_text(getattr(stage_cfg, "name", None)),
        agent_name=_clean_text(getattr(stage_cfg, "agent", None)),
        stage_order=stage_order,
        stage_count=stage_count,
        is_terminal_stage=(stage_order == max(stage_count - 1, 0)),
        timeout_minutes=max(1, int(getattr(stage_cfg, "timeout_minutes", 30) or 30)),
        context_prompt=_clean_text(getattr(stage_cfg, "context_prompt", None)),
        active_skills=[str(item).strip() for item in list(getattr(stage_cfg, "active_skills", []) or []) if str(item).strip()],
        hint_only_skills=[str(item).strip() for item in list(getattr(stage_cfg, "hint_only_skills", []) or []) if str(item).strip()],
        approval=ApprovalPolicy(
            kind=gate,
            required=(gate == "manual"),
        ),
        delivery=DeliveryContract(
            expected_artifacts=expected_artifacts,
            required=bool(expected_artifacts),
        ),
        rollback=RollbackPolicy(
            enabled=rollback_on_blocker,
            max_attempts=max(0, int(getattr(stage_cfg, "max_rollback_count", 0) or 0)),
            target_stage=rollback_target,
        ),
        metadata=_stage_tool_metadata(tool_policy_pack),
    )


def compile_pipeline_run_policy(
    *,
    pipeline_name: str | None,
    project_id: int | None,
    stages: list[Any],
    stage_tool_packs: dict[str, dict[str, Any]] | None = None,
) -> RunnerGovernancePolicy:
    stage_policies = [
        compile_pipeline_stage_policy(
            stage_cfg=stage_cfg,
            stage_order=index,
            stage_count=len(stages),
            tool_policy_pack=(stage_tool_packs or {}).get(_clean_text(getattr(stage_cfg, "name", None))),
        )
        for index, stage_cfg in enumerate(stages)
    ]
    return RunnerGovernancePolicy(
        mode="pipeline_governance",
        source="pipeline_template",
        pipeline_name=_clean_text(pipeline_name) or None,
        project_id=project_id,
        stage_count=len(stage_policies),
        stages=stage_policies,
        metadata={
            "stage_tool_packs": {
                stage_name: _tool_pack_metadata(pack, include_policies=True)
                for stage_name, pack in sorted((stage_tool_packs or {}).items())
            },
        },
    )


def compile_single_agent_run_policy(
    *,
    mode: str,
    source: str,
    agent_name: str,
    project_id: int | None,
    tool_names: list[str] | None = None,
    tool_policy_pack: dict[str, Any] | None = None,
    streaming: bool = False,
    standalone: bool = False,
) -> RunnerGovernancePolicy:
    normalized_tools = [str(item).strip() for item in list(tool_names or []) if str(item).strip()]
    normalized_tool_pack = _normalize_tool_policy_pack(
        tool_names=normalized_tools,
        tool_policy_pack=tool_policy_pack,
    )
    stage_policy = StageRunnerPolicy(
        stage_name="primary_turn",
        display_name="Primary Turn",
        agent_name=_clean_text(agent_name) or "assistant",
        stage_order=0,
        stage_count=1,
        is_terminal_stage=True,
        timeout_minutes=1,
        metadata={
            "streaming": streaming,
            "standalone": standalone,
            **_stage_tool_metadata(normalized_tool_pack),
        },
    )
    return RunnerGovernancePolicy(
        mode=_clean_text(mode) or "single_agent_runtime",
        source=_clean_text(source) or "chat_runtime",
        project_id=project_id,
        stage_count=1,
        stages=[stage_policy],
        metadata={
            "streaming": streaming,
            "standalone": standalone,
            **_tool_pack_metadata(normalized_tool_pack, include_policies=True),
            "project_bound": project_id is not None,
        },
    )


def compile_orchestration_run_policy(
    *,
    mode: str,
    source: str,
    project_id: int | None,
    steps: list[Any],
    sidecar_agent_types: list[str] | None = None,
    tool_names: list[str] | None = None,
    tool_policy_pack: dict[str, Any] | None = None,
    streaming: bool = False,
) -> RunnerGovernancePolicy:
    normalized_sidecars = [str(item).strip() for item in list(sidecar_agent_types or []) if str(item).strip()]
    normalized_tools = [str(item).strip() for item in list(tool_names or []) if str(item).strip()]
    normalized_tool_pack = _normalize_tool_policy_pack(
        tool_names=normalized_tools,
        tool_policy_pack=tool_policy_pack,
    )
    stage_policies = [
        StageRunnerPolicy(
            stage_name=_clean_text(getattr(step, "step_id", None)) or f"step-{index}",
            display_name=_clean_text(getattr(step, "agent_name", None)) or _clean_text(getattr(step, "requested_name", None)) or f"Step {index}",
            agent_name=_clean_text(getattr(step, "agent_name", None)) or _clean_text(getattr(step, "requested_name", None)) or "agent",
            stage_order=max(0, int(getattr(step, "position", index) or index) - 1),
            stage_count=len(steps),
            is_terminal_stage=(index == len(steps)),
            timeout_minutes=1,
            metadata={
                "requested_name": _clean_text(getattr(step, "requested_name", None)) or None,
                "agent_id": getattr(step, "agent_id", None),
                "agent_type": _clean_text(getattr(step, "agent_type", None)) or None,
                "dispatch_kind": _clean_text(getattr(step, "dispatch_kind", None)) or "blocking",
                "wait_for_step_id": _clean_text(getattr(step, "wait_for_step_id", None)) or None,
                "attached_to_step_id": _clean_text(getattr(step, "attached_to_step_id", None)) or None,
                "source": _clean_text(getattr(step, "source", None)) or "runtime",
                "streaming": streaming,
                **_stage_tool_metadata(normalized_tool_pack),
            },
        )
        for index, step in enumerate(steps, start=1)
    ]
    return RunnerGovernancePolicy(
        mode=_clean_text(mode) or "orchestration_runtime",
        source=_clean_text(source) or "orchestration_scheduler",
        project_id=project_id,
        stage_count=len(stage_policies),
        stages=stage_policies,
        metadata={
            "streaming": streaming,
            "sidecar_agent_types": normalized_sidecars,
            **_tool_pack_metadata(normalized_tool_pack, include_policies=True),
            "blocking_step_count": sum(
                1 for stage in stage_policies if stage.metadata.get("dispatch_kind") == "blocking"
            ),
            "sidecar_step_count": sum(
                1 for stage in stage_policies if stage.metadata.get("dispatch_kind") == "sidecar"
            ),
            "project_bound": project_id is not None,
        },
    )


def find_stage_policy(
    policy: RunnerGovernancePolicy | None,
    stage_name: str | None,
) -> StageRunnerPolicy | None:
    if policy is None:
        return None
    normalized_stage_name = _clean_text(stage_name)
    for stage_policy in policy.stages:
        if stage_policy.stage_name == normalized_stage_name:
            return stage_policy
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _trim(value: Any, *, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _normalize_tool_policy_pack(
    *,
    tool_names: list[str] | None,
    tool_policy_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_names = [str(item).strip() for item in list(tool_names or []) if str(item).strip()]
    pack = dict(tool_policy_pack or {})
    if not pack.get("tool_names"):
        pack["tool_names"] = normalized_names
    if "tool_policies" not in pack:
        pack["tool_policies"] = []
    if "tool_policy_summary" not in pack:
        pack["tool_policy_summary"] = {"tool_count": len(pack.get("tool_names") or [])}
    return pack


def _stage_tool_metadata(tool_policy_pack: dict[str, Any] | None) -> dict[str, Any]:
    pack = _normalize_tool_policy_pack(tool_names=[], tool_policy_pack=tool_policy_pack)
    return {
        "tool_names": list(pack.get("tool_names") or []),
        "tool_policy_summary": dict(pack.get("tool_policy_summary") or {}),
    }


def _tool_pack_metadata(
    tool_policy_pack: dict[str, Any] | None,
    *,
    include_policies: bool,
) -> dict[str, Any]:
    pack = _normalize_tool_policy_pack(tool_names=[], tool_policy_pack=tool_policy_pack)
    payload = {
        "tool_names": list(pack.get("tool_names") or []),
        "tool_policy_summary": dict(pack.get("tool_policy_summary") or {}),
    }
    if include_policies:
        payload["tool_policies"] = list(pack.get("tool_policies") or [])
    return payload
