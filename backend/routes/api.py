# -*- coding: utf-8 -*-
"""
API 路由 - 主要端点
"""
import logging
import re
import json
import os
import asyncio

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
        if not chatroom or not chatroom.project_id:
            logger.debug(f"[ No chatroom or project_id found")
            return
        
        project = db.query(Project).filter(Project.id == chatroom.project_id).first()
        if not project:
            logger.debug(f"[ No project found")
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
        system_prompt += f"\n\nCurrent project: {project.name}"
        if project.description:
            system_prompt += f"\nProject description: {project.description}"
        
        available_tools = tool_registry.list_tools()
        if available_tools:
            system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"
            system_prompt += "\nWhen you need to use a tool, respond with a tool call and the system will execute it."
        
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
        
        # 获取最近的对话历史（修复：使用 agent 关系而非不存在的 agent_name 字段）
        recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=10)
        for msg in recent_messages[-6:]:
            agent_name = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
            if msg.message_type == "user" or not agent_name:
                messages.append({"role": "user", "content": msg.content})
            else:
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

    # 注册协作者
    for a in agents:
        if a.id not in collaboration_coordinator.collaborators:
            collaboration_coordinator.register_collaborator(
                AgentCollaborator(agent_id=a.id, agent_name=a.name, chatroom_id=chatroom_id)
            )

    # 构建 system prompt
    system_prompt = agent.system_prompt or f"You are {agent.name}, a {agent.role}."
    system_prompt += f"\n\nCurrent project: {project.name}"
    if project.description:
        system_prompt += f"\nProject description: {project.description}"

    available_tools = tool_registry.list_tools()
    if available_tools:
        system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"

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
            # 自动分配
            global_agent = db.query(Agent).filter(Agent.name == name).first()
            if global_agent:
                assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                db.add(assignment)
                db.commit()
                agents.append(global_agent)
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


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    agent_names: List[str] = ["analyst"]


class ProjectInfo(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    chatroom_id: Optional[int]
    agents: List[AgentInfo]


class MessageRequest(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: int
    content: str
    agent_name: Optional[str]
    message_type: str


# ==================== Agent 相关 ====================

@router.get("/agents", response_model=List[AgentInfo])
async def list_agents(db: Session = Depends(get_db)):
    """获取所有可用 Agent 列表"""
    agents = db.query(Agent).filter(Agent.is_active == True).all()
    return [
        AgentInfo(
            id=agent.id,
            name=agent.name,
            role=agent.role,
            is_active=agent.is_active
        )
        for agent in agents
    ]


@router.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """获取 Agent 详情"""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return AgentInfo(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        is_active=agent.is_active
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
    projects = db.query(Project).all()
    
    result = []
    for project in projects:
        # 获取项目分配的 Agent
        assignments = db.query(AgentAssignment).filter(AgentAssignment.project_id == project.id).all()
        agent_ids = [a.agent_id for a in assignments]
        
        agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
        
        result.append(ProjectInfo(
            id=project.id,
            name=project.name,
            description=project.description,
            status=project.status,
            chatroom_id=project.chatroom.id if project.chatroom else None,
            agents=[
                AgentInfo(id=a.id, name=a.name, role=a.role, is_active=a.is_active)
                for a in agents
            ]
        ))
    
    return result


@router.post("/projects", response_model=ProjectInfo)
async def create_project(project_create: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    # 验证 Agent 名称
    registry = get_registry()
    valid_agent_names = registry.list_agents()
    
    for agent_name in project_create.agent_names:
        if agent_name not in valid_agent_names:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid agent name: {agent_name}. Valid agents: {valid_agent_names}"
            )
    
    # 创建项目
    project = Project(
        name=project_create.name,
        description=project_create.description
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    
    # 创建聊天室
    chatroom_id = await chatroom_manager.create_chatroom(project.id, project_create.name)
    
    # 分配 Agent
    for agent_name in project_create.agent_names:
        agent = db.query(Agent).filter(Agent.name == agent_name).first()
        if agent:
            assignment = AgentAssignment(
                project_id=project.id,
                agent_id=agent.id
            )
            db.add(assignment)
            
            # 添加到聊天室
            chatroom = chatroom_manager.get_chatroom(chatroom_id)
            if chatroom:
                chatroom.add_agent(agent.id)
    
    db.commit()
    
    # 返回项目信息
    assignments = db.query(AgentAssignment).filter(AgentAssignment.project_id == project.id).all()
    agent_ids = [a.agent_id for a in assignments]
    agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
    
    return ProjectInfo(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        chatroom_id=chatroom_id,
        agents=[
            AgentInfo(id=a.id, name=a.name, role=a.role, is_active=a.is_active)
            for a in agents
        ]
    )


@router.get("/projects/{project_id}", response_model=ProjectInfo)
async def get_project(project_id: int, db: Session = Depends(get_db)):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目分配的 Agent
    assignments = db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).all()
    agent_ids = [a.agent_id for a in assignments]
    agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
    
    return ProjectInfo(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        chatroom_id=project.chatroom.id if project.chatroom else None,
        agents=[
            AgentInfo(id=a.id, name=a.name, role=a.role, is_active=a.is_active)
            for a in agents
        ]
    )


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
    
    # 删除项目
    db.delete(project)
    db.commit()
    
    return {"message": "Project deleted successfully"}


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
            message_type=msg.message_type
        )
        for msg in messages
    ]


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
        message_type=response_msg.message_type
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

    async def event_generator():
        from models.database import get_db as _get_db
        from tools import tool_registry
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
        from routes.websocket import websocket_manager

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
            if not chatroom or not chatroom.project_id:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No chatroom or project'})}\n\n"
                return

            project = db.query(Project).filter(Project.id == chatroom.project_id).first()
            if not project:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No project found'})}\n\n"
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
                    sys_prompt += f"\n\nCurrent project: {project.name}"
                    if project.description:
                        sys_prompt += f"\nProject description: {project.description}"
                    if tool_registry.list_tools():
                        sys_prompt += f"\n\nTools: {', '.join(tool_registry.list_tools())}"
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
                    step_content = ""
                    tool_schemas = tool_registry.get_schemas()
                    for iteration in range(5):
                        tool_calls_found = False
                        async for event in llm_client.chat_stream(msgs, tool_schemas or None):
                            if event["type"] == "content":
                                step_content += event["delta"]
                                yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta'], 'agent': agent_name})}\n\n"
                            elif event["type"] == "done":
                                tc = event.get("tool_calls")
                                if tc:
                                    tool_calls_found = True
                                    msgs.append({"role": "assistant", "content": event.get("full_content", ""), "tool_calls": tc})
                                    for t in tc:
                                        tname = t["function"]["name"]
                                        yield f"data: {_json.dumps({'type': 'tool_start', 'tool': tname, 'agent': agent_name})}\n\n"
                                        try:
                                            targs = json.loads(t["function"]["arguments"])
                                            tres = await tool_registry.execute(tname, **targs)
                                            tres_str = str(tres)[:2000] if tres else "(no output)"
                                        except Exception as te:
                                            tres_str = f"Error: {te}"
                                        yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tname, 'result': tres_str[:500], 'agent': agent_name})}\n\n"
                                        msgs.append({"role": "tool", "tool_call_id": t["id"], "content": tres_str, "name": tname})
                            elif event["type"] == "error":
                                yield f"data: {_json.dumps({'type': 'error', 'error': event['error'], 'agent': agent_name})}\n\n"
                                break
                        if not tool_calls_found:
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
            system_prompt += f"\n\nCurrent project: {project.name}"
            if project.description:
                system_prompt += f"\nProject description: {project.description}"

            available_tools = tool_registry.list_tools()
            if available_tools:
                system_prompt += f"\n\nYou have access to the following tools: {', '.join(available_tools)}"

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
            max_tool_iterations = 5
            iteration = 0
            final_content = ""

            yield f"data: {_json.dumps({'type': 'agent_start', 'agent_name': target_agent.name})}\n\n"

            while iteration < max_tool_iterations:
                iteration += 1
                tool_calls_found = False

                async for event in llm_client.chat_stream(
                    messages, tool_schemas if tool_schemas else None
                ):
                    if event["type"] == "content":
                        final_content += event["delta"]
                        yield f"data: {_json.dumps({'type': 'content', 'delta': event['delta']})}\n\n"

                    elif event["type"] == "done":
                        tool_calls = event.get("tool_calls")
                        full_content = event.get("full_content", "")

                        if not tool_calls:
                            # 无工具调用，结束
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

                            try:
                                tool_args = json.loads(tool_args_str)
                                tool_result = await tool_registry.execute(tool_name, **tool_args)
                                result_str = str(tool_result) if tool_result is not None else "(no output)"
                            except Exception as te:
                                result_str = f"Error: {str(te)}"

                            # 截断过长的工具结果
                            if len(result_str) > 2000:
                                result_str = result_str[:2000] + "\n...(truncated)"

                            yield f"data: {_json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result_str[:500]})}\n\n"

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": result_str,
                                "name": tool_name
                            })

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
    更新指定 Agent 的 LLM 配置

    请求体：
    {
        "provider": {
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "models": [{"id": "gpt-4", ...}]
        },
        "default_model": "gpt-4"
    }

    设置 provider 为空对象 {} 可清除 Agent 级配置，回退到全局。
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

        # 更新 Agent 的 provider 和 default_model
        if "provider" in config:
            agents[agent_name]["provider"] = config["provider"]
        if "default_model" in config:
            agents[agent_name]["default_model"] = config["default_model"]

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
