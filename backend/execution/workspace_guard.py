# -*- coding: utf-8 -*-
"""Reusable workspace/path safety helpers for execution runtimes."""
from pathlib import Path
import os


def ensure_workspace_path(workspace_path: str | None, run_id: int) -> Path:
    """Resolve and create the workspace directory for an execution run."""
    workspace = Path(workspace_path) if workspace_path else Path("data") / "workspaces" / f"run_{run_id}"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def validate_workspace_target(workspace: Path, target: Path, allow_catown: bool = False) -> str | None:
    """Block traversal and protected metadata access outside the execution workspace."""
    try:
        real_target = target.resolve()
        real_workspace = workspace.resolve()
    except (OSError, RuntimeError) as exc:
        return f"Error: path resolution failed: {exc}"

    try:
        real_target.relative_to(real_workspace)
    except ValueError:
        return "Error: path traversal detected (access outside workspace)"

    if not allow_catown:
        try:
            rel = real_target.relative_to(real_workspace)
            if str(rel) == ".catown" or str(rel).startswith(".catown" + os.sep):
                return "Error: access to .catown/ directory is restricted (project metadata)"
        except ValueError:
            pass

    return None


def is_catown_protected(workspace: Path, target: Path) -> bool:
    """Check whether a path points into the protected .catown metadata directory."""
    try:
        rel = target.relative_to(workspace.resolve())
    except ValueError:
        return False
    return str(rel) == ".catown" or str(rel).startswith(".catown" + os.sep)
