# -*- coding: utf-8 -*-
"""Test output filter — compress pytest/jest/vitest/cargo test output."""

from __future__ import annotations

import re

from tools.filters.base import BaseFilter
from tools.output_filter import register_filter


class PytestFilter(BaseFilter):
    """Compress pytest output: keep failures, summarize passes."""

    # Patterns for pytest output
    _PASS_LINE = re.compile(r"PASSED", re.IGNORECASE)
    _FAIL_LINE = re.compile(r"FAILED", re.IGNORECASE)
    _ERROR_LINE = re.compile(r"ERROR", re.IGNORECASE)
    _TEST_ID = re.compile(r"(\S+::\S+)")
    _SUMMARY_LINE = re.compile(r"=+\s*(.*?(?:passed|failed|error|warnings?).*)\s*=+", re.IGNORECASE)
    _SHORT_SUMMARY = re.compile(r"(\d+) passed", re.IGNORECASE)
    _SHORT_FAIL = re.compile(r"(\d+) failed", re.IGNORECASE)
    _SHORT_ERROR = re.compile(r"(\d+) error", re.IGNORECASE)

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        failures: list[str] = []
        errors: list[str] = []
        pass_count = 0
        fail_count = 0
        error_count = 0
        summary_line = ""
        collected_line = ""
        error_section: list[str] = []
        in_error_section = False

        for line in lines:
            stripped = line.strip()

            # Capture summary
            summary_match = self._SUMMARY_LINE.search(stripped)
            if summary_match:
                summary_line = stripped
                in_error_section = False
                continue

            # Capture "collected N items"
            if "collected" in stripped and "item" in stripped:
                collected_line = stripped
                continue

            # Short summary lines (e.g., "2 failed, 8 passed")
            short_fail = self._SHORT_FAIL.search(stripped)
            short_pass = self._SHORT_SUMMARY.search(stripped)
            short_error = self._SHORT_ERROR.search(stripped)
            if short_fail:
                fail_count += int(short_fail.group(1))
            if short_pass:
                pass_count += int(short_pass.group(1))
            if short_error:
                error_count += int(short_error.group(1))

            # Track test results
            if self._FAIL_LINE.search(stripped):
                fail_count += 1
                failures.append(stripped)
                in_error_section = False
            elif self._ERROR_LINE.search(stripped) and ("ERROR" in stripped or "Error" in stripped):
                error_count += 1
                errors.append(stripped)
                in_error_section = True
            elif self._PASS_LINE.search(stripped):
                pass_count += 1
                in_error_section = False
            elif in_error_section and stripped:
                # Capture error detail lines (traceback, assertions)
                error_section.append(line)

        # Build output
        parts: list[str] = []

        if collected_line:
            parts.append(collected_line)

        # Show failures with details
        if failures:
            parts.append(f"\n{'='*60}")
            parts.append(f"FAILED TESTS ({len(failures)}):")
            parts.append(f"{'='*60}")
            for fail in failures:
                parts.append(f"  ✗ {fail}")
            parts.append("")

        # Show errors with details
        if errors:
            parts.append(f"{'='*60}")
            parts.append(f"ERRORS ({len(errors)}):")
            parts.append(f"{'='*60}")
            for err in errors:
                parts.append(f"  ✗ {err}")
            # Include first 20 lines of error section
            if error_section:
                parts.append("")
                parts.extend(error_section[:20])
                if len(error_section) > 20:
                    parts.append(f"  ... ({len(error_section) - 20} more lines)")
            parts.append("")

        # Summary
        if summary_line:
            parts.append(summary_line)
        elif pass_count or fail_count or error_count:
            status_parts = []
            if fail_count:
                status_parts.append(f"FAILED: {fail_count}")
            if error_count:
                status_parts.append(f"ERRORS: {error_count}")
            if pass_count:
                status_parts.append(f"passed: {pass_count}")
            parts.append(" / ".join(status_parts))
        else:
            # Couldn't parse; return last 30 lines
            return "\n".join(lines[-30:])

        return "\n".join(parts)


class JestVitestFilter(BaseFilter):
    """Compress Jest/Vitest output."""

    _PASS_LINE = re.compile(r"(✓|✔|PASS|√)\s+", re.IGNORECASE)
    _FAIL_LINE = re.compile(r"(✗|✘|FAIL|×)\s+", re.IGNORECASE)
    _TEST_SUITE = re.compile(r"(Test Suites?|Tests):", re.IGNORECASE)

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        failures: list[str] = []
        pass_count = 0
        fail_count = 0
        summary_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if self._FAIL_LINE.search(stripped):
                fail_count += 1
                failures.append(stripped)
            elif self._PASS_LINE.search(stripped):
                pass_count += 1
            elif self._TEST_SUITE.search(stripped):
                summary_lines.append(stripped)

        parts: list[str] = []
        if failures:
            parts.append(f"FAILURES ({len(failures)}):")
            for fail in failures[:10]:
                parts.append(f"  {fail}")
            if len(failures) > 10:
                parts.append(f"  ... and {len(failures) - 10} more")

        if summary_lines:
            parts.extend(summary_lines)
        elif pass_count or fail_count:
            parts.append(f"tests: {fail_count} failed / {pass_count} passed")

        return "\n".join(parts) if parts else "\n".join(lines[-20:])


class CargoTestFilter(BaseFilter):
    """Compress cargo test output."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        failures: list[str] = []
        pass_count = 0
        summary = ""

        for line in lines:
            stripped = line.strip()
            if "FAILED" in stripped or "failed" in stripped:
                failures.append(stripped)
            elif re.search(r"test\s+\S+\s+\.\.\.\s+ok", stripped):
                pass_count += 1
            elif re.search(r"test result:", stripped):
                summary = stripped

        parts: list[str] = []
        if failures:
            parts.append(f"FAILURES ({len(failures)}):")
            for fail in failures[:10]:
                parts.append(f"  {fail}")

        if summary:
            parts.append(summary)
        elif pass_count:
            parts.append(f"tests: {len(failures)} failed / {pass_count} passed")

        return "\n".join(parts) if parts else "\n".join(lines[-20:])


# Register: order matters — more specific patterns first
register_filter("pytest", PytestFilter)
register_filter("python -m pytest", PytestFilter)
register_filter("py.test", PytestFilter)
register_filter("vitest", JestVitestFilter)
register_filter("jest", JestVitestFilter)
register_filter("npm test", JestVitestFilter)
register_filter("npx jest", JestVitestFilter)
register_filter("cargo test", CargoTestFilter)
