# -*- coding: utf-8 -*-
"""Bridges publish_artifact action requests into artifact_contract v1."""

from __future__ import annotations

from typing import Any, Literal

from services.action_request_contracts import ActionRequest, parse_action_request
from services.artifact_contracts import ArtifactContract, parse_artifact_contract

ArtifactPublicationMode = Literal[
    "workspace_file",
    "workspace_directory",
    "document",
    "structured_asset",
]


def compile_publish_artifact_request_to_contract(
    *,
    request: ActionRequest | dict[str, Any],
    artifact_id: str | None = None,
    mode: ArtifactPublicationMode | None = None,
) -> ArtifactContract:
    """Compile one publish_artifact action request into an artifact contract."""

    parsed_request = _ensure_action_request(request)
    if parsed_request.type != "publish_artifact":
        raise ValueError("Only publish_artifact action requests can compile into artifact contracts.")

    payload = parsed_request.payload
    selected_mode = mode or _infer_publication_mode(payload)
    common_payload = {
        "kind": "artifact_contract",
        "version": 1,
        "artifact_id": artifact_id or _default_artifact_id(parsed_request.request_id),
        "artifact_type": payload.artifact_type,
        "title": payload.title,
        "summary": payload.summary,
        "producer": {
            "agent_name": parsed_request.source.agent_name,
            "agent_type": parsed_request.source.agent_type,
            "stage_name": parsed_request.source.stage_name,
            "task_run_id": parsed_request.source.task_run_id,
            "pipeline_run_id": parsed_request.source.pipeline_run_id,
            "pipeline_stage_id": parsed_request.source.pipeline_stage_id,
        },
        "source_input_refs": [parsed_request.request_id],
        "metadata": {
            "source_action_request_id": parsed_request.request_id,
            "source_action_request_type": parsed_request.type,
        },
    }

    if selected_mode == "workspace_file":
        file_path = _require_file_path(payload.file_path, mode=selected_mode)
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "workspace_file",
                "file_path": file_path,
                "media_type": _guess_media_type(file_path),
            }
        )

    if selected_mode == "workspace_directory":
        directory_path = _require_file_path(payload.file_path, mode=selected_mode)
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "workspace_directory",
                "directory_path": directory_path.rstrip("/") + "/",
            }
        )

    if selected_mode == "structured_asset":
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "structured_asset",
                "schema_name": payload.artifact_type,
                "storage_path": payload.file_path,
                "content_json": dict(payload.content_json or {}),
                "content_markdown": payload.content_markdown,
            }
        )

    return parse_artifact_contract(
        {
            **common_payload,
            "mode": "document",
            "format": _document_format(
                content_markdown=payload.content_markdown,
                content_json=payload.content_json,
            ),
            "file_path": payload.file_path,
            "content_markdown": payload.content_markdown,
            "content_json": dict(payload.content_json or {}),
        }
    )


def _ensure_action_request(request: ActionRequest | dict[str, Any]) -> ActionRequest:
    if isinstance(request, dict):
        return parse_action_request(request)
    return request


def _infer_publication_mode(payload: Any) -> ArtifactPublicationMode:
    file_path = _clean_text(getattr(payload, "file_path", None))
    content_markdown = _clean_text(getattr(payload, "content_markdown", None))
    content_json = dict(getattr(payload, "content_json", {}) or {})
    artifact_type = _clean_text(getattr(payload, "artifact_type", None)).lower()

    if artifact_type.startswith("structured.") or artifact_type.startswith("asset."):
        return "structured_asset"
    if content_markdown or content_json:
        return "document"
    if file_path.endswith("/"):
        return "workspace_directory"
    return "workspace_file"


def _document_format(*, content_markdown: str | None, content_json: dict[str, Any]) -> str:
    has_markdown = bool(_clean_text(content_markdown))
    has_json = bool(content_json)
    if has_markdown and has_json:
        return "mixed"
    if has_json:
        return "json"
    return "markdown"


def _require_file_path(file_path: str | None, *, mode: str) -> str:
    cleaned = _clean_text(file_path)
    if not cleaned:
        raise ValueError(f"{mode} artifact publication requires file_path.")
    return cleaned


def _default_artifact_id(request_id: str) -> str:
    cleaned = _clean_text(request_id).replace(":", "-")
    return f"artifact-{cleaned or 'request'}"


def _guess_media_type(file_path: str) -> str | None:
    normalized = file_path.lower()
    if normalized.endswith(".md"):
        return "text/markdown"
    if normalized.endswith(".json"):
        return "application/json"
    if normalized.endswith(".txt"):
        return "text/plain"
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
