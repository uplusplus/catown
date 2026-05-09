# -*- coding: utf-8 -*-
"""
Run Shell Tool — execute workspace-scoped shell commands with timeout and output caps.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Awaitable, Callable

from .base import BaseTool
from .file_operations import get_active_workspace
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
                progress_callback=progress_callback,
            )
        return await asyncio.to_thread(
            self._execute_sync,
            command,
            cwd,
            timeout_seconds,
            project_id,
            chatroom_id,
        )

    async def _execute_with_progress(
        self,
        command: str,
        cwd: str,
        timeout_seconds: int,
        *,
        project_id: int | None = None,
        chatroom_id: int | None = None,
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

        process = subprocess.Popen(
            shell_cmd,
            cwd=working_dir,
            env={**os.environ, "TERM": "dumb"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        loop = asyncio.get_running_loop()
        collected_lines: list[str] = []
        recent_lines: list[str] = []
        recent_changed = False
        recent_lock = threading.Lock()
        stop_reader = False

        def reader() -> None:
            nonlocal recent_changed
            stream = process.stdout
            if stream is None:
                return
            while not stop_reader:
                line = stream.readline()
                if line == "":
                    break
                with recent_lock:
                    collected_lines.append(line)
                    recent_lines.append(line)
                    while sum(len(part) for part in recent_lines) > TAIL_PROGRESS_MAX_CHARS and recent_lines:
                        recent_lines.pop(0)
                    recent_changed = True

        reader_thread = threading.Thread(target=reader, name="run-shell-progress-reader", daemon=True)
        reader_thread.start()
        start_time = time.monotonic()
        last_emitted_snapshot = ""

        async def maybe_emit_progress(force: bool = False) -> None:
            nonlocal recent_changed, last_emitted_snapshot
            if progress_callback is None:
                return
            with recent_lock:
                if not force and not recent_changed:
                    return
                snapshot = "".join(recent_lines).strip()
                recent_changed = False
            if not snapshot or snapshot == last_emitted_snapshot:
                return
            last_emitted_snapshot = snapshot
            await progress_callback({
                "tail_output": snapshot[-TAIL_PROGRESS_MAX_CHARS:],
                "duration_ms": int((time.monotonic() - start_time) * 1000),
                "pid": process.pid,
            })

        try:
            if wait_forever:
                while process.poll() is None:
                    await asyncio.sleep(TAIL_PROGRESS_INTERVAL_SECONDS)
                    await maybe_emit_progress()
            else:
                deadline = start_time + timeout
                while process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill()
                        await asyncio.to_thread(reader_thread.join, 1.0)
                        await maybe_emit_progress(force=True)
                        result_text = (
                            f"[Run Shell] Timed out after {timeout}s. "
                            "Waiting for user confirmation to continue without a timeout."
                        )
                        return build_structured_tool_result(
                            tool_name=self.name,
                            result_text=result_text,
                            success=False,
                            status="timeout_waiting",
                            blocked=True,
                            blocked_kind="timeout",
                            blocked_reason=result_text,
                        )
                    await asyncio.sleep(min(TAIL_PROGRESS_INTERVAL_SECONDS, max(0.1, remaining)))
                    await maybe_emit_progress()
        finally:
            stop_reader = True

        await asyncio.sleep(TAIL_PROGRESS_IDLE_FINAL_WAIT_SECONDS)
        await asyncio.to_thread(reader_thread.join, 1.0)
        await maybe_emit_progress(force=True)

        combined = "".join(collected_lines).strip()[:MAX_OUTPUT_CHARS]
        returncode = process.returncode if process.returncode is not None else 1

        if returncode == 0:
            return (
                f"[Run Shell] Success:\n{combined}"
                if combined
                else "[Run Shell] Success (no output)"
            )

        if not combined:
            combined = f"Command exited with status {returncode}."
        return f"[Run Shell] Error (exit {returncode}):\n{combined}"

    def _execute_sync(
        self,
        command: str,
        cwd: str,
        timeout_seconds: int,
        project_id: int | None = None,
        chatroom_id: int | None = None,
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

        run_kwargs = {
            "capture_output": True,
            "text": True,
            "cwd": working_dir,
            "env": {**os.environ, "TERM": "dumb"},
        }
        if not wait_forever:
            run_kwargs["timeout"] = timeout

        try:
            result = subprocess.run(
                shell_cmd,
                **run_kwargs,
            )
        except subprocess.TimeoutExpired:
            result_text = (
                f"[Run Shell] Timed out after {timeout}s. "
                "Waiting for user confirmation to continue without a timeout."
            )
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=result_text,
                success=False,
                status="timeout_waiting",
                blocked=True,
                blocked_kind="timeout",
                blocked_reason=result_text,
            )
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
