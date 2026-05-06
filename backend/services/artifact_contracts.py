# -*- coding: utf-8 -*-
"""Schema v1 for bounded artifact contracts emitted or stored by Catown."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, TypeAdapter, model_validator


class ArtifactProducer(BaseModel):
    """Stable producer envelope for one artifact contract."""

    agent_name: str | None = None
    agent_type: str | None = None
    stage_name: str | None = None
    task_run_id: int | None = None
    pipeline_run_id: int | None = None
    pipeline_stage_id: int | None = None
    stage_run_id: int | None = None


class ArtifactContractBase(BaseModel):
    """Common envelope for all artifact contracts."""

    kind: Literal["artifact_contract"] = "artifact_contract"
    version: Literal[1] = 1
    artifact_id: str
    artifact_type: str
    title: str
    summary: str | None = None
    producer: ArtifactProducer = Field(default_factory=ArtifactProducer)
    tags: list[str] = Field(default_factory=list)
    source_input_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: str


class WorkspaceFileArtifactContract(ArtifactContractBase):
    mode: Literal["workspace_file"] = "workspace_file"
    file_path: str
    media_type: str | None = None


class WorkspaceDirectoryArtifactContract(ArtifactContractBase):
    mode: Literal["workspace_directory"] = "workspace_directory"
    directory_path: str


class DocumentArtifactContract(ArtifactContractBase):
    mode: Literal["document"] = "document"
    format: Literal["markdown", "json", "mixed"] = "markdown"
    file_path: str | None = None
    content_markdown: str | None = None
    content_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_document_has_content(self) -> "DocumentArtifactContract":
        if not self.file_path and not self.content_markdown and not self.content_json:
            raise ValueError("Document artifact requires file_path, content_markdown, or content_json.")
        return self


class StructuredAssetArtifactContract(ArtifactContractBase):
    mode: Literal["structured_asset"] = "structured_asset"
    schema_name: str
    storage_path: str | None = None
    content_json: dict[str, Any]
    content_markdown: str | None = None


ArtifactContract: TypeAlias = Annotated[
    WorkspaceFileArtifactContract
    | WorkspaceDirectoryArtifactContract
    | DocumentArtifactContract
    | StructuredAssetArtifactContract,
    Field(discriminator="mode"),
]

ARTIFACT_CONTRACT_ADAPTER = TypeAdapter(ArtifactContract)


def parse_artifact_contract(payload: Any) -> ArtifactContract:
    """Validate and parse one schema-v1 artifact contract payload."""

    return ARTIFACT_CONTRACT_ADAPTER.validate_python(payload)


def dump_artifact_contract(contract: ArtifactContract) -> dict[str, Any]:
    """Return the canonical JSON-compatible payload for one artifact contract."""

    return contract.model_dump(mode="json")
