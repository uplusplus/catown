# -*- coding: utf-8 -*-
"""Canonical directory validation for workspace-visible artifact outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from services.artifact_history import normalize_workspace_artifact_path
from services.artifact_storage_policy import resolve_artifact_storage_policy

_TIMESTAMPED_ARTIFACT_FILENAME_RE = re.compile(
    r"^\d{8}T\d{12}Z(?:--[a-z0-9][a-z0-9-]*)+\.[A-Za-z0-9]+$"
)


@dataclass(frozen=True)
class ArtifactOutputPathPolicyViolation:
    code: str
    message: str
    field: str | None = "file_path"


@dataclass(frozen=True)
class ArtifactOutputPathPolicyDecision:
    accepted: bool
    artifact_class: str | None
    storage_mode: str
    canonical_directories: tuple[str, ...] = ()
    violations: list[ArtifactOutputPathPolicyViolation] = field(default_factory=list)


def validate_artifact_output_path(path: str | Path | None) -> ArtifactOutputPathPolicyDecision:
    normalized_path = normalize_workspace_artifact_path(path)
    storage_policy = resolve_artifact_storage_policy(normalized_path)
    violations: list[ArtifactOutputPathPolicyViolation] = []
    canonical_directories = tuple(storage_policy.canonical_directories or ())

    if not normalized_path or not canonical_directories:
        return ArtifactOutputPathPolicyDecision(
            accepted=True,
            artifact_class=storage_policy.artifact_class,
            storage_mode=storage_policy.storage_mode,
            canonical_directories=canonical_directories,
            violations=[],
        )

    if not any(_matches_canonical_directory(normalized_path, directory) for directory in canonical_directories):
        allowed = ", ".join(canonical_directories)
        violations.append(
            ArtifactOutputPathPolicyViolation(
                code="artifact_path_noncanonical_directory",
                message=(
                    f"Artifact path '{normalized_path}' must be stored under one of: {allowed}."
                ),
            )
        )

    if (
        storage_policy.storage_mode == "versioned-report"
        and not normalized_path.endswith("/")
        and not _TIMESTAMPED_ARTIFACT_FILENAME_RE.match(Path(normalized_path).name)
    ):
        violations.append(
            ArtifactOutputPathPolicyViolation(
                code="artifact_path_timestamp_required",
                message=(
                    "Versioned report outputs must use a timestamped filename like "
                    "'reports/tests/<timestamp>--<subject>.md'."
                ),
            )
        )

    return ArtifactOutputPathPolicyDecision(
        accepted=not violations,
        artifact_class=storage_policy.artifact_class,
        storage_mode=storage_policy.storage_mode,
        canonical_directories=canonical_directories,
        violations=violations,
    )


def format_artifact_output_path_failure(
    *,
    path: str | Path | None,
    decision: ArtifactOutputPathPolicyDecision,
) -> str:
    normalized_path = normalize_workspace_artifact_path(path)
    details = "; ".join(violation.message for violation in decision.violations)
    return f"Artifact output path validation failed for '{normalized_path}'. {details}"


def _matches_canonical_directory(path: str, directory: str) -> bool:
    normalized_directory = normalize_workspace_artifact_path(directory).rstrip("/") + "/"
    normalized_path = normalize_workspace_artifact_path(path)
    if not normalized_path:
        return False
    if normalized_path == normalized_directory.rstrip("/"):
        return True
    return normalized_path.startswith(normalized_directory)
