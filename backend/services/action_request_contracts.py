# -*- coding: utf-8 -*-
"""Schema v1 for bounded action requests emitted by orchestration/worker agents."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, TypeAdapter


class ActionRequestSource(BaseModel):
    """Stable source envelope for one agent-issued action request."""

    agent_name: str
    agent_type: str | None = None
    stage_name: str | None = None
    task_run_id: int | None = None
    pipeline_run_id: int | None = None
    pipeline_stage_id: int | None = None
    turn_index: int | None = None


class ActionRequestBase(BaseModel):
    """Common envelope shared by all bounded action request variants."""

    kind: Literal["action_request"] = "action_request"
    version: Literal[1] = 1
    request_id: str
    type: str
    source: ActionRequestSource
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UseToolActionRequestPayload(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class AskAgentActionRequestPayload(BaseModel):
    target_agent_name: str
    message: str
    attached_artifact_refs: list[str] = Field(default_factory=list)


class RequestApprovalActionRequestPayload(BaseModel):
    queue_kind: Literal["approval", "escalation"] = "approval"
    target_kind: str
    target_name: str | None = None
    reason: str
    resume_supported: bool = False
    request_payload: dict[str, Any] = Field(default_factory=dict)


class ReportBlockerActionRequestPayload(BaseModel):
    severity: Literal["minor", "major", "blocker"] = "blocker"
    reason: str
    blocker_code: str | None = None
    suggested_resolution: str | None = None


class SuggestRollbackActionRequestPayload(BaseModel):
    target_stage_name: str
    reason: str
    blocker_code: str | None = None


class PublishArtifactActionRequestPayload(BaseModel):
    artifact_type: str
    title: str
    summary: str | None = None
    file_path: str | None = None
    content_markdown: str | None = None
    content_json: dict[str, Any] = Field(default_factory=dict)


class UseToolActionRequest(ActionRequestBase):
    type: Literal["use_tool"] = "use_tool"
    payload: UseToolActionRequestPayload


class AskAgentActionRequest(ActionRequestBase):
    type: Literal["ask_agent"] = "ask_agent"
    payload: AskAgentActionRequestPayload


class RequestApprovalActionRequest(ActionRequestBase):
    type: Literal["request_approval"] = "request_approval"
    payload: RequestApprovalActionRequestPayload


class ReportBlockerActionRequest(ActionRequestBase):
    type: Literal["report_blocker"] = "report_blocker"
    payload: ReportBlockerActionRequestPayload


class SuggestRollbackActionRequest(ActionRequestBase):
    type: Literal["suggest_rollback"] = "suggest_rollback"
    payload: SuggestRollbackActionRequestPayload


class PublishArtifactActionRequest(ActionRequestBase):
    type: Literal["publish_artifact"] = "publish_artifact"
    payload: PublishArtifactActionRequestPayload


ActionRequest: TypeAlias = Annotated[
    UseToolActionRequest
    | AskAgentActionRequest
    | RequestApprovalActionRequest
    | ReportBlockerActionRequest
    | SuggestRollbackActionRequest
    | PublishArtifactActionRequest,
    Field(discriminator="type"),
]

ACTION_REQUEST_ADAPTER = TypeAdapter(ActionRequest)


def parse_action_request(payload: Any) -> ActionRequest:
    """Validate and parse one schema-v1 action request payload."""

    return ACTION_REQUEST_ADAPTER.validate_python(payload)


def dump_action_request(request: ActionRequest) -> dict[str, Any]:
    """Return the canonical JSON-compatible payload for one action request."""

    return request.model_dump(mode="json")
