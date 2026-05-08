# -*- coding: utf-8 -*-
"""Shared helpers for tool approval/sandbox blocking and result classification."""

from __future__ import annotations

import re
import shlex
from typing import Any, Mapping


_TOOL_ERROR_RESULT_RE = re.compile(r"^\[[^\]]+\]\s+error:", re.IGNORECASE)
_APPROVAL_BLOCKED_RE = re.compile(
    r"^\[Approval Blocked\]\s+Tool\s+'[^']+'\s+was blocked:",
    re.IGNORECASE,
)
_SANDBOX_BLOCKED_RE = re.compile(
    r"^\[Sandbox Blocked\]\s+Tool\s+'[^']+'\s+was blocked:",
    re.IGNORECASE,
)
_SANDBOX_TOOL_ERROR_RE = re.compile(
    r"^\[[^\]]+\]\s+Error:\s+.*(access denied|outside workspace|path outside workspace|working directory outside workspace|path traversal|not allowed in sandbox|directory is restricted)",
    re.IGNORECASE,
)
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_RUN_SHELL_SAFE_COMMANDS = {
    "basename",
    "cat",
    "cut",
    "dirname",
    "echo",
    "file",
    "grep",
    "head",
    "ls",
    "nl",
    "printf",
    "pwd",
    "readlink",
    "realpath",
    "rg",
    "sort",
    "stat",
    "tail",
    "tr",
    "uniq",
    "wc",
    "which",
}
_RUN_SHELL_MUTATING_COMMANDS = {
    "bash",
    "bun",
    "chmod",
    "chown",
    "cmd",
    "cmd.exe",
    "cp",
    "curl",
    "docker",
    "fish",
    "git-lfs",
    "install",
    "make",
    "mkdir",
    "mv",
    "node",
    "npm",
    "perl",
    "pip",
    "pip3",
    "pnpm",
    "podman",
    "poetry",
    "powershell",
    "pwsh",
    "python",
    "python3",
    "rm",
    "rsync",
    "ruby",
    "scp",
    "sh",
    "ssh",
    "sudo",
    "touch",
    "uv",
    "wget",
    "yarn",
    "zsh",
}
_RUN_SHELL_SEGMENT_SEPARATORS = {"&&", "||", ";", "|"}
_RUN_SHELL_BRANCH_SAFE_FLAGS = {
    "-a",
    "-r",
    "-v",
    "-vv",
    "--all",
    "--remotes",
    "--verbose",
    "--list",
    "--show-current",
    "--color",
    "--no-color",
}


def build_blocked_tool_result(status: str, tool_name: str, reason: str) -> str:
    normalized_status = str(status or "").strip().lower()
    normalized_tool_name = str(tool_name or "tool").strip() or "tool"
    normalized_reason = str(reason or "").strip() or f"{normalized_tool_name} was blocked."
    if normalized_status == "approval_blocked":
        return f"[Approval Blocked] Tool '{normalized_tool_name}' was blocked: {normalized_reason}"
    if normalized_status == "sandbox_blocked":
        return f"[Sandbox Blocked] Tool '{normalized_tool_name}' was blocked: {normalized_reason}"
    return f"[Tool Blocked] Tool '{normalized_tool_name}' was blocked: {normalized_reason}"


def build_structured_tool_result(
    *,
    tool_name: str,
    result_text: Any,
    success: bool,
    status: str,
    blocked: bool = False,
    blocked_kind: str | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "__catown_tool_result__": True,
        "tool_name": str(tool_name or "tool"),
        "result": str(result_text or "(no output)"),
        "success": bool(success),
        "status": str(status or ("succeeded" if success else "failed")),
        "blocked": bool(blocked),
        "blocked_kind": blocked_kind,
        "blocked_reason": blocked_reason,
    }


def assess_run_shell_command(command: Any) -> dict[str, Any]:
    normalized_command = str(command or "").strip()
    if not normalized_command:
        return {
            "requires_approval": True,
            "reason": "Shell command is empty or malformed and must be reviewed before execution.",
        }

    if "$(" in normalized_command or "`" in normalized_command:
        return {
            "requires_approval": True,
            "reason": "Shell command substitution can hide side effects and requires explicit approval.",
        }

    try:
        lexer = shlex.shlex(normalized_command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        return {
            "requires_approval": True,
            "reason": f"Shell command could not be parsed safely ({exc}) and requires explicit approval.",
        }

    if not tokens:
        return {
            "requires_approval": True,
            "reason": "Shell command is empty or malformed and must be reviewed before execution.",
        }

    segments: list[list[str]] = []
    current_segment: list[str] = []

    for token in tokens:
        if any(marker in token for marker in (">", "<")):
            return {
                "requires_approval": True,
                "reason": "Shell redirection or here-doc operators can write files or hide side effects and require approval.",
            }
        if token == "&":
            return {
                "requires_approval": True,
                "reason": "Background shell jobs require explicit approval.",
            }
        if token in _RUN_SHELL_SEGMENT_SEPARATORS:
            if current_segment:
                segments.append(current_segment)
                current_segment = []
            continue
        current_segment.append(token)

    if current_segment:
        segments.append(current_segment)

    if not segments:
        return {
            "requires_approval": True,
            "reason": "Shell command is empty or malformed and must be reviewed before execution.",
        }

    for segment in segments:
        requires_approval, reason = _segment_requires_shell_approval(segment)
        if requires_approval:
            return {
                "requires_approval": True,
                "reason": reason or "Shell command may mutate the workspace or external systems and requires approval.",
            }

    return {"requires_approval": False, "reason": None}


def _segment_requires_shell_approval(segment_tokens: list[str]) -> tuple[bool, str | None]:
    if not segment_tokens:
        return False, None

    first = segment_tokens[0]
    if _ENV_ASSIGNMENT_RE.fullmatch(first):
        return True, "Shell environment assignments can alter command behavior and require explicit approval."

    command_name = first
    command_args = segment_tokens[1:]

    if command_name == "git":
        return _git_segment_requires_approval(command_args)

    if command_name == "find":
        for arg in command_args:
            if arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}:
                return True, f"`find {arg}` can mutate files or emit artifacts and requires approval."
        return False, None

    if command_name == "sed":
        for arg in command_args:
            if arg == "-i" or arg.startswith("-i") or arg.startswith("--in-place"):
                return True, "`sed -i` can rewrite files and requires approval."
        return False, None

    if command_name == "command":
        if command_args and command_args[0] in {"-v", "-V"}:
            return False, None
        return True, "`command` is only auto-approved for lookup flags (-v/-V)."

    if command_name in _RUN_SHELL_SAFE_COMMANDS:
        return False, None

    if command_name in _RUN_SHELL_MUTATING_COMMANDS:
        return True, f"Shell command `{command_name}` may mutate the workspace or external systems and requires approval."

    return True, f"Shell command `{command_name}` is not in the read-only allowlist and requires approval."


def _git_segment_requires_approval(args: list[str]) -> tuple[bool, str | None]:
    if not args:
        return False, None

    subcommand = args[0]
    rest = args[1:]

    if subcommand in {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "grep"}:
        return False, None

    if subcommand == "branch":
        if not rest:
            return False, None
        for arg in rest:
            if arg in _RUN_SHELL_BRANCH_SAFE_FLAGS or arg.startswith("--sort="):
                continue
            return True, "`git branch` with branch mutations requires explicit approval."
        return False, None

    if subcommand == "remote":
        if not rest or rest == ["-v"] or rest == ["--verbose"]:
            return False, None
        return True, "`git remote` can rewrite remotes or contact external systems and requires approval."

    if subcommand == "config":
        safe_flags = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--show-scope"}
        if any(flag in rest for flag in safe_flags):
            return False, None
        return True, "`git config` writes repository or user config unless limited to read-only flags."

    if subcommand == "stash":
        if rest and rest[0] == "list":
            return False, None
        return True, "`git stash` mutates repository state unless only listing entries."

    if subcommand == "tag":
        if not rest or rest[0] in {"-l", "--list"}:
            return False, None
        return True, "`git tag` can create or delete tags and requires approval."

    if subcommand == "submodule":
        if rest and rest[0] == "status":
            return False, None
        return True, "`git submodule` can mutate nested repositories and requires approval."

    return True, f"`git {subcommand}` is not in the read-only allowlist and requires approval."


def tool_manual_approval_reason(
    tool_policy: Mapping[str, Any] | None,
    *,
    tool_name: str | None = None,
    arguments: Mapping[str, Any] | None = None,
) -> str | None:
    approval = dict((tool_policy or {}).get("approval") or {})
    approval_kind = str(approval.get("kind") or "").strip().lower()
    approval_required = bool(approval.get("required"))
    default_notes = [str(note).strip() for note in list(approval.get("notes") or []) if str(note).strip()]
    default_reason = default_notes[0] if default_notes else "This tool requires manual approval before execution."
    normalized_tool_name = str(tool_name or "").strip().lower()

    if approval_kind == "manual":
        return default_reason if approval_required else None

    if approval_kind == "conditional":
        if normalized_tool_name == "run_shell":
            command = (arguments or {}).get("command")
            assessment = assess_run_shell_command(command)
            return assessment["reason"] if assessment.get("requires_approval") else None
        return default_reason if approval_required else None

    return None


def tool_requires_manual_approval(
    tool_policy: Mapping[str, Any] | None,
    *,
    tool_name: str | None = None,
    arguments: Mapping[str, Any] | None = None,
) -> bool:
    return tool_manual_approval_reason(tool_policy, tool_name=tool_name, arguments=arguments) is not None


def classify_tool_result(
    tool_name: str,
    result_text: Any,
    *,
    success: bool | None = None,
) -> dict[str, Any]:
    if isinstance(result_text, Mapping) and result_text.get("__catown_tool_result__") is True:
        return {
            "status": str(result_text.get("status") or ("succeeded" if result_text.get("success") else "failed")),
            "success": bool(result_text.get("success")),
            "blocked": bool(result_text.get("blocked")),
            "blocked_kind": result_text.get("blocked_kind"),
            "blocked_reason": result_text.get("blocked_reason"),
            "tool_name": str(result_text.get("tool_name") or tool_name or "tool"),
        }

    text = str(result_text or "").strip()
    normalized = text.lower()

    if text and _APPROVAL_BLOCKED_RE.match(text):
        return {
            "status": "approval_blocked",
            "success": False,
            "blocked": True,
            "blocked_kind": "approval",
            "blocked_reason": text,
            "tool_name": str(tool_name or "tool"),
        }

    if text and (_SANDBOX_BLOCKED_RE.match(text) or _SANDBOX_TOOL_ERROR_RE.match(text)):
        return {
            "status": "sandbox_blocked",
            "success": False,
            "blocked": True,
            "blocked_kind": "sandbox",
            "blocked_reason": text,
            "tool_name": str(tool_name or "tool"),
        }

    looks_like_error = False
    if text:
        looks_like_error = (
            normalized.startswith("error:")
            or normalized.startswith("error executing ")
            or normalized.startswith("tool error")
            or _TOOL_ERROR_RESULT_RE.match(text) is not None
        )

    if success is False or looks_like_error:
        return {
            "status": "failed",
            "success": False,
            "blocked": False,
            "blocked_kind": None,
            "blocked_reason": None,
            "tool_name": str(tool_name or "tool"),
        }

    return {
        "status": "succeeded",
        "success": True,
        "blocked": False,
        "blocked_kind": None,
        "blocked_reason": None,
        "tool_name": str(tool_name or "tool"),
    }


def tool_result_succeeded(result_text: Any) -> bool:
    if isinstance(result_text, Mapping) and result_text.get("__catown_tool_result__") is True:
        return bool(result_text.get("success"))
    return bool(classify_tool_result("tool", result_text).get("success"))
