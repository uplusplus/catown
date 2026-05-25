# -*- coding: utf-8 -*-
"""Output filter registry — routes commands to their compression filter."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import settings


@dataclass
class FilterResult:
    output: str
    raw_tokens: int
    filtered_tokens: int
    savings_pct: float
    tee_path: Optional[str] = None


def _estimate_tokens(text: str) -> int:
    """Fast token estimate: ascii chars / 4 + non-ascii chars."""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, (ascii_chars // 4) + non_ascii)


def _tee_dir() -> Path:
    path = settings.STATE_DIR / "tee"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_tee(raw_output: str, command: str) -> Optional[str]:
    """Save raw output to tee file for later inspection."""
    try:
        tee_path = _tee_dir() / f"{uuid.uuid4().hex[:12]}.log"
        header = f"# command: {command}\n# chars: {len(raw_output)}\n\n"
        tee_path.write_text(header + raw_output, encoding="utf-8")
        return str(tee_path)
    except Exception:
        return None


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _dedup_adjacent_lines(text: str) -> str:
    """Collapse adjacent identical lines, appending count."""
    lines = text.splitlines()
    if not lines:
        return text
    result: list[str] = []
    prev = ""
    count = 0
    for line in lines:
        stripped = line.rstrip()
        if stripped == prev and stripped:
            count += 1
        else:
            if count > 1:
                result.append(f"  (×{count})")
            result.append(line)
            prev = stripped
            count = 1
    if count > 1:
        result.append(f"  (×{count})")
    return "\n".join(result)


# ── Filter registry ──────────────────────────────────────────────

_FILTER_REGISTRY: list[tuple[str, type]] = []


def register_filter(command_pattern: str, filter_cls: type) -> None:
    _FILTER_REGISTRY.append((command_pattern, filter_cls))


def _match_filter(command: str):
    """Return the first matching filter instance for a command."""
    cmd_lower = command.strip().lower()
    for pattern, filter_cls in _FILTER_REGISTRY:
        if pattern in cmd_lower:
            return filter_cls()
    return None


# ── Import filters (triggers registration) ───────────────────────

def _ensure_filters_loaded() -> None:
    """Lazy-load filter modules on first use."""
    if _FILTER_REGISTRY:
        return
    from tools.filters import git_filter  # noqa: F401
    from tools.filters import test_filter  # noqa: F401
    from tools.filters import build_filter  # noqa: F401
    from tools.filters import generic_filter  # noqa: F401


def filter_output(command: str, raw_output: str, exit_code: int = 0) -> FilterResult:
    """Apply the appropriate filter to a command's output."""
    _ensure_filters_loaded()

    raw_tokens = _estimate_tokens(raw_output)

    # Save tee first (before any filtering)
    tee_path = _save_tee(raw_output, command)

    filt = _match_filter(command)
    if filt is not None:
        try:
            filtered = filt.apply(raw_output, exit_code=exit_code, command=command)
        except Exception:
            filtered = raw_output  # Fail-safe: use original
    else:
        # Fallback: generic dedup + ANSI strip
        filtered = _strip_ansi(raw_output)
        filtered = _dedup_adjacent_lines(filtered)

    filtered_tokens = _estimate_tokens(filtered)
    savings_pct = round((1 - filtered_tokens / max(raw_tokens, 1)) * 100, 1)

    # Append tee reference if significant savings
    if tee_path and savings_pct > 20:
        filtered = f"{filtered}\n[full output: {tee_path}]"

    return FilterResult(
        output=filtered,
        raw_tokens=raw_tokens,
        filtered_tokens=filtered_tokens,
        savings_pct=savings_pct,
        tee_path=tee_path,
    )
