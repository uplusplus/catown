# -*- coding: utf-8 -*-
"""
Run Shell Tool — execute workspace-scoped shell commands with timeout and output caps.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

from .base import BaseTool
from .file_operations import get_active_workspace


DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 50000


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
        **kwargs,
    ) -> str:
        return await asyncio.to_thread(self._execute_sync, command, cwd, timeout_seconds)

    def _execute_sync(self, command: str, cwd: str, timeout_seconds: int) -> str:
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

        try:
            result = subprocess.run(
                shell_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
                env={**os.environ, "TERM": "dumb"},
            )
        except subprocess.TimeoutExpired:
            return f"[Run Shell] Error: Execution timed out ({timeout}s limit)"
        except Exception as exc:
            return f"[Run Shell] Error: {exc}"

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        combined = "\n".join(part for part in [stdout, stderr] if part).strip()[:MAX_OUTPUT_CHARS]

        if result.returncode == 0:
            return (
                f"[Run Shell] Success:\n{combined}"
                if combined
                else "[Run Shell] Success (no output)"
            )

        if not combined:
            combined = f"Command exited with status {result.returncode}."
        return f"[Run Shell] Error (exit {result.returncode}):\n{combined}"

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
