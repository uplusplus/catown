# -*- coding: utf-8 -*-
"""
Run Shell Tool — execute workspace-scoped shell commands with timeout and output caps.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from typing import Any, Awaitable, Callable

from .base import BaseTool
from .file_operations import get_active_workspace
from services.run_shell_processes import (
    build_tracked_run_shell_result,
    create_tracked_run_shell_handle,
    launch_tracked_run_shell,
    wait_for_tracked_run_shell,
)
from services.tool_execution_preferences import (
    build_run_shell_timeout_preference_key,
    prefers_wait_forever,
)
from services.tool_governance import build_structured_tool_result


DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 50000
TAIL_PROGRESS_INTERVAL_SECONDS = 2.0
TAIL_PROGRESS_MAX_CHARS = 4000
TAIL_PROGRESS_IDLE_FINAL_WAIT_SECONDS = 0.25


class RunShellTool(BaseTool):
    """Tool for executing real shell commands inside the active workspace."""

    name = "run_shell"
    description = (
        "Run a shell command inside the active workspace. "
        "Supports workspace-scoped cwd selection, time limits, and stdout/stderr capture. "
        "Use for git, test runners, build commands, and other real shell workflows."
    )

    def __init__(self, workspace: str | None = None):
        self.workspace = os.path.realpath(workspace or os.environ.get("CATOWN_WORKSPACE", os.getcwd()))

    async def execute(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        project_id: int | None = None,
        chatroom_id: int | None = None,
        task_run_id: int | None = None,
        client_turn_id: str | None = None,
        tool_call_id: str | None = None,
        turn: int | None = None,
        agent_name: str | None = None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        **kwargs,
    ) -> str:
        if progress_callback is not None:
            return await self._execute_with_progress(
                command,
                cwd,
                timeout_seconds,
                project_id=project_id,
                chatroom_id=chatroom_id,
                task_run_id=task_run_id,
                client_turn_id=client_turn_id,
                tool_call_id=tool_call_id,
                turn=turn,
                agent_name=agent_name,
                progress_callback=progress_callback,
            )
        return await self._execute_sync(
            command,
            cwd,
            timeout_seconds,
            project_id,
            chatroom_id,
            task_run_id,
            client_turn_id,
            tool_call_id,
            turn,
            agent_name,
        )

    async def _execute_with_progress(
        self,
        command: str,
        cwd: str,
        timeout_seconds: int,
        *,
        project_id: int | None = None,
        chatroom_id: int | None = None,
        task_run_id: int | None = None,
        client_turn_id: str | None = None,
        tool_call_id: str | None = None,
        turn: int | None = None,
        agent_name: str | None = None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str | dict[str, object]:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return "[Run Shell] Error: command is required."

        working_dir = self._resolve_working_directory(cwd)
        if not self._is_safe_path(working_dir):
            return f"[Run Shell] Error: Access denied. Working directory outside workspace: '{cwd}'"

        timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
        shell_cmd = self._shell_invocation(normalized_command)
        if not shell_cmd:
            return "[Run Shell] Error: No supported shell found on this system."

        preference_key = build_run_shell_timeout_preference_key(normalized_command, cwd)
        wait_forever = False
        from models.database import SessionLocal
        db = SessionLocal()
        try:
            wait_forever = prefers_wait_forever(
                db,
                tool_name=self.name,
                preference_key=preference_key,
                project_id=project_id,
                chatroom_id=chatroom_id,
            )
        finally:
            db.close()

        if not wait_forever:
            handle = create_tracked_run_shell_handle(
                command=normalized_command,
                cwd=working_dir,
                timeout_seconds=timeout,
                chatroom_id=chatroom_id,
                project_id=project_id,
                task_run_id=task_run_id,
                client_turn_id=client_turn_id,
                tool_call_id=tool_call_id,
                turn=turn,
                agent_name=agent_name,
            )
            launch_tracked_run_shell(handle)
            return await wait_for_tracked_run_shell(
                handle,
                progress_callback=progress_callback,
                timeout_seconds=timeout,
                progress_interval_seconds=TAIL_PROGRESS_INTERVAL_SECONDS,
                tail_chars=TAIL_PROGRESS_MAX_CHARS,
                result_chars=MAX_OUTPUT_CHARS,
            )

        handle = create_tracked_run_shell_handle(
            command=normalized_command,
            cwd=working_dir,
            timeout_seconds=timeout,
            chatroom_id=chatroom_id,
            project_id=project_id,
            task_run_id=task_run_id,
            client_turn_id=client_turn_id,
            tool_call_id=tool_call_id,
            turn=turn,
            agent_name=agent_name,
        )
        launch_tracked_run_shell(handle)
        return await wait_for_tracked_run_shell(
            handle,
            progress_callback=progress_callback,
            timeout_seconds=None,
            progress_interval_seconds=TAIL_PROGRESS_INTERVAL_SECONDS,
            tail_chars=TAIL_PROGRESS_MAX_CHARS,
            result_chars=MAX_OUTPUT_CHARS,
        )

    async def _execute_sync(
        self,
        command: str,
        cwd: str,
        timeout_seconds: int,
        project_id: int | None = None,
        chatroom_id: int | None = None,
        task_run_id: int | None = None,
        client_turn_id: str | None = None,
        tool_call_id: str | None = None,
        turn: int | None = None,
        agent_name: str | None = None,
    ) -> str | dict[str, object]:
        return await self._execute_with_progress(
            command,
            cwd,
            timeout_seconds,
            project_id=project_id,
            chatroom_id=chatroom_id,
            task_run_id=task_run_id,
            client_turn_id=client_turn_id,
            tool_call_id=tool_call_id,
            turn=turn,
            agent_name=agent_name,
            progress_callback=None,
        )

    def _resolve_working_directory(self, cwd: str) -> str:
        base_workspace = os.path.realpath(get_active_workspace() or self.workspace)
        requested = str(cwd or ".").strip() or "."
        if os.path.isabs(requested):
            return os.path.realpath(requested)
        return os.path.realpath(os.path.join(base_workspace, requested))

    def _is_safe_path(self, path: str) -> bool:
        workspace = os.path.realpath(get_active_workspace() or self.workspace)
        try:
            return os.path.commonpath([workspace, path]) == workspace
        except Exception:
            return False

    @staticmethod
    def _shell_invocation(command: str) -> list[str] | None:
        if os.name == "nt":
            powershell = shutil.which("powershell") or shutil.which("powershell.exe")
            if powershell:
                return [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ]
            comspec = os.environ.get("COMSPEC") or shutil.which("cmd")
            return [comspec or "cmd.exe", "/d", "/s", "/c", command]

        shell = os.environ.get("SHELL")
        if shell and os.path.isfile(shell) and os.access(shell, os.X_OK):
            return [shell, "-lc", command]
        for candidate in ("bash", "sh"):
            shell_path = shutil.which(candidate)
            if shell_path:
                return [shell_path, "-lc", command]
        return None

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute inside the workspace.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the active workspace (default: current workspace root).",
                    "default": ".",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": f"Execution timeout in seconds (1-{MAX_TIMEOUT_SECONDS}, default: {DEFAULT_TIMEOUT_SECONDS}).",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
            },
            "required": ["command"],
        }
