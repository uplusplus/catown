# -*- coding: utf-8 -*-
"""Shared runtime configuration for Catown multimodal file handling."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings

DEFAULT_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024
MIN_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES = 1 * 1024 * 1024
MAX_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES = 200 * 1024 * 1024


def _load_agent_config_data() -> Dict[str, Any]:
    config_file = Path(settings.AGENT_CONFIG_FILE)
    if not config_file.exists():
        return {}
    try:
        with config_file.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_bytes(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_megabytes(value: Any) -> Optional[int]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return int(parsed * 1024 * 1024)


def clamp_multimodal_upload_size_bytes(value: int) -> int:
    return min(
        max(int(value), MIN_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES),
        MAX_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES,
    )


def effective_multimodal_config(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = config_data if config_data is not None else _load_agent_config_data()
    multimodal_data = payload.get("multimodal") if isinstance(payload, dict) else None
    if not isinstance(multimodal_data, dict):
        multimodal_data = {}

    configured_bytes = _coerce_bytes(multimodal_data.get("max_upload_size_bytes"))
    if configured_bytes is None:
        configured_bytes = _coerce_megabytes(multimodal_data.get("max_upload_size_mb"))
    if configured_bytes is None:
        configured_bytes = DEFAULT_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES

    max_upload_size_bytes = clamp_multimodal_upload_size_bytes(configured_bytes)
    return {
        "max_upload_size_bytes": max_upload_size_bytes,
        "min_upload_size_bytes": MIN_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES,
        "max_allowed_upload_size_bytes": MAX_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES,
    }


def multimodal_max_upload_size_bytes() -> int:
    return int(effective_multimodal_config().get("max_upload_size_bytes") or DEFAULT_MULTIMODAL_MAX_UPLOAD_SIZE_BYTES)
