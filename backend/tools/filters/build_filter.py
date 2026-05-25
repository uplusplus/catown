# -*- coding: utf-8 -*-
"""Build output filter — compress build command output, keeping only errors/warnings."""

from __future__ import annotations

import re

from tools.filters.base import BaseFilter
from tools.output_filter import register_filter


class BuildFilter(BaseFilter):
    """Compress build output: keep errors and warnings, discard stdout noise."""

    _ERROR_RE = re.compile(r"(error|Error|ERROR|fatal|FATAL)", re.IGNORECASE)
    _WARNING_RE = re.compile(r"(warning|Warning|WARNING)", re.IGNORECASE)
    _BUILD_RESULT_RE = re.compile(
        r"(build|Build|BUILD|compil|Compil).*(success|fail|error|complete|finish)",
        re.IGNORECASE,
    )

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        errors: list[str] = []
        warnings: list[str] = []
        build_result: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if self._ERROR_RE.search(stripped) or self._BUILD_RESULT_RE.search(stripped):
                errors.append(stripped)
            elif self._WARNING_RE.search(stripped):
                warnings.append(stripped)

        # If no errors/warnings found, keep last 15 lines (might be progress)
        if not errors and not warnings:
            return "\n".join(lines[-15:])

        parts: list[str] = []
        if errors:
            parts.append(f"ERRORS ({len(errors)}):")
            for err in errors[:20]:
                parts.append(f"  {err}")
            if len(errors) > 20:
                parts.append(f"  ... and {len(errors) - 20} more errors")

        if warnings:
            parts.append(f"WARNINGS ({len(warnings)}):")
            for warn in warnings[:10]:
                parts.append(f"  {warn}")
            if len(warnings) > 10:
                parts.append(f"  ... and {len(warnings) - 10} more warnings")

        return "\n".join(parts)


class NpmBuildFilter(BaseFilter):
    """Compress npm/npx/vite/next build output."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        errors: list[str] = []
        warnings: list[str] = []
        in_error_block = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                in_error_block = False
                continue

            lower = stripped.lower()
            if any(kw in lower for kw in ("error", "failed", "fatal", "exception")):
                errors.append(stripped)
                in_error_block = True
            elif any(kw in lower for kw in ("warning", "warn")):
                warnings.append(stripped)
                in_error_block = False
            elif in_error_block:
                # Continuation of error block (stack trace, etc.)
                errors.append(stripped)

        if not errors and not warnings:
            # Keep last 10 lines
            return "\n".join(lines[-10:])

        parts: list[str] = []
        if errors:
            parts.append(f"ERRORS ({len(errors)}):")
            for err in errors[:15]:
                parts.append(f"  {err}")
        if warnings:
            parts.append(f"WARNINGS ({len(warnings)}):")
            for warn in warnings[:5]:
                parts.append(f"  {warn}")

        return "\n".join(parts)


# Register filters
register_filter("cargo build", BuildFilter)
register_filter("go build", BuildFilter)
register_filter("make", BuildFilter)
register_filter("cmake", BuildFilter)
register_filter("npm run build", NpmBuildFilter)
register_filter("npx next build", NpmBuildFilter)
register_filter("npx vite build", NpmBuildFilter)
register_filter("npx tsc", NpmBuildFilter)
register_filter("tsc", NpmBuildFilter)
