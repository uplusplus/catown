# -*- coding: utf-8 -*-
"""Helpers for preserving history when artifact-like files are overwritten."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


def normalize_workspace_artifact_path(path: str | Path | None) -> str:
    if path is None:
        return ""
    return str(path).replace("\\", "/").strip()


def classify_workspace_artifact_path(path: str | Path | None) -> str | None:
    normalized = normalize_workspace_artifact_path(path).lower()
    if not normalized or normalized in {".catown", ".catown/"} or normalized.startswith(".catown/"):
        return None
    if re.match(r"^@[\w.-]+\b", normalized):
        return None
    if re.search(r"(^|/)adr[-_./]|\badr[-_ ]?\d+|\barchitecture[-_ ]decision", normalized):
        return "ADR"
    if re.search(r"\bprd\b|product[-_ ]requirements?|requirements?[-_ ]doc", normalized):
        return "PRD"
    if re.search(r"\btech[-_ ]?spec\b|\bspecification\b|\bspec\b|design[-_ ]doc|proposal", normalized):
        return "Spec"
    if re.search(r"(^|/)reports/tests(/|$)|test[-_ ]?(plan|report|result|summary)|qa[-_ ]?report|verification", normalized):
        return "Test"
    if re.search(r"\breport\b|audit|review", normalized):
        return "Report"
    if re.search(r"changelog|change[-_ ]?log|release[-_ ]?notes?", normalized):
        return "Release"
    if re.search(r"readme|docs?/", normalized):
        return "Doc"
    if "artifact" in normalized:
        return "Artifact"
    if re.search(r"\.(md|mdx|pdf|docx?)$", normalized) and re.search(
        r"plan|summary|guide|notes|decision|migration|deploy",
        normalized,
    ):
        return "Doc"
    return None


def archive_workspace_artifact_snapshot(
    workspace_path: str | Path,
    artifact_path: str | Path,
    *,
    next_content: str | None = None,
) -> str | None:
    workspace = Path(workspace_path).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        return None

    target = _resolve_workspace_target(workspace, artifact_path)
    if target is None or not target.exists() or not target.is_file():
        return None

    relative_path = target.relative_to(workspace).as_posix()
    if classify_workspace_artifact_path(relative_path) is None:
        return None

    if next_content is not None:
        try:
            if target.read_text(encoding="utf-8") == next_content:
                return None
        except UnicodeDecodeError:
            pass

    relative_parent = Path(relative_path).parent
    archive_dir = workspace / ".catown" / "artifact-history"
    if str(relative_parent) not in {"", "."}:
        archive_dir = archive_dir / relative_parent
    archive_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = archive_dir / f"{timestamp}--{Path(relative_path).name}"
    counter = 1
    while archive_path.exists():
        archive_path = archive_dir / f"{timestamp}-{counter}--{Path(relative_path).name}"
        counter += 1

    shutil.copy2(target, archive_path)
    return archive_path.relative_to(workspace).as_posix()


def _resolve_workspace_target(workspace: Path, artifact_path: str | Path) -> Path | None:
    normalized = normalize_workspace_artifact_path(artifact_path)
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        return None
    return target
