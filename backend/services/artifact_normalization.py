# -*- coding: utf-8 -*-
"""Normalize existing persistence artifact shapes into artifact_contract v1."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from services.artifact_contracts import ArtifactContract, parse_artifact_contract


def compile_stage_artifact_to_contract(
    stage_artifact: Any,
    *,
    stage: Any | None = None,
    artifact_id: str | None = None,
) -> ArtifactContract:
    """Compile a pipeline StageArtifact-like object into an artifact contract."""

    file_path = _normalize_path(_read_field(stage_artifact, "file_path"))
    if not file_path:
        raise ValueError("StageArtifact normalization requires file_path.")

    raw_artifact_type = _clean_text(_read_field(stage_artifact, "artifact_type")).lower()
    is_directory = raw_artifact_type == "directory" or file_path.endswith("/")
    stage_obj = stage if stage is not None else _read_field(stage_artifact, "stage")
    run_obj = _read_field(stage_obj, "run")

    source_artifact_id = _read_field(stage_artifact, "id")
    source_stage_id = _read_field(stage_artifact, "stage_id")
    contract_id = artifact_id or _default_stage_artifact_id(
        source_artifact_id=source_artifact_id,
        source_stage_id=source_stage_id,
        file_path=file_path,
    )
    source_input_refs = []
    if source_artifact_id is not None:
        source_input_refs.append(f"stage_artifact:{source_artifact_id}")

    common_payload = {
        "kind": "artifact_contract",
        "version": 1,
        "artifact_id": contract_id,
        "artifact_type": _canonical_stage_artifact_type(raw_artifact_type, is_directory=is_directory),
        "title": _default_title(file_path),
        "summary": _optional_text(_read_field(stage_artifact, "summary")),
        "producer": {
            "agent_name": _optional_text(_read_field(stage_obj, "agent_name")),
            "stage_name": _optional_text(_read_field(stage_obj, "stage_name")),
            "task_run_id": _int_or_none(_read_field(run_obj, "task_run_id")),
            "pipeline_run_id": _int_or_none(
                _read_field(stage_obj, "run_id") or _read_field(run_obj, "id")
            ),
            "pipeline_stage_id": _int_or_none(_read_field(stage_obj, "id") or source_stage_id),
        },
        "source_input_refs": source_input_refs,
        "metadata": _stage_artifact_metadata(
            stage_artifact=stage_artifact,
            source_artifact_id=source_artifact_id,
            source_stage_id=source_stage_id,
            raw_artifact_type=raw_artifact_type,
        ),
    }

    if is_directory:
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "workspace_directory",
                "directory_path": file_path.rstrip("/") + "/",
            }
        )

    return parse_artifact_contract(
        {
            **common_payload,
            "mode": "workspace_file",
            "file_path": file_path,
            "media_type": _guess_media_type(file_path),
        }
    )


def compile_asset_to_contract(
    asset: Any,
    *,
    artifact_id: str | None = None,
) -> ArtifactContract:
    """Compile a project Asset-like object into an artifact contract."""

    asset_type = _clean_text(_read_field(asset, "asset_type"))
    if not asset_type:
        raise ValueError("Asset normalization requires asset_type.")

    content_json = _parse_json_object(_read_field(asset, "content_json"), field_name="content_json")
    content_markdown = _optional_text(_read_field(asset, "content_markdown"))
    storage_path = _optional_text(_normalize_path(_read_field(asset, "storage_path")))
    mode = _infer_asset_mode(
        asset_type=asset_type,
        content_json=content_json,
        content_markdown=content_markdown,
    )
    stage_run = _read_field(asset, "produced_by_stage_run")
    source_asset_id = _read_field(asset, "id")
    contract_id = artifact_id or _default_asset_artifact_id(
        source_asset_id=source_asset_id,
        project_id=_read_field(asset, "project_id"),
        title=_read_field(asset, "title"),
        asset_type=asset_type,
    )

    common_payload = {
        "kind": "artifact_contract",
        "version": 1,
        "artifact_id": contract_id,
        "artifact_type": asset_type,
        "title": _clean_text(_read_field(asset, "title")) or asset_type,
        "summary": _optional_text(_read_field(asset, "summary")),
        "producer": {
            "agent_name": _optional_text(_read_field(asset, "owner_agent")),
            "stage_name": _optional_text(_read_field(stage_run, "stage_type")),
            "stage_run_id": _int_or_none(
                _read_field(asset, "produced_by_stage_run_id") or _read_field(stage_run, "id")
            ),
        },
        "source_input_refs": _parse_json_list(
            _read_field(asset, "source_input_refs_json"),
            field_name="source_input_refs_json",
        ),
        "metadata": _asset_metadata(asset=asset, source_asset_id=source_asset_id),
    }

    if mode == "workspace_file":
        if not storage_path:
            raise ValueError("workspace_file Asset normalization requires storage_path.")
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "workspace_file",
                "file_path": storage_path,
                "media_type": _guess_media_type(storage_path),
            }
        )

    if mode == "workspace_directory":
        if not storage_path:
            raise ValueError("workspace_directory Asset normalization requires storage_path.")
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "workspace_directory",
                "directory_path": storage_path.rstrip("/") + "/",
            }
        )

    if mode == "document":
        return parse_artifact_contract(
            {
                **common_payload,
                "mode": "document",
                "format": _document_format(
                    content_markdown=content_markdown,
                    content_json=content_json,
                ),
                "file_path": storage_path,
                "content_markdown": content_markdown,
                "content_json": content_json,
            }
        )

    return parse_artifact_contract(
        {
            **common_payload,
            "mode": "structured_asset",
            "schema_name": asset_type,
            "storage_path": storage_path,
            "content_json": content_json,
            "content_markdown": content_markdown,
        }
    )


def _stage_artifact_metadata(
    *,
    stage_artifact: Any,
    source_artifact_id: Any,
    source_stage_id: Any,
    raw_artifact_type: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_model": "StageArtifact",
        "source_artifact_type": raw_artifact_type or None,
    }
    if source_artifact_id is not None:
        metadata["source_artifact_id"] = source_artifact_id
    if source_stage_id is not None:
        metadata["source_stage_id"] = source_stage_id

    created_at = _read_field(stage_artifact, "created_at")
    if created_at is not None:
        metadata["source_created_at"] = _serialize_datetime(created_at)

    return metadata


def _asset_metadata(*, asset: Any, source_asset_id: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_model": "Asset",
    }
    if source_asset_id is not None:
        metadata["source_asset_id"] = source_asset_id

    for field_name in (
        "project_id",
        "version",
        "status",
        "is_current",
        "supersedes_asset_id",
        "approval_decision_id",
    ):
        value = _read_field(asset, field_name)
        if value is not None:
            metadata[field_name] = value

    for field_name in ("created_at", "updated_at", "approved_at"):
        value = _read_field(asset, field_name)
        if value is not None:
            metadata[field_name] = _serialize_datetime(value)

    return metadata


def _canonical_stage_artifact_type(raw_artifact_type: str, *, is_directory: bool) -> str:
    if raw_artifact_type and raw_artifact_type not in {"file", "directory"}:
        return raw_artifact_type
    if is_directory:
        return "workspace.directory"
    return "workspace.file"


def _infer_asset_mode(
    *,
    asset_type: str,
    content_json: dict[str, Any],
    content_markdown: str | None,
) -> str:
    normalized_type = asset_type.lower()
    if normalized_type.startswith("workspace.directory"):
        return "workspace_directory"
    if normalized_type.startswith("workspace.file"):
        return "workspace_file"
    if normalized_type.startswith("document.") or content_markdown:
        return "document"
    if content_json:
        return "structured_asset"
    return "structured_asset"


def _document_format(*, content_markdown: str | None, content_json: dict[str, Any]) -> str:
    has_markdown = bool(_optional_text(content_markdown))
    has_json = bool(content_json)
    if has_markdown and has_json:
        return "mixed"
    if has_json:
        return "json"
    return "markdown"


def _default_stage_artifact_id(
    *,
    source_artifact_id: Any,
    source_stage_id: Any,
    file_path: str,
) -> str:
    if source_artifact_id is not None:
        return f"stage-artifact-{source_artifact_id}"

    slug = _slug_path(file_path)
    if source_stage_id is not None:
        return f"stage-artifact-{source_stage_id}-{slug}"
    return f"stage-artifact-{slug or 'artifact'}"


def _default_asset_artifact_id(
    *,
    source_asset_id: Any,
    project_id: Any,
    title: Any,
    asset_type: str,
) -> str:
    if source_asset_id is not None:
        return f"asset-{source_asset_id}"

    slug = _slug_path(_clean_text(title) or asset_type)
    if project_id is not None:
        return f"asset-{project_id}-{slug}"
    return f"asset-{slug or 'asset'}"


def _default_title(file_path: str) -> str:
    cleaned = file_path.rstrip("/")
    if not cleaned:
        return "Pipeline artifact"
    return cleaned.rsplit("/", maxsplit=1)[-1] or cleaned


def _guess_media_type(file_path: str) -> str | None:
    normalized = file_path.lower()
    if normalized.endswith(".md"):
        return "text/markdown"
    if normalized.endswith(".json"):
        return "application/json"
    if normalized.endswith(".txt"):
        return "text/plain"
    return None


def _read_field(value: Any, field_name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _normalize_path(value: Any) -> str:
    return _clean_text(value).replace("\\", "/")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Asset {field_name} must be a valid JSON object.") from exc
        if isinstance(parsed, dict):
            return dict(parsed)
    raise ValueError(f"Asset {field_name} must be a JSON object.")


def _parse_json_list(value: Any, *, field_name: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Asset {field_name} must be a valid JSON list.") from exc
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    raise ValueError(f"Asset {field_name} must be a JSON list.")


def _slug_path(file_path: str) -> str:
    return (
        file_path.strip("/")
        .replace("/", "-")
        .replace(" ", "-")
        .replace(":", "-")
        .replace(".", "-")
        or "artifact"
    )
