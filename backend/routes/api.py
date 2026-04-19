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
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, field_validator

from models.database import get_db, Agent, Project, Chatroom, AgentAssignment, Message, Base
from agents.registry import get_registry
from agents.core import Agent as AgentInstance
from chatrooms.manager import chatroom_manager
from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
from config import settings
from services.session_service import SessionService

logger = logging.getLogger("catown.api")


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


# ==================== Agent 响应处理 ====================

def _find_mentioned_agent_name(message: str) -> Optional[str]:
    """Return the first @mentioned agent name in a message, if any."""
    mentioned_names = re.findall(r'@(\w+)', message or "")
    return mentioned_names[0] if mentioned_names else None


def _resolve_standalone_target_agent(db: Session, user_message: str) -> Optional[Agent]:
    """
    Resolve which agent should answer in a standalone chat.

    Prefer the first @mentioned agent; otherwise fall back to assistant.
    """
    target_agent_name = _find_mentioned_agent_name(user_message)
    if target_agent_name:
        mentioned_agent = db.query(Agent).filter(Agent.name == target_agent_name).first()
        if mentioned_agent:
            return mentioned_agent

    return db.query(Agent).filter(Agent.name == "assistant").first()


def _list_global_agents(db: Session) -> List[Agent]:
    """List all available agents for standalone chat routing."""
    return db.query(Agent).order_by(Agent.name.asc()).all()


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


def _format_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


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
) -> Dict[str, Any]:
    usage = usage or {}
    raw_response = {
        "role": "assistant",
        "content": response_content or "",
        "tool_calls": raw_tool_calls or [],
        "usage": usage,
        "finish_reason": finish_reason,
    }
    return {
        "agent": agent_name,
        "model": getattr(llm_client, "model", ""),
        "turn": turn,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "duration_ms": duration_ms,
        "system_prompt": system_prompt or "",
        "prompt_messages": _format_json_block(prompt_messages),
        "response": response_content or "",
        "raw_response": _format_json_block(raw_response),
        "tool_calls": tool_call_previews,
    }

async def _trigger_standalone_assistant_response(db: Session, chatroom_id: int, user_message: str):
    """Generate a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        logger.debug("[ No chatroom found for standalone response")
        return

    assistant = _resolve_standalone_target_agent(db, user_message)

    if assistant:
        llm_client = get_llm_client_for_agent(assistant.name)
        assistant_name = assistant.name
        assistant_id = assistant.id
        system_prompt = assistant.system_prompt or "You are assistant, a helpful AI collaborator."
    else:
        llm_client = get_default_llm_client()
        assistant_name = "assistant"
        assistant_id = None
        system_prompt = "You are assistant, a helpful AI collaborator."

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
        agent_name=assistant_name,
    )

    from routes.websocket import websocket_manager
    await websocket_manager.broadcast_to_room({
        "type": "message",
        "id": agent_response.id,
        "content": response_content,
        "agent_name": assistant_name,
        "message_type": "text",
        "created_at": agent_response.created_at.isoformat(),
    }, chatroom_id)

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
):
    """Stream a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        yield f"data: {sse_json.dumps({'type': 'error', 'error': 'Chatroom not found'})}\n\n"
        return

    assistant = _resolve_standalone_target_agent(db, user_message)

    if assistant:
        llm_client = get_llm_client_for_agent(assistant.name)
        assistant_name = assistant.name
        assistant_id = assistant.id
        system_prompt = assistant.system_prompt or "You are assistant, a helpful AI collaborator."
    else:
        llm_client = get_default_llm_client()
        assistant_name = "assistant"
        assistant_id = None
        system_prompt = "You are assistant, a helpful AI collaborator."

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

    yield f"data: {sse_json.dumps({'type': 'agent_start', 'agent_name': assistant_name, 'model': getattr(llm_client, 'model', ''), 'turn': 1, 'system_prompt': system_prompt, 'prompt_messages': _format_json_block(prompt_snapshot)}, ensure_ascii=False)}\n\n"

    final_content = ""
    async for event in llm_client.chat_stream(context_messages):
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
                duration_ms=0,
                system_prompt=system_prompt,
                prompt_messages=prompt_snapshot,
                response_content=response_content,
                tool_call_previews=[],
                raw_tool_calls=event.get("tool_calls"),
                usage=event.get("usage"),
                finish_reason=event.get("finish_reason"),
            )
            llm_payload["type"] = "llm_call"
            llm_payload["source"] = "chatroom"
            await _store_runtime_card(chatroom_id, llm_payload)
            yield f"data: {sse_json.dumps(llm_payload, ensure_ascii=False)}\n\n"
            final_content = response_content
            break

        if event["type"] == "error":
            yield f"data: {sse_json.dumps({'type': 'error', 'error': event['error']})}\n\n"
            return

    if not final_content:
        final_content = "(Agent returned empty response)"

    agent_response = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=assistant_id,
        content=final_content,
        message_type="text",
        agent_name=assistant_name,
    )

    from routes.websocket import websocket_manager
    await websocket_manager.broadcast_to_room({
        "type": "message",
        "id": agent_response.id,
        "content": final_content,
        "agent_name": assistant_name,
        "message_type": "text",
        "created_at": agent_response.created_at.isoformat(),
    }, chatroom_id)

    yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': assistant_name, 'message_id': agent_response.id})}\n\n"

    if assistant_id and len(final_content) > 30:
        asyncio.create_task(_extract_memories(
            agent_id=assistant_id,
            agent_name=assistant_name,
            user_message=user_message,
            agent_response=final_content,
        ))


async def trigger_agent_response(chatroom_id: int, user_message: str):
    """触发 Agent 处理消息并生成响应（统一执行路径 + 工具结果回传 LLM）"""
    from models.database import get_db
    from tools import tool_registry
    import json
    
    db = next(get_db())
    try:
        logger.debug(f"[ trigger_agent_response called: chatroom_id={chatroom_id}, message={user_message[:50]}...")
        
        # 1. 获取聊天室关联的项目
        chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
        if not chatroom:
            logger.debug(f"[ No chatroom found")
            return

        project = _resolve_chatroom_project(db, chatroom)
        if not project:
            mentioned_names = re.findall(r'@(\w+)', user_message) if '@' in user_message else []
            if len(mentioned_names) > 1:
                logger.info(f"[Collab] Standalone multi-agent pipeline triggered: {mentioned_names}")
                await _run_multi_agent_pipeline(
                    chatroom_id=chatroom_id,
                    project=None,
                    agents=_list_global_agents(db),
                    agent_names=mentioned_names,
                    user_message=user_message,
                    db=db,
                )
                return
            await _trigger_standalone_assistant_response(db, chatroom_id, user_message)
            return
        
        logger.debug(f"[ Found project: {project.name}")

        # 2. 解析 @ 提及，检测多 Agent 协作
        mentioned_names = []
        if '@' in user_message:
            mentioned_names = re.findall(r'@(\w+)', user_message)

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
                db=db
            )
            return

        target_agent_name = mentioned_names[0] if mentioned_names else None
        logger.debug(f"[ Target agent name: {target_agent_name}")

        # 4. 确定响应的 Agent
        target_agent = None
        if target_agent_name:
            target_agent = next((a for a in agents if a.name == target_agent_name), None)

            # @mentioned agent 不在项目中 → 从全局注册表查找并自动分配
            if not target_agent:
                global_agent = db.query(Agent).filter(Agent.name == target_agent_name).first()
                if global_agent:
                    logger.info(f"[Agent] Auto-assigning '{target_agent_name}' to project '{project.name}'")
                    assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                    db.add(assignment)
                    db.commit()
                    target_agent = global_agent
                    agents.append(global_agent)

        if not target_agent:
            target_agent = next((a for a in agents if a.name == "assistant"), agents[0] if agents else None)

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
                    agent_name=agent.name,
                    chatroom_id=chatroom_id
                )
                collaboration_coordinator.register_collaborator(collaborator)
                logger.info(f"[Collab] Auto-registered collaborator: {agent.name}")
        
        # 5. 获取该 Agent 的 LLM 客户端
        llm_client = get_llm_client_for_agent(target_agent.name)
        logger.debug(f"[ LLM client obtained for {target_agent.name}: {llm_client.base_url}")
        
        # 6. 构建消息上下文
        messages = []
        
        system_prompt = target_agent.system_prompt or f"You are {target_agent.name}, a {target_agent.role}."
        system_prompt += _build_runtime_context_block(db, chatroom, project)
        
        available_tools = tool_registry.list_tools()
        if available_tools:
            system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"
            system_prompt += "\nWhen you need to use a tool, respond with a tool call and the system will execute it."

        # 注入项目中所有 Agent 列表（让 Agent 知道队友是谁）
        team_members = [f"- **{a.name}** (role: {a.role})" for a in agents]
        system_prompt += f"\n\nTeam members in this project:\n" + "\n".join(team_members)
        if target_agent.id:
            system_prompt += f"\n\nYou are **{target_agent.name}** (role: {target_agent.role}). You can use tools to communicate with or delegate tasks to your teammates."

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
            # 所有 agent 可见：包含所有消息，标注发言者
            for msg in recent_messages[-10:]:
                agent_name = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
                if msg.message_type == "user" or not agent_name:
                    messages.append({"role": "user", "content": msg.content})
                else:
                    messages.append({"role": "assistant", "content": f"[{agent_name}]: {msg.content}"})
        else:
            # 仅目标可见：只包含用户消息和该 agent 自己的消息
            for msg in recent_messages[-10:]:
                agent_name = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
                if msg.message_type == "user" or not agent_name:
                    messages.append({"role": "user", "content": msg.content})
                elif agent_name == target_agent.name:
                    messages.append({"role": "assistant", "content": msg.content})
        
        # 追加当前用户消息
        messages.append({"role": "user", "content": user_message})
        
        logger.debug(f"[ Context messages: {len(messages)} messages")
        
        # 7. 获取工具 schemas
        tool_schemas = tool_registry.get_schemas()
        
        # 8. 主循环：LLM → 执行工具 → 结果回传 LLM → 直到没有 tool_calls
        logger.info(f"[LLM] Calling LLM for agent: {target_agent.name} with {len(tool_schemas)} tools available")
        
        max_tool_iterations = 5
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
            agent_name=target_agent.name
        )
        
        logger.debug(f"[ Agent response saved: id={agent_response.id}")
        
        # 10. 通过 WebSocket 广播
        from routes.websocket import websocket_manager
        await websocket_manager.broadcast_to_room({
            "type": "message",
            "id": agent_response.id,
            "content": response_content,
            "agent_name": target_agent.name,
            "message_type": "text"
        }, chatroom_id)
        
        logger.info(f"[Agent] {target_agent.name} responded to message successfully")

        # 11. 异步提取记忆（不阻塞响应）
        if len(response_content) > 30:
            asyncio.create_task(_extract_memories(
                agent_id=target_agent.id,
                agent_name=target_agent.name,
                user_message=user_message,
                agent_response=response_content
            ))

    except Exception as e:
        logger.error(f"[ Agent response failed: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


async def _extract_memories(agent_id: int, agent_name: str, user_message: str, agent_response: str):
    """
    用 LLM 从对话中提取关键信息，存为 Agent 记忆

    提取内容：事实、决策、用户偏好、重要上下文
    跳过条件：简单问候、确认类回复
    """
    try:
        from models.database import get_db as _get_db, Memory
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache

        llm = get_llm_client_for_agent(agent_name)

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
            logger.info(f"[Memory] Extracted {len(memories)} memories for {agent_name}")
        except Exception as e:
            db.rollback()
            logger.debug(f"[Memory] Save failed: {e}")
        finally:
            db.close()

    except Exception as e:
        logger.debug(f"[Memory] Extraction failed: {e}")


async def _run_single_agent_turn(
    agent, chatroom_id, project, agents, user_message, extra_context, db
):
    """
    执行单个 Agent 的一次响应（供多 Agent 流水线调用）

    Returns: (response_content, agent_response_msg) 或 (None, None)
    """
    from tools import tool_registry
    from agents.collaboration import collaboration_coordinator, AgentCollaborator
    from models.database import Memory

    llm_client = get_llm_client_for_agent(agent.name)
    current_chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()

    # 注册协作者
    for a in agents:
        if a.id not in collaboration_coordinator.collaborators:
            collaboration_coordinator.register_collaborator(
                AgentCollaborator(agent_id=a.id, agent_name=a.name, chatroom_id=chatroom_id)
            )

    # 构建 system prompt
    system_prompt = agent.system_prompt or f"You are {agent.name}, a {agent.role}."
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
    team_members = [f"- **{a.name}** (role: {a.role})" for a in agents]
    team_label = "Team members in this project" if project else "Available agents in this standalone chat"
    system_prompt += f"\n\n{team_label}:\n" + "\n".join(team_members)
    system_prompt += (
        f"\n\nYou are **{agent.name}** (role: {agent.role}). "
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
            src = next((a.name for a in agents if a.id == mem.agent_id), "unknown")
            system_prompt += f"\n- [{ts}] [{src}] {mem.content[:200]}"

    # 如果有协作上下文（前一个 Agent 的回复），注入
    if extra_context:
        system_prompt += f"\n\nPrevious agent's work for you to build upon:\n{extra_context[:1500]}"

    messages = [{"role": "system", "content": system_prompt}]

    # 近期对话
    recent_msgs = await chatroom_manager.get_messages(chatroom_id, limit=6)
    for msg in recent_msgs[-4:]:
        a_name = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
        if msg.message_type == "user" or not a_name:
            messages.append({"role": "user", "content": msg.content})
        else:
            messages.append({"role": "assistant", "content": msg.content})

    messages.append({"role": "user", "content": user_message})

    tool_schemas = tool_registry.get_schemas()
    response_content = ""

    # 工具调用循环
    for iteration in range(5):
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
        content=response_content, message_type="text", agent_name=agent.name
    )

    # 异步提取记忆
    if len(response_content) > 30:
        asyncio.create_task(_extract_memories(agent.id, agent.name, user_message, response_content))

    return response_content, agent_msg


async def _run_multi_agent_pipeline(
    chatroom_id, project, agents, agent_names, user_message, db
):
    """
    多 Agent 协作流水线

    流程：每个按序 @mention 的 Agent 依次响应，后一个看到前一个的输出。
    最终通过 WebSocket 广播所有响应。
    """
    from routes.websocket import websocket_manager

    resolved_agents = []
    for name in agent_names:
        agent = next((a for a in agents if a.name == name), None)
        if not agent:
            global_agent = db.query(Agent).filter(Agent.name == name).first()
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

    logger.info(f"[Collab] Pipeline: {' → '.join(a.name for a in resolved_agents)}")

    previous_context = None
    results = []

    for i, agent in enumerate(resolved_agents):
        logger.info(f"[Collab] Step {i+1}/{len(resolved_agents)}: {agent.name}")

        # 为后续 Agent 注入前序上下文
        extra_msg = user_message
        if previous_context:
            extra_msg = (
                f"{user_message}\n\n"
                f"[Context from previous agent ({resolved_agents[i-1].name})]:\n{previous_context}"
            )

        content, msg = await _run_single_agent_turn(
            agent=agent,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=extra_msg,
            extra_context=previous_context,
            db=db
        )

        if content:
            # WebSocket 广播
            await websocket_manager.broadcast_to_room({
                "type": "message",
                "id": msg.id,
                "content": content,
                "agent_name": agent.name,
                "message_type": "text"
            }, chatroom_id)

            results.append({"agent": agent.name, "content": content})
            previous_context = content
        else:
            logger.warning(f"[Collab] {agent.name} returned empty response")

    logger.info(f"[Collab] Pipeline complete: {len(results)}/{len(resolved_agents)} agents responded")


# ==================== 数据模型 ====================

class AgentInfo(BaseModel):
    id: int
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
    agent_names: List[str] = ["assistant"]
    workspace_path: Optional[str] = None


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
    created_from_chatroom_id: Optional[int] = None
    agents: List[AgentInfo]


class ProjectUpdate(BaseModel):
    name: str


class ProjectReorderRequest(BaseModel):
    project_ids: List[int]


class MessageRequest(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: int
    content: str
    agent_name: Optional[str]
    message_type: str
    created_at: str


async def _store_runtime_card(chatroom_id: int, payload: Dict[str, Any]) -> None:
    """Persist runtime cards so chat refresh can replay execution history."""
    card_payload = dict(payload)
    await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,
        content=card_payload.get("type", "runtime_card"),
        message_type="runtime_card",
        metadata={"card": card_payload},
    )


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
        created_from_chatroom_id=default_chatroom.source_chatroom_id if default_chatroom else None,
        agents=[
            AgentInfo(id=agent.id, name=agent.name, role=agent.role, is_active=agent.is_active)
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
            id=agent.id, name=agent.name, role=agent.role,
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
        id=agent.id, name=agent.name, role=agent.role,
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
        if agent_name not in valid_agent_names:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid agent name: {agent_name}. Valid agents: {valid_agent_names}",
            )


def _normalize_agent_names(agent_names: List[str] | None) -> List[str]:
    normalized = [agent_name for agent_name in (agent_names or ["assistant"]) if agent_name]
    return normalized or ["assistant"]


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


@router.get("/projects/self-bootstrap", response_model=ProjectInfo)
@router.post("/projects/self-bootstrap", response_model=ProjectInfo)
async def get_or_create_self_bootstrap_project(db: Session = Depends(get_db)):
    """Open the Catown repo itself as the default self-bootstrap project workspace."""
    service = SessionService(db)
    project, _, _ = service.get_or_create_self_bootstrap_project()
    return _serialize_project(db, project)


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
    chatroom = db.query(Chatroom).filter(Chatroom.project_id == project_id).first()
    if chatroom:
        db.query(Message).filter(Message.chatroom_id == chatroom.id).delete()
        db.delete(chatroom)
        chatroom_manager.chatrooms.pop(chatroom.id, None)
    
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
        )
        for msg in messages
    ]


@router.get("/chatrooms/{chatroom_id}/runtime-cards")
async def get_runtime_cards(chatroom_id: int, limit: int = 200, db: Session = Depends(get_db)):
    """获取聊天室历史 runtime cards，用于刷新后回放 agent 执行过程。"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
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
            cards.append(card)

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
        message_type="text"
    )
    
    logger.info(f"[API] User message saved: id={response_msg.id}")
    
    # 触发 Agent 响应（同步等待，方便调试）
    try:
        await trigger_agent_response(chatroom_id, message.content)
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
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
        from routes.websocket import websocket_manager

        async def _sse_card(event_type, data):
            """格式化卡片事件 SSE，附带 source=chatroom"""
            payload = dict(data)
            payload["type"] = event_type
            payload["source"] = "chatroom"
            await _store_runtime_card(chatroom_id, payload)
            return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"

        db = next(_get_db())
        try:
            # 1. 保存用户消息
            user_msg = await chatroom_manager.send_message(
                chatroom_id=chatroom_id,
                agent_id=None,
                content=message.content,
                message_type="text"
            )

            yield f"data: {_json.dumps({'type': 'user_saved', 'id': user_msg.id})}\n\n"

            # 2. 获取聊天室和项目
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No chatroom found'})}\n\n"
                return

            project = _resolve_chatroom_project(db, chatroom)
            if not project:
                mentioned_names = re.findall(r'@(\w+)', message.content) if '@' in message.content else []
                if len(mentioned_names) > 1:
                    yield f"data: {_json.dumps({'type': 'collab_start', 'agents': mentioned_names})}\n\n"

                    agents = _list_global_agents(db)
                    previous_context = None

                    for step_idx, agent_name in enumerate(mentioned_names):
                        agent = next((a for a in agents if a.name == agent_name), None)
                        if not agent:
                            yield f"data: {_json.dumps({'type': 'collab_skip', 'agent': agent_name, 'reason': 'not found'})}\n\n"
                            continue

                        yield f"data: {_json.dumps({'type': 'collab_step', 'step': step_idx + 1, 'total': len(mentioned_names), 'agent': agent_name})}\n\n"

                        llm_client = get_llm_client_for_agent(agent.name)
                        sys_prompt = agent.system_prompt or f"You are {agent.name}, a {agent.role}."
                        sys_prompt += (
                            "\n\nThis is a standalone chat. Reply directly, stay concise, "
                            "and coordinate with the other mentioned agents."
                        )
                        sys_prompt += _build_runtime_context_block(db, chatroom, None)
                        team_members = [f"- **{a.name}** (role: {a.role})" for a in agents]
                        sys_prompt += f"\n\nAvailable agents in this standalone chat:\n" + "\n".join(team_members)
                        if previous_context:
                            sys_prompt += f"\n\nPrevious agent ({mentioned_names[step_idx-1]}) output:\n{previous_context[:1500]}"

                        msgs = [{"role": "system", "content": sys_prompt}]
                        recent = await chatroom_manager.get_messages(chatroom_id, limit=4)
                        for msg in recent[-3:]:
                            an = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
                            if msg.message_type == "user" or not an:
                                msgs.append({"role": "user", "content": msg.content})
                            else:
                                msgs.append({"role": "assistant", "content": msg.content})
                        msgs.append({"role": "user", "content": message.content})

                        import time as _time
                        step_content = ""
                        tool_schemas = tool_registry.get_schemas()
                        for iteration in range(5):
                            tool_calls_found = False
                            _llm_start = _time.time()
                            _llm_content = ""
                            _llm_tool_calls = []
                            llm_prompt_messages = _snapshot_llm_messages(msgs)
                            yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': agent_name, 'model': getattr(llm_client, 'model', ''), 'turn': iteration + 1, 'system_prompt': sys_prompt, 'prompt_messages': _format_json_block(llm_prompt_messages)}, ensure_ascii=False)}\n\n"
                            async for event in llm_client.chat_stream(msgs, tool_schemas or None):
                                if event["type"] == "content":
                                    step_content += event["delta"]
                                    _llm_content += event["delta"]
                                    yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta'], 'agent': agent_name})}\n\n"
                                elif event["type"] == "done":
                                    tc = event.get("tool_calls")
                                    _llm_full = event.get("full_content", _llm_content)
                                    if tc:
                                        tool_calls_found = True
                                        msgs.append({"role": "assistant", "content": _llm_full, "tool_calls": tc})
                                        for t in tc:
                                            tname = t["function"]["name"]
                                            yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tname, 'agent': agent_name})}\n\n"
                                            _tool_start = _time.time()
                                            try:
                                                targs = json.loads(t["function"]["arguments"])
                                                tres = await tool_registry.execute(tname, **targs)
                                                tres_str = str(tres)[:2000] if tres else "(no output)"
                                            except Exception as te:
                                                tres_str = f"Error: {te}"
                                            _tool_ms = int((_time.time() - _tool_start) * 1000)
                                            yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tname, 'result': tres_str[:500], 'agent': agent_name})}\n\n"
                                            msgs.append({"role": "tool", "tool_call_id": t["id"], "content": tres_str, "name": tname})
                                            _args_str = t["function"].get("arguments", "{}")
                                            yield await _sse_card("tool_call", {
                                                "agent": agent_name, "tool": tname,
                                                "arguments": _args_str,
                                                "success": True, "result": tres_str[:1500],
                                                "duration_ms": _tool_ms,
                                            })
                                            _llm_tool_calls.append({"name": tname, "args_preview": _args_str[:120]})
                                    _llm_ms = int((_time.time() - _llm_start) * 1000)
                                    yield await _sse_card(
                                        "llm_call",
                                        _build_llm_card_payload(
                                            agent_name=agent_name,
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
                                        ),
                                    )
                                elif event["type"] == "error":
                                    yield f"data: {_json.dumps({'type': 'error', 'error': event['error'], 'agent': agent_name})}\n\n"
                                    break

                            if not tool_calls_found:
                                if _llm_content:
                                    _llm_ms = int((_time.time() - _llm_start) * 1000)
                                    yield await _sse_card(
                                        "llm_call",
                                        _build_llm_card_payload(
                                            agent_name=agent_name,
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
                                agent_name=agent.name,
                            )
                            await websocket_manager.broadcast_to_room({
                                "type": "message",
                                "id": saved.id,
                                "content": step_content,
                                "agent_name": agent.name,
                                "message_type": "text",
                            }, chatroom_id)
                            if len(step_content) > 30:
                                asyncio.create_task(_extract_memories(agent.id, agent.name, message.content, step_content))
                            previous_context = step_content

                        yield f"data: {_json.dumps({'type': 'collab_step_done', 'agent': agent_name, 'message_id': saved.id if step_content else None})}\n\n"

                    yield f"data: {_json.dumps({'type': 'done', 'agent_name': ', '.join(mentioned_names), 'collab': True})}\n\n"
                    return

                async for chunk in _stream_standalone_assistant_response(
                    db=db,
                    chatroom_id=chatroom_id,
                    user_message=message.content,
                    sse_json=_json,
                ):
                    yield chunk
                return

            # 3. 解析 @mention（支持多 Agent 流水线）
            mentioned_names = []
            if '@' in message.content:
                mentioned_names = re.findall(r'@(\w+)', message.content)

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
                    if not any(a.name == name for a in agents):
                        ga = db.query(Agent).filter(Agent.name == name).first()
                        if ga:
                            db.add(AgentAssignment(project_id=project.id, agent_id=ga.id))
                            db.commit()
                            agents.append(ga)

                # 注册协作者
                from agents.collaboration import collaboration_coordinator, AgentCollaborator
                for a in agents:
                    if a.id not in collaboration_coordinator.collaborators:
                        collaboration_coordinator.register_collaborator(
                            AgentCollaborator(agent_id=a.id, agent_name=a.name, chatroom_id=chatroom_id)
                        )

                previous_context = None
                for step_idx, agent_name in enumerate(mentioned_names):
                    agent = next((a for a in agents if a.name == agent_name), None)
                    if not agent:
                        yield f"data: {_json.dumps({'type': 'collab_skip', 'agent': agent_name, 'reason': 'not found'})}\n\n"
                        continue

                    yield f"data: {_json.dumps({'type': 'collab_step', 'step': step_idx + 1, 'total': len(mentioned_names), 'agent': agent_name})}\n\n"

                    # 构建消息（注入前序上下文）
                    from tools import tool_registry
                    from models.database import Memory
                    llm_client = get_llm_client_for_agent(agent.name)

                    sys_prompt = agent.system_prompt or f"You are {agent.name}, a {agent.role}."
                    sys_prompt += _build_runtime_context_block(db, chatroom, project)
                    if tool_registry.list_tools():
                        sys_prompt += f"\n\nTools: {', '.join(tool_registry.list_tools())}"
                    # 注入团队列表
                    team_members = [f"- **{a.name}** (role: {a.role})" for a in agents]
                    sys_prompt += f"\n\nTeam members in this project:\n" + "\n".join(team_members)
                    if previous_context:
                        sys_prompt += f"\n\nPrevious agent ({mentioned_names[step_idx-1]}) output:\n{previous_context[:1500]}"

                    msgs = [{"role": "system", "content": sys_prompt}]
                    recent = await chatroom_manager.get_messages(chatroom_id, limit=4)
                    for msg in recent[-3:]:
                        an = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
                        if msg.message_type == "user" or not an:
                            msgs.append({"role": "user", "content": msg.content})
                        else:
                            msgs.append({"role": "assistant", "content": msg.content})
                    msgs.append({"role": "user", "content": message.content})

                    # 流式输出
                    import time as _time
                    step_content = ""
                    tool_schemas = tool_registry.get_schemas()
                    for iteration in range(5):
                        tool_calls_found = False
                        _llm_start = _time.time()
                        _llm_content = ""
                        _llm_tool_calls = []
                        llm_prompt_messages = _snapshot_llm_messages(msgs)
                        yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': agent_name, 'model': getattr(llm_client, 'model', ''), 'turn': iteration + 1, 'system_prompt': sys_prompt, 'prompt_messages': _format_json_block(llm_prompt_messages)}, ensure_ascii=False)}\n\n"
                        async for event in llm_client.chat_stream(msgs, tool_schemas or None):
                            if event["type"] == "content":
                                step_content += event["delta"]
                                _llm_content += event["delta"]
                                yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta'], 'agent': agent_name})}\n\n"
                            elif event["type"] == "done":
                                tc = event.get("tool_calls")
                                _llm_full = event.get("full_content", _llm_content)
                                if tc:
                                    tool_calls_found = True
                                    msgs.append({"role": "assistant", "content": _llm_full, "tool_calls": tc})
                                    for t in tc:
                                        tname = t["function"]["name"]
                                        yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tname, 'agent': agent_name})}\n\n"
                                        _tool_start = _time.time()
                                        try:
                                            targs = json.loads(t["function"]["arguments"])
                                            tres = await tool_registry.execute(tname, **targs)
                                            tres_str = str(tres)[:2000] if tres else "(no output)"
                                        except Exception as te:
                                            tres_str = f"Error: {te}"
                                        _tool_ms = int((_time.time() - _tool_start) * 1000)
                                        yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tname, 'result': tres_str[:500], 'agent': agent_name})}\n\n"
                                        msgs.append({"role": "tool", "tool_call_id": t["id"], "content": tres_str, "name": tname})
                                        # 工具卡片事件
                                        _args_str = t["function"].get("arguments", "{}")
                                        yield await _sse_card("tool_call", {
                                            "agent": agent_name, "tool": tname,
                                            "arguments": _args_str,
                                            "success": True, "result": tres_str[:1500],
                                            "duration_ms": _tool_ms,
                                        })
                                        _llm_tool_calls.append({"name": tname, "args_preview": _args_str[:120]})
                                # LLM 调用卡片事件
                                _llm_ms = int((_time.time() - _llm_start) * 1000)
                                yield await _sse_card(
                                    "llm_call",
                                    _build_llm_card_payload(
                                        agent_name=agent_name,
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
                                    ),
                                )
                            elif event["type"] == "error":
                                yield f"data: {_json.dumps({'type': 'error', 'error': event['error'], 'agent': agent_name})}\n\n"
                                break
                        if not tool_calls_found:
                            # 无工具调用的最终 LLM 回复卡片
                            if _llm_content:
                                _llm_ms = int((_time.time() - _llm_start) * 1000)
                                yield await _sse_card(
                                    "llm_call",
                                    _build_llm_card_payload(
                                        agent_name=agent_name,
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
                                    ),
                                )
                            break

                    # 保存
                    saved = None
                    if step_content:
                        saved = await chatroom_manager.send_message(
                            chatroom_id=chatroom_id, agent_id=agent.id,
                            content=step_content, message_type="text", agent_name=agent.name
                        )
                        await websocket_manager.broadcast_to_room({
                            "type": "message", "id": saved.id, "content": step_content,
                            "agent_name": agent.name, "message_type": "text"
                        }, chatroom_id)
                        if len(step_content) > 30:
                            asyncio.create_task(_extract_memories(agent.id, agent.name, message.content, step_content))
                        previous_context = step_content

                    yield f"data: {_json.dumps({'type': 'collab_step_done', 'agent': agent_name, 'message_id': saved.id if step_content else None})}\n\n"

                yield f"data: {_json.dumps({'type': 'done', 'agent_name': ', '.join(mentioned_names), 'collab': True})}\n\n"
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
                target_agent = next((a for a in agents if a.name == target_agent_name), None)
                # @mentioned agent 不在项目中 → 从全局查找并自动分配
                if not target_agent:
                    global_agent = db.query(Agent).filter(Agent.name == target_agent_name).first()
                    if global_agent:
                        logger.info(f"[Agent] Auto-assigning '{target_agent_name}' to project '{project.name}'")
                        assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                        db.add(assignment)
                        db.commit()
                        target_agent = global_agent
                        agents.append(global_agent)
            if not target_agent:
                target_agent = next((a for a in agents if a.name == "assistant"), agents[0] if agents else None)
            if not target_agent:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No agent available'})}\n\n"
                return

            # 注册项目中所有 agent 为协作者
            from agents.collaboration import collaboration_coordinator, AgentCollaborator
            for agent in agents:
                if agent.id not in collaboration_coordinator.collaborators:
                    collaborator = AgentCollaborator(
                        agent_id=agent.id,
                        agent_name=agent.name,
                        chatroom_id=chatroom_id
                    )
                    collaboration_coordinator.register_collaborator(collaborator)

            # 5. 构建该 Agent 的消息上下文
            llm_client = get_llm_client_for_agent(target_agent.name)
            messages = []

            system_prompt = target_agent.system_prompt or f"You are {target_agent.name}, a {target_agent.role}."
            system_prompt += _build_runtime_context_block(db, chatroom, project)

            available_tools = tool_registry.list_tools()
            if available_tools:
                system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"

            # 注入项目中所有 Agent 列表
            team_members = [f"- **{a.name}** (role: {a.role})" for a in agents]
            system_prompt += f"\n\nTeam members in this project:\n" + "\n".join(team_members)
            system_prompt += f"\n\nYou are **{target_agent.name}** (role: {target_agent.role}). You can use tools to communicate with or delegate tasks to your teammates."

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
                    source_name = source_agent.name if source_agent else "unknown"
                    system_prompt += f"\n- [{ts}] [{source_name}] {mem.content[:200]}"

            messages.append({"role": "system", "content": system_prompt})

            recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=10)
            for msg in recent_messages[-6:]:
                agent_name = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
                if msg.message_type == "user" or not agent_name:
                    messages.append({"role": "user", "content": msg.content})
                else:
                    messages.append({"role": "assistant", "content": msg.content})

            messages.append({"role": "user", "content": message.content})

            tool_schemas = tool_registry.get_schemas()

            # 6. 流式 LLM 循环
            import time as _time2
            max_tool_iterations = 5
            iteration = 0
            final_content = ""

            while iteration < max_tool_iterations:
                iteration += 1
                tool_calls_found = False
                _llm_start = _time2.time()
                _llm_content = ""
                _llm_tool_calls = []
                llm_prompt_messages = _snapshot_llm_messages(messages)
                yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': target_agent.name, 'model': getattr(llm_client, 'model', ''), 'turn': iteration, 'system_prompt': system_prompt, 'prompt_messages': _format_json_block(llm_prompt_messages)}, ensure_ascii=False)}\n\n"

                async for event in llm_client.chat_stream(
                    messages, tool_schemas if tool_schemas else None
                ):
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
                                        agent_name=target_agent.name,
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
                                    ),
                                )
                            break

                        tool_calls_found = True

                        # 将 assistant 消息加入上下文
                        assistant_msg = {
                            "role": "assistant",
                            "content": full_content,
                            "tool_calls": tool_calls
                        }
                        messages.append(assistant_msg)

                        # 执行工具
                        for tc in tool_calls:
                            tool_name = tc["function"]["name"]
                            tool_args_str = tc["function"]["arguments"]
                            tool_call_id = tc["id"]

                            yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tool_name, 'args': tool_args_str})}\n\n"
                            _tool_start = _time2.time()

                            try:
                                tool_args = json.loads(tool_args_str)
                                tool_result = await tool_registry.execute(tool_name, **tool_args)
                                result_str = str(tool_result) if tool_result is not None else "(no output)"
                            except Exception as te:
                                result_str = f"Error: {str(te)}"

                            # 截断过长的工具结果
                            if len(result_str) > 2000:
                                result_str = result_str[:2000] + "\n...(truncated)"
                            _tool_ms = int((_time2.time() - _tool_start) * 1000)

                            yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result_str[:500]})}\n\n"

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": result_str,
                                "name": tool_name
                            })

                            # 工具卡片事件
                            yield await _sse_card("tool_call", {
                                "agent": target_agent.name,
                                "tool": tool_name,
                                "arguments": tool_args_str,
                                "success": True,
                                "result": result_str[:1500],
                                "duration_ms": _tool_ms,
                            })
                            _llm_tool_calls.append({"name": tool_name, "args_preview": tool_args_str[:120]})

                        # LLM 调用卡片事件（含工具调用）
                        _llm_ms = int((_time2.time() - _llm_start) * 1000)
                        yield await _sse_card(
                            "llm_call",
                            _build_llm_card_payload(
                                agent_name=target_agent.name,
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
                            ),
                        )

                    elif event["type"] == "error":
                        yield f"data: {_json.dumps({'type': 'error', 'error': event['error']})}\n\n"
                        return

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
                agent_name=target_agent.name
            )

            # WebSocket 广播
            from routes.websocket import websocket_manager
            await websocket_manager.broadcast_to_room({
                "type": "message",
                "id": agent_response.id,
                "content": final_content,
                "agent_name": target_agent.name,
                "message_type": "text"
            }, chatroom_id)

            yield f"data: {_json.dumps({'type': 'done', 'agent_name': target_agent.name, 'message_id': agent_response.id})}\n\n"

            # 异步提取记忆
            if len(final_content) > 30:
                asyncio.create_task(_extract_memories(
                    agent_id=target_agent.id,
                    agent_name=target_agent.name,
                    user_message=message.content,
                    agent_response=final_content
                ))

        except Exception as e:
            logger.error(f"[SSE] Stream error: {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
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

            agents_data = agents_config.get("agents", {})
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
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        agents = data.get("agents", {})
        if agent_name not in agents:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        # 更新 Agent 的完整配置字段
        for field_name in ("provider", "default_model", "role", "soul", "tools", "skills"):
            if field_name in config:
                agents[agent_name][field_name] = config[field_name]

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
async def test_agent_config(agent_name: str = "assistant"):
    """
    测试指定 Agent 的 LLM 连接

    从 agents.json 读取该 Agent 的 provider 配置并发送测试请求。
    """
    from llm.client import _load_agent_provider
    from openai import AsyncOpenAI

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
    target_agent = db.query(Agent).filter(Agent.name == target_agent_name).first()
    if not target_agent:
        raise HTTPException(status_code=404, detail=f"Agent '{target_agent_name}' not found")
    
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
        "assigned_to": target_agent_name
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
