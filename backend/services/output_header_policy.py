# -*- coding: utf-8 -*-
"""Validation helpers for required output metadata headers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from services.artifact_history import (
    classify_workspace_artifact_path,
    normalize_workspace_artifact_path,
)

_DOC_EXTENSIONS = {
    ".md",
    ".mdx",
    ".rst",
}

_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".html",
    ".htm",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".m",
    ".mm",
    ".php",
    ".rb",
    ".swift",
    ".kt",
    ".kts",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".sql",
}

_DOCUMENT_ARTIFACT_CLASSES = {
    "ADR",
    "PRD",
    "Spec",
    "Test",
    "Report",
    "Release",
    "Doc",
    "Artifact",
}

_FIELD_PATTERNS = {
    "purpose": [
        re.compile(r"^(purpose|目的)\s*[:：]\s*\S+", re.IGNORECASE),
    ],
    "overview": [
        re.compile(r"^(overview|summary|概述)\s*[:：]\s*\S+", re.IGNORECASE),
    ],
    "author": [
        re.compile(r"^(author|作者)\s*[:：]\s*\S+", re.IGNORECASE),
    ],
    "created_at": [
        re.compile(
            r"^(created(?:\s*(?:at|time|datetime))?|创建(?:时间|日期|datetime)?)\s*[:：]\s*\S+",
            re.IGNORECASE,
        ),
    ],
    "modification_log": [
        re.compile(
            r"^(modification\s*log|change\s*log|revision\s*history|修改记录|修订记录)\s*[:：]?\s*\S*",
            re.IGNORECASE,
        ),
    ],
}

_TIMESTAMP_RE = re.compile(
    r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
_COMMENT_PREFIXES = (
    '"""',
    "'''",
    "<!--",
    "-->",
    "/*",
    "*/",
    "//",
    "#",
    "--",
    ";",
    "*",
)
_BULLET_PREFIXES = ("- ", "* ", "+ ")
_HEADER_LINE_WINDOW = 80


@dataclass(frozen=True)
class OutputHeaderPolicyViolation:
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class OutputHeaderPolicyDecision:
    required: bool
    accepted: bool
    target_kind: str | None = None
    violations: list[OutputHeaderPolicyViolation] = field(default_factory=list)


def output_header_required(path: str | Path | None) -> bool:
    return classify_output_header_target(path) is not None


def classify_output_header_target(path: str | Path | None) -> str | None:
    normalized = normalize_workspace_artifact_path(path)
    if not normalized:
        return None

    suffix = Path(normalized).suffix.lower()
    if suffix in _DOC_EXTENSIONS:
        return "document"
    if suffix in _CODE_EXTENSIONS:
        return "code"

    artifact_class = classify_workspace_artifact_path(normalized)
    if artifact_class in _DOCUMENT_ARTIFACT_CLASSES:
        return "document"
    return None


def output_header_guidance_text() -> str:
    return (
        "## Output Header Contract\n"
        "For every code or document file you create or overwrite, place a metadata header at the very top.\n"
        "Required fields:\n"
        "- Purpose\n"
        "- Overview\n"
        "- Author\n"
        "- Created At (datetime)\n"
        "- Modification Log (with at least one timestamped entry)\n"
        "Use normal Markdown lines for documents, and use the target language's comment syntax for code files.\n"
        "When editing an existing file, preserve the original Created At when possible and append a new Modification Log entry for the current change."
    )


def validate_output_header(
    *,
    path: str | Path | None,
    content: str | None,
) -> OutputHeaderPolicyDecision:
    target_kind = classify_output_header_target(path)
    if target_kind is None:
        return OutputHeaderPolicyDecision(required=False, accepted=True, target_kind=None)

    text = str(content or "")
    normalized_lines = _normalized_header_lines(text)
    violations: list[OutputHeaderPolicyViolation] = []
    line_indices: dict[str, int] = {}

    for field_name, patterns in _FIELD_PATTERNS.items():
        index = _find_matching_line_index(normalized_lines, patterns)
        if index is None:
            violations.append(
                OutputHeaderPolicyViolation(
                    code=f"output_header_{field_name}_missing",
                    field=field_name,
                    message=f"Missing required output header field '{field_name}'.",
                )
            )
            continue
        line_indices[field_name] = index

    created_index = line_indices.get("created_at")
    if created_index is not None and not _TIMESTAMP_RE.search(normalized_lines[created_index]):
        violations.append(
            OutputHeaderPolicyViolation(
                code="output_header_created_at_datetime_missing",
                field="created_at",
                message="Created At must include a concrete datetime.",
            )
        )

    modification_index = line_indices.get("modification_log")
    if modification_index is not None:
        log_window = "\n".join(normalized_lines[modification_index : modification_index + 8])
        if not _TIMESTAMP_RE.search(log_window):
            violations.append(
                OutputHeaderPolicyViolation(
                    code="output_header_modification_log_timestamp_missing",
                    field="modification_log",
                    message="Modification Log must include at least one timestamped entry.",
                )
            )

    return OutputHeaderPolicyDecision(
        required=True,
        accepted=not violations,
        target_kind=target_kind,
        violations=violations,
    )


def format_output_header_failure(
    *,
    path: str | Path | None,
    decision: OutputHeaderPolicyDecision,
) -> str:
    missing_bits = []
    for violation in decision.violations:
        if violation.field:
            missing_bits.append(violation.field)
        else:
            missing_bits.append(violation.code)
    detail = ", ".join(dict.fromkeys(missing_bits)) or "required header fields"
    return (
        f"Output header validation failed for '{normalize_workspace_artifact_path(path)}'. "
        f"Missing or invalid fields: {detail}. "
        "Required top-of-file fields: Purpose, Overview, Author, Created At, Modification Log."
    )


def _normalized_header_lines(content: str) -> list[str]:
    lines = []
    for raw_line in content.splitlines()[:_HEADER_LINE_WINDOW]:
        normalized = _normalize_header_line(raw_line)
        if normalized:
            lines.append(normalized)
    return lines


def _normalize_header_line(line: str) -> str:
    normalized = str(line or "").lstrip("\ufeff").strip()
    if not normalized:
        return ""

    changed = True
    while changed and normalized:
        changed = False
        for prefix in _COMMENT_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
                changed = True
        for prefix in _BULLET_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
                changed = True
        match = re.match(r"^\d+\.\s+", normalized)
        if match:
            normalized = normalized[match.end() :].strip()
            changed = True
    return normalized


def _find_matching_line_index(lines: list[str], patterns: list[re.Pattern[str]]) -> int | None:
    for index, line in enumerate(lines):
        for pattern in patterns:
            if pattern.search(line):
                return index
    return None
