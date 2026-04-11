# -*- coding: utf-8 -*-
"""
LLM 客户端封装

支持 per-Agent 独立 LLM 配置，所有配置来源为 agents.json。
"""
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
import json
import os
import logging

from config import settings

logger = logging.getLogger("catown.llm")

# 缓存已创建的客户端：agent_name → LLMClient
_client_cache: Dict[str, "LLMClient"] = {}


class LLMClient:
    """
    LLM 客户端（OpenAI 兼容接口）

    每个 Agent 可以有独立的 provider 配置（baseUrl, apiKey, model）。
    """

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None):
        if base_url is None or api_key is None or model is None:
            # 从环境变量获取默认配置（向后兼容）
            base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
            api_key = api_key or os.getenv("LLM_API_KEY", "")
            model = model or os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.base_url = base_url

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """发送聊天消息"""
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
            return response.choices[0].message.content
        except Exception as e:
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
                return {"content": response, "tool_calls": None}

            if not hasattr(response, 'choices') or not response.choices:
                logger.warning(f"Model '{self.model}' returned unexpected response type: {type(response)}. Falling back to plain chat.")
                return {"content": str(response), "tool_calls": None}

            choice = response.choices[0]
            return {
                "content": choice.message.content,
                "tool_calls": choice.message.tool_calls if hasattr(choice.message, 'tool_calls') else None
            }
        except Exception as e:
            raise Exception(f"LLM API error with tools: {str(e)}")

    async def chat_stream(self, messages: List[Dict], tools: List[Dict] = None):
        """
        流式聊天（SSE generator）

        Yields:
            dict: {"type": "content"|"tool_call"|"done"|"error", ...}
        """
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
                "stream": True
            }
            if tools:
                kwargs["tools"] = tools

            full_content = ""
            accumulated_tool_calls = []

            async for chunk in await self.client.chat.completions.create(**kwargs):
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta

                if delta.content:
                    full_content += delta.content
                    yield {"type": "content", "delta": delta.content}

                if delta.tool_calls:
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

                if choice.finish_reason in ("stop", "tool_calls", "length"):
                    break

            yield {
                "type": "done",
                "full_content": full_content,
                "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None
            }

        except Exception as e:
            yield {"type": "error", "error": str(e)}


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

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 先尝试 Agent 自身配置
        agents = data.get("agents", {})
        agent_data = agents.get(agent_name)
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
        logger.warning(f"Failed to load provider config for agent '{agent_name}': {e}")
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
    # 命中缓存
    if agent_name in _client_cache:
        return _client_cache[agent_name]

    # 从 agents.json 加载
    provider = _load_agent_provider(agent_name)
    if provider:
        client = LLMClient(
            base_url=provider["base_url"],
            api_key=provider["api_key"],
            model=provider["model"]
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
            model=fallback["model"]
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
    # 尝试找一个已缓存的
    if _client_cache:
        return next(iter(_client_cache.values()))

    fallback = _get_first_provider()
    if fallback:
        client = LLMClient(
            base_url=fallback["base_url"],
            api_key=fallback["api_key"],
            model=fallback["model"]
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
