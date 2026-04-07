# -*- coding: utf-8 -*-
"""
API 路由 - 主要端点
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import json

from models.database import get_db, Agent, Project, Chatroom, AgentAssignment, Message, Base
from agents.registry import get_registry, AgentConfig
from agents.core import Agent as AgentInstance
from chatrooms.manager import chatroom_manager
from llm.client import get_llm_client


router = APIRouter()


# ==================== Agent 响应处理 ====================

async def trigger_agent_response(chatroom_id: int, user_message: str):
    """触发 Agent 处理消息并生成响应（统一执行路径 + 工具结果回传 LLM）"""
    from models.database import get_db
    from tools import tool_registry
    import json
    
    db = next(get_db())
    try:
        print(f"[DEBUG] trigger_agent_response called: chatroom_id={chatroom_id}, message={user_message[:50]}...")
        
        # 1. 获取聊天室关联的项目
        chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
        if not chatroom or not chatroom.project_id:
            print(f"[DEBUG] No chatroom or project_id found")
            return
        
        project = db.query(Project).filter(Project.id == chatroom.project_id).first()
        if not project:
            print(f"[DEBUG] No project found")
            return
        
        print(f"[DEBUG] Found project: {project.name}")
        
        # 2. 解析 @ 提及，确定目标 Agent
        target_agent_name = None
        if '@' in user_message:
            import re
            mentions = re.findall(r'@(\w+)', user_message)
            if mentions:
                target_agent_name = mentions[0]
        
        print(f"[DEBUG] Target agent name: {target_agent_name}")
        
        # 3. 获取项目关联的 Agents
        assignments = db.query(AgentAssignment).filter(
            AgentAssignment.project_id == project.id
        ).all()
        agent_ids = [a.agent_id for a in assignments]
        agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
        
        print(f"[DEBUG] Found {len(agents)} agents for project")
        
        # 4. 确定响应的 Agent
        target_agent = None
        if target_agent_name:
            target_agent = next((a for a in agents if a.name == target_agent_name), None)
        
        if not target_agent:
            target_agent = next((a for a in agents if a.name == "assistant"), agents[0] if agents else None)
        
        if not target_agent:
            print(f"[DEBUG] No target agent found")
            return
        
        print(f"[DEBUG] Selected agent: {target_agent.name} (role: {target_agent.role})")
        
        # 5. 获取 LLM 客户端
        llm_client = get_llm_client()
        print(f"[DEBUG] LLM client obtained: {llm_client.client.base_url}")
        
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
        
        # 注入长期记忆到上下文（2.3 记忆系统接入）
        from models.database import Memory
        
        recent_memories = (
            db.query(Memory)
            .filter(Memory.agent_id.in_([target_agent.id, target_agent.id]))  # agent-specific memories
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(10)
            .all()
        )
        if recent_memories:
            system_prompt += "\n\nStored memories (context for your responses):"
            for mem in recent_memories:
                ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
                system_prompt += f"\n- [{ts}] [importance={mem.importance}] {mem.content[:200]}"
        
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
        
        print(f"[DEBUG] Context messages: {len(messages)} messages")
        
        # 7. 获取工具 schemas
        tool_schemas = tool_registry.get_schemas()
        
        # 8. 主循环：LLM → 执行工具 → 结果回传 LLM → 直到没有 tool_calls
        print(f"[LLM] Calling LLM for agent: {target_agent.name} with {len(tool_schemas)} tools available")
        
        max_tool_iterations = 5
        iteration = 0
        
        while iteration < max_tool_iterations:
            iteration += 1
            print(f"[LLM] Loop iteration {iteration}")
            
            llm_response = await llm_client.chat_with_tools(messages, tool_schemas if tool_schemas else None)
            
            response_content = llm_response.get("content", "")
            tool_calls = llm_response.get("tool_calls")
            
            print(f"[LLM] Response received: {response_content[:100] if response_content else 'None'}...")
            print(f"[LLM] Tool calls: {tool_calls}")
            
            if not tool_calls:
                # 没有更多工具调用，结束循环
                print(f"[LLM] No more tool calls, loop done after {iteration} iterations")
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
                
                print(f"[Tool] Executing: {tool_name} with args: {tool_args}")
                
                try:
                    tool_result = await tool_registry.execute(tool_name, **tool_args)
                    result_str = str(tool_result) if tool_result is not None else "(no output)"
                    print(f"[Tool] Result: {result_str[:150]}...")
                except Exception as te:
                    result_str = f"Error executing {tool_name}: {str(te)}"
                    print(f"[Tool] Error: {te}")
                
                # 以 tool role 消息将结果回传 LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_str,
                    "name": tool_name
                })
        
        if not response_content:
            print(f"[ERROR] LLM returned empty response after all tool iterations")
            return
        
        # 9. 发送 Agent 响应
        agent_response = await chatroom_manager.send_message(
            chatroom_id=chatroom_id,
            agent_id=target_agent.id,
            content=response_content,
            message_type="text",
            agent_name=target_agent.name
        )
        
        print(f"[DEBUG] Agent response saved: id={agent_response.id}")
        
        # 10. 通过 WebSocket 广播
        from routes.websocket import websocket_manager
        await websocket_manager.broadcast_to_room({
            "type": "message",
            "id": agent_response.id,
            "content": response_content,
            "agent_name": target_agent.name,
            "message_type": "text"
        }, chatroom_id)
        
        print(f"[Agent] {target_agent.name} responded to message successfully")
        
    except Exception as e:
        print(f"[Error] Agent response failed: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


# ==================== 数据模型 ====================

class AgentInfo(BaseModel):
    id: int
    name: str
    role: str
    is_active: bool


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    agent_names: List[str] = ["assistant"]


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
    print(f"[API] send_message called: chatroom_id={chatroom_id}, content={message.content[:50]}...")
    
    # 发送用户消息
    response_msg = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,  # None 表示用户
        content=message.content,
        message_type="text"
    )
    
    print(f"[API] User message saved: id={response_msg.id}")
    
    # 触发 Agent 响应（同步等待，方便调试）
    try:
        await trigger_agent_response(chatroom_id, message.content)
        print(f"[API] Agent response completed")
    except Exception as e:
        print(f"[API] Agent response error: {e}")
        import traceback
        traceback.print_exc()
    
    return MessageResponse(
        id=response_msg.id,
        content=response_msg.content,
        agent_name=response_msg.agent_name,
        message_type=response_msg.message_type
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

# ==================== 配置相关 ====================

@router.get("/config")
async def get_config():
    """获取前端配置信息（全局 + 各Agent实际生效配置）"""
    import os
    from pathlib import Path
    
    config = {
        "llm": {
            "apiKey": os.getenv("LLM_API_KEY", ""),
            "baseUrl": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            "model": os.getenv("LLM_MODEL", "gpt-4")
        },
        "server": {
            "host": os.getenv("HOST", "0.0.0.0"),
            "port": int(os.getenv("PORT", "8000"))
        },
        "features": {
            "llm_enabled": True,
            "websocket_enabled": True,
            "tools_enabled": True,
            "memory_enabled": True
        }
    }
    
    # 加载 agents.json 中各 Agent 的实际 provider 配置
    agents_config_file = Path("configs/agents.json")
    if agents_config_file.exists():
        try:
            with open(agents_config_file, 'r', encoding='utf-8') as f:
                agents_config = json.load(f)
                agents_data = agents_config.get("agents", {})
                config["agents"] = agents_data
                
                # 提取各 Agent 实际生效的 LLM 配置（方便前端对比显示）
                agent_llm_configs = {}
                for agent_name, agent_data in agents_data.items():
                    provider = agent_data.get("provider", {})
                    default_model = agent_data.get("default_model", "")
                    models = provider.get("models", [])
                    effective_model = default_model or (models[0]["id"] if models else "")
                    agent_llm_configs[agent_name] = {
                        "baseUrl": provider.get("baseUrl", ""),
                        "model": effective_model,
                        "hasApiKey": bool(provider.get("apiKey", "")),
                        "models": [m["id"] for m in models]
                    }
                config["agent_llm_configs"] = agent_llm_configs
        except:
            pass
    
    return config


class LLMConfigModel(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 2000


@router.post("/config")
async def update_config(config: LLMConfigModel):
    """更新LLM配置"""
    import os
    from pathlib import Path
    
    # 写入.env文件
    env_file = Path(__file__).parent.parent / ".env"
    
    env_content = f"""# LLM Configuration
LLM_API_KEY={config.api_key}
LLM_BASE_URL={config.base_url}
LLM_MODEL={config.model}

# Server Configuration
HOST=0.0.0.0
PORT=8000

# Database
DATABASE_URL=data/catown.db

# Logging
LOG_LEVEL=INFO
"""
    
    env_file.write_text(env_content)
    
    # 更新环境变量
    os.environ["LLM_API_KEY"] = config.api_key
    os.environ["LLM_BASE_URL"] = config.base_url
    os.environ["LLM_MODEL"] = config.model
    
    # 重新初始化LLM客户端
    from llm.client import set_llm_client, LLMClient
    from config import settings
    # 更新 settings 对象中的值（运行时生效）
    settings.LLM_API_KEY = config.api_key
    settings.LLM_BASE_URL = config.base_url
    settings.LLM_MODEL = config.model
    # 创建新的 LLM 客户端并替换
    set_llm_client(LLMClient())
    
    return {"message": "Configuration updated successfully"}


@router.post("/config/test")
async def test_config(config: LLMConfigModel):
    """测试LLM配置连接"""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url
        )
        
        # 发送一个简单的测试请求
        response = await client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=5
        )
        
        return {"status": "success", "message": "Connection successful"}
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
