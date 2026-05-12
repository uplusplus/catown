# -*- coding: utf-8 -*-
"""Persistent tracking for long-running run_shell processes."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from config import settings
from services.tool_governance import build_structured_tool_result


DEFAULT_PROGRESS_INTERVAL_SECONDS = 2.0
DEFAULT_TAIL_CHARS = 4000
DEFAULT_RESULT_CHARS = 50000
DEFAULT_LOG_MAX_BYTES = 1024 * 1024
LOG_READ_CHUNK_BYTES = 8192
LOG_TRIM_EXTRA_BYTES = 256 * 1024


def run_shell_process_state_dir() -> Path:
    path = settings.STATE_DIR / "run_shell_processes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_tracked_run_shell_handle(
    *,
    command: str,
    cwd: str,
    timeout_seconds: int,
    chatroom_id: int | None = None,
    project_id: int | None = None,
    task_run_id: int | None = None,
    client_turn_id: str | None = None,
    tool_call_id: str | None = None,
    turn: int | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    token = uuid.uuid4().hex
    root = run_shell_process_state_dir()
    state_path = root / f"{token}.json"
    log_path = root / f"{token}.log"
    exit_path = root / f"{token}.exit.json"
    created_at = datetime.now().isoformat()
    record = {
        "version": 1,
        "token": token,
        "tool_name": "run_shell",
        "command": str(command or ""),
        "cwd": str(cwd or "."),
        "timeout_seconds": int(timeout_seconds or 0),
        "chatroom_id": chatroom_id,
        "project_id": project_id,
        "task_run_id": task_run_id,
        "client_turn_id": client_turn_id,
        "tool_call_id": tool_call_id,
        "turn": turn,
        "agent_name": agent_name,
        "status": "created",
        "created_at": created_at,
        "updated_at": created_at,
        "state_path": str(state_path),
        "log_path": str(log_path),
        "exit_path": str(exit_path),
        "worker_pid": None,
        "pid": None,
        "pgid": None,
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "last_result_preview": None,
        "redirected_log_path": _detect_redirected_output_path(command, cwd),
    }
    _write_record(record)
    return _public_handle(record)


def load_tracked_run_shell_handle(handle: Any) -> dict[str, Any] | None:
    token = _extract_token(handle)
    if not token:
        return None
    path = run_shell_process_state_dir() / f"{token}.json"
    if not path.exists():
        return None
    try:
        record = _load_record(path)
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def launch_tracked_run_shell(handle: Any) -> dict[str, Any] | None:
    record = load_tracked_run_shell_handle(handle)
    if record is None:
        return None
    worker_script = Path(__file__).resolve().with_name("run_shell_worker.py")
    proc = subprocess.Popen(
        [sys.executable, str(worker_script), str(record["state_path"])],
        cwd=str(Path(record["cwd"]).resolve()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        text=False,
    )
    record["worker_pid"] = proc.pid
    record["status"] = "starting"
    record["updated_at"] = datetime.now().isoformat()
    _write_record(record)
    return _wait_for_process_boot(record["token"])


async def wait_for_tracked_run_shell(
    handle: Any,
    *,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    timeout_seconds: int | None = None,
    progress_interval_seconds: float = DEFAULT_PROGRESS_INTERVAL_SECONDS,
    tail_chars: int = DEFAULT_TAIL_CHARS,
    result_chars: int = DEFAULT_RESULT_CHARS,
) -> dict[str, Any]:
    record = load_tracked_run_shell_handle(handle)
    if record is None:
        return build_structured_tool_result(
            tool_name="run_shell",
            result_text="[Run Shell] Error: tracked process handle is missing.",
            success=False,
            status="failed",
            metadata={"tracked_process": _public_handle_from_handle(handle)},
        )

    last_snapshot = ""
    started = time.monotonic()
    deadline = None if timeout_seconds is None else started + max(1, int(timeout_seconds))

    while True:
        record = load_tracked_run_shell_handle(handle) or record
        snapshot = read_tracked_run_shell_tail(record, max_chars=tail_chars)
        if progress_callback is not None and snapshot and snapshot != last_snapshot:
            last_snapshot = snapshot
            await progress_callback(
                {
                    "tail_output": snapshot,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "pid": record.get("pid"),
                    "tracked_process": _public_handle(record),
                }
            )

        if tracked_run_shell_has_exit(record):
            if progress_callback is not None and snapshot:
                await progress_callback(
                    {
                        "tail_output": snapshot,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "pid": record.get("pid"),
                        "tracked_process": _public_handle(record),
                    }
                )
            return build_tracked_run_shell_result(record, max_chars=result_chars)

        if deadline is not None and time.monotonic() >= deadline:
            result_text = (
                f"[Run Shell] Timed out after {int(timeout_seconds or 0)}s. "
                "Waiting for user confirmation to continue without a timeout."
            )
            return build_structured_tool_result(
                tool_name="run_shell",
                result_text=result_text,
                success=False,
                status="timeout_waiting",
                blocked=True,
                blocked_kind="timeout",
                blocked_reason=result_text,
                metadata={"tracked_process": _public_handle(record)},
            )

        if not tracked_run_shell_is_active(record):
            await asyncio.sleep(0.25)
            record = load_tracked_run_shell_handle(handle) or record
            if tracked_run_shell_has_exit(record):
                return build_tracked_run_shell_result(record, max_chars=result_chars)
            if not tracked_run_shell_is_active(record):
                return build_structured_tool_result(
                    tool_name="run_shell",
                    result_text=_format_interrupted_result(record, max_chars=result_chars),
                    success=False,
                    status="interrupted",
                    metadata={"tracked_process": _public_handle(record)},
                )

        sleep_seconds = max(0.1, float(progress_interval_seconds))
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            sleep_seconds = min(sleep_seconds, max(0.1, remaining))
        await asyncio.sleep(sleep_seconds)


def tracked_run_shell_has_exit(record: dict[str, Any]) -> bool:
    exit_path = Path(str(record.get("exit_path") or "")).expanduser()
    return exit_path.exists()


def tracked_run_shell_is_active(record: dict[str, Any]) -> bool:
    for key in ("pid", "worker_pid"):
        pid = _coerce_int(record.get(key))
        if pid and _pid_alive(pid):
            return True
    return False


def terminate_tracked_run_shell(handle: Any) -> bool:
    record = load_tracked_run_shell_handle(handle)
    if record is None:
        return False
    pgid = _coerce_int(record.get("pgid"))
    pid = _coerce_int(record.get("pid"))
    worker_pid = _coerce_int(record.get("worker_pid"))
    killed = False
    if pgid:
        try:
            if hasattr(os, "killpg"):
                os.killpg(pgid, signal.SIGTERM)
            else:
                os.kill(pgid, signal.SIGTERM)
            killed = True
        except OSError:
            pass
    elif pid:
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except OSError:
            pass
    if worker_pid and _pid_alive(worker_pid):
        try:
            os.kill(worker_pid, signal.SIGTERM)
            killed = True
        except OSError:
            pass
    return killed


def list_tracked_run_shell_processes(
    *,
    limit: int = 120,
    tail_chars: int = 1200,
) -> list[dict[str, Any]]:
    root = run_shell_process_state_dir()
    candidates: list[tuple[tuple[str, str, str], dict[str, Any]]] = []

    for state_path in root.glob("*.json"):
        if state_path.name.endswith(".exit.json"):
            continue
        try:
            record = _load_record(state_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or not record.get("token"):
            continue

        sort_key = (
            str(record.get("created_at") or ""),
            str(record.get("updated_at") or ""),
            str(record.get("token") or ""),
        )
        candidates.append((sort_key, record))

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected_records = [record for _, record in candidates[: max(1, int(limit or 120))]]

    records: list[dict[str, Any]] = []
    include_output = tail_chars > 0
    for record in selected_records:
        has_exit = tracked_run_shell_has_exit(record)
        is_active = tracked_run_shell_is_active(record) and not has_exit
        status = str(record.get("status") or "").strip() or "unknown"
        if not has_exit and status in {"created", "starting", "launching_command", "unknown"}:
            status = "running"
        is_terminal = has_exit or status.lower() in {"completed", "failed", "cancelled", "interrupted"}

        entry = {
            **_public_handle(record),
            "id": record.get("token"),
            "tool_name": record.get("tool_name") or "run_shell",
            "command": record.get("command") or "",
            "cwd": record.get("cwd") or "",
            "timeout_seconds": record.get("timeout_seconds"),
            "chatroom_id": record.get("chatroom_id"),
            "project_id": record.get("project_id"),
            "turn": record.get("turn"),
            "agent_name": record.get("agent_name"),
            "status": status,
            "is_active": is_active,
            "is_terminal": is_terminal,
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "exit_code": record.get("exit_code"),
            "last_result_preview": record.get("last_result_preview") if include_output else None,
            "tail_output": read_tracked_run_shell_tail(record, max_chars=tail_chars) if include_output else "",
            "redirected_log_path": record.get("redirected_log_path"),
        }
        records.append(entry)

    return records


def read_tracked_run_shell_tail(record_or_handle: Any, *, max_chars: int = DEFAULT_TAIL_CHARS) -> str:
    record = record_or_handle if isinstance(record_or_handle, dict) else load_tracked_run_shell_handle(record_or_handle)
    if not isinstance(record, dict):
        return ""
    if max_chars <= 0:
        return ""
    log_path = _resolve_readable_tail_path(record)
    if log_path is None:
        return ""
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            read_bytes = min(size, max(4096, int(max_chars) * 4))
            log_file.seek(max(0, size - read_bytes))
            text = log_file.read(read_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def _resolve_readable_tail_path(record: dict[str, Any]) -> Path | None:
    log_path = Path(str(record.get("log_path") or "")).expanduser()
    if log_path.exists() and _path_has_content(log_path):
        return log_path

    redirected_log_path = Path(str(record.get("redirected_log_path") or "")).expanduser()
    if redirected_log_path.exists() and _safe_redirected_output_path(record, redirected_log_path):
        return redirected_log_path

    return log_path if log_path.exists() else None


def run_tracked_run_shell_worker(state_path: str) -> int:
    record = _load_record(Path(state_path))
    shell_cmd = _shell_invocation(str(record.get("command") or ""))
    if not shell_cmd:
        _write_exit(
            record,
            exit_code=127,
            error="No supported shell found on this system.",
        )
        return 127

    log_path = Path(str(record["log_path"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat()
    record["status"] = "launching_command"
    record["started_at"] = started_at
    record["updated_at"] = started_at
    _write_record(record)

    try:
        child = subprocess.Popen(
            shell_cmd,
            cwd=str(record.get("cwd") or "."),
            env={**os.environ, "TERM": "dumb"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=False,
            bufsize=0,
            start_new_session=True,
        )
    except Exception as exc:
        _append_bounded_log(log_path, f"[Run Shell] Error: {exc}\n".encode("utf-8", errors="replace"))
        _write_exit(record, exit_code=127, error=str(exc))
        return 127

    try:
        record = _load_record(Path(state_path))
        record["pid"] = child.pid
        record["pgid"] = _process_group_id(child.pid)
        record["status"] = "running"
        record["updated_at"] = datetime.now().isoformat()
        _write_record(record)

        if child.stdout is not None:
            while True:
                chunk = child.stdout.read(LOG_READ_CHUNK_BYTES)
                if not chunk:
                    break
                _append_bounded_log(log_path, chunk)

        returncode = child.wait()
    except Exception as exc:
        try:
            child.kill()
        except Exception:
            pass
        _append_bounded_log(log_path, f"[Run Shell] Worker error: {exc}\n".encode("utf-8", errors="replace"))
        _write_exit(record, exit_code=1, error=str(exc))
        return 1

    _write_exit(record, exit_code=returncode)
    return int(returncode or 0)


def _wait_for_process_boot(token: str, *, timeout_seconds: float = 1.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    latest = None
    while time.monotonic() < deadline:
        latest = load_tracked_run_shell_handle(token)
        if latest is None:
            break
        if latest.get("pid") or tracked_run_shell_has_exit(latest):
            return _public_handle(latest)
        time.sleep(0.05)
    return _public_handle(latest) if isinstance(latest, dict) else None


def _write_exit(record: dict[str, Any], *, exit_code: int, error: str | None = None) -> None:
    exit_payload = {
        "exit_code": int(exit_code),
        "finished_at": datetime.now().isoformat(),
    }
    if error:
        exit_payload["error"] = str(error)
    exit_path = Path(str(record["exit_path"]))
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    exit_path.write_text(json.dumps(exit_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    refreshed = _load_record(Path(record["state_path"]))
    refreshed["status"] = "completed" if int(exit_code) == 0 else "failed"
    refreshed["exit_code"] = int(exit_code)
    refreshed["finished_at"] = exit_payload["finished_at"]
    refreshed["updated_at"] = exit_payload["finished_at"]
    refreshed["last_result_preview"] = read_tracked_run_shell_tail(refreshed, max_chars=DEFAULT_TAIL_CHARS)
    _write_record(refreshed)


def _load_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_record(record: dict[str, Any]) -> None:
    path = Path(str(record["state_path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record["updated_at"] = datetime.now().isoformat()
    payload = json.dumps(record, ensure_ascii=False, indent=2)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)


def _append_bounded_log(log_path: Path, chunk: bytes, *, max_bytes: int = DEFAULT_LOG_MAX_BYTES) -> None:
    if not chunk:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = max(1, int(max_bytes or DEFAULT_LOG_MAX_BYTES))
    if len(chunk) >= max_bytes:
        log_path.write_bytes(chunk[-max_bytes:])
        return

    with log_path.open("ab") as log_file:
        log_file.write(chunk)

    try:
        current_size = log_path.stat().st_size
    except FileNotFoundError:
        return

    trim_extra_bytes = min(LOG_TRIM_EXTRA_BYTES, max(0, max_bytes // 4))
    if current_size <= max_bytes + trim_extra_bytes:
        return

    with log_path.open("rb") as log_file:
        log_file.seek(max(0, current_size - max_bytes))
        tail = log_file.read(max_bytes)
    log_path.write_bytes(tail)


def _path_has_content(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _detect_redirected_output_path(command: str, cwd: str) -> str | None:
    try:
        tokens = shlex.split(str(command or ""), posix=True)
    except ValueError:
        return None

    for index, token in enumerate(tokens):
        path_token: str | None = None
        if token in {">", "1>", ">>", "1>>"} and index + 1 < len(tokens):
            path_token = tokens[index + 1]
        elif token.startswith((">", "1>", ">>", "1>>")) and len(token.lstrip("1>")) > 0:
            path_token = token.lstrip("1>")

        if not path_token:
            continue
        if path_token.startswith("&"):
            continue
        path = Path(path_token)
        if not path.is_absolute():
            path = Path(str(cwd or ".")).expanduser() / path
        return str(path.expanduser().resolve())

    return None


def _safe_redirected_output_path(record: dict[str, Any], path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False

    allowed_roots = [Path(str(record.get("cwd") or ".")).expanduser().resolve(), Path("/tmp").resolve()]
    for root in allowed_roots:
        try:
            if os.path.commonpath([str(root), str(resolved)]) == str(root):
                return True
        except ValueError:
            continue
    return False


def _public_handle(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": record.get("token"),
        "pid": record.get("pid"),
        "worker_pid": record.get("worker_pid"),
        "pgid": record.get("pgid"),
        "state_path": record.get("state_path"),
        "log_path": record.get("log_path"),
        "exit_path": record.get("exit_path"),
        "status": record.get("status"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "task_run_id": record.get("task_run_id"),
        "client_turn_id": record.get("client_turn_id"),
        "tool_call_id": record.get("tool_call_id"),
    }


def _public_handle_from_handle(handle: Any) -> dict[str, Any]:
    if isinstance(handle, dict):
        if "token" in handle:
            return {"token": handle.get("token")}
    return {"token": _extract_token(handle)}


def _extract_token(handle: Any) -> str | None:
    if isinstance(handle, dict):
        token = str(handle.get("token") or "").strip()
        return token or None
    text = str(handle or "").strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_group_id(pid: int) -> int:
    if hasattr(os, "getpgid"):
        try:
            return int(os.getpgid(pid))
        except OSError:
            return int(pid)
    return int(pid)


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


def build_tracked_run_shell_result(record_or_handle: Any, *, max_chars: int = DEFAULT_RESULT_CHARS) -> dict[str, Any]:
    record = record_or_handle if isinstance(record_or_handle, dict) else load_tracked_run_shell_handle(record_or_handle)
    if not isinstance(record, dict):
        return build_structured_tool_result(
            tool_name="run_shell",
            result_text="[Run Shell] Error: tracked process state is missing.",
            success=False,
            status="failed",
            metadata={"tracked_process": _public_handle_from_handle(record_or_handle)},
        )

    exit_payload = _load_exit_payload(record)
    output = read_tracked_run_shell_tail(record, max_chars=max_chars)
    exit_code = None if exit_payload is None else _coerce_int(exit_payload.get("exit_code"))
    if exit_code is None and exit_payload is not None:
        try:
            exit_code = int(exit_payload.get("exit_code"))
        except (TypeError, ValueError):
            exit_code = 1
    if exit_code is None:
        return build_structured_tool_result(
            tool_name="run_shell",
            result_text=_format_interrupted_result(record, max_chars=max_chars),
            success=False,
            status="interrupted",
            metadata={"tracked_process": _public_handle(record)},
        )
    if exit_code == 0:
        result_text = f"[Run Shell] Success:\n{output}" if output else "[Run Shell] Success (no output)"
        return build_structured_tool_result(
            tool_name="run_shell",
            result_text=result_text,
            success=True,
            status="succeeded",
            metadata={"tracked_process": _public_handle(record)},
        )
    if not output:
        output = f"Command exited with status {exit_code}."
    return build_structured_tool_result(
        tool_name="run_shell",
        result_text=f"[Run Shell] Error (exit {exit_code}):\n{output}",
        success=False,
        status="failed",
        metadata={"tracked_process": _public_handle(record)},
    )


def _load_exit_payload(record: dict[str, Any]) -> dict[str, Any] | None:
    exit_path = Path(str(record.get("exit_path") or "")).expanduser()
    if not exit_path.exists():
        return None
    try:
        payload = json.loads(exit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _format_interrupted_result(record: dict[str, Any], *, max_chars: int) -> str:
    output = read_tracked_run_shell_tail(record, max_chars=max_chars)
    if output:
        return f"[Run Shell] Error: command stopped before completion.\n{output}"
    return "[Run Shell] Error: command stopped before completion."
