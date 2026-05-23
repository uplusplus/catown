# -*- coding: utf-8 -*-
"""Storage policy helpers for workspace-visible artifact classes."""

from __future__ import annotations

from dataclasses import dataclass

from services.artifact_history import classify_workspace_artifact_path


@dataclass(frozen=True)
class ArtifactStoragePolicy:
    artifact_class: str | None
    storage_mode: str


_VERSIONED_ARTIFACT_CLASSES = {"Test"}
_SINGLETON_SEMANTIC_ARTIFACT_CLASSES = {"ADR", "PRD", "Spec", "Release", "Report", "Doc", "Artifact"}


def resolve_artifact_storage_policy(path: str | None) -> ArtifactStoragePolicy:
    artifact_class = classify_workspace_artifact_path(path)
    if artifact_class in _VERSIONED_ARTIFACT_CLASSES:
        return ArtifactStoragePolicy(artifact_class=artifact_class, storage_mode="versioned-report")
    if artifact_class in _SINGLETON_SEMANTIC_ARTIFACT_CLASSES:
        return ArtifactStoragePolicy(artifact_class=artifact_class, storage_mode="singleton-semantic")
    return ArtifactStoragePolicy(artifact_class=artifact_class, storage_mode="non-artifact")
