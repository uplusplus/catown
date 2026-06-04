# -*- coding: utf-8 -*-
"""
LLM 客户端封装

支持 per-Agent 独立 LLM 配置，所有配置来源为 agents.json。
"""
from typing import Awaitable, Callable, List, Dict, Any, Optional
import asyncio
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from openai._base_client import DefaultAsyncHttpxClient
from copy import deepcopy
import httpx
import json
import math
import os
import logging
import time
import traceback
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from agents.identity import DEFAULT_AGENT_TYPE, normalize_agent_type
from config import settings
from monitoring import monitor_network_buffer
from services.llm_network_context import get_active_llm_network_audit_context
from services.multimodal_log_redaction import (
    redact_multimodal_payload,
    sanitized_json_dumps,
    sanitize_text_for_logging,
)
from services.log_secret_codec import (
    encode_log_secrets_in_text,
    encode_sensitive_headers_for_logging,
)

logger = logging.getLogger("catown.llm")

try:
    import brotli
except Exception:  # pragma: no cover - optional dependency
    brotli = None


def _supports_stream_usage_fallback(error: Exception) -> bool:
    message = str(error or "").lower()
    if not message:
        return False
    if "stream_options" not in message and "include_usage" not in message:
        return False
    return any(
        marker in message
        for marker in (
            "unsupported",
            "unknown",
            "unrecognized",
            "invalid",
            "not permitted",
            "not allowed",
            "extra inputs",
            "additional properties",
        )
    )


def _responses_previous_response_id_unsupported(error: Exception) -> bool:
    message = " ".join(fragment for fragment in _error_fragments(error) if fragment).lower()
    return "previous_response_id" in message and any(
        marker in message
        for marker in (
            "only supported",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized",
            "invalid_request_error",
        )
    )


def _is_retry_later_rate_limit(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    fragments = [
        str(error or ""),
        repr(error),
        str(getattr(error, "__cause__", "") or ""),
        str(getattr(error, "__context__", "") or ""),
    ]
    message = " ".join(fragment for fragment in fragments if fragment).lower()
    if status_code != 429 and "429" not in message and "too many requests" not in message:
        return False
    return any(
        marker in message
        for marker in (
            "please retry later",
            "retry later",
            "concurrency limit exceeded",
        )
    )


def _tool_chat_retry_delay_seconds(attempt: int) -> float:
    return min(60.0, float(2 ** max(0, attempt - 1)))


def _error_fragments(error: Exception) -> list[str]:
    return [
        str(error or ""),
        repr(error),
        str(getattr(error, "__cause__", "") or ""),
        str(getattr(error, "__context__", "") or ""),
    ]


def _is_retryable_upstream_failure(error: Exception) -> bool:
    if _is_retry_later_rate_limit(error):
        return True

    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return True

    if isinstance(error, httpx.TimeoutException):
        return True

    if isinstance(error, (httpx.NetworkError, httpx.ProtocolError)):
        return True

    status_code = getattr(error, "status_code", None)
    if status_code in {408, 500, 502, 503, 504, 520, 521, 522, 523, 524}:
        return True

    if isinstance(error, (RateLimitError, APIStatusError)) and status_code in {408, 500, 502, 503, 504, 520, 521, 522, 523, 524}:
        return True

    message = " ".join(fragment for fragment in _error_fragments(error) if fragment).lower()
    return any(
        marker in message
        for marker in (
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "connection refused",
            "temporary failure in name resolution",
            "name or service not known",
            "nodename nor servname provided",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "tls",
            "ssl",
            "handshake",
            "stream interrupted",
        )
    )


def _compact_text(value: Any, limit: int = 280) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        started_at = time.perf_counter()
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    compact = " ".join(text.strip().split())
    return compact[:limit]


def _estimate_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except TypeError:
        return len(str(value).encode("utf-8"))


def _estimate_prompt_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False).strip()
        except TypeError:
            text = str(value).strip()
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4) + non_ascii_chars)


def _estimate_responses_input_tokens(items: List[Dict[str, Any]]) -> int:
    total = 0
    for item in items or []:
        total += 6
        total += _estimate_prompt_tokens(item)
    return total


def _safe_text_bytes(value: bytes | str | None, limit: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value if limit is None else value[:limit]
    text = value.decode("utf-8", errors="replace")
    return text if limit is None else text[:limit]


def _http_header_value(headers: dict[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def _normalized_content_encoding(headers: dict[str, str] | None) -> str:
    raw = _http_header_value(headers, "content-encoding").strip().lower()
    if not raw or raw == "identity":
        return ""
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) == 1:
        return parts[0]
    return ",".join(parts)


def _compressed_payload_placeholder(content_encoding: str, byte_count: int) -> str:
    label = content_encoding or "compressed"
    return f"[{label}-compressed body, {byte_count} B]"


class _MonitorContentDecoder:
    def __init__(self, content_encoding: str) -> None:
        self.content_encoding = content_encoding
        self._decoder = self._build_decoder(content_encoding)

    def decode_chunk(self, chunk: bytes) -> str | None:
        if not chunk:
            return ""
        if not self.content_encoding:
            return _safe_text_bytes(chunk)
        if self._decoder is None:
            return None
        try:
            if self.content_encoding == "br":
                decoded = self._decoder.process(chunk)
            else:
                decoded = self._decoder.decompress(chunk)
        except Exception:
            self._decoder = None
            return None
        return _safe_text_bytes(decoded)

    @staticmethod
    def _build_decoder(content_encoding: str):
        if content_encoding == "gzip":
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        if content_encoding == "deflate":
            return zlib.decompressobj()
        if content_encoding == "br" and brotli is not None:
            return brotli.Decompressor()
        return None


def _sanitize_http_headers(headers: Any) -> dict[str, str]:
    return encode_sensitive_headers_for_logging(headers)


def _connect_responses_websocket(uri: str, headers: Dict[str, str]):
    import websockets

    try:
        return websockets.connect(uri, additional_headers=headers, max_size=None)
    except TypeError:
        return websockets.connect(uri, extra_headers=headers, max_size=None)


class _ObservedAsyncResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        inner: httpx.AsyncByteStream,
        on_chunk: Callable[[bytes], Awaitable[None]],
        on_close: Callable[[], Awaitable[None]],
    ) -> None:
        self._inner = inner
        self._on_chunk = on_chunk
        self._on_close = on_close
        self._closed = False

    async def __aiter__(self):
        async for chunk in self._inner:
            await self._on_chunk(chunk)
            yield chunk

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._inner.aclose()
        finally:
            await self._on_close()

# 缓存已创建的客户端：agent_name → LLMClient
PROVIDER_MODE_CHAT_COMPLETIONS = "chat_completions"
PROVIDER_MODE_RESPONSES_HTTP = "responses_http"
PROVIDER_MODE_RESPONSES_WEBSOCKET = "responses_websocket"

SUPPORTED_PROVIDER_MODES = {
    PROVIDER_MODE_CHAT_COMPLETIONS,
    PROVIDER_MODE_RESPONSES_HTTP,
    PROVIDER_MODE_RESPONSES_WEBSOCKET,
}


def _normalize_provider_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_PROVIDER_MODES:
        return normalized
    return PROVIDER_MODE_CHAT_COMPLETIONS


def _runtime_config_from_payload(
    payload: Dict[str, Any] | None,
    *,
    fallback: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    fallback_runtime = fallback.get("runtime") if isinstance(fallback, dict) else {}
    runtime = payload.get("runtime") if isinstance(payload, dict) else {}
    merged = {
        **(fallback_runtime if isinstance(fallback_runtime, dict) else {}),
        **(runtime if isinstance(runtime, dict) else {}),
    }
    return {
        "provider_mode": _normalize_provider_mode(
            merged.get("provider_mode")
            or merged.get("providerMode")
        )
    }


_client_cache: Dict[str, "LLMClient"] = {}
_framework_client: Optional["LLMClient"] = None
_framework_fallback_client: Optional["LLMClient"] = None

# 旧测试夹具仍会直接注入这个全局 mock。
_llm_client: Optional["LLMClient"] = None


class LLMClient:
    """
    LLM 客户端（OpenAI 兼容接口）

    每个 Agent 可以有独立的 provider 配置（baseUrl, apiKey, model）。
    支持多模态消息（图片等）。
    """

    # 已知支持 vision 的模型前缀
    _VISION_MODEL_PREFIXES = (
        "gpt-4o", "gpt-4-vision", "gpt-4-turbo",
        "claude-3", "claude-3.5", "claude-4",
        "gemini", "qwen-vl", "qwen2-vl",
        "glm-4v", "deepseek-vl",
    )

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        model: str = None,
        agent_name: str | None = None,
        provider_mode: str | None = None,
    ):
        if base_url is None or api_key is None or model is None:
            # 从环境变量获取默认配置（向后兼容）
            base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
            api_key = api_key or os.getenv("LLM_API_KEY", "")
            model = model or os.getenv("LLM_MODEL", "gpt-3.5-turbo")
            provider_mode = provider_mode or os.getenv("LLM_PROVIDER_MODE")
        self.base_url = base_url
        self._api_key = api_key
        self.agent_name = agent_name or "agent"
        self.model = model
        self.provider_mode = _normalize_provider_mode(provider_mode)
        self.runtime_provider_mode = self.provider_mode
        self._stream_usage_supported: Optional[bool] = None
        self._http_client = DefaultAsyncHttpxClient(
            event_hooks={
                "request": [self._capture_http_request],
                "response": [self._capture_http_response],
            }
        )
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=self._http_client)

    def supports_multimodal(self) -> bool:
        """Check if the current model supports multimodal (vision) inputs."""
        model_lower = (self.model or "").lower()
        return any(model_lower.startswith(prefix) for prefix in self._VISION_MODEL_PREFIXES)

    @staticmethod
    def _prepare_multimodal_content(
        text: str,
        images: Optional[List[Dict[str, Any]]] = None,
        files: Optional[List[Dict[str, Any]]] = None,
        detail: str = "auto",
    ) -> str | List[Dict[str, Any]]:
        """
        Build OpenAI-compatible multimodal content.

        Args:
            text: The text prompt.
            images: List of image dicts, each with:
                - "url": str (data URI or HTTP URL)
                - "mime_type": str (optional, e.g. "image/png")
                - "detail": str (optional, overrides default detail)
            files: List of file dicts, each with:
                - "data": str (data URI or base64 content)
                - "filename": str (optional)
                - "mime_type": str (optional, e.g. "application/pdf")
            detail: Default image detail level ("low", "high", "auto").

        Returns:
            str if no images/files provided, otherwise a list of content parts.
        """
        if not images and not files:
            return text

        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for img in images:
            url = img.get("url", "")
            if not url:
                continue
            image_detail = img.get("detail", detail)
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": url, "detail": image_detail},
            })
        for file in files or []:
            file_data = file.get("data", "")
            if not file_data:
                continue
            file_part: Dict[str, Any] = {"file_data": file_data}
            filename = file.get("filename")
            if filename:
                file_part["filename"] = filename
            mime_type = file.get("mime_type")
            if mime_type:
                file_part["mime_type"] = mime_type
            content_parts.append({
                "type": "file",
                "file": file_part,
            })
        return content_parts

    @staticmethod
    def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalize message content for the OpenAI API.

        Ensures each message's content field is valid for multimodal:
        - str content passes through as-is
        - list content passes through as-is (already multimodal format)
        - Other types are converted to str
        """
        normalized: List[Dict[str, Any]] = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                normalized.append(msg)
                continue
            content = msg.get("content")
            if content is None:
                normalized.append(msg)
            elif isinstance(content, (str, list)):
                normalized.append(msg)
            else:
                normalized.append({**msg, "content": str(content)})
        return normalized

    def _llm_target(self, host: str | None = None) -> str:
        parsed = urlparse(self.base_url or "")
        return f"LLM ({host or parsed.netloc or (self.base_url or '')})"

    def _network_audit_context_payload(self, target: str) -> Dict[str, Any]:
        context = get_active_llm_network_audit_context()
        metadata = dict(context.metadata or {})
        call_purpose = str(context.call_purpose or "").strip()
        purpose_label = str(context.purpose_label or "").strip()
        if call_purpose:
            metadata.setdefault("llm_call_purpose", call_purpose)
        if not purpose_label and call_purpose:
            purpose_label = call_purpose.replace("_", " ")
        if purpose_label:
            metadata.setdefault("llm_call_purpose_label", purpose_label)
            source = f"{self.agent_name} {purpose_label}"
            return {
                "request_direction": f"{source} -> {target}",
                "response_direction": f"{target} -> {source}",
                "metadata": metadata,
            }
        return {
            "request_direction": "",
            "response_direction": "",
            "metadata": metadata,
        }

    def _append_network_event(self, payload: Dict[str, Any]) -> None:
        parsed = urlparse(self.base_url or "")
        host = payload.get("host") or parsed.netloc or (self.base_url or "")
        target = self._llm_target(host)
        metadata = {
            "model": self.model,
            "http_version": payload.pop("http_version", "1.1+"),
            **(payload.pop("metadata", {}) or {}),
        }
        monitor_network_buffer.append(
            {
                "category": "backend_llm",
                "source": "backend",
                "protocol": payload.get("protocol") or (parsed.scheme or "https").upper(),
                "from_entity": self.agent_name,
                "to_entity": target,
                "request_direction": payload.get("request_direction") or f"{self.agent_name} -> {target}",
                "response_direction": payload.get("response_direction") or f"{target} -> {self.agent_name}",
                "method": payload.get("method", "POST"),
                "url": payload.get("url") or self.base_url,
                "host": host,
                "path": payload.get("path") or parsed.path or "/",
                "success": payload.get("success"),
                "status_code": payload.get("status_code"),
                "request_bytes": payload.get("request_bytes", 0),
                "response_bytes": payload.get("response_bytes", 0),
                "duration_ms": payload.get("duration_ms", 0),
                "content_type": payload.get("content_type") or "application/json",
                "preview": payload.get("preview", ""),
                "error": payload.get("error", ""),
                "raw_request": payload.get("raw_request", ""),
                "raw_response": payload.get("raw_response", ""),
                "request_headers": payload.get("request_headers") or {},
                "response_headers": payload.get("response_headers") or {},
                "flow_id": payload.get("flow_id"),
                "flow_kind": payload.get("flow_kind"),
                "flow_seq": payload.get("flow_seq"),
                "aggregated": payload.get("aggregated"),
                "metadata": metadata,
            }
        )

    async def _capture_http_request(self, request: httpx.Request) -> None:
        body = await request.aread()
        body_text = _safe_text_bytes(body)
        sanitized_body_text = encode_log_secrets_in_text(
            sanitize_text_for_logging(body_text, limit=40000)
        )
        url = request.url
        host = url.netloc.decode("ascii", errors="ignore") if isinstance(url.netloc, bytes) else url.netloc
        path = url.raw_path.decode("utf-8", errors="replace").split("?", 1)[0] if isinstance(url.raw_path, bytes) else str(url.path)
        request_headers = _sanitize_http_headers(request.headers)
        target = self._llm_target(host)
        audit_context = self._network_audit_context_payload(target)
        context = {
            "flow_id": f"llm-http-{uuid.uuid4().hex[:12]}",
            "flow_kind": "llm_http",
            "flow_seq": 1,
            "started_at": time.perf_counter(),
            "request_body": body,
            "request_headers": request_headers,
            "protocol": (url.scheme or "https").upper(),
            "host": host,
            "path": path or "/",
            "url": encode_log_secrets_in_text(str(url)),
            "request_direction": audit_context["request_direction"],
            "response_direction": audit_context["response_direction"],
            "audit_metadata": audit_context["metadata"],
        }
        request.extensions["catown_raw_capture"] = context
        self._append_network_event(
            {
                "protocol": context["protocol"],
                "method": request.method,
                "url": context["url"],
                "host": context["host"],
                "path": context["path"],
                "success": None,
                "request_bytes": len(body),
                "response_bytes": 0,
                "duration_ms": 0,
                "request_direction": context["request_direction"],
                "response_direction": context["response_direction"],
                "content_type": request.headers.get("content-type", "application/json"),
                "preview": _compact_text(sanitized_body_text),
                "raw_request": sanitized_body_text,
                "raw_response": "",
                "request_headers": request_headers,
                "response_headers": {},
                "flow_id": context["flow_id"],
                "flow_kind": context["flow_kind"],
                "flow_seq": 1,
                "aggregated": False,
                "metadata": {**context["audit_metadata"], "frame_type": "request"},
            }
        )

    async def _capture_http_response(self, response: httpx.Response) -> None:
        context = response.request.extensions.get("catown_raw_capture")
        if not context:
            return

        response_headers = _sanitize_http_headers(response.headers)
        closed = False
        body_decoder = _MonitorContentDecoder(_normalized_content_encoding(response_headers))

        self._append_network_event(
            {
                "protocol": context["protocol"],
                "method": response.request.method,
                "url": context["url"],
                "host": context["host"],
                "path": context["path"],
                "success": response.is_success,
                "status_code": response.status_code,
                "request_bytes": 0,
                "response_bytes": 0,
                "duration_ms": int((time.perf_counter() - context["started_at"]) * 1000),
                "request_direction": context.get("request_direction"),
                "response_direction": context.get("response_direction"),
                "content_type": response.headers.get("content-type", ""),
                "preview": "",
                "raw_request": "",
                "raw_response": "",
                "request_headers": {},
                "response_headers": response_headers,
                "flow_id": context["flow_id"],
                "flow_kind": context["flow_kind"],
                "flow_seq": context["flow_seq"] + 1,
                "aggregated": False,
                "metadata": {**(context.get("audit_metadata") or {}), "frame_type": "response_start"},
            }
        )
        context["flow_seq"] += 1

        async def on_chunk(chunk: bytes) -> None:
            context["flow_seq"] += 1
            elapsed_ms = int((time.perf_counter() - context["started_at"]) * 1000)
            decoded_chunk = body_decoder.decode_chunk(chunk)
            if decoded_chunk is None:
                display_chunk = _compressed_payload_placeholder(body_decoder.content_encoding, len(chunk))
            else:
                display_chunk = decoded_chunk
            self._append_network_event(
                {
                    "protocol": context["protocol"],
                    "method": response.request.method,
                    "url": context["url"],
                    "host": context["host"],
                    "path": context["path"],
                    "success": True,
                    "status_code": response.status_code,
                    "request_bytes": 0,
                    "response_bytes": len(chunk),
                    "duration_ms": elapsed_ms,
                    "request_direction": context.get("request_direction"),
                    "response_direction": context.get("response_direction"),
                    "content_type": response.headers.get("content-type", ""),
                    "preview": _compact_text(display_chunk) if display_chunk else "",
                    "raw_request": "",
                    "raw_response": display_chunk or "",
                    "request_headers": {},
                    "response_headers": response_headers,
                    "flow_id": context["flow_id"],
                    "flow_kind": context["flow_kind"],
                    "flow_seq": context["flow_seq"],
                    "aggregated": False,
                    "metadata": {**(context.get("audit_metadata") or {}), "frame_type": "response_chunk"},
                }
            )

        async def on_close() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            return

        response.stream = _ObservedAsyncResponseStream(response.stream, on_chunk=on_chunk, on_close=on_close)

    def _record_network_event(
        self,
        *,
        request_payload: Any,
        response_payload: Any = None,
        duration_ms: int = 0,
        success: bool | None = None,
        error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        parsed = urlparse(self.base_url or "")
        protocol = (parsed.scheme or "https").upper()
        host = parsed.netloc or (self.base_url or "")
        target = f"LLM ({host})"
        audit_context = self._network_audit_context_payload(target)
        safe_request_payload = redact_multimodal_payload(request_payload)
        safe_response_payload = redact_multimodal_payload(response_payload)
        monitor_network_buffer.append(
            {
                "category": "backend_llm",
                "source": "backend",
                "protocol": protocol,
                "from_entity": self.agent_name,
                "to_entity": target,
                "request_direction": audit_context["request_direction"] or f"{self.agent_name} -> {target}",
                "response_direction": audit_context["response_direction"] or f"{target} -> {self.agent_name}",
                "method": "POST",
                "url": self.base_url,
                "host": host,
                "path": parsed.path or "/",
                "success": success,
                "request_bytes": _estimate_bytes(request_payload),
                "response_bytes": _estimate_bytes(response_payload),
                "duration_ms": duration_ms,
                "content_type": "application/json",
                "preview": _compact_text(safe_response_payload or safe_request_payload),
                "error": error,
                "raw_request": sanitized_json_dumps(request_payload, limit=40000) if request_payload is not None else "",
                "raw_response": sanitized_json_dumps(response_payload, limit=40000) if response_payload is not None else "",
                "request_headers": {},
                "response_headers": {},
                "metadata": {
                    "model": self.model,
                    "http_version": "1.1+",
                    **audit_context["metadata"],
                    **(metadata or {}),
                },
            }
        )

    async def chat(self, messages: List[Dict[str, Any]], **kwargs) -> str:
        """发送聊天消息（支持 multimodal content）"""
        started_at = time.perf_counter()
        try:
            # Normalize messages: ensure content can be str or list
            normalized = self._normalize_messages(messages)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=normalized,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2000)
            )
            if isinstance(response, str):
                return response
            if not hasattr(response, 'choices') or not response.choices:
                logger.warning(f"Model '{self.model}' returned unexpected response: {type(response)}")
                return str(response)
            content = response.choices[0].message.content
            return content
        except Exception as e:
            self._record_network_event(
                request_payload={"messages": messages, **kwargs},
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                success=False,
                error=str(e),
            )
            raise Exception(f"LLM API error: {str(e)}")

    async def chat_with_tools(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """支持工具调用的聊天（支持 multimodal content）"""
        started_at = time.perf_counter()
        attempt = 0
        retry_budget_seconds = 300.0
        try:
            # Normalize messages: ensure content can be str or list
            normalized = self._normalize_messages(messages)
            kwargs = {
                "model": self.model,
                "messages": normalized,
                "temperature": 0.7
            }
            if tools:
                kwargs["tools"] = tools

            while True:
                attempt += 1
                try:
                    response = await self.client.chat.completions.create(**kwargs)
                    if attempt > 1:
                        logger.info(
                            "LLM tool chat retry succeeded: model=%s attempts=%s",
                            self.model,
                            attempt,
                        )
                    break
                except Exception as create_error:
                    elapsed_seconds = time.perf_counter() - started_at
                    delay_seconds = _tool_chat_retry_delay_seconds(attempt)
                    next_elapsed_seconds = elapsed_seconds + delay_seconds
                    will_retry = (
                        _is_retryable_upstream_failure(create_error)
                        and next_elapsed_seconds <= retry_budget_seconds
                    )
                    if will_retry:
                        delay_seconds = _tool_chat_retry_delay_seconds(attempt)
                        logger.warning(
                            "LLM tool chat upstream failure; retrying: model=%s attempt=%s elapsed_s=%.1f next_delay_s=%.1f retry_budget_s=%.1f error=%r",
                            self.model,
                            attempt,
                            elapsed_seconds,
                            delay_seconds,
                            retry_budget_seconds,
                            create_error,
                        )
                        self._record_network_event(
                            request_payload=kwargs,
                            duration_ms=int((time.perf_counter() - started_at) * 1000),
                            success=False,
                            error=f"{type(create_error).__name__}: {create_error}",
                            metadata={
                                "sync_tool_chat": True,
                                "retryable": True,
                                "retry_attempt": attempt,
                                "will_retry": True,
                                "retry_delay_seconds": delay_seconds,
                                "retry_budget_seconds": retry_budget_seconds,
                            },
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    raise

            # 调试：记录实际响应类型和内容，帮助定位 'str' object has no attribute 'choices' 问题
            if not hasattr(response, 'choices'):
                logger.error(
                    f"LLM response is not ChatCompletion!\n"
                    f"  type={type(response).__name__}\n"
                    f"  dir={type(response).__mro__}\n"
                    f"  repr={repr(response)[:2000]}"
                )

            # 兼容 API 返回字符串/非标准响应的情况
            if isinstance(response, str):
                return {"content": response, "tool_calls": None, "usage": None}

            if not hasattr(response, 'choices') or not response.choices:
                logger.warning(f"Model '{self.model}' returned unexpected response type: {type(response)}. Falling back to plain chat.")
                return {"content": str(response), "tool_calls": None, "usage": None}

            choice = response.choices[0]

            # 提取 usage 信息
            usage = None
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0),
                }

            content = choice.message.content
            tool_calls = choice.message.tool_calls if hasattr(choice.message, 'tool_calls') else None
            finish_reason = getattr(choice, 'finish_reason', None)
            refusal = getattr(choice.message, 'refusal', None) if hasattr(choice.message, 'refusal') else None

            if content is None and not tool_calls:
                logger.error(
                    "LLM tool chat returned empty completion: model=%s finish_reason=%r refusal=%r response_type=%s",
                    self.model,
                    finish_reason,
                    refusal,
                    type(response).__name__,
                )
                self._record_network_event(
                    request_payload=kwargs,
                    response_payload={
                        "content": content,
                        "tool_calls": tool_calls,
                        "finish_reason": finish_reason,
                        "refusal": refusal,
                    },
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                    success=False,
                    error="empty tool chat completion",
                    metadata={
                        "sync_tool_chat": True,
                        "response_validation": "empty_completion",
                        "attempts": attempt,
                    },
                )

            return {
                "content": content,
                "tool_calls": tool_calls,
                "usage": usage,
            }
        except Exception as e:
            logger.error(
                "LLM tool chat failed: model=%s attempts=%s type=%s repr=%r cause=%r context=%r\n%s",
                self.model,
                attempt,
                type(e).__name__,
                e,
                e.__cause__,
                e.__context__,
                traceback.format_exc(),
            )
            self._record_network_event(
                request_payload=locals().get("kwargs", {"messages": messages, "tools": tools}),
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={"sync_tool_chat": True, "attempts": attempt},
            )
            raise Exception(f"LLM API error with tools: {str(e)}") from e

    @staticmethod
    def _responses_content_from_chat_content(content: Any) -> str | List[Dict[str, Any]]:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")

        parts: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                parts.append({"type": "input_text", "text": str(part)})
                continue

            part_type = part.get("type")
            if part_type == "text":
                parts.append({"type": "input_text", "text": str(part.get("text") or "")})
                continue

            if part_type == "image_url":
                image = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
                image_url = image.get("url") or part.get("image_url")
                if image_url:
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": image.get("detail") or part.get("detail") or "auto",
                        }
                    )
                continue

            if part_type == "file":
                file_payload = part.get("file") if isinstance(part.get("file"), dict) else {}
                input_file = {"type": "input_file"}
                for source_key, target_key in (
                    ("file_data", "file_data"),
                    ("file_id", "file_id"),
                    ("file_url", "file_url"),
                    ("filename", "filename"),
                    ("detail", "detail"),
                ):
                    value = file_payload.get(source_key) or part.get(source_key)
                    if value:
                        input_file[target_key] = value
                if len(input_file) > 1:
                    parts.append(input_file)
                continue

            parts.append({"type": "input_text", "text": json.dumps(part, ensure_ascii=False)})

        return parts if parts else ""

    @staticmethod
    def _content_as_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")
        text_parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in {"text", "input_text"}:
                    text_parts.append(str(part.get("text") or ""))
                else:
                    text_parts.append(json.dumps(part, ensure_ascii=False))
            else:
                text_parts.append(str(part))
        return "\n".join(item for item in text_parts if item)

    def _responses_request_parts(
        self,
        messages: List[Dict[str, Any]],
        *,
        previous_response_id: str | None = None,
    ) -> tuple[str | None, List[Dict[str, Any]], Dict[str, Any]]:
        instructions: List[str] = []
        input_items: List[Dict[str, Any]] = []
        normalized_messages = self._normalize_messages(messages)
        for message in normalized_messages:
            if not isinstance(message, dict):
                input_items.append({"role": "user", "content": str(message)})
                continue

            role = str(message.get("role") or "user")
            content = message.get("content")
            if role in {"system", "developer"}:
                text_content = self._content_as_text(content).strip()
                if text_content:
                    instructions.append(text_content)
                continue

            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if call_id:
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": self._content_as_text(content),
                        }
                    )
                continue

            if role == "assistant" and message.get("tool_calls"):
                text_content = self._content_as_text(content).strip()
                if text_content:
                    input_items.append({"role": "assistant", "content": text_content})
                for tool_call in message.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                    name = str(function.get("name") or "").strip()
                    arguments = function.get("arguments") or "{}"
                    call_id = str(tool_call.get("id") or tool_call.get("call_id") or "").strip()
                    if name and call_id:
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": name,
                                "arguments": str(arguments),
                                "status": "completed",
                            }
                        )
                continue

            input_role = role if role in {"user", "assistant"} else "user"
            input_items.append(
                {
                    "role": input_role,
                    "content": self._responses_content_from_chat_content(content),
                }
            )

        full_input_items = list(input_items)
        full_input_count = len(full_input_items)
        if previous_response_id:
            input_items = self._responses_stateful_delta_items(normalized_messages, input_items)
        full_input_tokens = _estimate_responses_input_tokens(full_input_items)
        sent_input_tokens = _estimate_responses_input_tokens(input_items)
        instruction_tokens = _estimate_prompt_tokens("\n\n".join(instructions))

        diagnostics = {
            "stateful_delta": bool(previous_response_id),
            "full_input_item_count": full_input_count,
            "sent_input_item_count": len(input_items),
            "omitted_input_item_count": max(full_input_count - len(input_items), 0),
            "estimated_full_input_tokens": full_input_tokens,
            "estimated_sent_input_tokens": sent_input_tokens,
            "estimated_omitted_input_tokens": max(full_input_tokens - sent_input_tokens, 0),
            "estimated_instruction_tokens": instruction_tokens,
        }
        return ("\n\n".join(instructions) if instructions else None), input_items, diagnostics

    def _responses_stateful_delta_items(
        self,
        messages: List[Dict[str, Any]],
        fallback_items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        last_protocol_role = None
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if role not in {"system", "developer"}:
                last_protocol_role = role
                break

        if last_protocol_role == "tool":
            trailing_tool_messages: List[Dict[str, Any]] = []
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "")
                if role == "tool":
                    trailing_tool_messages.append(message)
                    continue
                if role not in {"system", "developer"}:
                    break
            tool_outputs = [
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or "").strip(),
                    "output": self._content_as_text(message.get("content")),
                }
                for message in reversed(trailing_tool_messages)
                if str(message.get("tool_call_id") or "").strip()
            ]
            if tool_outputs:
                return tool_outputs

        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if role == "user":
                return [
                    {
                        "role": "user",
                        "content": self._responses_content_from_chat_content(message.get("content")),
                    }
                ]

        return fallback_items[-1:] if fallback_items else []

    @staticmethod
    def _responses_tools_from_chat_tools(tools: List[Dict] | None) -> List[Dict[str, Any]] | None:
        converted: List[Dict[str, Any]] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                converted.append(dict(tool))
                continue
            function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            converted_tool: Dict[str, Any] = {
                "type": "function",
                "name": name,
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or {},
                "strict": False,
            }
            converted.append(converted_tool)
        return converted or None

    @staticmethod
    def _event_value(event: Any, key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    @staticmethod
    def _event_model_dump(value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump(mode="json")
            except TypeError:
                return value.model_dump()
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        } if hasattr(value, "__dict__") else {}

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): LLMClient._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [LLMClient._jsonable(item) for item in value]
        if hasattr(value, "model_dump"):
            try:
                return LLMClient._jsonable(value.model_dump(mode="json"))
            except TypeError:
                return LLMClient._jsonable(value.model_dump())
        if hasattr(value, "__dict__"):
            return {
                key: LLMClient._jsonable(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return str(value)

    @staticmethod
    def _usage_from_responses_usage(usage: Any) -> Dict[str, int] | None:
        if usage is None:
            return None
        if isinstance(usage, dict):
            return {
                "prompt_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                "completion_tokens": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
        return {
            "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    @staticmethod
    def _response_summary(response: Any) -> Dict[str, Any]:
        if response is None:
            return {}
        payload = LLMClient._event_model_dump(response)
        return {
            key: payload.get(key)
            for key in (
                "id",
                "status",
                "conversation",
                "previous_response_id",
                "prompt_cache_key",
                "prompt_cache_retention",
            )
            if payload.get(key) is not None
        }

    @staticmethod
    def _tool_call_from_responses_item(item: Any) -> Dict[str, Any] | None:
        item_type = LLMClient._event_value(item, "type")
        if item_type != "function_call":
            return None
        call_id = str(LLMClient._event_value(item, "call_id") or LLMClient._event_value(item, "id") or "").strip()
        name = str(LLMClient._event_value(item, "name") or "").strip()
        if not call_id or not name:
            return None
        return {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": str(LLMClient._event_value(item, "arguments") or ""),
            },
        }

    @staticmethod
    def _text_from_responses_output(output: Any) -> str:
        text_parts: List[str] = []
        for item in output if isinstance(output, list) else []:
            item_type = LLMClient._event_value(item, "type")
            if item_type == "message":
                content = LLMClient._event_value(item, "content")
                for part in content if isinstance(content, list) else []:
                    part_type = LLMClient._event_value(part, "type")
                    if part_type in {"output_text", "text"}:
                        text = str(LLMClient._event_value(part, "text") or "")
                        if text:
                            text_parts.append(text)
                continue
            if item_type in {"output_text", "text"}:
                text = str(LLMClient._event_value(item, "text") or "")
                if text:
                    text_parts.append(text)
                continue
            if LLMClient._event_value(item, "role") == "assistant":
                content = LLMClient._event_value(item, "content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
        return "".join(text_parts)

    @staticmethod
    def _compaction_item_from_output(output: Any) -> Dict[str, Any]:
        for item in output if isinstance(output, list) else []:
            if isinstance(item, dict) and item.get("type") == "compaction":
                return item
        return {}

    @staticmethod
    def _provider_compaction_context(provider_session: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not isinstance(provider_session, dict):
            return None
        checkpoint = provider_session.get("provider_compaction")
        if not isinstance(checkpoint, dict) or not checkpoint.get("ready_for_next_request"):
            return None
        path_text = str(checkpoint.get("path") or "").strip()
        if not path_text:
            return None
        try:
            payload = json.loads(Path(path_text).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        output = payload.get("output")
        if not isinstance(output, list) or not output:
            return None
        return {
            "id": str(checkpoint.get("id") or payload.get("id") or "").strip(),
            "kind": str(checkpoint.get("kind") or payload.get("kind") or "").strip(),
            "input": output,
            "path": path_text,
        }

    def _responses_request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict] | None,
        *,
        previous_response_id: str | None,
        compact_context: Dict[str, Any] | None,
        stream: bool,
    ) -> tuple[Dict[str, Any], Dict[str, Any], str | None]:
        instructions, input_items, input_diagnostics = self._responses_request_parts(
            messages,
            previous_response_id=previous_response_id,
        )
        compact_input_items = (
            compact_context.get("input")
            if isinstance(compact_context, dict) and isinstance(compact_context.get("input"), list)
            else []
        )
        if compact_input_items:
            _, full_input_items, full_input_diagnostics = self._responses_request_parts(
                messages,
                previous_response_id=None,
            )
            delta_items = self._responses_stateful_delta_items(
                self._normalize_messages(messages),
                full_input_items,
            )
            input_items = [*deepcopy(compact_input_items), *delta_items]
            previous_response_id = None
            sent_tokens = _estimate_responses_input_tokens(input_items)
            full_tokens = int(full_input_diagnostics.get("estimated_full_input_tokens") or 0)
            input_diagnostics = {
                **full_input_diagnostics,
                "stateful_delta": False,
                "compact_window_reused": True,
                "compact_checkpoint_id": compact_context.get("id"),
                "compact_checkpoint_kind": compact_context.get("kind"),
                "compacted_input_item_count": len(compact_input_items),
                "sent_delta_item_count": len(delta_items),
                "sent_input_item_count": len(input_items),
                "estimated_sent_input_tokens": sent_tokens,
                "estimated_omitted_input_tokens": max(full_tokens - sent_tokens, 0),
            }

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "temperature": 0.7,
            "store": True,
        }
        if stream:
            kwargs["stream"] = True
        if instructions:
            kwargs["instructions"] = instructions
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        responses_tools = self._responses_tools_from_chat_tools(tools)
        if responses_tools:
            kwargs["tools"] = responses_tools
        return kwargs, input_diagnostics, previous_response_id

    def _responses_websocket_url(self) -> str:
        parsed = urlparse(self.base_url or "https://api.openai.com/v1")
        scheme = "ws" if parsed.scheme == "http" else "wss"
        path = parsed.path.rstrip("/")
        if path.endswith("/chat/completions"):
            path = path[: -len("/chat/completions")]
        if not path.endswith("/responses"):
            path = f"{path}/responses" if path else "/responses"
        return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))

    async def _iter_responses_stream_events(
        self,
        stream: Any,
        *,
        request_started_at: float,
        timings: Dict[str, int],
        input_diagnostics: Dict[str, Any],
    ):
        full_content = ""
        accumulated_tool_calls: List[Dict[str, Any]] = []
        tool_call_indexes: Dict[str, int] = {}
        usage = None
        finish_reason = None
        response_payload: Dict[str, Any] = {}
        response_id = None
        first_chunk_seen = False
        tool_call_ready_emitted = False
        completed_event_seen = False

        def ensure_tool_call(item_id: str, output_index: int | None = None) -> tuple[int, Dict[str, Any]]:
            key = item_id or f"output:{output_index if output_index is not None else len(accumulated_tool_calls)}"
            if key in tool_call_indexes:
                idx = tool_call_indexes[key]
                return idx, accumulated_tool_calls[idx]
            idx = len(accumulated_tool_calls)
            tool_call_indexes[key] = idx
            accumulated_tool_calls.append(
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            )
            return idx, accumulated_tool_calls[idx]

        async for event in stream:
            elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
            if not first_chunk_seen:
                first_chunk_seen = True
                timings["first_chunk_ms"] = elapsed_ms
                yield {"type": "first_chunk", "elapsed_ms": elapsed_ms}

            event_type = str(self._event_value(event, "type") or "")
            if event_type == "response.output_text.delta":
                delta = str(self._event_value(event, "delta") or "")
                if delta:
                    if "first_content_ms" not in timings:
                        timings["first_content_ms"] = elapsed_ms
                        yield {"type": "first_content", "elapsed_ms": elapsed_ms}
                    full_content += delta
                    yield {"type": "content", "delta": delta}
                continue

            if event_type == "response.function_call_arguments.delta":
                item_id = str(self._event_value(event, "item_id") or "")
                output_index = self._event_value(event, "output_index")
                idx, tool_call = ensure_tool_call(item_id, output_index)
                delta = str(self._event_value(event, "delta") or "")
                if delta:
                    tool_call["function"]["arguments"] += delta
                if "first_tool_call_ms" not in timings:
                    timings["first_tool_call_ms"] = elapsed_ms
                snapshot = deepcopy(tool_call)
                yield {
                    "type": "tool_call_delta",
                    "tool_call_index": idx,
                    "tool_call": snapshot,
                    "tool_name": snapshot.get("function", {}).get("name") or "",
                    "arguments": snapshot.get("function", {}).get("arguments") or "",
                    "elapsed_ms": elapsed_ms,
                }
                continue

            if event_type == "response.function_call_arguments.done":
                item_id = str(self._event_value(event, "item_id") or "")
                output_index = self._event_value(event, "output_index")
                _, tool_call = ensure_tool_call(item_id, output_index)
                tool_call["function"]["arguments"] = str(self._event_value(event, "arguments") or "")
                name = str(self._event_value(event, "name") or "").strip()
                if name:
                    tool_call["function"]["name"] = name
                continue

            if event_type in {"response.output_item.added", "response.output_item.done"}:
                item = self._event_value(event, "item")
                tool_call = self._tool_call_from_responses_item(item)
                if tool_call:
                    item_id = str(self._event_value(item, "id") or self._event_value(event, "item_id") or "")
                    output_index = self._event_value(event, "output_index")
                    _, target = ensure_tool_call(item_id, output_index)
                    target["id"] = tool_call["id"]
                    target["function"]["name"] = tool_call["function"]["name"]
                    if tool_call["function"]["arguments"]:
                        target["function"]["arguments"] = tool_call["function"]["arguments"]
                continue

            if event_type in {"response.created", "response.in_progress", "response.completed"}:
                response = self._event_value(event, "response")
                response_payload = self._response_summary(response)
                response_id = response_payload.get("id") or response_id
                if event_type != "response.completed":
                    continue
                completed_event_seen = True
                output_items = self._event_value(response, "output")
                if not full_content:
                    full_content = self._text_from_responses_output(output_items)
                if isinstance(output_items, list):
                    for output_index, item in enumerate(output_items):
                        tool_call = self._tool_call_from_responses_item(item)
                        if not tool_call:
                            continue
                        item_id = str(self._event_value(item, "id") or "")
                        _, target = ensure_tool_call(item_id, output_index)
                        target["id"] = tool_call["id"]
                        target["function"]["name"] = tool_call["function"]["name"]
                        target["function"]["arguments"] = tool_call["function"]["arguments"]
                response_usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
                usage = self._usage_from_responses_usage(response_usage)
                finish_reason = "tool_calls" if accumulated_tool_calls else "stop"
                if accumulated_tool_calls and not tool_call_ready_emitted:
                    tool_call_ready_emitted = True
                    timings["tool_call_ready_ms"] = elapsed_ms
                    yield {
                        "type": "tool_call_ready",
                        "elapsed_ms": elapsed_ms,
                        "tool_calls": deepcopy(accumulated_tool_calls),
                    }
                timings["completed_ms"] = elapsed_ms
                yield {
                    "type": "done",
                    "full_content": full_content,
                    "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                    "usage": usage,
                    "finish_reason": finish_reason,
                    "timings": timings,
                    "response_id": response_id,
                    "response": response_payload,
                    "provider_mode": self.provider_mode,
                    "provider_request": input_diagnostics,
                }
                break

            if event_type in {"response.failed", "response.incomplete"}:
                response = self._event_value(event, "response")
                response_payload = self._response_summary(response)
                error = self._event_value(response, "error") if response is not None else None
                raise RuntimeError(str(error or response_payload.get("status") or event_type))

            if event_type == "error":
                raise RuntimeError(str(self._event_value(event, "message") or "Responses stream error"))

        if not completed_event_seen:
            timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
            yield {
                "type": "done",
                "full_content": full_content,
                "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                "usage": usage,
                "finish_reason": finish_reason,
                "timings": timings,
                "response_id": response_id,
                "response": response_payload,
                "provider_mode": self.provider_mode,
                "provider_request": input_diagnostics,
            }

    async def compact_responses_context(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict] | None = None,
    ) -> Dict[str, Any]:
        """Call the provider-native Responses compact endpoint for a full context window."""

        if self.provider_mode not in {PROVIDER_MODE_RESPONSES_HTTP, PROVIDER_MODE_RESPONSES_WEBSOCKET}:
            raise RuntimeError("Responses compact requires a Responses provider mode")

        compact_call = getattr(self.client.responses, "compact", None)
        if compact_call is None:
            raise RuntimeError("Responses compact is not supported by the installed OpenAI SDK")

        instructions, input_items, input_diagnostics = self._responses_request_parts(
            messages,
            previous_response_id=None,
        )
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "input": input_items,
        }
        if instructions:
            kwargs["instructions"] = instructions
        responses_tools = self._responses_tools_from_chat_tools(tools)
        if responses_tools:
            kwargs["tools"] = responses_tools

        response = await compact_call(**kwargs)
        payload = self._jsonable(response)
        if not isinstance(payload, dict):
            payload = {}
        output = payload.get("output")
        if not isinstance(output, list) or not output:
            raise RuntimeError("Responses compact returned no output window")

        compaction_item = self._compaction_item_from_output(output)
        usage = self._usage_from_responses_usage(payload.get("usage"))
        return {
            "id": str(compaction_item.get("id") or payload.get("id") or "").strip() or None,
            "response_id": str(payload.get("id") or "").strip() or None,
            "status": payload.get("status"),
            "output": output,
            "usage": usage,
            "request": {
                "model": self.model,
                "input_item_count": len(input_items),
                "tool_count": len(responses_tools or []),
                "has_instructions": bool(instructions),
            },
            "provider_request": input_diagnostics,
            "response": {
                key: payload.get(key)
                for key in ("id", "status")
                if payload.get(key) is not None
            },
            "compaction_item": compaction_item,
        }

    async def _responses_http_stream(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        *,
        previous_response_id: str | None = None,
        compact_context: Dict[str, Any] | None = None,
    ):
        request_started_at = time.perf_counter()
        timings: Dict[str, int] = {}
        attempt = 0
        retry_budget_seconds = 300.0
        first_visible_event_emitted = False
        try:
            kwargs, input_diagnostics, previous_response_id = self._responses_request_kwargs(
                messages,
                tools,
                previous_response_id=previous_response_id,
                compact_context=compact_context,
                stream=True,
            )

            request_dispatched_at = time.perf_counter()
            timings["request_sent_ms"] = int((request_dispatched_at - request_started_at) * 1000)
            yield {
                "type": "request_sent",
                "elapsed_ms": timings["request_sent_ms"],
                "provider_mode": self.provider_mode,
                "previous_response_id": previous_response_id,
                "provider_request": input_diagnostics,
            }

            retried_without_previous_response_id = False
            while True:
                attempt += 1
                try:
                    stream = await self.client.responses.create(**kwargs)
                    full_content = ""
                    accumulated_tool_calls: List[Dict[str, Any]] = []
                    tool_call_indexes: Dict[str, int] = {}
                    usage = None
                    finish_reason = None
                    response_payload: Dict[str, Any] = {}
                    response_id = None
                    first_chunk_seen = False
                    tool_call_ready_emitted = False
                    completed_event_seen = False

                    def ensure_tool_call(item_id: str, output_index: int | None = None) -> tuple[int, Dict[str, Any]]:
                        key = item_id or f"output:{output_index if output_index is not None else len(accumulated_tool_calls)}"
                        if key in tool_call_indexes:
                            idx = tool_call_indexes[key]
                            return idx, accumulated_tool_calls[idx]
                        idx = len(accumulated_tool_calls)
                        tool_call_indexes[key] = idx
                        accumulated_tool_calls.append(
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )
                        return idx, accumulated_tool_calls[idx]

                    async for event in stream:
                        elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
                        if not first_chunk_seen:
                            first_chunk_seen = True
                            first_visible_event_emitted = True
                            timings["first_chunk_ms"] = elapsed_ms
                            yield {"type": "first_chunk", "elapsed_ms": elapsed_ms}

                        event_type = str(self._event_value(event, "type") or "")
                        if event_type == "response.output_text.delta":
                            delta = str(self._event_value(event, "delta") or "")
                            if delta:
                                if "first_content_ms" not in timings:
                                    timings["first_content_ms"] = elapsed_ms
                                    yield {"type": "first_content", "elapsed_ms": elapsed_ms}
                                full_content += delta
                                first_visible_event_emitted = True
                                yield {"type": "content", "delta": delta}
                            continue

                        if event_type == "response.function_call_arguments.delta":
                            item_id = str(self._event_value(event, "item_id") or "")
                            output_index = self._event_value(event, "output_index")
                            idx, tool_call = ensure_tool_call(item_id, output_index)
                            delta = str(self._event_value(event, "delta") or "")
                            if delta:
                                tool_call["function"]["arguments"] += delta
                            if "first_tool_call_ms" not in timings:
                                timings["first_tool_call_ms"] = elapsed_ms
                            first_visible_event_emitted = True
                            snapshot = deepcopy(tool_call)
                            yield {
                                "type": "tool_call_delta",
                                "tool_call_index": idx,
                                "tool_call": snapshot,
                                "tool_name": snapshot.get("function", {}).get("name") or "",
                                "arguments": snapshot.get("function", {}).get("arguments") or "",
                                "elapsed_ms": elapsed_ms,
                            }
                            continue

                        if event_type == "response.function_call_arguments.done":
                            item_id = str(self._event_value(event, "item_id") or "")
                            output_index = self._event_value(event, "output_index")
                            _, tool_call = ensure_tool_call(item_id, output_index)
                            tool_call["function"]["arguments"] = str(self._event_value(event, "arguments") or "")
                            name = str(self._event_value(event, "name") or "").strip()
                            if name:
                                tool_call["function"]["name"] = name
                            continue

                        if event_type in {"response.output_item.added", "response.output_item.done"}:
                            item = self._event_value(event, "item")
                            tool_call = self._tool_call_from_responses_item(item)
                            if tool_call:
                                item_id = str(self._event_value(item, "id") or self._event_value(event, "item_id") or "")
                                output_index = self._event_value(event, "output_index")
                                _, target = ensure_tool_call(item_id, output_index)
                                target["id"] = tool_call["id"]
                                target["function"]["name"] = tool_call["function"]["name"]
                                if tool_call["function"]["arguments"]:
                                    target["function"]["arguments"] = tool_call["function"]["arguments"]
                            continue

                        if event_type in {"response.created", "response.in_progress", "response.completed"}:
                            response = self._event_value(event, "response")
                            response_payload = self._response_summary(response)
                            response_id = response_payload.get("id") or response_id
                            if event_type != "response.completed":
                                continue
                            completed_event_seen = True
                            response_usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
                            usage = self._usage_from_responses_usage(response_usage)
                            finish_reason = "tool_calls" if accumulated_tool_calls else "stop"
                            if accumulated_tool_calls and not tool_call_ready_emitted:
                                tool_call_ready_emitted = True
                                timings["tool_call_ready_ms"] = elapsed_ms
                                yield {
                                    "type": "tool_call_ready",
                                    "elapsed_ms": elapsed_ms,
                                    "tool_calls": deepcopy(accumulated_tool_calls),
                                }
                            timings["completed_ms"] = elapsed_ms
                            yield {
                                "type": "done",
                                "full_content": full_content,
                                "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                                "usage": usage,
                                "finish_reason": finish_reason,
                                "timings": timings,
                                "response_id": response_id,
                                "response": response_payload,
                                "provider_mode": self.provider_mode,
                                "provider_request": input_diagnostics,
                            }
                            break

                        if event_type in {"response.failed", "response.incomplete"}:
                            response = self._event_value(event, "response")
                            response_payload = self._response_summary(response)
                            response_id = response_payload.get("id") or response_id
                            error = getattr(response, "error", None)
                            raise RuntimeError(str(error or response_payload.get("status") or event_type))

                        if event_type == "error":
                            raise RuntimeError(str(self._event_value(event, "message") or "Responses stream error"))

                    if not completed_event_seen:
                        timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
                        yield {
                            "type": "done",
                            "full_content": full_content,
                            "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                            "usage": usage,
                            "finish_reason": finish_reason,
                            "timings": timings,
                            "response_id": response_id,
                            "response": response_payload,
                            "provider_mode": self.provider_mode,
                            "provider_request": input_diagnostics,
                        }
                    break
                except Exception as stream_error:
                    if (
                        previous_response_id
                        and not first_visible_event_emitted
                        and not retried_without_previous_response_id
                        and _responses_previous_response_id_unsupported(stream_error)
                    ):
                        retried_without_previous_response_id = True
                        logger.warning(
                            "Responses HTTP provider rejected previous_response_id; retrying with full context: model=%s error=%r",
                            self.model,
                            stream_error,
                        )
                        self._record_network_event(
                            request_payload=kwargs,
                            duration_ms=int((time.perf_counter() - request_started_at) * 1000),
                            success=False,
                            error=f"{type(stream_error).__name__}: {stream_error}",
                            metadata={
                                "stream": True,
                                "provider_mode": self.provider_mode,
                                "response_state_fallback": True,
                                "retry_attempt": attempt,
                            },
                        )
                        kwargs, input_diagnostics, previous_response_id = self._responses_request_kwargs(
                            messages,
                            tools,
                            previous_response_id=None,
                            compact_context=compact_context,
                            stream=True,
                        )
                        input_diagnostics["response_state_fallback"] = True
                        input_diagnostics["response_state_fallback_reason"] = "previous_response_id_unsupported"
                        continue

                    elapsed_seconds = time.perf_counter() - request_started_at
                    delay_seconds = _tool_chat_retry_delay_seconds(attempt)
                    next_elapsed_seconds = elapsed_seconds + delay_seconds
                    will_retry = (
                        not first_visible_event_emitted
                        and _is_retryable_upstream_failure(stream_error)
                        and next_elapsed_seconds <= retry_budget_seconds
                    )
                    if will_retry:
                        logger.warning(
                            "Responses stream upstream failure before first output; retrying: model=%s attempt=%s elapsed_s=%.1f next_delay_s=%.1f retry_budget_s=%.1f error=%r",
                            self.model,
                            attempt,
                            elapsed_seconds,
                            delay_seconds,
                            retry_budget_seconds,
                            stream_error,
                        )
                        self._record_network_event(
                            request_payload=kwargs,
                            duration_ms=int((time.perf_counter() - request_started_at) * 1000),
                            success=False,
                            error=f"{type(stream_error).__name__}: {stream_error}",
                            metadata={
                                "stream": True,
                                "provider_mode": self.provider_mode,
                                "retryable": True,
                                "retry_attempt": attempt,
                                "will_retry": True,
                                "retry_delay_seconds": delay_seconds,
                                "retry_budget_seconds": retry_budget_seconds,
                                "retry_phase": "pre_first_output",
                            },
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    raise
        except Exception as e:
            if "completed_ms" not in timings:
                timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
            logger.error(
                "Responses stream failed: model=%s attempts=%s type=%s repr=%r cause=%r context=%r timings=%s\n%s",
                self.model,
                attempt,
                type(e).__name__,
                e,
                e.__cause__,
                e.__context__,
                timings,
                traceback.format_exc(),
            )
            self._record_network_event(
                request_payload=locals().get("kwargs", {"messages": messages, "tools": tools, "stream": True}),
                duration_ms=timings["completed_ms"],
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={"stream": True, "aggregated": True, "attempts": attempt, "provider_mode": self.provider_mode},
            )
            yield {"type": "error", "error": str(e), "timings": timings}

    async def _responses_websocket_stream(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        *,
        previous_response_id: str | None = None,
        compact_context: Dict[str, Any] | None = None,
    ):
        request_started_at = time.perf_counter()
        timings: Dict[str, int] = {}
        attempt = 0
        retry_budget_seconds = 300.0
        first_visible_event_emitted = False
        kwargs: Dict[str, Any] = {"messages": messages, "tools": tools}
        try:
            kwargs, input_diagnostics, previous_response_id = self._responses_request_kwargs(
                messages,
                tools,
                previous_response_id=previous_response_id,
                compact_context=compact_context,
                stream=False,
            )
            ws_url = self._responses_websocket_url()
            headers = {"Authorization": f"Bearer {self._api_key or ''}"}
            response_create_event = {
                "type": "response.create",
                **kwargs,
            }

            while True:
                attempt += 1
                try:
                    async with _connect_responses_websocket(ws_url, headers) as websocket:
                        await websocket.send(json.dumps(response_create_event, ensure_ascii=False))
                        request_dispatched_at = time.perf_counter()
                        timings["request_sent_ms"] = int((request_dispatched_at - request_started_at) * 1000)
                        yield {
                            "type": "request_sent",
                            "elapsed_ms": timings["request_sent_ms"],
                            "provider_mode": self.provider_mode,
                            "previous_response_id": previous_response_id,
                            "provider_request": input_diagnostics,
                            "transport": "websocket",
                        }

                        async def websocket_events():
                            async for raw_event in websocket:
                                if isinstance(raw_event, bytes):
                                    raw_event = raw_event.decode("utf-8", errors="replace")
                                try:
                                    payload = json.loads(str(raw_event or "{}"))
                                except json.JSONDecodeError as exc:
                                    raise RuntimeError("Responses WebSocket returned invalid JSON") from exc
                                if isinstance(payload, dict):
                                    yield payload

                        async for event in self._iter_responses_stream_events(
                            websocket_events(),
                            request_started_at=request_started_at,
                            timings=timings,
                            input_diagnostics=input_diagnostics,
                        ):
                            if event.get("type") in {
                                "first_chunk",
                                "first_content",
                                "content",
                                "tool_call_delta",
                                "tool_call_ready",
                                "done",
                            }:
                                first_visible_event_emitted = True
                            yield event
                            if event.get("type") == "done":
                                self._record_network_event(
                                    request_payload=response_create_event,
                                    response_payload=event.get("response") or {"response_id": event.get("response_id")},
                                    duration_ms=int((time.perf_counter() - request_started_at) * 1000),
                                    success=True,
                                    metadata={
                                        "stream": True,
                                        "transport": "websocket",
                                        "aggregated": True,
                                        "attempts": attempt,
                                        "provider_mode": self.provider_mode,
                                        "websocket_url": ws_url,
                                    },
                                )
                                break
                    break
                except Exception as stream_error:
                    elapsed_seconds = time.perf_counter() - request_started_at
                    delay_seconds = _tool_chat_retry_delay_seconds(attempt)
                    next_elapsed_seconds = elapsed_seconds + delay_seconds
                    will_retry = (
                        not first_visible_event_emitted
                        and _is_retryable_upstream_failure(stream_error)
                        and next_elapsed_seconds <= retry_budget_seconds
                    )
                    if will_retry:
                        logger.warning(
                            "Responses WebSocket upstream failure before first output; retrying: model=%s attempt=%s elapsed_s=%.1f next_delay_s=%.1f retry_budget_s=%.1f error=%r",
                            self.model,
                            attempt,
                            elapsed_seconds,
                            delay_seconds,
                            retry_budget_seconds,
                            stream_error,
                        )
                        self._record_network_event(
                            request_payload=response_create_event,
                            duration_ms=int((time.perf_counter() - request_started_at) * 1000),
                            success=False,
                            error=f"{type(stream_error).__name__}: {stream_error}",
                            metadata={
                                "stream": True,
                                "transport": "websocket",
                                "provider_mode": self.provider_mode,
                                "retryable": True,
                                "retry_attempt": attempt,
                                "will_retry": True,
                                "retry_delay_seconds": delay_seconds,
                                "retry_budget_seconds": retry_budget_seconds,
                                "retry_phase": "pre_first_output",
                                "websocket_url": ws_url,
                            },
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    raise
        except Exception as e:
            if "completed_ms" not in timings:
                timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
            logger.error(
                "Responses WebSocket stream failed: model=%s attempts=%s type=%s repr=%r timings=%s\n%s",
                self.model,
                attempt,
                type(e).__name__,
                e,
                timings,
                traceback.format_exc(),
            )
            self._record_network_event(
                request_payload=kwargs,
                duration_ms=timings["completed_ms"],
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={
                    "stream": True,
                    "transport": "websocket",
                    "aggregated": True,
                    "attempts": attempt,
                    "provider_mode": self.provider_mode,
                },
            )
            yield {"type": "error", "error": str(e), "timings": timings}

    async def chat_stream(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        *,
        previous_response_id: str | None = None,
        provider_session: Dict[str, Any] | None = None,
    ):
        """
        流式聊天（SSE generator，支持 multimodal content）

        Yields:
            dict: {"type": "request_sent"|"first_chunk"|"first_content"|
                   "tool_call_delta"|"tool_call_ready"|"content"|"done"|"error", ...}
        """
        if previous_response_id is None and isinstance(provider_session, dict):
            previous_response_id = (
                provider_session.get("previous_response_id")
                or provider_session.get("last_response_id")
            )
        compact_context = self._provider_compaction_context(provider_session)
        if compact_context is not None:
            previous_response_id = None
        if self.provider_mode == PROVIDER_MODE_RESPONSES_HTTP:
            async for event in self._responses_http_stream(
                messages,
                tools,
                previous_response_id=str(previous_response_id or "").strip() or None,
                compact_context=compact_context,
            ):
                yield event
            return
        if self.provider_mode == PROVIDER_MODE_RESPONSES_WEBSOCKET:
            async for event in self._responses_websocket_stream(
                messages,
                tools,
                previous_response_id=str(previous_response_id or "").strip() or None,
                compact_context=compact_context,
            ):
                yield event
            return

        request_started_at = time.perf_counter()
        request_dispatched_at = request_started_at
        timings: Dict[str, int] = {}
        attempt = 0
        retry_budget_seconds = 300.0
        first_visible_event_emitted = False
        try:
            # Normalize messages: ensure content can be str or list
            normalized = self._normalize_messages(messages)
            kwargs = {
                "model": self.model,
                "messages": normalized,
                "temperature": 0.7,
                "stream": True
            }
            if tools:
                kwargs["tools"] = tools
            request_stream_usage = self._stream_usage_supported is not False
            if request_stream_usage:
                kwargs["stream_options"] = {"include_usage": True}

            request_dispatched_at = time.perf_counter()
            timings["request_sent_ms"] = int((request_dispatched_at - request_started_at) * 1000)
            yield {"type": "request_sent", "elapsed_ms": timings["request_sent_ms"]}

            while True:
                attempt += 1
                try:
                    try:
                        stream = await self.client.chat.completions.create(**kwargs)
                        if request_stream_usage:
                            self._stream_usage_supported = True
                    except Exception as create_error:
                        if request_stream_usage and _supports_stream_usage_fallback(create_error):
                            logger.warning(
                                "Model '%s' rejected stream_options.include_usage; retrying stream without usage collection. error=%s",
                                self.model,
                                create_error,
                            )
                            self._stream_usage_supported = False
                            request_stream_usage = False
                            kwargs = dict(kwargs)
                            kwargs.pop("stream_options", None)
                            continue
                        raise

                    full_content = ""
                    accumulated_tool_calls = []
                    usage = None
                    finish_reason = None
                    first_chunk_seen = False
                    tool_call_ready_emitted = False

                    async for chunk in stream:
                        elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
                        if not first_chunk_seen:
                            first_chunk_seen = True
                            first_visible_event_emitted = True
                            timings["first_chunk_ms"] = elapsed_ms
                            yield {"type": "first_chunk", "elapsed_ms": elapsed_ms}

                        choice = chunk.choices[0] if chunk.choices else None

                        # 捕获 usage（流式模式下通常在最后一个 chunk）
                        if hasattr(chunk, 'usage') and chunk.usage:
                            usage = {
                                "prompt_tokens": getattr(chunk.usage, 'prompt_tokens', 0),
                                "completion_tokens": getattr(chunk.usage, 'completion_tokens', 0),
                                "total_tokens": getattr(chunk.usage, 'total_tokens', 0),
                            }

                        if not choice:
                            continue

                        delta = choice.delta

                        if delta.content:
                            if "first_content_ms" not in timings:
                                timings["first_content_ms"] = elapsed_ms
                                yield {"type": "first_content", "elapsed_ms": elapsed_ms}
                            full_content += delta.content
                            first_visible_event_emitted = True
                            yield {"type": "content", "delta": delta.content}

                        if delta.tool_calls:
                            if "first_tool_call_ms" not in timings:
                                timings["first_tool_call_ms"] = elapsed_ms
                            for tc_delta in delta.tool_calls:
                                first_visible_event_emitted = True
                                idx = tc_delta.index
                                while len(accumulated_tool_calls) <= idx:
                                    accumulated_tool_calls.append({
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""}
                                    })

                                if tc_delta.id:
                                    accumulated_tool_calls[idx]["id"] = tc_delta.id
                                if tc_delta.function:
                                    if tc_delta.function.name:
                                        accumulated_tool_calls[idx]["function"]["name"] = tc_delta.function.name
                                    if tc_delta.function.arguments:
                                        accumulated_tool_calls[idx]["function"]["arguments"] += tc_delta.function.arguments
                                snapshot = deepcopy(accumulated_tool_calls[idx])
                                yield {
                                    "type": "tool_call_delta",
                                    "tool_call_index": idx,
                                    "tool_call": snapshot,
                                    "tool_name": snapshot.get("function", {}).get("name") or "",
                                    "arguments": snapshot.get("function", {}).get("arguments") or "",
                                    "elapsed_ms": elapsed_ms,
                                }

                        if choice.finish_reason in ("stop", "tool_calls", "length"):
                            finish_reason = choice.finish_reason
                            if finish_reason == "tool_calls" and not tool_call_ready_emitted:
                                tool_call_ready_emitted = True
                                timings["tool_call_ready_ms"] = elapsed_ms
                                yield {
                                    "type": "tool_call_ready",
                                    "elapsed_ms": elapsed_ms,
                                    "tool_calls": deepcopy(accumulated_tool_calls),
                                }
                            continue

                    timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
                    if attempt > 1:
                        logger.info(
                            "LLM stream retry succeeded before first output: model=%s attempts=%s",
                            self.model,
                            attempt,
                        )
                    yield {
                        "type": "done",
                        "full_content": full_content,
                        "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                        "usage": usage,
                        "finish_reason": finish_reason,
                        "timings": timings,
                    }
                    break
                except Exception as stream_error:
                    elapsed_seconds = time.perf_counter() - request_started_at
                    delay_seconds = _tool_chat_retry_delay_seconds(attempt)
                    next_elapsed_seconds = elapsed_seconds + delay_seconds
                    will_retry = (
                        not first_visible_event_emitted
                        and _is_retryable_upstream_failure(stream_error)
                        and next_elapsed_seconds <= retry_budget_seconds
                    )
                    if will_retry:
                        logger.warning(
                            "LLM stream upstream failure before first output; retrying: model=%s attempt=%s elapsed_s=%.1f next_delay_s=%.1f retry_budget_s=%.1f error=%r",
                            self.model,
                            attempt,
                            elapsed_seconds,
                            delay_seconds,
                            retry_budget_seconds,
                            stream_error,
                        )
                        self._record_network_event(
                            request_payload=kwargs,
                            duration_ms=int((time.perf_counter() - request_started_at) * 1000),
                            success=False,
                            error=f"{type(stream_error).__name__}: {stream_error}",
                            metadata={
                                "stream": True,
                                "retryable": True,
                                "retry_attempt": attempt,
                                "will_retry": True,
                                "retry_delay_seconds": delay_seconds,
                                "retry_budget_seconds": retry_budget_seconds,
                                "retry_phase": "pre_first_output",
                            },
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    raise

        except Exception as e:
            if "completed_ms" not in timings:
                timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
            logger.error(
                "LLM stream failed: model=%s attempts=%s type=%s repr=%r cause=%r context=%r timings=%s\n%s",
                self.model,
                attempt,
                type(e).__name__,
                e,
                e.__cause__,
                e.__context__,
                timings,
                traceback.format_exc(),
            )
            self._record_network_event(
                request_payload=locals().get("kwargs", {"messages": messages, "tools": tools, "stream": True}),
                duration_ms=timings["completed_ms"],
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={"stream": True, "aggregated": True, "attempts": attempt},
            )
            yield {"type": "error", "error": str(e), "timings": timings}


def _resolve_env_vars(value: str) -> str:
    """解析字符串中的 ${ENV_VAR} 占位符"""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        return os.getenv(env_name, value)
    return value


def _provider_from_config_section(
    section: Dict[str, Any] | None,
    *,
    fallback: Dict[str, Any] | None = None,
) -> Optional[Dict[str, str]]:
    if not isinstance(section, dict):
        return None
    provider = section.get("provider", {})
    if not isinstance(provider, dict):
        return None
    base_url = provider.get("baseUrl", "")
    api_key = _resolve_env_vars(provider.get("apiKey", ""))
    model = section.get("default_model", "")
    if not model:
        models = provider.get("models", [])
        if models:
            model = models[0].get("id", "")
    if not base_url or not model:
        return None
    runtime = _runtime_config_from_payload(section, fallback=fallback)
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        **runtime,
    }


def _load_agent_provider(agent_name: str) -> Optional[Dict[str, str]]:
    """
    从 agents.json 加载指定 Agent 的 provider 配置

    优先级：Agent 自身 provider > global_llm provider

    Returns: {"base_url": str, "api_key": str, "model": str} 或 None
    """
    config_file = settings.AGENT_CONFIG_FILE
    if not os.path.exists(config_file):
        return None
    agent_type = normalize_agent_type(agent_name)

    try:
        with open(config_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        # 先尝试 Agent 自身配置
        agents = data.get("agents", {})
        agent_data = agents.get(agent_type) or agents.get(agent_name)
        if agent_data is None and agent_type == DEFAULT_AGENT_TYPE:
            agent_data = agents.get("assistant")
        if agent_data:
            resolved = _provider_from_config_section(
                agent_data,
                fallback=data.get("global_llm", {}),
            )
            if resolved is not None:
                return resolved

        # fallback: 全局 LLM 配置
        return _load_global_provider(data)

    except Exception as e:
        logger.warning(f"Failed to load provider config for agent '{agent_type}': {e}")
        return None


def _load_global_provider(data: Dict = None) -> Optional[Dict[str, str]]:
    """从 agents.json 的 global_llm 段加载全局 provider 配置"""
    if data is None:
        config_file = settings.AGENT_CONFIG_FILE
        if not os.path.exists(config_file):
            return None
        try:
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except Exception:
            return None

    return _provider_from_config_section(data.get("global_llm", {}))


def _load_framework_provider(data: Dict = None) -> Optional[Dict[str, str]]:
    """Load the dedicated framework provider without agent/global fallback."""
    if data is None:
        config_file = settings.AGENT_CONFIG_FILE
        if not os.path.exists(config_file):
            return None
        try:
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except Exception:
            return None

    return _provider_from_config_section(data.get("framework_llm", {}))


def _load_framework_fallback_provider(data: Dict = None) -> Optional[Dict[str, str]]:
    """Load the explicitly configured framework fallback provider, if enabled."""
    if data is None:
        config_file = settings.AGENT_CONFIG_FILE
        if not os.path.exists(config_file):
            return None
        try:
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except Exception:
            return None

    framework_cfg = data.get("framework_llm", {}) if isinstance(data, dict) else {}
    fallback_cfg = framework_cfg.get("fallback", {}) if isinstance(framework_cfg, dict) else {}
    if not isinstance(fallback_cfg, dict) or fallback_cfg.get("enabled") is not True:
        return None
    return _provider_from_config_section(fallback_cfg)


def get_llm_client_for_agent(agent_name: str) -> LLMClient:
    """
    获取指定 Agent 的 LLM 客户端（带缓存）

    配置来源：agents.json → 该 Agent 的 provider 配置
    """
    agent_name = normalize_agent_type(agent_name)
    if _llm_client is not None:
        return _llm_client

    # 命中缓存
    if agent_name in _client_cache:
        return _client_cache[agent_name]

    # 从 agents.json 加载
    provider = _load_agent_provider(agent_name)
    if provider:
        client = LLMClient(
            base_url=provider["base_url"],
            api_key=provider["api_key"],
            model=provider["model"],
            agent_name=agent_name,
            provider_mode=provider.get("provider_mode"),
        )
        _client_cache[agent_name] = client
        logger.info(f"Created LLM client for agent '{agent_name}': {provider['base_url']} / {provider['model']}")
        return client

    # fallback：使用 agents.json 中第一个有 provider 的 Agent 配置
    fallback = _get_first_provider()
    if fallback:
        client = LLMClient(
            base_url=fallback["base_url"],
            api_key=fallback["api_key"],
            model=fallback["model"],
            agent_name=agent_name,
            provider_mode=fallback.get("provider_mode"),
        )
        _client_cache[agent_name] = client
        logger.warning(f"Agent '{agent_name}' has no provider config, using fallback: {fallback['model']}")
        return client

    raise RuntimeError(
        f"No LLM provider configured for agent '{agent_name}'. "
        f"Please configure provider in {settings.AGENT_CONFIG_FILE}"
    )


def get_framework_llm_client() -> LLMClient:
    """Get the dedicated LLM client for Catown framework maintenance tasks."""
    global _framework_client
    if _framework_client is not None:
        return _framework_client

    provider = _load_framework_provider()
    if not provider:
        raise RuntimeError(
            f"No framework LLM provider configured. Please configure framework_llm in {settings.AGENT_CONFIG_FILE}"
        )

    _framework_client = LLMClient(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        model=provider["model"],
        agent_name="framework",
        provider_mode=provider.get("provider_mode"),
    )
    logger.info("Created framework LLM client: %s / %s", provider["base_url"], provider["model"])
    return _framework_client


def get_framework_fallback_llm_client() -> Optional[LLMClient]:
    """Return the optional framework fallback LLM client."""
    global _framework_fallback_client
    if _framework_fallback_client is not None:
        return _framework_fallback_client

    provider = _load_framework_fallback_provider()
    if not provider:
        return None

    _framework_fallback_client = LLMClient(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        model=provider["model"],
        agent_name="framework-fallback",
        provider_mode=provider.get("provider_mode"),
    )
    logger.info("Created framework fallback LLM client: %s / %s", provider["base_url"], provider["model"])
    return _framework_fallback_client


async def chat_framework_llm(messages: List[Dict[str, Any]], **kwargs) -> str:
    """Call the framework LLM, using only the configured framework fallback on failure."""
    try:
        return await get_framework_llm_client().chat(messages, **kwargs)
    except Exception as primary_exc:
        fallback = get_framework_fallback_llm_client()
        if fallback is None:
            raise
        logger.warning(
            "Framework LLM failed; using configured framework fallback: %s",
            primary_exc,
        )
        return await fallback.chat(messages, **kwargs)


def get_default_llm_client() -> LLMClient:
    """
    获取默认 LLM 客户端（用于无 Agent 上下文的兼容场景）

    配置来源：agents.json 中第一个有 provider 的 Agent
    """
    if _llm_client is not None:
        return _llm_client

    # 尝试找一个已缓存的
    if _client_cache:
        return next(iter(_client_cache.values()))

    fallback = _get_first_provider()
    if fallback:
        client = LLMClient(
            base_url=fallback["base_url"],
            api_key=fallback["api_key"],
            model=fallback["model"],
            agent_name="default",
            provider_mode=fallback.get("provider_mode"),
        )
        return client

    raise RuntimeError(
        f"No LLM provider configured. Please configure at least one agent's provider in {settings.AGENT_CONFIG_FILE}"
    )


# 全局默认客户端（用于 set_llm_client 测试兼容）
_default_client: Optional[LLMClient] = None


def set_llm_client(client: Optional[LLMClient]):
    """
    设置全局默认 LLM 客户端（测试用）

    Args:
        client: 新的客户端实例，None 表示重置
    """
    global _default_client
    _default_client = client


def get_llm_client() -> LLMClient:
    """
    获取默认 LLM 客户端（向后兼容入口）

    支持 set_llm_client 设置的全局客户端。
    优先返回已缓存的客户端，否则从 agents.json 第一个 Agent 加载。
    新代码建议使用 get_llm_client_for_agent(agent_name)。
    """
    global _default_client
    if _default_client is not None:
        return _default_client
    try:
        _default_client = get_default_llm_client()
    except RuntimeError:
        # fallback: 用环境变量创建
        _default_client = LLMClient()
    return _default_client


def _get_first_provider() -> Optional[Dict[str, str]]:
    """从 agents.json 获取第一个有 provider 配置的 Agent，兜底用 global_llm"""
    config_file = settings.AGENT_CONFIG_FILE
    if not os.path.exists(config_file):
        return None

    try:
        with open(config_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        for agent_name, agent_data in data.get("agents", {}).items():
            provider = agent_data.get("provider", {})
            base_url = provider.get("baseUrl", "")
            api_key = _resolve_env_vars(provider.get("apiKey", ""))

            model = agent_data.get("default_model", "")
            if not model:
                models = provider.get("models", [])
                if models:
                    model = models[0].get("id", "")

            if base_url and model:
                runtime = _runtime_config_from_payload(
                    agent_data,
                    fallback=data.get("global_llm", {}),
                )
                return {
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                    **runtime,
                }

        # fallback: 全局配置
        return _load_global_provider(data)

    except Exception as e:
        logger.warning(f"Failed to load first provider from agents.json: {e}")

    return None


def clear_client_cache():
    """清空客户端缓存（配置更新后调用）"""
    global _default_client, _framework_client, _framework_fallback_client
    _client_cache.clear()
    _default_client = None
    _framework_client = None
    _framework_fallback_client = None
    logger.info("LLM client cache cleared")
