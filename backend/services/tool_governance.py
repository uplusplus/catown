# -*- coding: utf-8 -*-
"""Shared helpers for tool approval/sandbox blocking and result classification."""

from __future__ import annotations

import re
from typing import Any, Mapping


_TOOL_ERROR_RESULT_RE = re.compile(r"^\[[^\]]+\]\s+error:", re.IGNORECASE)
_APPROVAL_BLOCKED_RE = re.compile(
    r"(approval blocked|requires manual approval|requires approval|not authorized to use tool|unauthorized tool)",
    re.IGNORECASE,
)
_SANDBOX_BLOCKED_RE = re.compile(
    r"(sandbox blocked|access denied|outside workspace|path traversal|not allowed in sandbox|project metadata\)|directory is restricted)",
    re.IGNORECASE,
)


def build_blocked_tool_result(status: str, tool_name: str, reason: str) -> str:
    normalized_status = str(status or "").strip().lower()
    normalized_tool_name = str(tool_name or "tool").strip() or "tool"
    normalized_reason = str(reason or "").strip() or f"{normalized_tool_name} was blocked."
    if normalized_status == "approval_blocked":
        return f"[Approval Blocked] Tool '{normalized_tool_name}' was blocked: {normalized_reason}"
    if normalized_status == "sandbox_blocked":
        return f"[Sandbox Blocked] Tool '{normalized_tool_name}' was blocked: {normalized_reason}"
    return f"[Tool Blocked] Tool '{normalized_tool_name}' was blocked: {normalized_reason}"


def tool_requires_manual_approval(tool_policy: Mapping[str, Any] | None) -> bool:
    approval = dict((tool_policy or {}).get("approval") or {})
    return bool(approval.get("required")) and str(approval.get("kind") or "").strip().lower() == "manual"


def classify_tool_result(
    tool_name: str,
    result_text: Any,
    *,
    success: bool | None = None,
) -> dict[str, Any]:
    text = str(result_text or "").strip()
    normalized = text.lower()

    if text and _APPROVAL_BLOCKED_RE.search(text):
        return {
            "status": "approval_blocked",
            "success": False,
            "blocked": True,
            "blocked_kind": "approval",
            "blocked_reason": text,
            "tool_name": str(tool_name or "tool"),
        }

    if text and _SANDBOX_BLOCKED_RE.search(text):
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
    return bool(classify_tool_result("tool", result_text).get("success"))
