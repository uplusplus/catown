# -*- coding: utf-8 -*-
"""Tools that ask the user to inspect or edit workspace files in chat."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from services.tool_governance import build_structured_tool_result
from tools.base import BaseTool
from tools.file_operations import get_active_workspace


def _normalize_relative_workspace_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        raise ValueError("path is required")
    if os.path.isabs(normalized):
        workspace = get_active_workspace()
        if not workspace:
            raise ValueError("absolute paths require an active workspace")
        real_workspace = os.path.realpath(workspace)
        real_path = os.path.realpath(os.path.expanduser(normalized))
        if os.path.commonpath([real_workspace, real_path]) != real_workspace:
            raise ValueError("path is outside the active workspace")
        normalized = os.path.relpath(real_path, real_workspace).replace("\\", "/")
    else:
        normalized = os.path.normpath(normalized).replace("\\", "/")
    if normalized in {"", "."} or normalized.startswith("../") or normalized == "..":
        raise ValueError("path must be inside the active workspace")
    return normalized


class OpenFileForUserTool(BaseTool):
    """Request an interactive chat file reader/editor for the user."""

    name = "open_file_for_user"
    description = (
        "Open a project workspace file for the user in the chat UI so they can inspect it and, "
        "when appropriate, edit and save it themselves. Use this when the user asks to view, "
        "review, confirm, or manually edit a file. This tool does not modify files by itself."
    )

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path to show to the user.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["read", "edit"],
                    "default": "read",
                    "description": "Initial user-facing mode. Use edit only when the user should change the file manually.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason shown in tool logs for why this file was opened.",
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        path: str,
        mode: str = "read",
        reason: str | None = None,
        project_id: int | None = None,
        **_: Any,
    ) -> Dict[str, Any]:
        try:
            relative_path = _normalize_relative_workspace_path(path)
        except ValueError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"Cannot open file for user: {exc}",
                success=False,
                status="failed",
            )

        resolved_mode = "edit" if str(mode or "").strip().lower() == "edit" else "read"
        payload = {
            "catown_interactive_tool": "file_reader_editor",
            "path": relative_path,
            "mode": resolved_mode,
            "reason": str(reason or "").strip(),
            "project_id": project_id,
        }
        return build_structured_tool_result(
            tool_name=self.name,
            result_text=json.dumps(payload, ensure_ascii=False),
            success=True,
            status="succeeded",
            metadata=payload,
        )
