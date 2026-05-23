# -*- coding: utf-8 -*-
"""Normalize test runner output into a runtime-owned contract."""

from __future__ import annotations

import re
from typing import Any


PYTEST_FAILURE_MARKERS = (
    "=================================== FAILURES ===================================",
    "==================================== ERRORS ====================================",
    "=========================== short test summary info ============================",
)

RUNNER_FAILURE_PATTERNS = (
    (r"\bcommand not found\b", "command_not_found"),
    (r"\bno such file or directory\b", "missing_path"),
    (r"\bpermission denied\b", "permission_denied"),
    (r"\bmodulenotfounderror\b", "dependency_missing"),
    (r"\bimporterror\b", "dependency_or_import_error"),
    (r"\berror collecting\b", "collection_error"),
    (r"\bcollected 0 items\b", "no_tests_collected"),
    (r"\bfixture ['\"][^'\"]+['\"] not found\b", "fixture_not_found"),
    (r"\bsyntaxerror\b", "test_script_syntax_error"),
    (r"\bindentationerror\b", "test_script_syntax_error"),
    (r"\binternalerror\b", "pytest_internal_error"),
)


def normalize_test_runner_result(
    *,
    command: Any = None,
    cwd: Any = None,
    status: Any = None,
    success: Any = None,
    result_text: Any = "",
    exit_code: Any = None,
) -> dict[str, Any]:
    """Return a stable test runner contract for tool output."""

    raw_output = str(result_text or "")
    counts = extract_pytest_counts(raw_output)
    runner_error_kind = _first_runner_error_kind(raw_output)
    parseable_pytest_result = _has_parseable_pytest_result(raw_output, counts)
    blocked_by_tool = str(status or "").strip().lower() in {"approval_blocked", "blocked"} or "[approval blocked]" in raw_output.lower()

    if blocked_by_tool:
        runner_status = "failed"
        test_status = "unknown"
        runner_error_kind = runner_error_kind or "tool_blocked"
    elif parseable_pytest_result:
        runner_status = "completed"
        test_status = _pytest_test_status(counts, bool(success), status)
    elif runner_error_kind:
        runner_status = "failed"
        test_status = "unknown"
    else:
        runner_status = "inconclusive"
        test_status = "unknown"

    environment_errors = []
    if runner_error_kind and runner_status != "completed":
        environment_errors.append(
            {
                "kind": runner_error_kind,
                "message": _compact_text(_first_matching_line(raw_output, runner_error_kind) or raw_output, limit=500),
            }
        )

    return {
        "kind": "test_runner_result",
        "version": 1,
        "framework": "pytest" if _looks_like_pytest(command, raw_output) else "unknown",
        "command": str(command or "").strip() or None,
        "cwd": str(cwd or "").strip() or None,
        "runner_status": runner_status,
        "test_status": test_status,
        "tool_status": str(status or "").strip() or None,
        "tool_success": bool(success),
        "exit_code": _coerce_int(exit_code),
        "counts": counts,
        "environment_errors": environment_errors,
        "failure_summary": extract_pytest_failure_summary(raw_output),
        "raw_output_preview": _compact_text(raw_output, limit=3000),
    }


def extract_pytest_counts(result_text: Any) -> dict[str, int | None]:
    lowered = str(result_text or "").lower()
    counts: dict[str, int | None] = {"passed": None, "failed": None, "skipped": None, "errors": None}
    for key, patterns in {
        "passed": [r"(\d+)\s+passed"],
        "failed": [r"(\d+)\s+failed"],
        "skipped": [r"(\d+)\s+skipped"],
        "errors": [r"(\d+)\s+errors?", r"(\d+)\s+error"],
    }.items():
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                counts[key] = int(match.group(1))
                break
    return counts


def extract_pytest_failure_summary(result_text: Any, *, limit: int = 1800) -> str:
    text = str(result_text or "")
    for marker in PYTEST_FAILURE_MARKERS:
        index = text.find(marker)
        if index >= 0:
            return _compact_text(text[index:], limit=limit)
    return _compact_text(text, limit=limit)


def is_valid_test_runner_result(contract: Any) -> bool:
    return (
        isinstance(contract, dict)
        and contract.get("kind") == "test_runner_result"
        and contract.get("runner_status") == "completed"
        and contract.get("test_status") in {"passed", "failed", "errored"}
    )


def _has_parseable_pytest_result(raw_output: str, counts: dict[str, int | None]) -> bool:
    if any(value is not None for value in counts.values()):
        return True
    lowered = raw_output.lower()
    if "[100%]" in lowered and any(marker in raw_output for marker in PYTEST_FAILURE_MARKERS):
        return True
    if "short test summary info" in lowered:
        return True
    return False


def _pytest_test_status(counts: dict[str, int | None], success: bool, status: Any) -> str:
    failed = counts.get("failed") or 0
    errors = counts.get("errors") or 0
    if errors:
        return "errored"
    if failed:
        return "failed"
    if success or str(status or "").strip().lower() in {"succeeded", "success", "ok"}:
        return "passed"
    return "failed"


def _first_runner_error_kind(raw_output: str) -> str | None:
    lowered = raw_output.lower()
    for pattern, kind in RUNNER_FAILURE_PATTERNS:
        if re.search(pattern, lowered):
            return kind
    return None


def _first_matching_line(raw_output: str, kind: str) -> str:
    pattern = next((pattern for pattern, candidate in RUNNER_FAILURE_PATTERNS if candidate == kind), "")
    if not pattern:
        return ""
    for line in raw_output.splitlines():
        if re.search(pattern, line.lower()):
            return line.strip()
    return ""


def _looks_like_pytest(command: Any, raw_output: str) -> bool:
    lowered_command = str(command or "").lower()
    lowered_output = raw_output.lower()
    return "pytest" in lowered_command or "short test summary info" in lowered_output or "collected " in lowered_output


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
