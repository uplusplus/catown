# -*- coding: utf-8 -*-
"""
API 路由 - 主要端点
"""
import logging
import re
import json
import os
import asyncio
import shutil
import subprocess
import traceback
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, field_validator

from agents.identity import (
    DEFAULT_AGENT_TYPE,
    agent_name_of,
    default_agent_name,
    find_agent_by_type,
    is_legacy_default_agent_name,
    legacy_default_agent_names,
    normalize_agent_type,
)
from models.database import get_db, Agent, Project, Chatroom, AgentAssignment, Message, Base
from agents.registry import get_registry
from agents.core import Agent as AgentInstance
from chatrooms.manager import chatroom_manager
from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
from config import settings
from services.monitor_projection import (
    resolve_chatroom_project as monitor_resolve_chatroom_project,
    serialize_monitor_message_item,
    serialize_monitor_runtime_item,
)
from services.session_service import SessionService

logger = logging.getLogger("catown.api")

_HEARTBEAT_EVENT_TYPE = "__heartbeat__"
_RESULT_EVENT_TYPE = "__result__"
MAX_TOOL_ITERATIONS = 50


async def _iter_with_heartbeat(stream, timeout: float = 1.0):
    """Yield stream events, inserting heartbeat markers while waiting."""
    iterator = stream.__aiter__()
    pending = asyncio.create_task(iterator.__anext__())
    try:
        while True:
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=timeout)
            except asyncio.TimeoutError:
                yield {"type": _HEARTBEAT_EVENT_TYPE}
                continue
            except StopAsyncIteration:
                break

            yield event
            pending = asyncio.create_task(iterator.__anext__())
    finally:
        if pending and not pending.done():
            pending.cancel()


async def _await_with_heartbeat(awaitable, timeout: float = 1.0):
    """Await a coroutine, yielding heartbeat markers while it is still running."""
    pending = asyncio.create_task(awaitable)
    try:
        while True:
            try:
                result = await asyncio.wait_for(asyncio.shield(pending), timeout=timeout)
            except asyncio.TimeoutError:
                yield {"type": _HEARTBEAT_EVENT_TYPE}
                continue

            yield {"type": _RESULT_EVENT_TYPE, "result": result}
            break
    finally:
        if pending and not pending.done():
            pending.cancel()


class LLMConfigModel(BaseModel):
    """LLM 配置验证模型"""
    api_key: str
    base_url: Optional[str] = "https://api.openai.com/v1"
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.7
    max_tokens: int = 2000

    @field_validator('api_key')
    @classmethod
    def api_key_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('api_key cannot be empty')
        return v

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, v):
        if v and not v.startswith(('http://', 'https://')):
            raise ValueError('base_url must start with http:// or https://')
        return v.rstrip('/') if v else v

    @field_validator('temperature')
    @classmethod
    def validate_temperature(cls, v):
        if not 0 <= v <= 2:
            raise ValueError('temperature must be between 0 and 2')
        return v

    @field_validator('max_tokens')
    @classmethod
    def validate_max_tokens(cls, v):
        if not 1 <= v <= 100000:
            raise ValueError('max_tokens must be between 1 and 100000')
        return v


router = APIRouter()


def _agent_type(agent: Optional[Agent]) -> str:
    return normalize_agent_type(agent.agent_type if agent else None)


def _find_db_agent_by_type(db: Session, agent_type: Optional[str]) -> Optional[Agent]:
    normalized = normalize_agent_type(agent_type)
    candidate_names = {normalized, default_agent_name(normalized)}
    candidate_names.update({value.title() for value in legacy_default_agent_names(normalized)})
    candidate_names.update(legacy_default_agent_names(normalized))
    return (
        db.query(Agent)
        .filter(
            or_(
                Agent.agent_type == normalized,
                Agent.name.in_(sorted(candidate_names)),
            )
        )
        .order_by(Agent.id.asc())
        .first()
    )


# ==================== Agent 响应处理 ====================

def _find_mentioned_agent_name(message: str) -> Optional[str]:
    """Return the first @mentioned agent name in a message, if any."""
    mentioned_names = re.findall(r'@(\w+)', message or "")
    return normalize_agent_type(mentioned_names[0]) if mentioned_names else None


def _resolve_standalone_target_agent(db: Session, user_message: str) -> Optional[Agent]:
    """
    Resolve which agent should answer in a standalone chat.

    Prefer the first @mentioned agent; otherwise fall back to valet.
    """
    target_agent_name = _find_mentioned_agent_name(user_message)
    if target_agent_name:
        mentioned_agent = _find_db_agent_by_type(db, target_agent_name)
        if mentioned_agent:
            return mentioned_agent

    return _find_db_agent_by_type(db, DEFAULT_AGENT_TYPE)


def _list_global_agents(db: Session) -> List[Agent]:
    """List all available agents for standalone chat routing."""
    return db.query(Agent).order_by(Agent.agent_type.asc(), Agent.id.asc()).all()


def _snapshot_llm_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create a JSON-safe snapshot of the exact messages sent to the LLM."""
    normalized: List[Dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            normalized.append({"value": str(item)})
            continue
        try:
            normalized.append(json.loads(json.dumps(item, ensure_ascii=False)))
        except TypeError:
            fallback: Dict[str, Any] = {}
            for key, value in item.items():
                try:
                    fallback[key] = json.loads(json.dumps(value, ensure_ascii=False))
                except TypeError:
                    fallback[key] = str(value)
            normalized.append(fallback)
    return normalized


def _chat_message_agent_name(message: Any) -> Optional[str]:
    agent_name = getattr(message, "agent_name", None)
    return agent_name if isinstance(agent_name, str) and agent_name else None


def _append_recent_llm_history(
    messages: List[Dict[str, Any]],
    recent_messages: List[Any],
    *,
    limit: int,
    visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
) -> None:
    for msg in recent_messages[-limit:]:
        agent_name = _chat_message_agent_name(msg)
        if getattr(msg, "message_type", "") == "user" or not agent_name:
            messages.append({"role": "user", "content": msg.content})
            continue

        if visibility == "target" and target_agent_name and agent_name != target_agent_name:
            continue

        assistant_content = f"[{agent_name}]: {msg.content}" if prefix_assistant_name else msg.content
        messages.append({"role": "assistant", "content": assistant_content})


def _append_current_user_message(messages: List[Dict[str, Any]], user_message: str) -> None:
    normalized_user = (user_message or "").strip()
    if not normalized_user:
        return

    if messages:
        last_message = messages[-1]
        if (
            isinstance(last_message, dict)
            and last_message.get("role") == "user"
            and str(last_message.get("content") or "").strip() == normalized_user
        ):
            return

    messages.append({"role": "user", "content": user_message})


def _format_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_TOOL_ERROR_RESULT_RE = re.compile(r"^\[[^\]]+\]\s+error:", re.IGNORECASE)


def _tool_result_succeeded(result_text: str) -> bool:
    normalized = (result_text or "").strip()
    if not normalized:
        return True
    if normalized.lower().startswith("error:") or normalized.lower().startswith("error executing "):
        return False
    return _TOOL_ERROR_RESULT_RE.match(normalized) is None


def _message_client_turn_id(message_like: Any) -> Optional[str]:
    metadata: Dict[str, Any] = {}
    if hasattr(message_like, "metadata") and isinstance(getattr(message_like, "metadata"), dict):
        metadata = getattr(message_like, "metadata")
    elif hasattr(message_like, "metadata_json"):
        try:
            metadata = json.loads(getattr(message_like, "metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}

    client_turn_id = metadata.get("client_turn_id")
    return client_turn_id if isinstance(client_turn_id, str) and client_turn_id else None


def _preview_tool_calls(raw_tool_calls: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    previews: List[Dict[str, Any]] = []
    for index, tool_call in enumerate(raw_tool_calls or []):
        function = tool_call.get("function") if isinstance(tool_call, dict) else {}
        if not isinstance(function, dict):
            function = {}
        arguments = str(function.get("arguments") or "")
        previews.append(
            {
                "index": index,
                "id": tool_call.get("id") if isinstance(tool_call, dict) else None,
                "name": function.get("name") or "tool",
                "args_preview": arguments[:120],
            }
        )
    return previews


def _trim_prompt_context(value: Any, limit: int = 600) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _build_runtime_context_block(db: Session, chatroom: Optional[Chatroom], project: Optional[Project]) -> str:
    blocks: List[str] = []

    if project:
        project_lines = [f"- Project ID: {project.id}", f"- Name: {project.name}"]
        optional_project_fields = [
            ("Description", _trim_prompt_context(project.description, 800)),
            ("Workspace path", _trim_prompt_context(project.workspace_path, 260)),
            ("Status", project.status),
            ("Current stage", project.current_stage),
            ("Execution mode", project.execution_mode),
            ("Health status", project.health_status),
            ("Vision", _trim_prompt_context(project.one_line_vision, 320)),
            ("Primary outcome", _trim_prompt_context(project.primary_outcome, 320)),
            ("Current focus", _trim_prompt_context(project.current_focus, 500)),
            ("Blocking reason", _trim_prompt_context(project.blocking_reason, 320)),
            ("Latest summary", _trim_prompt_context(project.latest_summary, 700)),
        ]
        for label, value in optional_project_fields:
            if value:
                project_lines.append(f"- {label}: {value}")
        blocks.append("Current project context:\n" + "\n".join(project_lines))

    if chatroom:
        source_chat = None
        if chatroom.source_chatroom_id:
            source_chat = db.query(Chatroom).filter(Chatroom.id == chatroom.source_chatroom_id).first()

        chat_role = "standalone chat"
        if project:
            if project.default_chatroom_id and chatroom.id == project.default_chatroom_id:
                chat_role = "project main chat"
            elif chatroom.source_chatroom_id == project.default_chatroom_id:
                chat_role = "project subchat"
            else:
                chat_role = "project-linked chat"

        chat_lines = [
            f"- Chat ID: {chatroom.id}",
            f"- Title: {chatroom.title or 'New Chat'}",
            f"- Chat role: {chat_role}",
            f"- Session type: {chatroom.session_type or ('project-bound' if project else 'standalone')}",
            f"- Message visibility: {chatroom.message_visibility or 'all'}",
            f"- Visible in chat list: {'yes' if chatroom.is_visible_in_chat_list else 'no'}",
        ]
        if source_chat:
            chat_lines.append(f"- Source chat: #{source_chat.id} {source_chat.title or 'New Chat'}")
        blocks.append("Current chat context:\n" + "\n".join(chat_lines))

    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks)


@lru_cache(maxsize=8)
def _load_agent_config_snapshot(config_path: str, modified_ns: int) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_agent_config_data() -> Dict[str, Any]:
    config_path = Path(settings.AGENT_CONFIG_FILE)
    if not config_path.exists():
        return {}
    try:
        stat = config_path.stat()
        return _load_agent_config_snapshot(str(config_path.resolve()), stat.st_mtime_ns)
    except Exception as exc:
        logger.warning(f"Failed to load agent config for runtime card metadata: {exc}")
        return {}


def _context_window_from_provider(provider_data: Any, model_id: str) -> Optional[int]:
    if not isinstance(provider_data, dict):
        return None
    models = provider_data.get("models", [])
    if not isinstance(models, list):
        return None
    for model in models:
        if not isinstance(model, dict) or model.get("id") != model_id:
            continue
        context_window = model.get("contextWindow")
        if isinstance(context_window, (int, float)) and context_window > 0:
            return int(context_window)
    return None


def _resolve_llm_context_window(agent_name: str, model_id: str) -> Optional[int]:
    if not model_id:
        return None

    registry = get_registry()
    registered_agent = registry.get(agent_name) if agent_name else None
    if registered_agent:
        try:
            model_info = registered_agent.get_model_info(model_id)
        except Exception:
            model_info = None
        if isinstance(model_info, dict):
            context_window = model_info.get("context_window")
            if isinstance(context_window, (int, float)) and context_window > 0:
                return int(context_window)

    config_data = _load_agent_config_data()
    if not config_data:
        return None

    agents_data = config_data.get("agents", {})
    if agent_name:
        context_window = _context_window_from_provider((agents_data.get(agent_name) or {}).get("provider"), model_id)
        if context_window:
            return context_window

    context_window = _context_window_from_provider((config_data.get("global_llm") or {}).get("provider"), model_id)
    if context_window:
        return context_window

    for agent_data in agents_data.values():
        context_window = _context_window_from_provider(agent_data.get("provider"), model_id)
        if context_window:
            return context_window

    return None


def _build_llm_card_payload(
    *,
    agent_name: str,
    llm_client: Any,
    turn: int,
    duration_ms: int,
    system_prompt: str,
    prompt_messages: List[Dict[str, Any]],
    response_content: str,
    tool_call_previews: List[Dict[str, Any]],
    raw_tool_calls: Optional[List[Dict[str, Any]]] = None,
    usage: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    timings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    usage = usage or {}
    model = getattr(llm_client, "model", "")
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    tokens_total = int(usage.get("total_tokens", 0) or (tokens_in + tokens_out))
    context_window = _resolve_llm_context_window(agent_name, model)
    context_usage_ratio = None
    if context_window and tokens_in > 0:
        context_usage_ratio = round(tokens_in / context_window, 6)
    raw_response = {
        "role": "assistant",
        "content": response_content or "",
        "tool_calls": raw_tool_calls or [],
        "usage": usage,
        "finish_reason": finish_reason,
        "timings": timings or {},
    }
    return {
        "agent": agent_name,
        "model": model,
        "turn": turn,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_total,
        "context_window": context_window,
        "context_usage_ratio": context_usage_ratio,
        "duration_ms": duration_ms,
        "system_prompt": system_prompt or "",
        "prompt_messages": _format_json_block(prompt_messages),
        "response": response_content or "",
        "raw_response": _format_json_block(raw_response),
        "tool_calls": tool_call_previews or _preview_tool_calls(raw_tool_calls),
        "timings": timings or {},
    }

async def _trigger_standalone_assistant_response(
    db: Session,
    chatroom_id: int,
    user_message: str,
    client_turn_id: Optional[str] = None,
):
    """Generate a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        logger.debug("[ No chatroom found for standalone response")
        return

    assistant = _resolve_standalone_target_agent(db, user_message)

    if assistant:
        llm_client = get_llm_client_for_agent(_agent_type(assistant))
        assistant_name = agent_name_of(assistant)
        assistant_id = assistant.id
        system_prompt = assistant.system_prompt or f"You are {assistant_name}, a helpful AI collaborator."
    else:
        llm_client = get_default_llm_client()
        assistant_name = default_agent_name(DEFAULT_AGENT_TYPE)
        assistant_id = None
        system_prompt = f"You are {assistant_name}, a helpful AI collaborator."

    system_prompt += "\n\nThis is a standalone chat. Reply directly, be concise, and help the user explore before creating a project if needed."
    system_prompt += _build_runtime_context_block(db, chatroom, None)

    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=20)
    context_messages = [{"role": "system", "content": system_prompt}]
    for msg in recent_messages[-10:]:
        if msg.agent_name:
            context_messages.append({"role": "assistant", "content": msg.content})
        else:
            context_messages.append({"role": "user", "content": msg.content})

    response_content = await llm_client.chat(context_messages, temperature=0.7, max_tokens=1200)
    if not response_content:
        logger.debug("[ Standalone assistant returned empty response")
        return

    agent_response = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=assistant_id,
        content=response_content,
        message_type="text",
        metadata=_message_metadata_with_turn(client_turn_id),
        agent_name=assistant_name,
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=agent_response.id,
        content=response_content,
        agent_name=assistant_name,
        message_type="text",
        created_at=agent_response.created_at,
        metadata=_message_metadata_with_turn(client_turn_id),
    )

    if assistant_id and len(response_content) > 30:
        asyncio.create_task(_extract_memories(
            agent_id=assistant_id,
            agent_name=assistant_name,
            user_message=user_message,
            agent_response=response_content,
        ))


async def _stream_standalone_assistant_response(
    db: Session,
    chatroom_id: int,
    user_message: str,
    sse_json,
    client_turn_id: Optional[str] = None,
):
    """Stream a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        yield f"data: {sse_json.dumps({'type': 'error', 'error': 'Chatroom not found'})}\n\n"
        return

    assistant = _resolve_standalone_target_agent(db, user_message)

    if assistant:
        llm_client = get_llm_client_for_agent(_agent_type(assistant))
        assistant_name = agent_name_of(assistant)
        assistant_id = assistant.id
        system_prompt = assistant.system_prompt or f"You are {assistant_name}, a helpful AI collaborator."
    else:
        llm_client = get_default_llm_client()
        assistant_name = default_agent_name(DEFAULT_AGENT_TYPE)
        assistant_id = None
        system_prompt = f"You are {assistant_name}, a helpful AI collaborator."

    system_prompt += "\n\nThis is a standalone chat. Reply directly, be concise, and help the user explore before creating a project if needed."
    system_prompt += _build_runtime_context_block(db, chatroom, None)

    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=20)
    context_messages = [{"role": "system", "content": system_prompt}]
    for msg in recent_messages[-10:]:
        if msg.agent_name:
            context_messages.append({"role": "assistant", "content": msg.content})
        else:
            context_messages.append({"role": "user", "content": msg.content})
    prompt_snapshot = _snapshot_llm_messages(context_messages)

    yield f"data: {sse_json.dumps({'type': 'agent_start', 'agent_name': assistant_name, 'model': getattr(llm_client, 'model', ''), 'turn': 1, 'system_prompt': system_prompt, 'prompt_messages': _format_json_block(prompt_snapshot), 'client_turn_id': client_turn_id}, ensure_ascii=False)}\n\n"

    import time as _time
    final_content = ""
    _llm_start = _time.time()
    try:
        async for event in _iter_with_heartbeat(llm_client.chat_stream(context_messages)):
            if event["type"] == _HEARTBEAT_EVENT_TYPE:
                elapsed_ms = int((_time.time() - _llm_start) * 1000)
                yield f"data: {sse_json.dumps({'type': 'llm_wait', 'agent': assistant_name, 'elapsed_ms': elapsed_ms, 'turn': 1})}\n\n"
                continue

            if event["type"] in {"request_sent", "first_chunk", "first_content"}:
                yield f"data: {sse_json.dumps({'type': event['type'], 'agent': assistant_name, 'turn': 1, 'elapsed_ms': event.get('elapsed_ms')})}\n\n"
                continue

            if event["type"] == "content":
                final_content += event["delta"]
                yield f"data: {sse_json.dumps({'type': 'content', 'delta': event['delta']})}\n\n"
                continue

            if event["type"] == "done":
                response_content = event.get("full_content") or final_content
                llm_payload = _build_llm_card_payload(
                    agent_name=assistant_name,
                    llm_client=llm_client,
                    turn=1,
                    duration_ms=int(event.get("timings", {}).get("completed_ms") or 0),
                    system_prompt=system_prompt,
                    prompt_messages=prompt_snapshot,
                    response_content=response_content,
                    tool_call_previews=[],
                    raw_tool_calls=event.get("tool_calls"),
                    usage=event.get("usage"),
                    finish_reason=event.get("finish_reason"),
                    timings=event.get("timings"),
                )
                llm_payload["type"] = "llm_call"
                llm_payload["source"] = "chatroom"
                if client_turn_id:
                    llm_payload["client_turn_id"] = client_turn_id
                await _store_runtime_card(chatroom_id, llm_payload)
                yield f"data: {sse_json.dumps(llm_payload, ensure_ascii=False)}\n\n"
                final_content = response_content
                break

            if event["type"] == "error":
                raise RuntimeError(event["error"])
    except Exception as exc:
        saved = await _persist_stream_failure(
            db,
            chatroom_id=chatroom_id,
            client_turn_id=client_turn_id,
            error_message=str(exc),
            agent_name=assistant_name,
            agent_id=assistant_id,
            detail=traceback.format_exc(),
        )
        yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': assistant_name, 'message_id': saved.id, 'client_turn_id': client_turn_id}, ensure_ascii=False)}\n\n"
        return

    if not final_content:
        final_content = "(Agent returned empty response)"

    agent_response = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=assistant_id,
        content=final_content,
        message_type="text",
        metadata=_message_metadata_with_turn(client_turn_id),
        agent_name=assistant_name,
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=agent_response.id,
        content=final_content,
        agent_name=assistant_name,
        message_type="text",
        created_at=agent_response.created_at,
        metadata=_message_metadata_with_turn(client_turn_id),
    )

    yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': assistant_name, 'message_id': agent_response.id, 'client_turn_id': client_turn_id})}\n\n"

    if assistant_id and len(final_content) > 30:
        asyncio.create_task(_extract_memories(
            agent_id=assistant_id,
            agent_name=assistant_name,
            user_message=user_message,
            agent_response=final_content,
        ))


async def trigger_agent_response(chatroom_id: int, user_message: str, client_turn_id: Optional[str] = None):
    """触发 Agent 处理消息并生成响应（统一执行路径 + 工具结果回传 LLM）"""
    from models.database import get_db
    from tools import tool_registry
    from tools.file_operations import reset_active_workspace, set_active_workspace
    import json
    
    db = next(get_db())
    workspace_token = None
    try:
        logger.debug(f"[ trigger_agent_response called: chatroom_id={chatroom_id}, message={user_message[:50]}...")
        
        # 1. 获取聊天室关联的项目
        chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
        if not chatroom:
            logger.debug(f"[ No chatroom found")
            return

        project = _resolve_chatroom_project(db, chatroom)
        workspace_token = set_active_workspace(project.workspace_path if project and project.workspace_path else None)
        if not project:
            mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', user_message)] if '@' in user_message else []
            if len(mentioned_names) > 1:
                logger.info(f"[Collab] Standalone multi-agent pipeline triggered: {mentioned_names}")
                await _run_multi_agent_pipeline(
                    chatroom_id=chatroom_id,
                    project=None,
                    agents=_list_global_agents(db),
                    agent_names=mentioned_names,
                    user_message=user_message,
                    db=db,
                    client_turn_id=client_turn_id,
                )
                return
            await _trigger_standalone_assistant_response(db, chatroom_id, user_message, client_turn_id)
            return
        
        logger.debug(f"[ Found project: {project.name}")

        # 2. 解析 @ 提及，检测多 Agent 协作
        mentioned_names = []
        if '@' in user_message:
            mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', user_message)]

        # 3. 获取项目关联的 Agents（必须在多 Agent 检查之前）
        assignments = db.query(AgentAssignment).filter(
            AgentAssignment.project_id == project.id
        ).all()
        agent_ids = [a.agent_id for a in assignments]
        agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

        logger.debug(f"[ Found {len(agents)} agents for project")

        # 多 Agent 协作：多个 @mention 或包含协作关键词
        if len(mentioned_names) > 1:
            logger.info(f"[Collab] Multi-agent pipeline triggered: {mentioned_names}")
            await _run_multi_agent_pipeline(
                chatroom_id=chatroom_id,
                project=project,
                agents=agents,
                agent_names=mentioned_names,
                user_message=user_message,
                db=db,
                client_turn_id=client_turn_id,
            )
            return

        target_agent_name = mentioned_names[0] if mentioned_names else None
        logger.debug(f"[ Target agent name: {target_agent_name}")

        # 4. 确定响应的 Agent
        target_agent = None
        if target_agent_name:
            target_agent = find_agent_by_type(agents, target_agent_name)

            # @mentioned agent 不在项目中 → 从全局注册表查找并自动分配
            if not target_agent:
                global_agent = _find_db_agent_by_type(db, target_agent_name)
                if global_agent:
                    logger.info(f"[Agent] Auto-assigning '{target_agent_name}' to project '{project.name}'")
                    assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                    db.add(assignment)
                    db.commit()
                    target_agent = global_agent
                    agents.append(global_agent)

        if not target_agent:
            target_agent = find_agent_by_type(agents, DEFAULT_AGENT_TYPE) or (agents[0] if agents else None)

        if not target_agent:
            logger.debug(f"[ No target agent found")
            return

        logger.debug(f"[ Selected agent: {target_agent.name} (role: {target_agent.role})")

        # 注册 Agent 为协作者（如果尚未注册）
        from agents.collaboration import collaboration_coordinator, AgentCollaborator
        # 同时注册项目中所有 agent 为协作者（让 list_collaborators 能看到它们）
        for agent in agents:
            if agent.id not in collaboration_coordinator.collaborators:
                collaborator = AgentCollaborator(
                    agent_id=agent.id,
                    agent_name=_agent_type(agent),
                    chatroom_id=chatroom_id
                )
                collaboration_coordinator.register_collaborator(collaborator)
                logger.info(f"[Collab] Auto-registered collaborator: {_agent_type(agent)}")
        
        # 5. 获取该 Agent 的 LLM 客户端
        llm_client = get_llm_client_for_agent(_agent_type(target_agent))
        logger.debug(f"[ LLM client obtained for {_agent_type(target_agent)}: {llm_client.base_url}")
        
        # 6. 构建消息上下文
        messages = []
        
        system_prompt = target_agent.system_prompt or f"You are {agent_name_of(target_agent)}, a {target_agent.role}."
        system_prompt += _build_runtime_context_block(db, chatroom, project)
        
        available_tools = tool_registry.list_tools()
        if available_tools:
            system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"
            system_prompt += "\nWhen you need to use a tool, respond with a tool call and the system will execute it."

        # 注入项目中所有 Agent 列表（让 Agent 知道队友是谁）
        team_members = [f"- **{a.name}** (role: {a.role})" for a in agents]
        system_prompt += f"\n\nTeam members in this project:\n" + "\n".join(team_members)
        if target_agent.id:
            system_prompt += f"\n\nYou are **{agent_name_of(target_agent)}** (type: `{_agent_type(target_agent)}`, role: {target_agent.role}). You can use tools to communicate with or delegate tasks to your teammates."

        # 注入记忆到上下文（含跨 Agent 共享）
        from models.database import Memory

        # 自身记忆（最重要的 8 条）
        own_memories = (
            db.query(Memory)
            .filter(Memory.agent_id == target_agent.id)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(8)
            .all()
        )
        # 其他 Agent 的高重要性记忆（共享上下文，最多 5 条）
        other_agent_ids = [a.id for a in agents if a.id != target_agent.id]
        shared_memories = []
        if other_agent_ids:
            shared_memories = (
                db.query(Memory)
                .filter(Memory.agent_id.in_(other_agent_ids), Memory.importance >= 7)
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(5)
                .all()
            )

        if own_memories:
            system_prompt += "\n\nYour memories (context for your responses):"
            for mem in own_memories:
                ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
                system_prompt += f"\n- [{ts}] [importance={mem.importance}] {mem.content[:200]}"

        if shared_memories:
            system_prompt += "\n\nShared context from other agents:"
            for mem in shared_memories:
                ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
                source_agent = next((a for a in agents if a.id == mem.agent_id), None)
                source_name = source_agent.name if source_agent else "unknown"
                system_prompt += f"\n- [{ts}] [{source_name}] {mem.content[:200]}"

        messages.append({
            "role": "system",
            "content": system_prompt
        })
        
        # 获取对话历史（根据房间可见度配置）
        visibility = chatroom.message_visibility or "all"
        recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=20)

        if visibility == "all":
            _append_recent_llm_history(messages, recent_messages, limit=10, prefix_assistant_name=True)
        else:
            _append_recent_llm_history(
                messages,
                recent_messages,
                limit=10,
                visibility="target",
                target_agent_name=agent_name_of(target_agent),
            )
        
        # 追加当前用户消息；如果最近历史里已经包含这条刚保存的消息，则避免重复。
        _append_current_user_message(messages, user_message)
        
        logger.debug(f"[ Context messages: {len(messages)} messages")
        
        # 7. 获取工具 schemas
        tool_schemas = tool_registry.get_schemas()
        
        # 8. 主循环：LLM → 执行工具 → 结果回传 LLM → 直到没有 tool_calls
        logger.info(f"[LLM] Calling LLM for agent: {_agent_type(target_agent)} with {len(tool_schemas)} tools available")
        
        max_tool_iterations = MAX_TOOL_ITERATIONS
        iteration = 0
        
        while iteration < max_tool_iterations:
            iteration += 1
            logger.info(f"[LLM] Loop iteration {iteration}")
            
            llm_response = await llm_client.chat_with_tools(messages, tool_schemas if tool_schemas else None)
            
            response_content = llm_response.get("content", "")
            tool_calls = llm_response.get("tool_calls")
            
            logger.info(f"[LLM] Response received: {response_content[:100] if response_content else 'None'}...")
            logger.info(f"[LLM] Tool calls: {tool_calls}")
            
            if not tool_calls:
                # 没有更多工具调用，结束循环
                logger.info(f"[LLM] No more tool calls, loop done after {iteration} iterations")
                break
            
            # 将 LLM 的 assistant 消息加入上下文（包含 tool_calls）
            assistant_msg = {
                "role": "assistant",
                "content": response_content,
                "tool_calls": [tc.model_dump() if hasattr(tc, 'model_dump') else {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                } for tc in tool_calls]
            }
            messages.append(assistant_msg)
            
            # 执行工具并将结果追加到 messages
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                tool_call_id = tool_call.id
                
                logger.debug(f"[Tool] Executing: {tool_name} with args: {tool_args}")
                
                try:
                    tool_result = await tool_registry.execute(tool_name, **tool_args)
                    result_str = str(tool_result) if tool_result is not None else "(no output)"
                    logger.debug(f"[Tool] Result: {result_str[:150]}...")
                except Exception as te:
                    result_str = f"Error executing {tool_name}: {str(te)}"
                    logger.debug(f"[Tool] Error: {te}")
                
                # 以 tool role 消息将结果回传 LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_str,
                    "name": tool_name
                })
        
        if not response_content:
            logger.error(f"[ LLM returned empty response after all tool iterations")
            return
        
        # 9. 发送 Agent 响应
        agent_response = await chatroom_manager.send_message(
            chatroom_id=chatroom_id,
            agent_id=target_agent.id,
            content=response_content,
            message_type="text",
            metadata=_message_metadata_with_turn(client_turn_id),
            agent_name=agent_name_of(target_agent)
        )
        
        logger.debug(f"[ Agent response saved: id={agent_response.id}")
        
        await _publish_saved_chat_message(
            db,
            chatroom_id,
            message_id=agent_response.id,
            content=response_content,
            agent_name=agent_name_of(target_agent),
            message_type="text",
            created_at=agent_response.created_at,
            metadata=_message_metadata_with_turn(client_turn_id),
        )
        
        logger.info(f"[Agent] {_agent_type(target_agent)} responded to message successfully")

        # 11. 异步提取记忆（不阻塞响应）
        if len(response_content) > 30:
            asyncio.create_task(_extract_memories(
                agent_id=target_agent.id,
                agent_type=_agent_type(target_agent),
                user_message=user_message,
                agent_response=response_content
            ))

    except Exception as e:
        logger.error(f"[ Agent response failed: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        if workspace_token is not None:
            reset_active_workspace(workspace_token)
        db.close()


async def _extract_memories(agent_id: int, agent_type: str, user_message: str, agent_response: str):
    """
    用 LLM 从对话中提取关键信息，存为 Agent 记忆

    提取内容：事实、决策、用户偏好、重要上下文
    跳过条件：简单问候、确认类回复
    """
    try:
        from models.database import get_db as _get_db, Memory
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache

        llm = get_llm_client_for_agent(normalize_agent_type(agent_type))

        extraction_messages = [
            {
                "role": "system",
                "content": (
                    "You are a memory extraction system. Analyze the conversation and extract "
                    "important information worth remembering. Return a JSON array of objects with fields: "
                    "'content' (the memory text, concise), 'type' (one of: fact, preference, decision, context), "
                    "'importance' (1-10).\n\n"
                    "Rules:\n"
                    "- Extract factual information, user preferences, decisions made, and important context\n"
                    "- Skip greetings, small talk, simple confirmations, and generic Q&A\n"
                    "- Each memory should be self-contained and meaningful\n"
                    "- Max 3 memories per extraction\n"
                    "- If nothing worth remembering, return an empty array []\n"
                    "- Return ONLY the JSON array, no explanation"
                )
            },
            {
                "role": "user",
                "content": f"User: {user_message[:500]}\n\nAgent {agent_name}: {agent_response[:800]}"
            }
        ]

        result = await llm.chat(extraction_messages, temperature=0.3, max_tokens=500)

        if not result:
            return

        # 解析 JSON
        import json as _json
        result = result.strip()
        # 提取 JSON 数组
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        memories = _json.loads(result)
        if not isinstance(memories, list) or not memories:
            return

        # 保存记忆
        db = next(_get_db())
        try:
            for mem in memories[:3]:  # 最多 3 条
                content = mem.get("content", "").strip()
                if not content or len(content) < 10:
                    continue
                mem_type = mem.get("type", "context")
                importance = min(max(int(mem.get("importance", 5)), 1), 10)

                db_memory = Memory(
                    agent_id=agent_id,
                    memory_type=mem_type,
                    content=content,
                    importance=importance
                )
                db.add(db_memory)

            db.commit()
            logger.info(f"[Memory] Extracted {len(memories)} memories for {agent_type}")
        except Exception as e:
            db.rollback()
            logger.debug(f"[Memory] Save failed: {e}")
        finally:
            db.close()

    except Exception as e:
        logger.debug(f"[Memory] Extraction failed: {e}")


async def _run_single_agent_turn(
    agent, chatroom_id, project, agents, user_message, extra_context, db, client_turn_id: Optional[str] = None
):
    """
    执行单个 Agent 的一次响应（供多 Agent 流水线调用）

    Returns: (response_content, agent_response_msg) 或 (None, None)
    """
    from tools import tool_registry
    from agents.collaboration import collaboration_coordinator, AgentCollaborator
    from models.database import Memory

    llm_client = get_llm_client_for_agent(_agent_type(agent))
    current_chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()

    # 注册协作者
    for a in agents:
        if a.id not in collaboration_coordinator.collaborators:
            collaboration_coordinator.register_collaborator(
                AgentCollaborator(agent_id=a.id, agent_name=_agent_type(a), chatroom_id=chatroom_id)
            )

    # 构建 system prompt
    system_prompt = agent.system_prompt or f"You are {agent_name_of(agent)}, a {agent.role}."
    if project:
        system_prompt += _build_runtime_context_block(db, current_chatroom, project)
    else:
        system_prompt += (
            "\n\nThis is a standalone chat. Reply directly, stay concise, "
            "and coordinate with mentioned teammates when it helps."
        )
        system_prompt += _build_runtime_context_block(db, current_chatroom, None)

    available_tools = tool_registry.list_tools()
    if available_tools:
        system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"

    # 注入当前可用 Agent 列表
    team_members = [f"- **{agent_name_of(a)}** (type: `{_agent_type(a)}`, role: {a.role})" for a in agents]
    team_label = "Team members in this project" if project else "Available agents in this standalone chat"
    system_prompt += f"\n\n{team_label}:\n" + "\n".join(team_members)
    system_prompt += (
        f"\n\nYou are **{agent_name_of(agent)}** (type: `{_agent_type(agent)}`, role: {agent.role}). "
        "You can use tools to communicate with or delegate tasks to your teammates."
    )

    # 记忆注入
    own_memories = (
        db.query(Memory).filter(Memory.agent_id == agent.id)
        .order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(8).all()
    )
    other_ids = [a.id for a in agents if a.id != agent.id]
    shared = []
    if other_ids:
        shared = (
            db.query(Memory).filter(Memory.agent_id.in_(other_ids), Memory.importance >= 7)
            .order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(5).all()
        )
    if own_memories:
        system_prompt += "\n\nYour memories:"
        for mem in own_memories:
            ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "?"
            system_prompt += f"\n- [{ts}] [importance={mem.importance}] {mem.content[:200]}"
    if shared:
        system_prompt += "\n\nShared context from other agents:"
        for mem in shared:
            ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "?"
            source_agent = next((a for a in agents if a.id == mem.agent_id), None)
            src = agent_name_of(source_agent) if source_agent else "unknown"
            system_prompt += f"\n- [{ts}] [{src}] {mem.content[:200]}"

    # 如果有协作上下文（前一个 Agent 的回复），注入
    if extra_context:
        system_prompt += f"\n\nPrevious agent's work for you to build upon:\n{extra_context[:1500]}"

    messages = [{"role": "system", "content": system_prompt}]

    # 近期对话
    recent_msgs = await chatroom_manager.get_messages(chatroom_id, limit=6)
    _append_recent_llm_history(messages, recent_msgs, limit=4)
    _append_current_user_message(messages, user_message)

    tool_schemas = tool_registry.get_schemas()
    response_content = ""

    # 工具调用循环
    for iteration in range(MAX_TOOL_ITERATIONS):
        llm_response = await llm_client.chat_with_tools(messages, tool_schemas if tool_schemas else None)
        response_content = llm_response.get("content", "") or ""
        tool_calls = llm_response.get("tool_calls")

        if not tool_calls:
            break

        assistant_msg = {
            "role": "assistant", "content": response_content,
            "tool_calls": [tc.model_dump() if hasattr(tc, 'model_dump') else {
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            } for tc in tool_calls]
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_args = json.loads(tc.function.arguments)
            try:
                tool_result = await tool_registry.execute(tool_name, **tool_args)
                result_str = str(tool_result) if tool_result else "(no output)"
            except Exception as te:
                result_str = f"Error: {te}"
            if len(result_str) > 2000:
                result_str = result_str[:2000]
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str, "name": tool_name})

    if not response_content:
        return None, None

    # 保存到数据库
    agent_msg = await chatroom_manager.send_message(
        chatroom_id=chatroom_id, agent_id=agent.id,
        content=response_content,
        message_type="text",
        metadata=_message_metadata_with_turn(client_turn_id),
        agent_name=agent_name_of(agent),
    )

    # 异步提取记忆
    if len(response_content) > 30:
        asyncio.create_task(_extract_memories(agent.id, _agent_type(agent), user_message, response_content))

    return response_content, agent_msg


async def _run_multi_agent_pipeline(
    chatroom_id, project, agents, agent_names, user_message, db, client_turn_id: Optional[str] = None
):
    """
    多 Agent 协作流水线

    流程：每个按序 @mention 的 Agent 依次响应，后一个看到前一个的输出。
    最终通过 WebSocket 广播所有响应。
    """
    resolved_agents = []
    for name in agent_names:
        agent = find_agent_by_type(agents, name)
        if not agent:
            global_agent = _find_db_agent_by_type(db, name)
            if global_agent and project:
                assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                db.add(assignment)
                db.commit()
                agents.append(global_agent)
            if global_agent:
                agent = global_agent
        if agent and agent not in resolved_agents:
            resolved_agents.append(agent)

    if not resolved_agents:
        logger.warning("[Collab] No valid agents found for multi-agent pipeline")
        return

    logger.info(f"[Collab] Pipeline: {' → '.join(_agent_type(a) for a in resolved_agents)}")

    previous_context = None
    results = []

    for i, agent in enumerate(resolved_agents):
        logger.info(f"[Collab] Step {i+1}/{len(resolved_agents)}: {_agent_type(agent)}")

        # 为后续 Agent 注入前序上下文
        extra_msg = user_message
        if previous_context:
            extra_msg = (
                f"{user_message}\n\n"
                f"[Context from previous agent ({agent_name_of(resolved_agents[i-1])})]:\n{previous_context}"
            )

        content, msg = await _run_single_agent_turn(
            agent=agent,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=extra_msg,
            extra_context=previous_context,
            db=db,
            client_turn_id=client_turn_id,
        )

        if content:
            await _publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=msg.id,
                content=content,
                agent_name=agent_name_of(agent),
                message_type="text",
                created_at=msg.created_at,
                metadata=_message_metadata_with_turn(client_turn_id),
            )

            results.append({"agent": agent_name_of(agent), "content": content})
            previous_context = content
        else:
            logger.warning(f"[Collab] {agent.name} returned empty response")

    logger.info(f"[Collab] Pipeline complete: {len(results)}/{len(resolved_agents)} agents responded")


# ==================== 数据模型 ====================

class AgentInfo(BaseModel):
    id: int
    type: str
    name: str
    role: str
    is_active: bool
    soul: Optional[Dict[str, Any]] = None
    tools: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    system_prompt_preview: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    agent_names: List[str] = [DEFAULT_AGENT_TYPE]
    workspace_path: Optional[str] = None


class GitHubProjectCreate(BaseModel):
    repo_url: str
    name: Optional[str] = None
    description: Optional[str] = ""
    ref: Optional[str] = None
    agent_names: List[str] = [DEFAULT_AGENT_TYPE]

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, value: str) -> str:
        repo_url = (value or "").strip()
        if not repo_url:
            raise ValueError("repo_url is required")
        return repo_url


class ProjectFromChatCreate(ProjectCreate):
    source_chatroom_id: int


class ChatCreate(BaseModel):
    title: Optional[str] = None


class ChatUpdate(BaseModel):
    title: str


class ProjectSubchatCreate(BaseModel):
    title: Optional[str] = None


class ChatInfo(BaseModel):
    id: int
    title: str
    session_type: str
    is_visible_in_chat_list: bool
    project_id: Optional[int] = None
    agent_count: int = 0
    updated_at: str


class ProjectInfo(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    display_order: int
    chatroom_id: Optional[int]
    default_chatroom_id: Optional[int]
    workspace_path: Optional[str] = None
    source_type: Optional[str] = None
    repo_url: Optional[str] = None
    repo_full_name: Optional[str] = None
    clone_ref: Optional[str] = None
    created_from_chatroom_id: Optional[int] = None
    agents: List[AgentInfo]


class ProjectSyncInfo(BaseModel):
    project: ProjectInfo
    updated: bool
    branch: Optional[str] = None
    head_commit: Optional[str] = None
    head_short: Optional[str] = None
    previous_head_commit: Optional[str] = None
    detached: bool = False
    summary: str


class ProjectUpdate(BaseModel):
    name: str


class ProjectReorderRequest(BaseModel):
    project_ids: List[int]


class MessageRequest(BaseModel):
    content: str
    client_turn_id: Optional[str] = None


class MessageResponse(BaseModel):
    id: int
    content: str
    agent_name: Optional[str]
    message_type: str
    created_at: str
    client_turn_id: Optional[str] = None


async def _publish_saved_chat_message(
    db: Session,
    chatroom_id: int,
    *,
    message_id: int,
    content: str,
    agent_name: Optional[str],
    message_type: str,
    created_at: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    from routes.websocket import websocket_manager

    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    room_payload = {
        "type": "chat_message",
        "chatroom_id": chatroom_id,
        "id": message_id,
        "content": content,
        "agent_name": agent_name,
        "message_type": message_type,
        "created_at": created_value,
        "client_turn_id": (metadata or {}).get("client_turn_id"),
    }
    await websocket_manager.broadcast_to_room(room_payload, chatroom_id)

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        return

    project = monitor_resolve_chatroom_project(db, chatroom)
    monitor_payload = serialize_monitor_message_item(
        message_id=message_id,
        chatroom_id=chatroom_id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        agent_name=agent_name,
        content=content,
        message_type=message_type,
        created_at=created_value,
        metadata=metadata,
    )
    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_message",
            "payload": monitor_payload,
        },
        "monitor",
    )


async def _publish_runtime_card_event(
    db: Session,
    chatroom_id: int,
    *,
    runtime_message_id: int,
    created_at: Any,
    card_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    from routes.websocket import websocket_manager

    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    room_card_payload = dict(card_payload)
    room_card_payload.setdefault("created_at", created_value)
    room_card_payload.setdefault("runtime_message_id", runtime_message_id)

    await websocket_manager.broadcast_to_room(
        {
            "type": "runtime_card",
            "chatroom_id": chatroom_id,
            "card": room_card_payload,
        },
        chatroom_id,
    )

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        return

    project = monitor_resolve_chatroom_project(db, chatroom)
    monitor_payload = serialize_monitor_runtime_item(
        runtime_message_id=runtime_message_id,
        chatroom_id=chatroom_id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        card=room_card_payload,
        created_at=created_value,
        metadata=metadata,
    )
    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_runtime",
            "payload": monitor_payload,
        },
        "monitor",
    )


async def _store_runtime_card(chatroom_id: int, payload: Dict[str, Any]) -> Any:
    """Persist runtime cards so chat refresh can replay execution history."""
    card_payload = dict(payload)
    runtime_message = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,
        content=card_payload.get("type", "runtime_card"),
        message_type="runtime_card",
        metadata={"card": card_payload},
    )
    db = next(get_db())
    try:
        await _publish_runtime_card_event(
            db,
            chatroom_id,
            runtime_message_id=runtime_message.id,
            created_at=runtime_message.created_at,
            card_payload=card_payload,
            metadata={"card": card_payload},
        )
    finally:
        db.close()
    return runtime_message


def _summarize_stream_error(error_message: str, limit: int = 240) -> str:
    text = str(error_message or "").strip()
    if not text:
        return "Unknown streaming error"
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


async def _persist_stream_failure(
    db: Session,
    *,
    chatroom_id: int,
    client_turn_id: Optional[str],
    error_message: str,
    agent_name: Optional[str] = None,
    agent_id: Optional[int] = None,
    detail: Optional[str] = None,
) -> Any:
    """
    Persist a visible fallback message plus a runtime error card for failed SSE turns.

    This ensures refresh/reconnect still shows why a turn terminated before a final reply
    could be saved.
    """
    safe_agent_name = (agent_name or default_agent_name(DEFAULT_AGENT_TYPE)).strip() or default_agent_name(DEFAULT_AGENT_TYPE)
    error_summary = _summarize_stream_error(error_message)
    detail_text = str(detail or error_message or "").strip() or error_summary
    failure_text = (
        "本轮执行中断，未生成最终答复。\n\n"
        f"错误摘要: {error_summary}"
    )

    try:
        db.rollback()
    except Exception:
        pass

    error_card = {
        "type": "agent_error",
        "source": "chatroom",
        "agent": safe_agent_name,
        "summary": "Stream failed before a final reply was saved.",
        "error": error_summary,
        "content": f"### Stream Failure\n\n- Agent: `{safe_agent_name}`\n- Error: `{error_summary}`\n\n```text\n{detail_text}\n```",
    }
    if client_turn_id:
        error_card["client_turn_id"] = client_turn_id

    await _store_runtime_card(chatroom_id, error_card)

    saved = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content=failure_text,
        message_type="text",
        metadata=_message_metadata_with_turn(
            client_turn_id,
            {
                "stream_failure": {
                    "agent": safe_agent_name,
                    "error": error_summary,
                }
            },
        ),
        agent_name=safe_agent_name,
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=saved.id,
        content=failure_text,
        agent_name=safe_agent_name,
        message_type="text",
        created_at=saved.created_at,
        metadata=_message_metadata_with_turn(
            client_turn_id,
            {
                "stream_failure": {
                    "agent": safe_agent_name,
                    "error": error_summary,
                }
            },
        ),
    )
    return saved


def _message_metadata_with_turn(client_turn_id: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = dict(extra or {})
    if client_turn_id:
        metadata["client_turn_id"] = client_turn_id
    return metadata


def _serialize_project_agents(db: Session, project_id: int) -> List[Agent]:
    assignments = db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).all()
    agent_ids = [assignment.agent_id for assignment in assignments]
    if not agent_ids:
        return []
    return db.query(Agent).filter(Agent.id.in_(agent_ids)).all()


def _open_workspace_path(workspace_path: str) -> None:
    resolved = Path(workspace_path).expanduser().resolve()
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    if os.name == "nt":
        subprocess.Popen(["explorer", str(resolved)])
        return

    if os.getenv("WSL_DISTRO_NAME"):
        explorer = shutil.which("explorer.exe")
        wslpath = shutil.which("wslpath")
        if explorer and wslpath:
            windows_path = subprocess.check_output([wslpath, "-w", str(resolved)], text=True).strip()
            subprocess.Popen([explorer, windows_path])
            return

    opener = "open" if shutil.which("open") else shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, str(resolved)])
        return

    raise HTTPException(status_code=500, detail="No file manager opener is available")


def _resolve_chatroom_project(db: Session, chatroom: Chatroom | None) -> Optional[Project]:
    current = chatroom
    visited_ids: set[int] = set()
    while current:
        if current.project_id:
            return db.query(Project).filter(Project.id == current.project_id).first()
        if not current.source_chatroom_id or current.source_chatroom_id in visited_ids:
            return None
        visited_ids.add(current.source_chatroom_id)
        current = db.query(Chatroom).filter(Chatroom.id == current.source_chatroom_id).first()
    return None


def _collect_descendant_chatrooms(db: Session, root_chatroom_ids: List[int]) -> List[Chatroom]:
    descendants: List[Chatroom] = []
    visited_ids: set[int] = set()
    pending_ids = [chatroom_id for chatroom_id in root_chatroom_ids if chatroom_id]

    while pending_ids:
        parent_id = pending_ids.pop(0)
        children = (
            db.query(Chatroom)
            .filter(Chatroom.source_chatroom_id == parent_id)
            .order_by(Chatroom.id.asc())
            .all()
        )
        for child in children:
            if child.id in visited_ids:
                continue
            visited_ids.add(child.id)
            descendants.append(child)
            pending_ids.append(child.id)

    return descendants


def _delete_chatrooms_with_messages(db: Session, chatrooms: List[Chatroom]) -> None:
    unique_chatrooms: List[Chatroom] = []
    seen_ids: set[int] = set()
    for chatroom in chatrooms:
        if not chatroom or chatroom.id in seen_ids:
            continue
        seen_ids.add(chatroom.id)
        unique_chatrooms.append(chatroom)

    if not unique_chatrooms:
        return

    chatroom_ids = [chatroom.id for chatroom in unique_chatrooms]
    db.query(Message).filter(Message.chatroom_id.in_(chatroom_ids)).delete(synchronize_session=False)
    for chatroom in unique_chatrooms:
        chatroom_manager.chatrooms.pop(chatroom.id, None)
        db.delete(chatroom)


def _serialize_chat(db: Session, chatroom: Chatroom) -> ChatInfo:
    project = _resolve_chatroom_project(db, chatroom)
    agent_count = 0
    if project:
        agent_count = db.query(AgentAssignment).filter(AgentAssignment.project_id == project.id).count()

    return ChatInfo(
        id=chatroom.id,
        title=chatroom.title or "New Chat",
        session_type="project-bound" if project else (chatroom.session_type or "standalone"),
        is_visible_in_chat_list=bool(chatroom.is_visible_in_chat_list),
        project_id=project.id if project else None,
        agent_count=agent_count,
        updated_at=chatroom.created_at.isoformat() if chatroom.created_at else "",
    )


def _serialize_project(db: Session, project: Project) -> ProjectInfo:
    agents = _serialize_project_agents(db, project.id)
    default_chatroom = None
    if project.default_chatroom_id:
        default_chatroom = db.query(Chatroom).filter(Chatroom.id == project.default_chatroom_id).first()
    if default_chatroom is None:
        default_chatroom = db.query(Chatroom).filter(Chatroom.project_id == project.id).first()

    chatroom_id = default_chatroom.id if default_chatroom else None

    return ProjectInfo(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        display_order=int(project.display_order or 0),
        chatroom_id=chatroom_id,
        default_chatroom_id=chatroom_id,
        workspace_path=project.workspace_path,
        source_type=project.source_type,
        repo_url=project.repo_url,
        repo_full_name=project.repo_full_name,
        clone_ref=project.clone_ref,
        created_from_chatroom_id=default_chatroom.source_chatroom_id if default_chatroom else None,
        agents=[
            AgentInfo(
                id=agent.id,
                type=agent.type,
                name=agent_name_of(agent),
                role=agent.role,
                is_active=agent.is_active,
            )
            for agent in agents
        ],
    )


# ==================== Agent 相关 ====================

@router.get("/agents", response_model=List[AgentInfo])
async def list_agents(db: Session = Depends(get_db)):
    """获取所有可用 Agent 列表"""
    import json as _json2
    agents = db.query(Agent).filter(Agent.is_active == True).all()
    result = []
    for agent in agents:
        soul = {}
        tools = []
        skills = []
        try:
            soul = _json2.loads(agent.soul) if agent.soul else {}
        except Exception:
            pass
        try:
            tools = _json2.loads(agent.tools) if agent.tools else []
        except Exception:
            pass
        try:
            skills = _json2.loads(agent.skills) if agent.skills else []
        except Exception:
            pass
        preview = (agent.system_prompt or "")[:300]
        result.append(AgentInfo(
            id=agent.id, type=agent.type, name=agent_name_of(agent), role=agent.role,
            is_active=agent.is_active, soul=soul, tools=tools,
            skills=skills, system_prompt_preview=preview,
        ))
    return result


@router.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """获取 Agent 详情"""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    import json as _json3
    soul = {}
    tools = []
    skills = []
    try:
        soul = _json3.loads(agent.soul) if agent.soul else {}
    except Exception:
        pass
    try:
        tools = _json3.loads(agent.tools) if agent.tools else []
    except Exception:
        pass
    try:
        skills = _json3.loads(agent.skills) if agent.skills else []
    except Exception:
        pass
    preview = (agent.system_prompt or "")[:300]

    return AgentInfo(
        id=agent.id, type=agent.type, name=agent_name_of(agent), role=agent.role,
        is_active=agent.is_active, soul=soul, tools=tools,
        skills=skills, system_prompt_preview=preview,
    )


@router.get("/agents/{agent_id}/memory")
async def get_agent_memory(agent_id: int, db: Session = Depends(get_db)):
    """获取 Agent 记忆信息"""
    from models.database import Memory
    
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    memories = db.query(Memory).filter(Memory.agent_id == agent_id).all()
    
    return {
        "agent_name": agent.name,
        "memory_count": len(memories),
        "memories": [
            {
                "id": m.id,
                "type": m.memory_type,
                "content": m.content[:200] + "..." if len(m.content) > 200 else m.content,
                "importance": m.importance,
                "created_at": m.created_at.isoformat()
            }
            for m in memories
        ]
    }


# ==================== 项目相关 ====================

@router.get("/projects", response_model=List[ProjectInfo])
async def list_projects(db: Session = Depends(get_db)):
    """获取所有项目列表"""
    projects = db.query(Project).order_by(Project.display_order.asc(), Project.id.asc()).all()
    return [_serialize_project(db, project) for project in projects]


@router.get("/chats", response_model=List[ChatInfo])
async def list_chats(db: Session = Depends(get_db)):
    """List visible standalone chats."""
    service = SessionService(db)
    return [_serialize_chat(db, chatroom) for chatroom in service.list_visible_chats()]


@router.post("/chats", response_model=ChatInfo)
async def create_chat(chat_create: ChatCreate, db: Session = Depends(get_db)):
    """Create a standalone chat."""
    service = SessionService(db)
    chatroom = service.create_standalone_chat(chat_create.title)
    return _serialize_chat(db, chatroom)


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: int, db: Session = Depends(get_db)):
    """Delete a standalone chat and its messages."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chat_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chatroom.session_type != "standalone":
        raise HTTPException(status_code=400, detail="Only standalone chats can be deleted here")

    (
        db.query(Chatroom)
        .filter(Chatroom.source_chatroom_id == chatroom.id)
        .update({Chatroom.source_chatroom_id: None}, synchronize_session=False)
    )
    db.query(Message).filter(Message.chatroom_id == chatroom.id).delete()
    db.delete(chatroom)
    chatroom_manager.chatrooms.pop(chatroom.id, None)
    db.commit()

    return {"message": "Chat deleted successfully"}


@router.put("/chats/{chat_id}", response_model=ChatInfo)
async def update_chat(chat_id: int, chat_update: ChatUpdate, db: Session = Depends(get_db)):
    """Rename a standalone chat."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chat_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chatroom.session_type != "standalone":
        raise HTTPException(status_code=400, detail="Only standalone chats can be renamed here")

    next_title = (chat_update.title or "").strip()
    if not next_title:
        raise HTTPException(status_code=400, detail="Chat title cannot be empty")

    chatroom.title = next_title
    db.add(chatroom)
    db.commit()
    db.refresh(chatroom)
    return _serialize_chat(db, chatroom)


def _validate_agent_names(agent_names: List[str]) -> None:
    registry = get_registry()
    valid_agent_names = registry.list_agents()

    for agent_name in agent_names:
        normalized = normalize_agent_type(agent_name)
        if normalized not in valid_agent_names:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid agent type: {agent_name}. Valid agents: {valid_agent_names}",
            )


def _normalize_agent_names(agent_names: List[str] | None) -> List[str]:
    normalized = [normalize_agent_type(agent_name) for agent_name in (agent_names or [DEFAULT_AGENT_TYPE]) if agent_name]
    deduped = list(dict.fromkeys(normalized))
    return deduped or [DEFAULT_AGENT_TYPE]


@router.post("/projects", response_model=ProjectInfo)
async def create_project(project_create: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names)
    service = SessionService(db)
    project, _, _ = service.create_project_directly(
        name=project_create.name,
        description=project_create.description or "",
        agent_names=agent_names,
        workspace_path=project_create.workspace_path,
    )
    return _serialize_project(db, project)


@router.post("/projects/from-github", response_model=ProjectInfo)
async def create_project_from_github(project_create: GitHubProjectCreate, db: Session = Depends(get_db)):
    """Import a GitHub repository into a managed Catown workspace and create a project."""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names)
    service = SessionService(db)
    project, _, _ = service.create_project_from_github(
        repo_url=project_create.repo_url,
        name=project_create.name,
        description=project_create.description or "",
        ref=project_create.ref,
        agent_names=agent_names,
    )
    return _serialize_project(db, project)


@router.get("/projects/self-bootstrap", response_model=ProjectInfo)
@router.post("/projects/self-bootstrap", response_model=ProjectInfo)
async def get_or_create_self_bootstrap_project(db: Session = Depends(get_db)):
    """Open the Catown repo itself as the default self-bootstrap project workspace."""
    service = SessionService(db)
    project, _, _ = service.get_or_create_self_bootstrap_project()
    return _serialize_project(db, project)


@router.post("/projects/{project_id}/sync", response_model=ProjectSyncInfo)
async def sync_project(project_id: int, db: Session = Depends(get_db)):
    """Sync a GitHub-backed project workspace with its upstream repository."""
    service = SessionService(db)
    project, sync_info = service.sync_github_project(project_id)
    return ProjectSyncInfo(project=_serialize_project(db, project), **sync_info)


@router.put("/projects/reorder", response_model=List[ProjectInfo])
async def reorder_projects(payload: ProjectReorderRequest, db: Session = Depends(get_db)):
    """Persist project sidebar ordering."""
    existing_projects = db.query(Project).order_by(Project.display_order.asc(), Project.id.asc()).all()
    if not existing_projects:
        return []

    existing_ids = [project.id for project in existing_projects]
    requested_ids = payload.project_ids or []
    requested_id_set = set(requested_ids)
    if len(requested_id_set) != len(requested_ids):
        raise HTTPException(status_code=400, detail="Duplicate project ids are not allowed")

    ordered_ids = [project_id for project_id in requested_ids if project_id in requested_id_set and project_id in existing_ids]
    ordered_ids.extend(project_id for project_id in existing_ids if project_id not in requested_id_set)

    project_map = {project.id: project for project in existing_projects}
    for index, project_id in enumerate(ordered_ids):
        project = project_map[project_id]
        project.display_order = index
        db.add(project)

    db.commit()
    reordered = db.query(Project).order_by(Project.display_order.asc(), Project.id.asc()).all()
    return [_serialize_project(db, project) for project in reordered]


@router.post("/projects/from-chat", response_model=ProjectInfo)
async def create_project_from_chat(project_create: ProjectFromChatCreate, db: Session = Depends(get_db)):
    """Create a project from the current standalone chat and copy its context."""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names)
    service = SessionService(db)
    project, _, _ = service.create_project_from_chat(
        source_chatroom_id=project_create.source_chatroom_id,
        name=project_create.name,
        description=project_create.description or "",
        agent_names=agent_names,
        workspace_path=project_create.workspace_path,
    )
    return _serialize_project(db, project)


@router.get("/projects/{project_id}", response_model=ProjectInfo)
async def get_project(project_id: int, db: Session = Depends(get_db)):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialize_project(db, project)


@router.get("/projects/{project_id}/chat", response_model=ChatInfo)
async def get_project_chat(project_id: int, db: Session = Depends(get_db)):
    """Return the hidden default chat for a project."""
    service = SessionService(db)
    chatroom = service.get_project_chat(project_id)
    return _serialize_chat(db, chatroom)


@router.post("/projects/{project_id}/subchats", response_model=ChatInfo)
async def create_project_subchat(
    project_id: int,
    payload: ProjectSubchatCreate,
    db: Session = Depends(get_db),
):
    """Create a visible sub chat linked to a project's hidden main chat."""
    service = SessionService(db)
    chatroom = service.create_project_subchat(project_id, payload.title)
    return _serialize_chat(db, chatroom)


@router.post("/projects/{project_id}/open-workspace")
async def open_project_workspace(project_id: int, db: Session = Depends(get_db)):
    """Open the project's workspace folder in the local file manager."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")

    _open_workspace_path(project.workspace_path)
    return {"message": "Workspace opened"}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    """删除项目"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 清理子记录（模型未配置级联删除）
    db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).delete()
    root_chatrooms = db.query(Chatroom).filter(Chatroom.project_id == project_id).order_by(Chatroom.id.asc()).all()
    if not root_chatrooms and project.default_chatroom_id:
        fallback_chatroom = db.query(Chatroom).filter(Chatroom.id == project.default_chatroom_id).first()
        if fallback_chatroom:
            root_chatrooms = [fallback_chatroom]

    descendant_chatrooms = _collect_descendant_chatrooms(db, [chatroom.id for chatroom in root_chatrooms])
    # Delete descendants first so visible sub-chats do not survive a project deletion.
    _delete_chatrooms_with_messages(db, list(reversed(descendant_chatrooms)) + root_chatrooms)
    
    # 删除项目
    db.delete(project)
    db.commit()
    
    return {"message": "Project deleted successfully"}


@router.put("/projects/{project_id}", response_model=ProjectInfo)
async def update_project(project_id: int, project_update: ProjectUpdate, db: Session = Depends(get_db)):
    """Rename a project and its hidden main chat."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    next_name = (project_update.name or "").strip()
    if not next_name:
        raise HTTPException(status_code=400, detail="Project name cannot be empty")

    project.name = next_name
    db.add(project)

    chatroom = db.query(Chatroom).filter(Chatroom.project_id == project_id).first()
    if chatroom:
        chatroom.title = next_name
        db.add(chatroom)

    db.commit()
    db.refresh(project)
    return _serialize_project(db, project)


# ==================== 聊天相关 ====================

@router.get("/chatrooms/{chatroom_id}/messages", response_model=List[MessageResponse])
async def get_messages(chatroom_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """获取聊天室消息"""
    messages = await chatroom_manager.get_messages(chatroom_id, limit)
    
    return [
        MessageResponse(
            id=msg.id,
            content=msg.content,
            agent_name=msg.agent_name,
            message_type=msg.message_type,
            created_at=msg.created_at.isoformat(),
            client_turn_id=_message_client_turn_id(msg),
        )
        for msg in messages
    ]


@router.get("/chatrooms/{chatroom_id}/runtime-cards")
async def get_runtime_cards(chatroom_id: int, limit: int = 200, db: Session = Depends(get_db)):
    """获取聊天室历史 runtime cards，用于刷新后回放 agent 执行过程。"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        available_chatrooms = [row[0] for row in db.query(Chatroom.id).order_by(Chatroom.id.asc()).limit(20).all()]
        logger.warning(
            "[RuntimeCards] chatroom not found: id=%s available=%s",
            chatroom_id,
            available_chatrooms,
        )
        raise HTTPException(status_code=404, detail="Chatroom not found")

    rows = (
        db.query(Message)
        .filter(Message.chatroom_id == chatroom_id, Message.message_type == "runtime_card")
        .order_by(Message.created_at.asc())
        .limit(limit)
        .all()
    )

    cards: List[Dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        card = metadata.get("card")
        if isinstance(card, dict):
            card_payload = dict(card)
            card_payload.setdefault("created_at", row.created_at.isoformat())
            card_payload.setdefault("runtime_message_id", row.id)
            cards.append(card_payload)

    return cards


@router.get("/chatrooms/{chatroom_id}/visibility")
async def get_chatroom_visibility(chatroom_id: int, db: Session = Depends(get_db)):
    """获取聊天室消息可见度配置"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")
    return {"chatroom_id": chatroom_id, "message_visibility": chatroom.message_visibility or "all"}


@router.put("/chatrooms/{chatroom_id}/visibility")
async def set_chatroom_visibility(
    chatroom_id: int,
    body: dict,
    db: Session = Depends(get_db)
):
    """设置聊天室消息可见度: all=所有agent可见, target=仅目标agent可见"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")
    visibility = body.get("message_visibility")
    if visibility not in ("all", "target"):
        raise HTTPException(status_code=400, detail="message_visibility must be 'all' or 'target'")
    chatroom.message_visibility = visibility
    db.commit()
    return {"chatroom_id": chatroom_id, "message_visibility": visibility}


@router.post("/chatrooms/{chatroom_id}/messages", response_model=MessageResponse)
async def send_message(chatroom_id: int, message: MessageRequest, db: Session = Depends(get_db)):
    """发送消息到聊天室"""
    logger.info(f"[API] send_message called: chatroom_id={chatroom_id}, content={message.content[:50]}...")
    
    # 发送用户消息
    response_msg = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,  # None 表示用户
        content=message.content,
        message_type="text",
        metadata=_message_metadata_with_turn(message.client_turn_id),
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=response_msg.id,
        content=response_msg.content,
        agent_name=None,
        message_type=response_msg.message_type,
        created_at=response_msg.created_at,
        metadata=_message_metadata_with_turn(message.client_turn_id),
    )
    
    logger.info(f"[API] User message saved: id={response_msg.id}")
    
    # 触发 Agent 响应（同步等待，方便调试）
    try:
        await trigger_agent_response(chatroom_id, message.content, message.client_turn_id)
        logger.info(f"[API] Agent response completed")
    except Exception as e:
        logger.info(f"[API] Agent response error: {e}")
        import traceback
        traceback.print_exc()
    
    return MessageResponse(
        id=response_msg.id,
        content=response_msg.content,
        agent_name=response_msg.agent_name,
        message_type=response_msg.message_type,
        created_at=response_msg.created_at.isoformat(),
        client_turn_id=message.client_turn_id,
    )


@router.post("/chatrooms/{chatroom_id}/messages/stream")
async def send_message_stream(chatroom_id: int, message: MessageRequest):
    """
    发送消息到聊天室（SSE 流式响应）

    返回 SSE 事件流：
    - data: {"type": "content", "delta": "..."}      — LLM 生成的文本增量
    - data: {"type": "tool_start", "tool": "..."}     — 开始执行工具
    - data: {"type": "tool_result", "tool": "...", "result": "..."} — 工具执行完毕
    - data: {"type": "done", "agent_name": "...", "message_id": 123} — 全部完成
    - data: {"type": "error", "error": "..."}         — 出错
    """
    import asyncio
    import json as _json

    async def raw_event_generator():
        from models.database import get_db as _get_db
        from tools import tool_registry
        from tools.file_operations import reset_active_workspace, set_active_workspace
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
        async def _sse_card(event_type, data):
            """格式化卡片事件 SSE，附带 source=chatroom"""
            payload = dict(data)
            payload["type"] = event_type
            payload["source"] = "chatroom"
            if message.client_turn_id:
                payload["client_turn_id"] = message.client_turn_id
            await _store_runtime_card(chatroom_id, payload)
            return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"

        db = next(_get_db())
        workspace_token = None
        active_agent_name: Optional[str] = None
        active_agent_id: Optional[int] = None
        final_message_saved = False
        try:
            # 1. 保存用户消息
            user_msg = await chatroom_manager.send_message(
                chatroom_id=chatroom_id,
                agent_id=None,
                content=message.content,
                message_type="text",
                metadata=_message_metadata_with_turn(message.client_turn_id),
            )
            await _publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=user_msg.id,
                content=user_msg.content,
                agent_name=None,
                message_type=user_msg.message_type,
                created_at=user_msg.created_at,
                metadata=_message_metadata_with_turn(message.client_turn_id),
            )

            yield f"data: {_json.dumps({'type': 'user_saved', 'id': user_msg.id, 'client_turn_id': message.client_turn_id})}\n\n"

            # 2. 获取聊天室和项目
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No chatroom found'})}\n\n"
                return

            project = _resolve_chatroom_project(db, chatroom)
            workspace_token = set_active_workspace(project.workspace_path if project and project.workspace_path else None)
            if not project:
                mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', message.content)] if '@' in message.content else []
                if len(mentioned_names) > 1:
                    yield f"data: {_json.dumps({'type': 'collab_start', 'agents': mentioned_names})}\n\n"

                    agents = _list_global_agents(db)
                    previous_context = None

                    for step_idx, agent_name in enumerate(mentioned_names):
                        agent = find_agent_by_type(agents, agent_name)
                        if not agent:
                            yield f"data: {_json.dumps({'type': 'collab_skip', 'agent': agent_name, 'reason': 'not found'})}\n\n"
                            continue

                        agent_label = agent_name_of(agent)
                        yield f"data: {_json.dumps({'type': 'collab_step', 'step': step_idx + 1, 'total': len(mentioned_names), 'agent': agent_label})}\n\n"

                        active_agent_name = agent_label
                        active_agent_id = agent.id
                        llm_client = get_llm_client_for_agent(_agent_type(agent))
                        sys_prompt = agent.system_prompt or f"You are {agent_label}, a {agent.role}."
                        sys_prompt += (
                            "\n\nThis is a standalone chat. Reply directly, stay concise, "
                            "and coordinate with the other mentioned agents."
                        )
                        sys_prompt += _build_runtime_context_block(db, chatroom, None)
                        team_members = [f"- **{agent_name_of(a)}** (type: `{_agent_type(a)}`, role: {a.role})" for a in agents]
                        sys_prompt += f"\n\nAvailable agents in this standalone chat:\n" + "\n".join(team_members)
                        if previous_context:
                            previous_agent = find_agent_by_type(agents, mentioned_names[step_idx - 1])
                            previous_label = agent_name_of(previous_agent) if previous_agent else mentioned_names[step_idx - 1]
                            sys_prompt += f"\n\nPrevious agent ({previous_label}) output:\n{previous_context[:1500]}"

                        msgs = [{"role": "system", "content": sys_prompt}]
                        recent = await chatroom_manager.get_messages(chatroom_id, limit=4)
                        _append_recent_llm_history(msgs, recent, limit=3)
                        _append_current_user_message(msgs, message.content)

                        import time as _time
                        step_content = ""
                        tool_schemas = tool_registry.get_schemas()
                        for iteration in range(MAX_TOOL_ITERATIONS):
                            tool_calls_found = False
                            _llm_start = _time.time()
                            _llm_content = ""
                            _llm_tool_calls = []
                            llm_prompt_messages = _snapshot_llm_messages(msgs)
                            yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': agent_label, 'model': getattr(llm_client, 'model', ''), 'turn': iteration + 1, 'system_prompt': sys_prompt, 'prompt_messages': _format_json_block(llm_prompt_messages), 'client_turn_id': message.client_turn_id}, ensure_ascii=False)}\n\n"
                            async for event in _iter_with_heartbeat(llm_client.chat_stream(msgs, tool_schemas or None)):
                                if event["type"] == _HEARTBEAT_EVENT_TYPE:
                                    elapsed_ms = int((_time.time() - _llm_start) * 1000)
                                    yield f"data: {_json.dumps({'type': 'llm_wait', 'agent': agent_label, 'elapsed_ms': elapsed_ms, 'turn': iteration + 1})}\n\n"
                                    continue

                                if event["type"] in {"request_sent", "first_chunk", "first_content"}:
                                    yield f"data: {_json.dumps({'type': event['type'], 'agent': agent_label, 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration + 1})}\n\n"
                                    continue

                                if event["type"] == "tool_call_delta":
                                    yield f"data: {_json.dumps({'type': 'tool_call_delta', 'agent': agent_label, 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration + 1, 'tool_call_index': event.get('tool_call_index'), 'tool': event.get('tool_name') or 'tool', 'args': event.get('arguments') or '', 'tool_call_id': (event.get('tool_call') or {}).get('id')})}\n\n"
                                    continue

                                if event["type"] == "tool_call_ready":
                                    yield f"data: {_json.dumps({'type': 'tool_call_ready', 'agent': agent_label, 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration + 1, 'tool_calls': event.get('tool_calls') or []})}\n\n"
                                    continue

                                if event["type"] == "content":
                                    step_content += event["delta"]
                                    _llm_content += event["delta"]
                                    yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta'], 'agent': agent_label})}\n\n"
                                elif event["type"] == "done":
                                    tc = event.get("tool_calls")
                                    _llm_full = event.get("full_content", _llm_content)
                                    if tc:
                                        tool_calls_found = True
                                        _llm_tool_calls = _preview_tool_calls(tc)
                                        msgs.append({"role": "assistant", "content": _llm_full, "tool_calls": tc})
                                        for tool_index, t in enumerate(tc):
                                            tname = t["function"]["name"]
                                            _args_str = t["function"].get("arguments", "{}")
                                            yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tname, 'agent': agent_label, 'args': _args_str, 'tool_call_index': tool_index, 'tool_call_id': t.get('id')})}\n\n"
                                            _tool_start = _time.time()
                                            try:
                                                targs = json.loads(t["function"]["arguments"])
                                                async for tool_event in _await_with_heartbeat(tool_registry.execute(tname, **targs)):
                                                    if tool_event["type"] == _HEARTBEAT_EVENT_TYPE:
                                                        elapsed_ms = int((_time.time() - _tool_start) * 1000)
                                                        yield f"data: {_json.dumps({'type': 'tool_wait', 'tool': tname, 'agent': agent_label, 'elapsed_ms': elapsed_ms, 'tool_call_index': tool_index, 'tool_call_id': t.get('id')})}\n\n"
                                                        continue
                                                    tres = tool_event["result"]
                                                    tres_str = str(tres)[:2000] if tres else "(no output)"
                                                    break
                                            except Exception as te:
                                                tres_str = f"Error: {te}"
                                            tool_success = _tool_result_succeeded(tres_str)
                                            _tool_ms = int((_time.time() - _tool_start) * 1000)
                                            yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tname, 'result': tres_str[:500], 'success': tool_success, 'agent': agent_label, 'tool_call_index': tool_index, 'tool_call_id': t.get('id')})}\n\n"
                                            msgs.append({"role": "tool", "tool_call_id": t["id"], "content": tres_str, "name": tname})
                                            yield await _sse_card("tool_call", {
                                                "agent": agent_label, "tool": tname,
                                                "arguments": _args_str,
                                                "success": tool_success, "result": tres_str[:1500],
                                                "duration_ms": _tool_ms,
                                                "tool_call_index": tool_index,
                                                "tool_call_id": t.get("id"),
                                            })
                                    _llm_ms = int((_time.time() - _llm_start) * 1000)
                                    yield await _sse_card(
                                        "llm_call",
                                        _build_llm_card_payload(
                                            agent_name=agent_label,
                                            llm_client=llm_client,
                                            turn=iteration + 1,
                                            duration_ms=_llm_ms,
                                            system_prompt=sys_prompt,
                                            prompt_messages=llm_prompt_messages,
                                            response_content=_llm_full or _llm_content,
                                            tool_call_previews=_llm_tool_calls,
                                            raw_tool_calls=tc,
                                            usage=event.get("usage"),
                                            finish_reason=event.get("finish_reason"),
                                            timings=event.get("timings"),
                                        ),
                                    )
                                elif event["type"] == "error":
                                    raise RuntimeError(event["error"])

                            if not tool_calls_found:
                                if _llm_content:
                                    _llm_ms = int((_time.time() - _llm_start) * 1000)
                                    yield await _sse_card(
                                        "llm_call",
                                        _build_llm_card_payload(
                                            agent_name=agent_label,
                                            llm_client=llm_client,
                                            turn=iteration + 1,
                                            duration_ms=_llm_ms,
                                            system_prompt=sys_prompt,
                                            prompt_messages=llm_prompt_messages,
                                            response_content=_llm_content,
                                            tool_call_previews=[],
                                            raw_tool_calls=[],
                                            usage=event.get("usage") if isinstance(event, dict) else None,
                                            finish_reason=event.get("finish_reason") if isinstance(event, dict) else None,
                                            timings=event.get("timings") if isinstance(event, dict) else None,
                                        ),
                                    )
                                break

                        saved = None
                        if step_content:
                            saved = await chatroom_manager.send_message(
                                chatroom_id=chatroom_id,
                                agent_id=agent.id,
                                content=step_content,
                                message_type="text",
                                metadata=_message_metadata_with_turn(message.client_turn_id),
                                agent_name=agent_label,
                            )
                            await _publish_saved_chat_message(
                                db,
                                chatroom_id,
                                message_id=saved.id,
                                content=step_content,
                                agent_name=agent_label,
                                message_type="text",
                                created_at=saved.created_at,
                                metadata=_message_metadata_with_turn(message.client_turn_id),
                            )
                            if len(step_content) > 30:
                                asyncio.create_task(_extract_memories(agent.id, _agent_type(agent), message.content, step_content))
                            previous_context = step_content

                        yield f"data: {_json.dumps({'type': 'collab_step_done', 'agent': agent_label, 'message_id': saved.id if step_content else None})}\n\n"

                    resolved_names = [agent_name_of(find_agent_by_type(agents, name)) for name in mentioned_names]
                    yield f"data: {_json.dumps({'type': 'done', 'agent_name': ', '.join(resolved_names), 'collab': True, 'client_turn_id': message.client_turn_id})}\n\n"
                    return

                async for chunk in _stream_standalone_assistant_response(
                    db=db,
                    chatroom_id=chatroom_id,
                    user_message=message.content,
                    sse_json=_json,
                    client_turn_id=message.client_turn_id,
                ):
                    yield chunk
                return

            # 3. 解析 @mention（支持多 Agent 流水线）
            mentioned_names = []
            if '@' in message.content:
                mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', message.content)]

            # 多 Agent 模式：逐个流式输出
            if len(mentioned_names) > 1:
                yield f"data: {_json.dumps({'type': 'collab_start', 'agents': mentioned_names})}\n\n"

                # 获取项目 Agents
                assignments = db.query(AgentAssignment).filter(
                    AgentAssignment.project_id == project.id
                ).all()
                agent_ids = [a.agent_id for a in assignments]
                agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

                # 自动分配未在项目中的 agent
                for name in mentioned_names:
                    if not find_agent_by_type(agents, name):
                        ga = _find_db_agent_by_type(db, name)
                        if ga:
                            db.add(AgentAssignment(project_id=project.id, agent_id=ga.id))
                            db.commit()
                            agents.append(ga)

                # 注册协作者
                from agents.collaboration import collaboration_coordinator, AgentCollaborator
                for a in agents:
                    if a.id not in collaboration_coordinator.collaborators:
                        collaboration_coordinator.register_collaborator(
                            AgentCollaborator(agent_id=a.id, agent_name=_agent_type(a), chatroom_id=chatroom_id)
                        )

                previous_context = None
                for step_idx, agent_name in enumerate(mentioned_names):
                    agent = find_agent_by_type(agents, agent_name)
                    if not agent:
                        yield f"data: {_json.dumps({'type': 'collab_skip', 'agent': agent_name, 'reason': 'not found'})}\n\n"
                        continue

                    agent_label = agent_name_of(agent)
                    yield f"data: {_json.dumps({'type': 'collab_step', 'step': step_idx + 1, 'total': len(mentioned_names), 'agent': agent_label})}\n\n"

                    # 构建消息（注入前序上下文）
                    from tools import tool_registry
                    from models.database import Memory
                    active_agent_name = agent_label
                    active_agent_id = agent.id
                    llm_client = get_llm_client_for_agent(_agent_type(agent))

                    sys_prompt = agent.system_prompt or f"You are {agent_label}, a {agent.role}."
                    sys_prompt += _build_runtime_context_block(db, chatroom, project)
                    if tool_registry.list_tools():
                        sys_prompt += f"\n\nTools: {', '.join(tool_registry.list_tools())}"
                    # 注入团队列表
                    team_members = [f"- **{agent_name_of(a)}** (type: `{_agent_type(a)}`, role: {a.role})" for a in agents]
                    sys_prompt += f"\n\nTeam members in this project:\n" + "\n".join(team_members)
                    if previous_context:
                        previous_agent = find_agent_by_type(agents, mentioned_names[step_idx - 1])
                        previous_label = agent_name_of(previous_agent) if previous_agent else mentioned_names[step_idx - 1]
                        sys_prompt += f"\n\nPrevious agent ({previous_label}) output:\n{previous_context[:1500]}"

                    msgs = [{"role": "system", "content": sys_prompt}]
                    recent = await chatroom_manager.get_messages(chatroom_id, limit=4)
                    _append_recent_llm_history(msgs, recent, limit=3)
                    _append_current_user_message(msgs, message.content)

                    # 流式输出
                    import time as _time
                    step_content = ""
                    tool_schemas = tool_registry.get_schemas()
                    for iteration in range(MAX_TOOL_ITERATIONS):
                        tool_calls_found = False
                        _llm_start = _time.time()
                        _llm_content = ""
                        _llm_tool_calls = []
                        llm_prompt_messages = _snapshot_llm_messages(msgs)
                        yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': agent_label, 'model': getattr(llm_client, 'model', ''), 'turn': iteration + 1, 'system_prompt': sys_prompt, 'prompt_messages': _format_json_block(llm_prompt_messages), 'client_turn_id': message.client_turn_id}, ensure_ascii=False)}\n\n"
                        async for event in _iter_with_heartbeat(llm_client.chat_stream(msgs, tool_schemas or None)):
                            if event["type"] == _HEARTBEAT_EVENT_TYPE:
                                elapsed_ms = int((_time.time() - _llm_start) * 1000)
                                yield f"data: {_json.dumps({'type': 'llm_wait', 'agent': agent_label, 'elapsed_ms': elapsed_ms, 'turn': iteration + 1})}\n\n"
                                continue

                            if event["type"] in {"request_sent", "first_chunk", "first_content"}:
                                yield f"data: {_json.dumps({'type': event['type'], 'agent': agent_label, 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration + 1})}\n\n"
                                continue

                            if event["type"] == "tool_call_delta":
                                yield f"data: {_json.dumps({'type': 'tool_call_delta', 'agent': agent_label, 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration + 1, 'tool_call_index': event.get('tool_call_index'), 'tool': event.get('tool_name') or 'tool', 'args': event.get('arguments') or '', 'tool_call_id': (event.get('tool_call') or {}).get('id')})}\n\n"
                                continue

                            if event["type"] == "tool_call_ready":
                                yield f"data: {_json.dumps({'type': 'tool_call_ready', 'agent': agent_label, 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration + 1, 'tool_calls': event.get('tool_calls') or []})}\n\n"
                                continue

                            if event["type"] == "content":
                                step_content += event["delta"]
                                _llm_content += event["delta"]
                                yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta'], 'agent': agent_label})}\n\n"
                            elif event["type"] == "done":
                                tc = event.get("tool_calls")
                                _llm_full = event.get("full_content", _llm_content)
                                if tc:
                                    tool_calls_found = True
                                    _llm_tool_calls = _preview_tool_calls(tc)
                                    msgs.append({"role": "assistant", "content": _llm_full, "tool_calls": tc})
                                    for tool_index, t in enumerate(tc):
                                        tname = t["function"]["name"]
                                        _args_str = t["function"].get("arguments", "{}")
                                        yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tname, 'agent': agent_label, 'args': _args_str, 'tool_call_index': tool_index, 'tool_call_id': t.get('id')})}\n\n"
                                        _tool_start = _time.time()
                                        try:
                                            targs = json.loads(t["function"]["arguments"])
                                            async for tool_event in _await_with_heartbeat(tool_registry.execute(tname, **targs)):
                                                if tool_event["type"] == _HEARTBEAT_EVENT_TYPE:
                                                    elapsed_ms = int((_time.time() - _tool_start) * 1000)
                                                    yield f"data: {_json.dumps({'type': 'tool_wait', 'tool': tname, 'agent': agent_label, 'elapsed_ms': elapsed_ms, 'tool_call_index': tool_index, 'tool_call_id': t.get('id')})}\n\n"
                                                    continue
                                                tres = tool_event["result"]
                                                tres_str = str(tres)[:2000] if tres else "(no output)"
                                                break
                                        except Exception as te:
                                            tres_str = f"Error: {te}"
                                        tool_success = _tool_result_succeeded(tres_str)
                                        _tool_ms = int((_time.time() - _tool_start) * 1000)
                                        yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tname, 'result': tres_str[:500], 'success': tool_success, 'agent': agent_label, 'tool_call_index': tool_index, 'tool_call_id': t.get('id')})}\n\n"
                                        msgs.append({"role": "tool", "tool_call_id": t["id"], "content": tres_str, "name": tname})
                                        # 工具卡片事件
                                        yield await _sse_card("tool_call", {
                                            "agent": agent_label, "tool": tname,
                                            "arguments": _args_str,
                                            "success": tool_success, "result": tres_str[:1500],
                                            "duration_ms": _tool_ms,
                                            "tool_call_index": tool_index,
                                            "tool_call_id": t.get("id"),
                                        })
                                # LLM 调用卡片事件
                                _llm_ms = int((_time.time() - _llm_start) * 1000)
                                yield await _sse_card(
                                    "llm_call",
                                    _build_llm_card_payload(
                                        agent_name=agent_label,
                                        llm_client=llm_client,
                                        turn=iteration + 1,
                                        duration_ms=_llm_ms,
                                        system_prompt=sys_prompt,
                                        prompt_messages=llm_prompt_messages,
                                        response_content=_llm_full or _llm_content,
                                        tool_call_previews=_llm_tool_calls,
                                        raw_tool_calls=tc,
                                        usage=event.get("usage"),
                                        finish_reason=event.get("finish_reason"),
                                        timings=event.get("timings"),
                                    ),
                                )
                            elif event["type"] == "error":
                                raise RuntimeError(event["error"])
                        if not tool_calls_found:
                            # 无工具调用的最终 LLM 回复卡片
                            if _llm_content:
                                _llm_ms = int((_time.time() - _llm_start) * 1000)
                                yield await _sse_card(
                                    "llm_call",
                                    _build_llm_card_payload(
                                        agent_name=agent_label,
                                        llm_client=llm_client,
                                        turn=iteration + 1,
                                        duration_ms=_llm_ms,
                                        system_prompt=sys_prompt,
                                        prompt_messages=llm_prompt_messages,
                                        response_content=_llm_content,
                                        tool_call_previews=[],
                                        raw_tool_calls=[],
                                        usage=event.get("usage") if isinstance(event, dict) else None,
                                        finish_reason=event.get("finish_reason") if isinstance(event, dict) else None,
                                        timings=event.get("timings") if isinstance(event, dict) else None,
                                    ),
                                )
                            break

                    # 保存
                    saved = None
                    if step_content:
                        saved = await chatroom_manager.send_message(
                            chatroom_id=chatroom_id, agent_id=agent.id,
                            content=step_content,
                            message_type="text",
                            metadata=_message_metadata_with_turn(message.client_turn_id),
                            agent_name=agent_label,
                        )
                        await _publish_saved_chat_message(
                            db,
                            chatroom_id,
                            message_id=saved.id,
                            content=step_content,
                            agent_name=agent_label,
                            message_type="text",
                            created_at=saved.created_at,
                            metadata=_message_metadata_with_turn(message.client_turn_id),
                        )
                        if len(step_content) > 30:
                            asyncio.create_task(_extract_memories(agent.id, _agent_type(agent), message.content, step_content))
                        previous_context = step_content

                    yield f"data: {_json.dumps({'type': 'collab_step_done', 'agent': agent_label, 'message_id': saved.id if step_content else None})}\n\n"

                resolved_names = [agent_name_of(find_agent_by_type(agents, name)) for name in mentioned_names]
                yield f"data: {_json.dumps({'type': 'done', 'agent_name': ', '.join(resolved_names), 'collab': True, 'client_turn_id': message.client_turn_id})}\n\n"
                return

            # 单 Agent 模式（原有逻辑）
            target_agent_name = mentioned_names[0] if mentioned_names else None

            # 4. 获取项目 Agents
            assignments = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == project.id
            ).all()
            agent_ids = [a.agent_id for a in assignments]
            agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

            target_agent = None
            if target_agent_name:
                target_agent = find_agent_by_type(agents, target_agent_name)
                # @mentioned agent 不在项目中 → 从全局查找并自动分配
                if not target_agent:
                    global_agent = _find_db_agent_by_type(db, target_agent_name)
                    if global_agent:
                        logger.info(f"[Agent] Auto-assigning '{target_agent_name}' to project '{project.name}'")
                        assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                        db.add(assignment)
                        db.commit()
                        target_agent = global_agent
                        agents.append(global_agent)
            if not target_agent:
                target_agent = find_agent_by_type(agents, DEFAULT_AGENT_TYPE) or (agents[0] if agents else None)
            if not target_agent:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No agent available'})}\n\n"
                return

            active_agent_name = agent_name_of(target_agent)
            active_agent_id = target_agent.id

            # 注册项目中所有 agent 为协作者
            from agents.collaboration import collaboration_coordinator, AgentCollaborator
            for agent in agents:
                if agent.id not in collaboration_coordinator.collaborators:
                    collaborator = AgentCollaborator(
                        agent_id=agent.id,
                        agent_name=_agent_type(agent),
                        chatroom_id=chatroom_id
                    )
                    collaboration_coordinator.register_collaborator(collaborator)

            # 5. 构建该 Agent 的消息上下文
            llm_client = get_llm_client_for_agent(_agent_type(target_agent))
            messages = []

            system_prompt = target_agent.system_prompt or f"You are {agent_name_of(target_agent)}, a {target_agent.role}."
            system_prompt += _build_runtime_context_block(db, chatroom, project)

            available_tools = tool_registry.list_tools()
            if available_tools:
                system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"

            # 注入项目中所有 Agent 列表
            team_members = [f"- **{agent_name_of(a)}** (type: `{_agent_type(a)}`, role: {a.role})" for a in agents]
            system_prompt += f"\n\nTeam members in this project:\n" + "\n".join(team_members)
            system_prompt += f"\n\nYou are **{agent_name_of(target_agent)}** (type: `{_agent_type(target_agent)}`, role: {target_agent.role}). You can use tools to communicate with or delegate tasks to your teammates."

            # 注入记忆（含跨 Agent 共享）
            from models.database import Memory
            own_memories = (
                db.query(Memory)
                .filter(Memory.agent_id == target_agent.id)
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(8)
                .all()
            )
            other_agent_ids = [a.id for a in agents if a.id != target_agent.id]
            shared_memories = []
            if other_agent_ids:
                shared_memories = (
                    db.query(Memory)
                    .filter(Memory.agent_id.in_(other_agent_ids), Memory.importance >= 7)
                    .order_by(Memory.importance.desc(), Memory.created_at.desc())
                    .limit(5)
                    .all()
                )
            if own_memories:
                system_prompt += "\n\nYour memories (context for your responses):"
                for mem in own_memories:
                    ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
                    system_prompt += f"\n- [{ts}] [importance={mem.importance}] {mem.content[:200]}"
            if shared_memories:
                system_prompt += "\n\nShared context from other agents:"
                for mem in shared_memories:
                    ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
                    source_agent = next((a for a in agents if a.id == mem.agent_id), None)
                    source_name = agent_name_of(source_agent) if source_agent else "unknown"
                    system_prompt += f"\n- [{ts}] [{source_name}] {mem.content[:200]}"

            messages.append({"role": "system", "content": system_prompt})

            recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=10)
            _append_recent_llm_history(messages, recent_messages, limit=6)
            _append_current_user_message(messages, message.content)

            tool_schemas = tool_registry.get_schemas()

            # 6. 流式 LLM 循环
            import time as _time2
            max_tool_iterations = MAX_TOOL_ITERATIONS
            iteration = 0
            final_content = ""

            while iteration < max_tool_iterations:
                iteration += 1
                tool_calls_found = False
                _llm_start = _time2.time()
                _llm_content = ""
                _llm_tool_calls = []
                llm_prompt_messages = _snapshot_llm_messages(messages)
                yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': agent_name_of(target_agent), 'model': getattr(llm_client, 'model', ''), 'turn': iteration, 'system_prompt': system_prompt, 'prompt_messages': _format_json_block(llm_prompt_messages), 'client_turn_id': message.client_turn_id}, ensure_ascii=False)}\n\n"

                async for event in _iter_with_heartbeat(
                    llm_client.chat_stream(messages, tool_schemas if tool_schemas else None)
                ):
                    if event["type"] == _HEARTBEAT_EVENT_TYPE:
                        elapsed_ms = int((_time2.time() - _llm_start) * 1000)
                        yield f"data: {_json.dumps({'type': 'llm_wait', 'agent': agent_name_of(target_agent), 'elapsed_ms': elapsed_ms, 'turn': iteration})}\n\n"
                        continue

                    if event["type"] in {"request_sent", "first_chunk", "first_content"}:
                        yield f"data: {_json.dumps({'type': event['type'], 'agent': agent_name_of(target_agent), 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration})}\n\n"
                        continue

                    if event["type"] == "tool_call_delta":
                        yield f"data: {_json.dumps({'type': 'tool_call_delta', 'agent': agent_name_of(target_agent), 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration, 'tool_call_index': event.get('tool_call_index'), 'tool': event.get('tool_name') or 'tool', 'args': event.get('arguments') or '', 'tool_call_id': (event.get('tool_call') or {}).get('id')})}\n\n"
                        continue

                    if event["type"] == "tool_call_ready":
                        yield f"data: {_json.dumps({'type': 'tool_call_ready', 'agent': agent_name_of(target_agent), 'elapsed_ms': event.get('elapsed_ms'), 'turn': iteration, 'tool_calls': event.get('tool_calls') or []})}\n\n"
                        continue

                    if event["type"] == "content":
                        final_content += event["delta"]
                        _llm_content += event["delta"]
                        yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta']})}\n\n"

                    elif event["type"] == "done":
                        tool_calls = event.get("tool_calls")
                        full_content = event.get("full_content", "")

                        if not tool_calls:
                            # 无工具调用，结束 — 发 LLM 卡片
                            if _llm_content:
                                _llm_ms = int((_time2.time() - _llm_start) * 1000)
                                yield await _sse_card(
                                    "llm_call",
                                    _build_llm_card_payload(
                                        agent_name=agent_name_of(target_agent),
                                        llm_client=llm_client,
                                        turn=iteration,
                                        duration_ms=_llm_ms,
                                        system_prompt=system_prompt,
                                        prompt_messages=llm_prompt_messages,
                                        response_content=_llm_content,
                                        tool_call_previews=[],
                                        raw_tool_calls=[],
                                        usage=event.get("usage"),
                                        finish_reason=event.get("finish_reason"),
                                        timings=event.get("timings"),
                                    ),
                                )
                            break

                        tool_calls_found = True
                        _llm_tool_calls = _preview_tool_calls(tool_calls)

                        # 将 assistant 消息加入上下文
                        assistant_msg = {
                            "role": "assistant",
                            "content": full_content,
                            "tool_calls": tool_calls
                        }
                        messages.append(assistant_msg)

                        # 执行工具
                        for tool_index, tc in enumerate(tool_calls):
                            tool_name = tc["function"]["name"]
                            tool_args_str = tc["function"]["arguments"]
                            tool_call_id = tc["id"]

                            yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tool_name, 'args': tool_args_str, 'agent': agent_name_of(target_agent), 'tool_call_index': tool_index, 'tool_call_id': tool_call_id})}\n\n"
                            _tool_start = _time2.time()

                            try:
                                tool_args = json.loads(tool_args_str)
                                async for tool_event in _await_with_heartbeat(tool_registry.execute(tool_name, **tool_args)):
                                    if tool_event["type"] == _HEARTBEAT_EVENT_TYPE:
                                        elapsed_ms = int((_time2.time() - _tool_start) * 1000)
                                        yield f"data: {_json.dumps({'type': 'tool_wait', 'tool': tool_name, 'agent': agent_name_of(target_agent), 'elapsed_ms': elapsed_ms, 'tool_call_index': tool_index, 'tool_call_id': tool_call_id})}\n\n"
                                        continue
                                    tool_result = tool_event["result"]
                                    result_str = str(tool_result) if tool_result is not None else "(no output)"
                                    break
                            except Exception as te:
                                result_str = f"Error: {str(te)}"
                            tool_success = _tool_result_succeeded(result_str)

                            # 截断过长的工具结果
                            if len(result_str) > 2000:
                                result_str = result_str[:2000] + "\n...(truncated)"
                            _tool_ms = int((_time2.time() - _tool_start) * 1000)

                            yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result_str[:500], 'success': tool_success, 'agent': agent_name_of(target_agent), 'tool_call_index': tool_index, 'tool_call_id': tool_call_id})}\n\n"

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": result_str,
                                "name": tool_name
                            })

                            # 工具卡片事件
                            yield await _sse_card("tool_call", {
                                "agent": agent_name_of(target_agent),
                                "tool": tool_name,
                                "arguments": tool_args_str,
                                "success": tool_success,
                                "result": result_str[:1500],
                                "duration_ms": _tool_ms,
                                "tool_call_index": tool_index,
                                "tool_call_id": tool_call_id,
                            })

                        # LLM 调用卡片事件（含工具调用）
                        _llm_ms = int((_time2.time() - _llm_start) * 1000)
                        yield await _sse_card(
                            "llm_call",
                            _build_llm_card_payload(
                                agent_name=agent_name_of(target_agent),
                                llm_client=llm_client,
                                turn=iteration,
                                duration_ms=_llm_ms,
                                system_prompt=system_prompt,
                                prompt_messages=llm_prompt_messages,
                                response_content=full_content or _llm_content,
                                tool_call_previews=_llm_tool_calls,
                                raw_tool_calls=tool_calls,
                                usage=event.get("usage"),
                                finish_reason=event.get("finish_reason"),
                                timings=event.get("timings"),
                            ),
                        )

                    elif event["type"] == "error":
                        raise RuntimeError(event["error"])

                if not tool_calls_found:
                    break

            # 7. 保存最终响应
            if not final_content:
                final_content = "(Agent returned empty response)"

            agent_response = await chatroom_manager.send_message(
                chatroom_id=chatroom_id,
                agent_id=target_agent.id,
                content=final_content,
                message_type="text",
                metadata=_message_metadata_with_turn(message.client_turn_id),
                agent_name=agent_name_of(target_agent)
            )

            await _publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=agent_response.id,
                content=final_content,
                agent_name=agent_name_of(target_agent),
                message_type="text",
                created_at=agent_response.created_at,
                metadata=_message_metadata_with_turn(message.client_turn_id),
            )
            final_message_saved = True

            yield f"data: {_json.dumps({'type': 'done', 'agent_name': agent_name_of(target_agent), 'message_id': agent_response.id, 'client_turn_id': message.client_turn_id})}\n\n"

            # 异步提取记忆
            if len(final_content) > 30:
                asyncio.create_task(_extract_memories(
                    agent_id=target_agent.id,
                    agent_type=_agent_type(target_agent),
                    user_message=message.content,
                    agent_response=final_content
                ))

        except Exception as e:
            logger.error(f"[SSE] Stream error: {e}")
            traceback.print_exc()
            if final_message_saved:
                yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            else:
                try:
                    saved = await _persist_stream_failure(
                        db,
                        chatroom_id=chatroom_id,
                        client_turn_id=message.client_turn_id,
                        error_message=str(e),
                        agent_name=active_agent_name,
                        agent_id=active_agent_id,
                        detail=traceback.format_exc(),
                    )
                    yield f"data: {_json.dumps({'type': 'done', 'agent_name': active_agent_name or default_agent_name(DEFAULT_AGENT_TYPE), 'message_id': saved.id, 'client_turn_id': message.client_turn_id}, ensure_ascii=False)}\n\n"
                except Exception as persist_exc:
                    logger.error(f"[SSE] Failed to persist stream failure: {persist_exc}")
                    traceback.print_exc()
                    yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            if workspace_token is not None:
                reset_active_workspace(workspace_token)
            db.close()

    async def event_generator():
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
        client_connected = True

        async def producer():
            nonlocal client_connected
            try:
                async for chunk in raw_event_generator():
                    if not client_connected:
                        continue
                    try:
                        queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            # If the client is lagging badly, drop intermediate chunks
                            # and let the persisted runtime cards/final message catch up.
                            pass
            finally:
                if client_connected:
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

        asyncio.create_task(producer())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            client_connected = False
            raise
        finally:
            client_connected = False

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==================== 状态相关 ====================

@router.get("/status")
async def get_status(db: Session = Depends(get_db)):
    """获取系统状态"""
    agent_count = db.query(Agent).count()
    project_count = db.query(Project).count()
    chatroom_count = db.query(Chatroom).count()
    message_count = db.query(Message).count()
    
    return {
        "status": "healthy",
        "version": "1.0.0",
        "stats": {
            "agents": agent_count,
            "projects": project_count,
            "chatrooms": chatroom_count,
            "messages": message_count
        },
        "features": {
            "llm_enabled": True,
            "websocket_enabled": True,
            "tools_enabled": True,
            "memory_enabled": True
        }
    }


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok"}


# ==================== 配置相关 ====================

@router.get("/config")
async def get_config():
    """
    获取配置信息（唯一来源：agents.json）

    返回：
    - global_llm: 全局 LLM 配置（Agent 未配置时的 fallback）
    - agents: 各 Agent 的完整配置
    - agent_llm_configs: 各 Agent 实际生效的 LLM 配置摘要
    - server: 服务器配置
    - features: 功能开关
    """
    from pathlib import Path

    config = {
        "server": {
            "host": os.getenv("HOST", "0.0.0.0"),
            "port": int(os.getenv("PORT", "8000"))
        },
        "llm": {
            "base_url": os.getenv("LLM_BASE_URL", ""),
            "model": os.getenv("LLM_MODEL", ""),
            "has_api_key": bool(os.getenv("LLM_API_KEY", ""))
        },
        "global_llm": {},
        "features": {
            "llm_enabled": True,
            "websocket_enabled": True,
            "tools_enabled": True,
            "memory_enabled": True
        },
        "agents": {},
        "agent_llm_configs": {}
    }

    # 从 agents.json 加载（唯一配置源）
    agents_config_file = Path(settings.AGENT_CONFIG_FILE)
    if agents_config_file.exists():
        try:
            with open(agents_config_file, 'r', encoding='utf-8') as f:
                agents_config = json.load(f)

            # 全局 LLM 配置
            config["global_llm"] = agents_config.get("global_llm", {})

            agents_data = dict(agents_config.get("agents", {}))
            if "assistant" in agents_data and DEFAULT_AGENT_TYPE not in agents_data:
                agents_data[DEFAULT_AGENT_TYPE] = agents_data.pop("assistant")
            for agent_type, agent_data in agents_data.items():
                raw_name = agent_data.get("name")
                agent_data["name"] = (
                    default_agent_name(agent_type)
                    if is_legacy_default_agent_name(raw_name, agent_type)
                    else str(raw_name).strip()
                )
            config["agents"] = agents_data

            # 全局 provider 摘要（用于显示 fallback 来源）
            global_provider = agents_config.get("global_llm", {}).get("provider", {})
            global_model = agents_config.get("global_llm", {}).get("default_model", "")
            if not global_model:
                gm = global_provider.get("models", [])
                if gm:
                    global_model = gm[0].get("id", "")

            # 提取各 Agent 实际生效的 LLM 配置摘要
            for agent_name, agent_data in agents_data.items():
                provider = agent_data.get("provider", {})
                default_model = agent_data.get("default_model", "")
                models = provider.get("models", [])

                # 判断是否使用 Agent 自身配置还是全局 fallback
                has_own_provider = bool(provider.get("baseUrl", ""))
                if has_own_provider:
                    effective_model = default_model or (models[0]["id"] if models else "")
                    effective_url = provider.get("baseUrl", "")
                else:
                    effective_model = global_model
                    effective_url = global_provider.get("baseUrl", "")

                config["agent_llm_configs"][agent_name] = {
                    "baseUrl": effective_url,
                    "model": effective_model,
                    "hasApiKey": bool(provider.get("apiKey", "") if has_own_provider else global_provider.get("apiKey", "")),
                    "models": [m["id"] for m in models] if has_own_provider else [m["id"] for m in global_provider.get("models", [])],
                    "source": "agent" if has_own_provider else "global"
                }
        except Exception as e:
            logger.warning(f"Failed to load agents.json: {e}")

    return config


@router.post("/config")
async def update_config(config: LLMConfigModel):
    """
    更新 LLM 配置（验证通过的配置）

    配置将通过 LLMConfigModel 验证：
    - api_key 不能为空
    - base_url 必须是有效的 URL
    - temperature 必须在 0-2 之间
    - max_tokens 必须在 1-100000 之间
    """
    return {
        "message": "Configuration validated successfully",
        "config": config.model_dump()
    }


@router.put("/config/global")
async def update_global_llm_config(config: Dict[str, Any]):
    """
    更新全局 LLM 配置（global_llm 段）

    请求体：
    {
        "provider": {
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "models": [{"id": "gpt-4", ...}]
        },
        "default_model": "gpt-4"
    }
    """
    from pathlib import Path

    config_file = Path(settings.AGENT_CONFIG_FILE)
    try:
        # 读取现有配置
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        # 更新全局配置
        data["global_llm"] = config
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 清空 LLM 客户端缓存
        clear_client_cache()

        return {"message": "Global LLM config updated", "global_llm": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


@router.put("/config/agent/{agent_name}")
async def update_agent_llm_config(agent_name: str, config: Dict[str, Any]):
    """
    更新指定 Agent 的完整配置

    请求体：
    {
        "provider": {
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "models": [{"id": "gpt-4", ...}]
        },
        "default_model": "gpt-4"
    }

    支持更新 provider/default_model/role/soul/tools/skills。
    设置 provider 为空对象 {} 可清除 Agent 级 LLM 配置，回退到全局。
    """
    from pathlib import Path

    config_file = Path(settings.AGENT_CONFIG_FILE)
    agent_name = normalize_agent_type(agent_name)
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        agents = data.get("agents", {})
        if "assistant" in agents and DEFAULT_AGENT_TYPE not in agents:
            agents[DEFAULT_AGENT_TYPE] = agents.pop("assistant")
        if agent_name not in agents:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        # 更新 Agent 的完整配置字段
        for field_name in ("provider", "default_model", "role", "soul", "tools", "skills"):
            if field_name in config:
                agents[agent_name][field_name] = config[field_name]

        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 清空该 Agent 的 LLM 客户端缓存
        clear_client_cache()

        return {
            "message": f"Agent '{agent_name}' LLM config updated",
            "agent": agents[agent_name]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


@router.post("/config/reload")
async def reload_config():
    """
    重新加载 agents.json 配置（清空 LLM 客户端缓存）

    用于外部修改 agents.json 后通知服务生效，无需重启。
    """
    clear_client_cache()

    # 重新注册 Agent
    from agents.registry import register_builtin_agents
    register_builtin_agents()

    return {"message": "Configuration reloaded from agents.json"}


@router.post("/config/test")
async def test_agent_config(agent_name: str = DEFAULT_AGENT_TYPE):
    """
    测试指定 Agent 的 LLM 连接

    从 agents.json 读取该 Agent 的 provider 配置并发送测试请求。
    """
    from llm.client import _load_agent_provider
    from openai import AsyncOpenAI

    agent_name = normalize_agent_type(agent_name)
    provider = _load_agent_provider(agent_name)
    if not provider:
        raise HTTPException(
            status_code=404,
            detail=f"No provider config found for agent '{agent_name}' in agents.json"
        )

    try:
        client = AsyncOpenAI(
            api_key=provider["api_key"],
            base_url=provider["base_url"]
        )
        response = await client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=5
        )
        return {
            "status": "success",
            "agent": agent_name,
            "model": provider["model"],
            "baseUrl": provider["base_url"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")


# ==================== Tools 相关 ====================

@router.get("/tools")
async def list_tools():
    """获取所有可用的工具列表"""
    from tools import tool_registry
    
    tools = []
    for name in tool_registry.list_tools():
        tool = tool_registry.get(name)
        if tool:
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "schema": tool.get_schema()
            })
    
    return {"tools": tools, "count": len(tools)}


@router.post("/tools/{tool_name}/execute")
async def execute_tool(tool_name: str, arguments: Dict[str, Any]):
    """执行指定的工具"""
    from tools import tool_registry
    
    try:
        result = await tool_registry.execute(tool_name, **arguments)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== Collaboration 相关 ====================

@router.get("/collaboration/status")
async def get_collaboration_status():
    """获取协作系统状态"""
    from agents.collaboration import collaboration_coordinator
    
    return {
        "active_collaborators": len(collaboration_coordinator.collaborators),
        "chatrooms": len(collaboration_coordinator.chatroom_agents),
        "pending_tasks": len(collaboration_coordinator.task_registry),
        "status": "active"
    }


@router.get("/collaboration/chatrooms/{chatroom_id}/status")
async def get_chatroom_collaboration_status(chatroom_id: int):
    """获取聊天室的协作状态"""
    from agents.collaboration import collaboration_coordinator
    
    status = collaboration_coordinator.get_chatroom_status(chatroom_id)
    return status


@router.get("/collaboration/tasks/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    from agents.collaboration import collaboration_coordinator
    
    task = collaboration_coordinator.get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assigned_to": task.assigned_to_agent_id,
        "result": task.result,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None
    }


@router.post("/collaboration/delegate")
async def delegate_task_to_agent(
    target_agent_name: str,
    task_title: str,
    task_description: str,
    chatroom_id: int,
    db: Session = Depends(get_db)
):
    """委托任务给指定 Agent"""
    from agents.collaboration import collaboration_coordinator, CollaborationTask, TaskStatus, uuid
    from tools import tool_registry
    
    # 查找目标 Agent
    target_agent_type = normalize_agent_type(target_agent_name)
    target_agent = _find_db_agent_by_type(db, target_agent_type)
    if not target_agent:
        raise HTTPException(status_code=404, detail=f"Agent '{target_agent_type}' not found")
    
    # 创建任务
    task = CollaborationTask(
        id=str(uuid.uuid4()),
        title=task_title,
        description=task_description,
        status=TaskStatus.DELEGATED,
        created_by_agent_id=0,  # User
        assigned_to_agent_id=target_agent.id,
        chatroom_id=chatroom_id
    )
    
    collaboration_coordinator.task_registry[task.id] = task
    
    return {
        "task_id": task.id,
        "status": "delegated",
        "assigned_to": target_agent_type
    }


@router.get("/collaboration/tasks")
async def list_collaboration_tasks(chatroom_id: Optional[int] = None):
    """列出协作任务"""
    from agents.collaboration import collaboration_coordinator
    
    tasks = list(collaboration_coordinator.task_registry.values())
    
    if chatroom_id:
        tasks = [t for t in tasks if t.chatroom_id == chatroom_id]
    
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "assigned_to": t.assigned_to_agent_id,
                "created_at": t.created_at.isoformat()
            }
            for t in tasks
        ],
        "count": len(tasks)
    }
