# -*- coding: utf-8 -*-
"""
LLM 客户端封装

支持 per-Agent 独立 LLM 配置，所有配置来源为 agents.json。
"""
from typing import Awaitable, Callable, List, Dict, Any, Optional
from openai import AsyncOpenAI
from openai._base_client import DefaultAsyncHttpxClient
from copy import deepcopy
import httpx
import json
import os
import logging
import time
import traceback
import uuid
from urllib.parse import urlparse

from agents.identity import DEFAULT_AGENT_TYPE, normalize_agent_type
from config import settings
from monitoring import monitor_network_buffer

logger = logging.getLogger("catown.llm")


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


def _safe_text_bytes(value: bytes | str | None, limit: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value if limit is None else value[:limit]
    text = value.decode("utf-8", errors="replace")
    return text if limit is None else text[:limit]


def _sanitize_http_headers(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    try:
        items = headers.multi_items()
    except AttributeError:
        try:
            items = headers.items()
        except AttributeError:
            return {}
    return {str(key): str(value) for key, value in items}


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
_client_cache: Dict[str, "LLMClient"] = {}

# 旧测试夹具仍会直接注入这个全局 mock。
_llm_client: Optional["LLMClient"] = None


class LLMClient:
    """
    LLM 客户端（OpenAI 兼容接口）

    每个 Agent 可以有独立的 provider 配置（baseUrl, apiKey, model）。
    """

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None, agent_name: str | None = None):
        if base_url is None or api_key is None or model is None:
            # 从环境变量获取默认配置（向后兼容）
            base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
            api_key = api_key or os.getenv("LLM_API_KEY", "")
            model = model or os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self.base_url = base_url
        self.agent_name = agent_name or "agent"
        self.model = model
        self._http_client = DefaultAsyncHttpxClient(
            event_hooks={
                "request": [self._capture_http_request],
                "response": [self._capture_http_response],
            }
        )
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=self._http_client)

    def _llm_target(self, host: str | None = None) -> str:
        parsed = urlparse(self.base_url or "")
        return f"LLM ({host or parsed.netloc or (self.base_url or '')})"

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
        url = request.url
        host = url.netloc.decode("ascii", errors="ignore") if isinstance(url.netloc, bytes) else url.netloc
        path = url.raw_path.decode("utf-8", errors="replace").split("?", 1)[0] if isinstance(url.raw_path, bytes) else str(url.path)
        request_headers = _sanitize_http_headers(request.headers)
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
            "url": str(url),
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
                "content_type": request.headers.get("content-type", "application/json"),
                "preview": _compact_text(_safe_text_bytes(body)),
                "raw_request": _safe_text_bytes(body),
                "raw_response": "",
                "request_headers": request_headers,
                "response_headers": {},
                "flow_id": context["flow_id"],
                "flow_kind": context["flow_kind"],
                "flow_seq": 1,
                "aggregated": False,
                "metadata": {"frame_type": "request"},
            }
        )

    async def _capture_http_response(self, response: httpx.Response) -> None:
        context = response.request.extensions.get("catown_raw_capture")
        if not context:
            return

        response_headers = _sanitize_http_headers(response.headers)
        raw_chunks: List[bytes] = []
        closed = False

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
                "metadata": {"frame_type": "response_start"},
            }
        )
        context["flow_seq"] += 1

        async def on_chunk(chunk: bytes) -> None:
            raw_chunks.append(chunk)
            context["flow_seq"] += 1
            elapsed_ms = int((time.perf_counter() - context["started_at"]) * 1000)
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
                    "content_type": response.headers.get("content-type", ""),
                    "preview": _compact_text(_safe_text_bytes(chunk)),
                    "raw_request": "",
                    "raw_response": _safe_text_bytes(chunk),
                    "request_headers": {},
                    "response_headers": response_headers,
                    "flow_id": context["flow_id"],
                    "flow_kind": context["flow_kind"],
                    "flow_seq": context["flow_seq"],
                    "aggregated": False,
                    "metadata": {"frame_type": "response_chunk"},
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
        monitor_network_buffer.append(
            {
                "category": "backend_llm",
                "source": "backend",
                "protocol": protocol,
                "from_entity": self.agent_name,
                "to_entity": f"LLM ({host})",
                "request_direction": f"{self.agent_name} -> LLM ({host})",
                "response_direction": f"LLM ({host}) -> {self.agent_name}",
                "method": "POST",
                "url": self.base_url,
                "host": host,
                "path": parsed.path or "/",
                "success": success,
                "request_bytes": _estimate_bytes(request_payload),
                "response_bytes": _estimate_bytes(response_payload),
                "duration_ms": duration_ms,
                "content_type": "application/json",
                "preview": _compact_text(response_payload or request_payload),
                "error": error,
                "raw_request": json.dumps(request_payload, ensure_ascii=False, default=str)[:40000] if request_payload is not None else "",
                "raw_response": json.dumps(response_payload, ensure_ascii=False, default=str)[:40000] if response_payload is not None else "",
                "request_headers": {},
                "response_headers": {},
                "metadata": {
                    "model": self.model,
                    "http_version": "1.1+",
                    **(metadata or {}),
                },
            }
        )

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """发送聊天消息"""
        started_at = time.perf_counter()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
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
        """支持工具调用的聊天"""
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7
            }
            if tools:
                kwargs["tools"] = tools

            response = await self.client.chat.completions.create(**kwargs)

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

            return {
                "content": choice.message.content,
                "tool_calls": choice.message.tool_calls if hasattr(choice.message, 'tool_calls') else None,
                "usage": usage,
            }
        except Exception as e:
            raise Exception(f"LLM API error with tools: {str(e)}")

    async def chat_stream(self, messages: List[Dict], tools: List[Dict] = None):
        """
        流式聊天（SSE generator）

        Yields:
            dict: {"type": "request_sent"|"first_chunk"|"first_content"|
                   "tool_call_delta"|"tool_call_ready"|"content"|"done"|"error", ...}
        """
        request_started_at = time.perf_counter()
        request_dispatched_at = request_started_at
        timings: Dict[str, int] = {}
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
                "stream": True
            }
            if tools:
                kwargs["tools"] = tools

            request_dispatched_at = time.perf_counter()
            timings["request_sent_ms"] = int((request_dispatched_at - request_started_at) * 1000)
            yield {"type": "request_sent", "elapsed_ms": timings["request_sent_ms"]}
            stream = await self.client.chat.completions.create(**kwargs)

            full_content = ""
            accumulated_tool_calls = []
            usage = None
            finish_reason = None
            first_chunk_seen = False

            async for chunk in stream:
                elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
                if not first_chunk_seen:
                    first_chunk_seen = True
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
                    yield {"type": "content", "delta": delta.content}

                if delta.tool_calls:
                    if "first_tool_call_ms" not in timings:
                        timings["first_tool_call_ms"] = elapsed_ms
                    for tc_delta in delta.tool_calls:
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
                    if finish_reason == "tool_calls":
                        timings["tool_call_ready_ms"] = elapsed_ms
                        yield {
                            "type": "tool_call_ready",
                            "elapsed_ms": elapsed_ms,
                            "tool_calls": deepcopy(accumulated_tool_calls),
                        }
                    break

            timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
            yield {
                "type": "done",
                "full_content": full_content,
                "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                "usage": usage,
                "finish_reason": finish_reason,
                "timings": timings,
            }

        except Exception as e:
            if "completed_ms" not in timings:
                timings["completed_ms"] = int((time.perf_counter() - request_started_at) * 1000)
            logger.error(
                "LLM stream failed: type=%s repr=%r cause=%r context=%r timings=%s\n%s",
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
                metadata={"stream": True, "aggregated": True},
            )
            yield {"type": "error", "error": str(e), "timings": timings}


def _resolve_env_vars(value: str) -> str:
    """解析字符串中的 ${ENV_VAR} 占位符"""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        return os.getenv(env_name, value)
    return value


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
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 先尝试 Agent 自身配置
        agents = data.get("agents", {})
        agent_data = agents.get(agent_type) or agents.get(agent_name)
        if agent_data is None and agent_type == DEFAULT_AGENT_TYPE:
            agent_data = agents.get("assistant")
        if agent_data:
            provider = agent_data.get("provider", {})
            if provider:
                base_url = provider.get("baseUrl", "")
                api_key = _resolve_env_vars(provider.get("apiKey", ""))
                model = agent_data.get("default_model", "")
                if not model:
                    models = provider.get("models", [])
                    if models:
                        model = models[0].get("id", "")
                if base_url and model:
                    return {"base_url": base_url, "api_key": api_key, "model": model}

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
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return None

    global_cfg = data.get("global_llm", {})
    provider = global_cfg.get("provider", {})
    base_url = provider.get("baseUrl", "")
    api_key = _resolve_env_vars(provider.get("apiKey", ""))
    model = global_cfg.get("default_model", "")
    if not model:
        models = provider.get("models", [])
        if models:
            model = models[0].get("id", "")
    if base_url and model:
        return {"base_url": base_url, "api_key": api_key, "model": model}
    return None


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
        )
        _client_cache[agent_name] = client
        logger.warning(f"Agent '{agent_name}' has no provider config, using fallback: {fallback['model']}")
        return client

    raise RuntimeError(
        f"No LLM provider configured for agent '{agent_name}'. "
        f"Please configure provider in {settings.AGENT_CONFIG_FILE}"
    )


def get_default_llm_client() -> LLMClient:
    """
    获取默认 LLM 客户端（用于无 Agent 上下文的场景，如记忆提取）

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
        with open(config_file, 'r', encoding='utf-8') as f:
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
                return {"base_url": base_url, "api_key": api_key, "model": model}

        # fallback: 全局配置
        return _load_global_provider(data)

    except Exception as e:
        logger.warning(f"Failed to load first provider from agents.json: {e}")

    return None


def clear_client_cache():
    """清空客户端缓存（配置更新后调用）"""
    _client_cache.clear()
    logger.info("LLM client cache cleared")
