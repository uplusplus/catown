"""Tiny reversible encoding for secrets that are persisted for diagnostics."""
from __future__ import annotations

import json
import re
from typing import Any


SENSITIVE_HEADER_NAMES = {"authorization", "api-key", "x-api-key", "x-openai-api-key"}
SENSITIVE_JSON_KEYS = {
    "apikey",
    "api_key",
    "api-key",
    "x-api-key",
    "x-openai-api-key",
    "authorization",
}
_BEARER_RE = re.compile(r"(?i)^(bearer\s+)(.+)$")
_BEARER_IN_TEXT_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{6,})")
_QUERY_API_KEY_RE = re.compile(r"(?i)\b(api[_-]?key=)([^&\s]+)")
_API_KEY_FIELD_RE = re.compile(
    r'("(?P<key>apiKey|api_key|api-key|x-api-key|x-openai-api-key|authorization)"\s*:\s*")(?P<value>[^"\\]*)(")',
    re.IGNORECASE,
)
_SK_LIKE_TOKEN_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{24,})\b")


def encode_secret_ascii_shift(value: str) -> str:
    """Encode a secret by shifting each character one code point forward."""
    return "".join(chr(ord(char) + 1) for char in value)


def encode_sensitive_header_value(value: str) -> str:
    match = _BEARER_RE.match(value)
    if match:
        return f"{match.group(1)}{encode_secret_ascii_shift(match.group(2))}"
    return encode_secret_ascii_shift(value)


def encode_sensitive_headers_for_logging(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not headers:
        return result
    try:
        items = headers.multi_items()
    except AttributeError:
        try:
            items = headers.items()
        except AttributeError:
            return result

    for key, value in items:
        name = str(key)
        text = str(value)
        if name.lower() in SENSITIVE_HEADER_NAMES:
            text = encode_sensitive_header_value(text)
        result[name] = text
    return result


def encode_log_secrets_in_text(text: str) -> str:
    if not text:
        return text

    try:
        parsed = json.loads(text)
    except Exception:
        return _encode_log_secrets_in_plain_text(text)

    encoded = _encode_log_secrets_in_json_value(parsed)
    try:
        return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return _encode_log_secrets_in_plain_text(text)


def _encode_log_secrets_in_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        encoded: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(item, str) and str(key).lower() in SENSITIVE_JSON_KEYS:
                encoded[key] = encode_sensitive_header_value(item)
            else:
                encoded[key] = _encode_log_secrets_in_json_value(item)
        return encoded
    if isinstance(value, list):
        return [_encode_log_secrets_in_json_value(item) for item in value]
    if isinstance(value, str):
        return _SK_LIKE_TOKEN_RE.sub(
            lambda match: encode_secret_ascii_shift(match.group(1)),
            value,
        )
    return value


def _encode_log_secrets_in_plain_text(text: str) -> str:
    def replace_field(match: re.Match[str]) -> str:
        return (
            f"{match.group(1)}"
            f"{encode_sensitive_header_value(match.group('value'))}"
            f"{match.group(4)}"
        )

    text = _API_KEY_FIELD_RE.sub(replace_field, text)
    text = _BEARER_IN_TEXT_RE.sub(
        lambda match: f"{match.group(1)}{encode_secret_ascii_shift(match.group(2))}",
        text,
    )
    text = _QUERY_API_KEY_RE.sub(
        lambda match: f"{match.group(1)}{encode_secret_ascii_shift(match.group(2))}",
        text,
    )
    return _SK_LIKE_TOKEN_RE.sub(
        lambda match: encode_secret_ascii_shift(match.group(1)),
        text,
    )
