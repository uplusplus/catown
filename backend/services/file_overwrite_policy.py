# -*- coding: utf-8 -*-
"""Shared default-deny policy for overwriting existing workspace files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileOverwriteDecision:
    allowed: bool
    target_exists: bool
    requires_explicit_overwrite: bool
    reason: str | None = None


def decide_file_overwrite(
    target: str | Path,
    *,
    allow_overwrite: bool = False,
) -> FileOverwriteDecision:
    target_path = Path(target)
    target_exists = target_path.exists()
    requires_explicit_overwrite = target_exists

    if not target_exists:
        return FileOverwriteDecision(
            allowed=True,
            target_exists=False,
            requires_explicit_overwrite=False,
        )

    if allow_overwrite:
        return FileOverwriteDecision(
            allowed=True,
            target_exists=True,
            requires_explicit_overwrite=True,
        )

    normalized = target_path.as_posix()
    return FileOverwriteDecision(
        allowed=False,
        target_exists=True,
        requires_explicit_overwrite=True,
        reason=(
            f"Overwrite is disabled by default for existing file '{normalized}'. "
            "Pass allow_overwrite=true to replace it explicitly."
        ),
    )


def format_file_overwrite_failure(
    target: str | Path,
    decision: FileOverwriteDecision,
) -> str:
    if decision.reason:
        return decision.reason
    normalized = Path(target).as_posix()
    return (
        f"Overwrite is disabled by default for existing file '{normalized}'. "
        "Pass allow_overwrite=true to replace it explicitly."
    )
