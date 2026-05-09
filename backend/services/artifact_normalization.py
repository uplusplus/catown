# -*- coding: utf-8 -*-
"""Normalize existing persistence artifact shapes into artifact_contract v1."""

from __future__ import annotations

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


def _canonical_stage_artifact_type(raw_artifact_type: str, *, is_directory: bool) -> str:
    if raw_artifact_type and raw_artifact_type not in {"file", "directory"}:
        return raw_artifact_type
    if is_directory:
        return "workspace.directory"
    return "workspace.file"


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


def _slug_path(file_path: str) -> str:
    return (
        file_path.strip("/")
        .replace("/", "-")
        .replace(" ", "-")
        .replace(":", "-")
        .replace(".", "-")
        or "artifact"
    )
