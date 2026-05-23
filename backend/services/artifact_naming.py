# -*- coding: utf-8 -*-
"""Canonical naming helpers for workspace-visible artifacts."""

from __future__ import annotations

import re
from datetime import datetime, timezone


def build_timestamped_artifact_path(
    *,
    directory: str,
    subject: str,
    run_ref: str | None = None,
    task_ref: str | None = None,
    extension: str = ".md",
    timestamp: datetime | None = None,
) -> str:
    normalized_directory = _normalize_directory(directory)
    normalized_subject = _slug(subject) or "artifact"
    normalized_extension = _normalize_extension(extension)
    timestamp_value = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp_text = timestamp_value.strftime("%Y%m%dT%H%M%S%fZ")

    parts = [timestamp_text]
    if run_ref:
        parts.append(_prefixed_slug("run", run_ref))
    if task_ref:
        parts.append(_prefixed_slug("task", task_ref))
    parts.append(normalized_subject)
    filename = "--".join(parts) + normalized_extension
    return f"{normalized_directory}{filename}"


def slug_artifact_subject(value: str | None) -> str:
    return _slug(value)


def _normalize_directory(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().strip("/")
    return f"{normalized}/" if normalized else ""


def _normalize_extension(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ".md"
    return normalized if normalized.startswith(".") else f".{normalized}"


def _slug(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    normalized = normalized.replace("\\", "/")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized.strip("-")


def _prefixed_slug(prefix: str, value: str | None) -> str:
    normalized = _slug(value) or "unknown"
    expected_prefix = f"{prefix}-"
    return normalized if normalized.startswith(expected_prefix) else f"{expected_prefix}{normalized}"
