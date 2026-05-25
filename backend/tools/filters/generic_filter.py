# -*- coding: utf-8 -*-
"""Generic output filter — ANSI strip + dedup for unrecognized commands."""

from __future__ import annotations

import re

from tools.filters.base import BaseFilter
from tools.output_filter import _dedup_adjacent_lines, _strip_ansi, register_filter


class GenericFilter(BaseFilter):
    """Fallback filter: ANSI strip + adjacent line deduplication."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        text = _strip_ansi(output)
        text = _dedup_adjacent_lines(text)

        # If still very long, keep first 2000 + last 1000 chars
        if len(text) > 6000:
            lines = text.splitlines()
            if len(lines) > 80:
                head = "\n".join(lines[:40])
                tail = "\n".join(lines[-20:])
                text = f"{head}\n\n  ... ({len(lines) - 60} lines omitted) ...\n\n{tail}"

        return text


# This is the fallback, not registered by pattern — it's used by output_filter.py directly
