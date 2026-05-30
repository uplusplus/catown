# -*- coding: utf-8 -*-
"""Replace inline multimodal bytes with recoverable file refs before logging."""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


_DATA_URI_BASE64_RE = re.compile(
    r"data:([A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+)(?:;[^,\s\"']*)?;base64,([A-Za-z0-9+/=_-]+)",
    re.IGNORECASE,
)
_FILE_DATA_KEYS = {"file_data"}
_MAX_REFERENCE_CACHE_SIZE = 4096


@dataclass(frozen=True)
class MultimodalDataUriReference:
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    sha256: str | None = None


_REFERENCE_LOCK = threading.Lock()
_REFERENCES_BY_DATA_URI: OrderedDict[str, MultimodalDataUriReference] = OrderedDict()


def _data_uri_key(data_uri: str) -> str:
    import hashlib

    return hashlib.sha256(data_uri.encode("utf-8", errors="surrogatepass")).hexdigest()


def register_multimodal_data_uri_reference(
    data_uri: str,
    *,
    file_id: str,
    mime_type: str | None = None,
    file_name: str | None = None,
    file_size: int | None = None,
    sha256: str | None = None,
) -> None:
    """Register the recoverable DB file reference for an inline LLM data URI."""

    normalized_file_id = str(file_id or "").strip()
    if not data_uri or not normalized_file_id:
        return
    reference = MultimodalDataUriReference(
        file_id=normalized_file_id,
        mime_type=(mime_type or None),
        file_name=(file_name or None),
        file_size=file_size,
        sha256=(sha256 or None),
    )
    key = _data_uri_key(data_uri)
    with _REFERENCE_LOCK:
        _REFERENCES_BY_DATA_URI[key] = reference
        _REFERENCES_BY_DATA_URI.move_to_end(key)
        while len(_REFERENCES_BY_DATA_URI) > _MAX_REFERENCE_CACHE_SIZE:
            _REFERENCES_BY_DATA_URI.popitem(last=False)


def clear_multimodal_data_uri_references() -> None:
    with _REFERENCE_LOCK:
        _REFERENCES_BY_DATA_URI.clear()


def _lookup_data_uri_reference(data_uri: str) -> MultimodalDataUriReference | None:
    key = _data_uri_key(data_uri)
    with _REFERENCE_LOCK:
        reference = _REFERENCES_BY_DATA_URI.get(key)
        if reference is not None:
            _REFERENCES_BY_DATA_URI.move_to_end(key)
        return reference


def _quote_attr(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _cached_file_placeholder(mime_type: str, encoded: str, data_uri: str) -> str:
    clean_size = len("".join(encoded.split()))
    approx_bytes = (clean_size * 3) // 4 if clean_size else 0
    reference = _lookup_data_uri_reference(data_uri)
    effective_mime = (reference.mime_type if reference and reference.mime_type else mime_type).lower()
    attrs: list[tuple[str, Any]] = [
        ("file_id", reference.file_id if reference else "unregistered"),
        ("mime", effective_mime),
        ("bytes", reference.file_size if reference and reference.file_size is not None else approx_bytes),
    ]
    if reference and reference.file_name:
        attrs.append(("filename", reference.file_name))
    if reference and reference.sha256:
        attrs.append(("sha256", reference.sha256))
    rendered_attrs = " ".join(f'{key}="{_quote_attr(value)}"' for key, value in attrs)
    tag = "cached_file" if reference else "inline_file"
    return f"<{tag} {rendered_attrs}>"


def _redact_data_uris(text: str) -> str:
    return _DATA_URI_BASE64_RE.sub(
        lambda match: _cached_file_placeholder(match.group(1), match.group(2), match.group(0)),
        text,
    )


def redact_multimodal_payload(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """Return a JSON-like copy with inline media/document bytes replaced by file_id refs."""
    if _depth > 64:
        return "<redacted: nesting too deep>"
    if isinstance(value, dict):
        return {
            str(item_key): redact_multimodal_payload(item_value, key=str(item_key), _depth=_depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_multimodal_payload(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        redacted = _redact_data_uris(value)
        if key in _FILE_DATA_KEYS and redacted == value and len(value) > 120:
            return f'<inline_file file_id="unregistered" chars="{len(value)}">'
        return redacted
    return value


def sanitized_json_dumps(value: Any, *, limit: int | None = None, indent: int | None = None) -> str:
    redacted = redact_multimodal_payload(value)
    try:
        text = json.dumps(redacted, ensure_ascii=False, default=str, indent=indent)
    except Exception:
        text = str(redacted)
    return text[:limit] if limit is not None else text


def sanitize_text_for_logging(text: str, *, limit: int | None = None) -> str:
    try:
        parsed = json.loads(text)
    except Exception:
        redacted_text = _redact_data_uris(text)
    else:
        redacted_text = sanitized_json_dumps(parsed)
    return redacted_text[:limit] if limit is not None else redacted_text
