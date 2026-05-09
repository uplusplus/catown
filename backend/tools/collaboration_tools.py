# -*- coding: utf-8 -*-
"""
Collaboration Tools

Tools that enable agents to collaborate with each other:
- delegate_task: Delegate a task to another agent
- broadcast_message: Send a message to all agents
- check_task_status: Check status of a delegated task
- list_collaborators: List available collaborators
"""
from .base import BaseTool
from typing import Optional, Dict, Any, List
import json
import asyncio
import logging
from datetime import datetime

from agents.identity import agent_name_of, normalize_agent_type
from chatrooms.manager import chatroom_manager
from services.chat_publish import publish_saved_chat_message
from services.stream_runtime_persistence import store_runtime_card

logger = logging.getLogger("catown.collaboration_tools")


def _mark_delegated_task_run_interrupted(
    *,
    client_turn_id: str,
    agent_name: str,
    summary: str,
) -> None:
    try:
        from models.database import SessionLocal, TaskRun
        from services.run_ledger import append_task_event, complete_task_run
    except Exception:
        return

    db = SessionLocal()
    try:
        task_run = (
            db.query(TaskRun)
            .filter(TaskRun.client_turn_id == client_turn_id)
            .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            .first()
        )
        if task_run is None or (task_run.status or "").strip().lower() != "running":
            return
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            agent_name=agent_name,
            summary=summary,
            payload={
                "reason": "delegated_task_interrupted",
                "client_turn_id": client_turn_id,
            },
        )
        complete_task_run(db, task_run, status="failed", summary=summary)
    except Exception:
        db.rollback()
    finally:
        db.close()


class DelegateTaskTool(BaseTool):
    """Tool for delegating tasks to other agents"""
    
    name = "delegate_task"
    description = (
        "Delegate a tracked async task to another agent. "
        "Use this for real work that may take time, needs progress tracking, approvals, runtime cards, or later status checks."
    )
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(
        self,
        target_agent_name: str,
        task_title: str,
        task_description: str,
        context: str = "",
        **kwargs
    ) -> str:
        """
        Delegate a task to another agent
        
        Args:
            target_agent_name: Name of the agent to delegate to (e.g., 'coder', 'researcher', 'reviewer')
            task_title: Short title for the task
            task_description: Detailed description of the task
            context: Additional context or data needed for the task
            
        Returns:
            Delegation status and task ID
        """
        # Get current agent info from kwargs
        current_agent_id = kwargs.get('agent_id', 0)
        current_agent_name = kwargs.get('agent_name', 'unknown')
        chatroom_id = kwargs.get('chatroom_id', 0)
        target_agent_type = normalize_agent_type(target_agent_name)
        
        # Find target agent
        target_agent_id = None
        if self.coordinator:
            for aid, collab in self.coordinator.collaborators.items():
                if collab.agent_name == target_agent_type:
                    target_agent_id = aid
                    break

        if not target_agent_id:
            # 从全局数据库查找并自动注册为协作者
            try:
                from models.database import get_db, Agent as DBAgent
                from agents.collaboration import AgentCollaborator
                db = next(get_db())
                try:
                    db_agent = db.query(DBAgent).filter(
                        DBAgent.agent_type == target_agent_type,
                        DBAgent.is_active == True
                    ).first()
                    if db_agent and self.coordinator:
                        collaborator = AgentCollaborator(
                            agent_id=db_agent.id,
                            agent_name=db_agent.agent_type or db_agent.name,
                            chatroom_id=chatroom_id
                        )
                        self.coordinator.register_collaborator(collaborator)
                        target_agent_id = db_agent.id
                finally:
                    db.close()
            except Exception:
                pass

        if not target_agent_id:
            available = []
            if self.coordinator:
                available = [c.agent_name for c in self.coordinator.collaborators.values()]
            return f"[Delegate Task] Error: Agent '{target_agent_type}' not found. Available agents: {available}"
        
        # Create task
        from agents.collaboration import CollaborationTask, TaskStatus, uuid, datetime
        task = CollaborationTask(
            id=str(uuid.uuid4()),
            title=task_title,
            description=task_description,
            status=TaskStatus.DELEGATED,
            created_by_agent_id=current_agent_id,
            assigned_to_agent_id=target_agent_id,
            chatroom_id=chatroom_id,
            metadata={"context": context, "delegator": current_agent_name}
        )
        
        # Register task
        if self.coordinator:
            self.coordinator.task_registry[task.id] = task
            
            # Notify target agent
            from agents.collaboration import CollaborationMessage, CollaborationMessageType
            message = CollaborationMessage(
                id=str(uuid.uuid4()),
                message_type=CollaborationMessageType.TASK_REQUEST,
                from_agent_id=current_agent_id,
                from_agent_name=current_agent_name,
                to_agent_id=target_agent_id,
                to_agent_name=target_agent_type,
                chatroom_id=chatroom_id,
                content=f"**Task: {task_title}**\n\n{task_description}\n\nContext: {context}",
                task_id=task.id,
                metadata={"task": task.dict()}
            )
            
            # Route message
            await self.coordinator.route_message(message)

            delegated_turn_id = f"delegate-{task.id}"
            task_metadata = {
                "task_id": task.id,
                "task_title": task_title,
                "task_description": task_description,
                "context": context,
                "delegator": current_agent_name,
                "target_agent_name": target_agent_type,
            }
            await self._publish_delegate_task_card(
                chatroom_id=chatroom_id,
                from_agent=current_agent_name,
                to_agent=target_agent_type,
                content=self._delegate_task_card_content(task_title, task_description, context, task.id),
                client_turn_id=delegated_turn_id,
            )
            asyncio.create_task(
                self._run_delegated_task_in_chat(
                    task=task,
                    target_agent_type=target_agent_type,
                    task_description=task_description,
                    context=context,
                    current_agent_name=current_agent_name,
                    client_turn_id=delegated_turn_id,
                    task_metadata=task_metadata,
                )
            )
        
        return f"[Delegate Task] Task '{task_title}' delegated to {target_agent_type}. Task ID: {task.id}"

    async def _publish_delegate_task_card(
        self,
        *,
        chatroom_id: int,
        from_agent: str,
        to_agent: str,
        content: str,
        client_turn_id: str,
    ) -> None:
        try:
            await store_runtime_card(
                chatroom_id,
                {
                    "type": "agent_message",
                    "source": "chatroom",
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "content": content,
                    "client_turn_id": client_turn_id,
                },
            )
        except Exception:
            # Delegated execution should still proceed even if the trace card fails.
            pass

    async def _run_delegated_task_in_chat(
        self,
        *,
        task,
        target_agent_type: str,
        task_description: str,
        context: str,
        current_agent_name: str,
        client_turn_id: str,
        task_metadata: Dict[str, Any],
    ) -> None:
        from agents.collaboration import TaskStatus
        from models.database import SessionLocal, Message
        from routes.api import trigger_agent_response

        db = SessionLocal()
        try:
            task.status = TaskStatus.IN_PROGRESS
            self.coordinator.task_registry[task.id] = task

            instruction = (
                f"@{target_agent_type} [Delegated task from {current_agent_name}] {task_description.strip()}\n\n"
                f"Delegation context:\n{context.strip() or '(none)'}"
            )
            delegated_msg = await chatroom_manager.send_message(
                chatroom_id=task.chatroom_id,
                agent_id=task.created_by_agent_id if getattr(task, "created_by_agent_id", 0) and getattr(task, "created_by_agent_id", 0) > 0 else None,
                content=instruction,
                message_type="text",
                metadata={
                    "client_turn_id": client_turn_id,
                    "delegated_task": task_metadata,
                },
                agent_name=current_agent_name,
            )
            await publish_saved_chat_message(
                db,
                task.chatroom_id,
                message_id=delegated_msg.id,
                content=delegated_msg.content,
                agent_name=delegated_msg.agent_name,
                message_type=delegated_msg.message_type,
                created_at=delegated_msg.created_at,
                metadata={
                    "client_turn_id": client_turn_id,
                    "delegated_task": task_metadata,
                },
            )

            trigger_result = await trigger_agent_response(
                task.chatroom_id,
                instruction,
                client_turn_id=client_turn_id,
                extra_context=f"Delegated by {current_agent_name}. {context}".strip(),
            )
            completed = True
            awaiting_tool_approval = False
            if isinstance(trigger_result, dict):
                completed = bool(trigger_result.get("completed"))
                awaiting_tool_approval = bool(trigger_result.get("awaiting_tool_approval"))
            if not completed:
                task.status = TaskStatus.IN_PROGRESS
                if awaiting_tool_approval:
                    task.result = "Waiting for approval."
                self.coordinator.task_registry[task.id] = task
                return

            task.status = TaskStatus.COMPLETED
            delegated_result = None
            try:
                final_messages = (
                    db.query(Message)
                    .filter(Message.chatroom_id == task.chatroom_id)
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .all()
                )
                for message in final_messages:
                    try:
                        metadata = json.loads(getattr(message, "metadata_json", "") or "{}")
                    except json.JSONDecodeError:
                        metadata = {}
                    if (metadata.get("client_turn_id") or "") != client_turn_id:
                        continue
                    if getattr(message, "message_type", "") == "text" and getattr(message, "agent_id", None):
                        delegated_result = (getattr(message, "content", "") or "").strip()
                        if delegated_result:
                            break
            except Exception:
                delegated_result = None
            task.result = delegated_result or "Completed in chat window. See delegated turn output."
        except asyncio.CancelledError:
            interruption_summary = "Delegated execution interrupted during server reload or shutdown."
            logger.warning(
                "Delegated task cancelled before finalization: task_id=%s target=%s client_turn_id=%s",
                task.id,
                target_agent_type,
                client_turn_id,
            )
            _mark_delegated_task_run_interrupted(
                client_turn_id=client_turn_id,
                agent_name=target_agent_type,
                summary=interruption_summary,
            )
            task.status = TaskStatus.FAILED
            task.result = interruption_summary
            task.completed_at = datetime.now()
        except Exception as exc:
            logger.exception("Delegated task failed: task_id=%s target=%s error=%s", task.id, target_agent_type, exc)
            task.status = TaskStatus.FAILED
            task.result = f"Delegated execution failed: {exc}"
        finally:
            task.completed_at = getattr(task, "completed_at", None) if task.status != "completed" else task.completed_at
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED} and task.completed_at is None:
                task.completed_at = datetime.now()
            self.coordinator.task_registry[task.id] = task
            db.close()

    @staticmethod
    def _delegate_task_card_content(task_title: str, task_description: str, context: str, task_id: str) -> str:
        parts = [f"**Task: {task_title}**", task_description.strip()]
        if context.strip():
            parts.append(f"Context: {context.strip()}")
        parts.append(f"Task ID: {task_id}")
        return "\n\n".join(parts)
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "Type of the agent to delegate to (analyst, architect, developer, tester, release, valet)"
                },
                "task_title": {
                    "type": "string",
                    "description": "Short title for the delegated task"
                },
                "task_description": {
                    "type": "string",
                    "description": "Detailed description of what needs to be done"
                },
                "context": {
                    "type": "string",
                    "description": "Additional context or data for the task",
                    "default": ""
                }
            },
            "required": ["target_agent_name", "task_title", "task_description"]
        }


class BroadcastMessageTool(BaseTool):
    """Tool for broadcasting messages to all agents"""
    
    name = "broadcast_message"
    description = "Send a message to all agents in the current chatroom. Use for sharing information or requesting help from any available agent."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, message: str, **kwargs) -> str:
        """
        Broadcast a message to all agents
        
        Args:
            message: The message to broadcast
            
        Returns:
            Broadcast status
        """
        current_agent_id = kwargs.get('agent_id', 0)
        current_agent_name = kwargs.get('agent_name', 'unknown')
        chatroom_id = kwargs.get('chatroom_id', 0)
        
        if not self.coordinator:
            return "[Broadcast] Error: Collaboration coordinator not available"
        
        from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid
        
        # Create broadcast message
        broadcast = CollaborationMessage(
            id=str(uuid.uuid4()),
            message_type=CollaborationMessageType.BROADCAST,
            from_agent_id=current_agent_id,
            from_agent_name=current_agent_name,
            chatroom_id=chatroom_id,
            content=message
        )
        
        # Route message
        await self.coordinator.route_message(broadcast)
        
        # Count recipients
        agent_ids = self.coordinator.chatroom_agents.get(chatroom_id, set())
        recipient_count = len([aid for aid in agent_ids if aid != current_agent_id])
        
        return f"[Broadcast] Message sent to {recipient_count} other agent(s) in chatroom"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to broadcast to all agents"
                }
            },
            "required": ["message"]
        }


class CheckTaskStatusTool(BaseTool):
    """Tool for checking status of delegated tasks"""
    
    name = "check_task_status"
    description = "Check the status of a delegated task. Use this to see if a task you delegated has been completed."
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, task_id: str, **kwargs) -> str:
        """
        Check task status
        
        Args:
            task_id: The ID of the task to check
            
        Returns:
            Task status information
        """
        if not self.coordinator:
            return "[Check Task] Error: Collaboration coordinator not available"
        
        from agents.collaboration import (
            enrich_collaboration_task_result_details,
            normalize_delegated_task_id,
            rebuild_collaboration_task_from_runtime,
            refresh_collaboration_task_from_runtime,
        )

        normalized_task_id = normalize_delegated_task_id(task_id)
        task = self.coordinator.get_task_status(normalized_task_id)
        if task is None:
            task = rebuild_collaboration_task_from_runtime(normalized_task_id)
        task = refresh_collaboration_task_from_runtime(task) or task
        if task is not None:
            self.coordinator.task_registry[task.id] = task
        
        if not task:
            return f"[Check Task] Error: Task '{task_id}' not found"
        
        status_emoji = {
            "pending": "⏳",
            "in_progress": "🔄",
            "stalled": "⛔",
            "completed": "✅",
            "failed": "❌",
            "delegated": "📤"
        }.get(task.status, "❓")
        
        result = f"[Check Task] Task: {task.title}\n"
        result += f"Status: {status_emoji} {task.status}\n"
        result += f"Assigned to: Agent #{task.assigned_to_agent_id}\n"
        
        if task.result:
            result += f"Result: {task.result[:500]}...\n" if len(task.result) > 500 else f"Result: {task.result}\n"

        details = enrich_collaboration_task_result_details(task)
        if details.get("command"):
            result += f"Command: {details['command']}\n"
        if details.get("phase"):
            result += f"Phase: {details['phase']}\n"
        if details.get("duration_ms") is not None:
            result += f"Duration: {details['duration_ms']}ms\n"
        if details.get("tail_output"):
            tail = str(details["tail_output"])
            result += f"Tail output: {tail[:800]}...\n" if len(tail) > 800 else f"Tail output: {tail}\n"
        if details.get("blocked") is True:
            result += f"Blocked: {details.get('blocked_reason') or 'approval required'}\n"
        if details.get("passed_count") is not None or details.get("failed_count") is not None:
            passed = details.get("passed_count")
            failed = details.get("failed_count")
            result += f"Test summary: {passed if passed is not None else '?'} passed / {failed if failed is not None else '?'} failed\n"
        if details.get("next_step"):
            result += f"Next step: {details['next_step']}\n"
        
        if task.completed_at:
            result += f"Completed at: {task.completed_at}\n"
        
        return result
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to check"
                }
            },
            "required": ["task_id"]
        }


class ListCollaboratorsTool(BaseTool):
    """Tool for listing available collaborators"""
    
    name = "list_collaborators"
    description = (
        "List all available agents for collaboration in the current chatroom, plus guidance on when to use "
        "delegate_task, query_agent, send_direct_message, or @mentions."
    )
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, **kwargs) -> str:
        """
        List available collaborators

        Returns:
            List of available agents with their capabilities
        """
        chatroom_id = kwargs.get('chatroom_id', 0)

        if not self.coordinator:
            return "[List Collaborators] Error: Collaboration coordinator not available"

        agent_ids = self.coordinator.chatroom_agents.get(chatroom_id, set())

        # 如果聊天室没有注册的协作者，查询当前房间（项目）关联的 Agent
        if not agent_ids:
            try:
                from models.database import get_db, Agent as DBAgent, Chatroom, AgentAssignment
                db = next(get_db())
                try:
                    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
                    if chatroom and chatroom.project_id:
                        assignments = db.query(AgentAssignment).filter(
                            AgentAssignment.project_id == chatroom.project_id
                        ).all()
                        assigned_ids = [a.agent_id for a in assignments]
                        room_agents = db.query(DBAgent).filter(
                            DBAgent.id.in_(assigned_ids), DBAgent.is_active == True
                        ).all() if assigned_ids else []
                    else:
                        room_agents = []

                    if room_agents:
                        result = f"[List Collaborators] {len(room_agents)} agent(s) in this room:\n"
                        for a in room_agents:
                            tools = a.tools if isinstance(a.tools, str) else str(a.tools)
                            result += f"  - **{a.name}** (role: {a.role}, tools: {tools})\n"
                        result += (
                            "\nSelection guide:\n"
                            "- Use delegate_task for tracked async work.\n"
                            "- Use query_agent for an immediate expert answer.\n"
                            "- Use send_direct_message for notification-only delivery.\n"
                            "- Use @agent_name in normal chat when you want a lightweight live handoff."
                        )
                        return result
                finally:
                    db.close()
            except Exception:
                pass
            return "[List Collaborators] No other agents available in this chatroom"

        result = f"[List Collaborators] {len(agent_ids)} agent(s) in chatroom:\n"

        for aid in agent_ids:
            if aid in self.coordinator.collaborators:
                collab = self.coordinator.collaborators[aid]
                status = "🟢 active" if collab.is_active else "🔴 inactive"
                pending = len(collab.assigned_tasks)
                result += f"  - **{collab.agent_name}** (ID: {aid}): {status}, {pending} pending tasks\n"

        result += (
            "\nSelection guide:\n"
            "- Use delegate_task for tracked async work.\n"
            "- Use query_agent for an immediate expert answer.\n"
            "- Use send_direct_message for notification-only delivery.\n"
            "- Use @agent_name in normal chat when you want a lightweight live handoff."
        )
        return result
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


class SendDirectMessageTool(BaseTool):
    """Tool for sending direct messages to specific agents"""
    
    name = "send_direct_message"
    description = (
        "Send a one-way direct message to a specific agent without creating a tracked task. "
        "Use this for notifications or context sharing when you do not need an immediate response."
    )
    
    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator
    
    async def execute(self, target_agent_name: str, message: str, **kwargs) -> str:
        """
        Send a direct message to another agent
        
        Args:
            target_agent_name: Name of the target agent
            message: The message to send
            
        Returns:
            Send status
        """
        current_agent_id = kwargs.get('agent_id', 0)
        current_agent_name = kwargs.get('agent_name', 'unknown')
        chatroom_id = kwargs.get('chatroom_id', 0)
        
        if not self.coordinator:
            return "[Direct Message] Error: Collaboration coordinator not available"
        
        # Find target agent
        target_agent_id = None
        for aid, collab in self.coordinator.collaborators.items():
            if collab.agent_name == normalize_agent_type(target_agent_name):
                target_agent_id = aid
                break
        
        if not target_agent_id:
            return f"[Direct Message] Error: Agent '{normalize_agent_type(target_agent_name)}' not found"
        
        from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid
        
        # Create direct message
        direct_msg = CollaborationMessage(
            id=str(uuid.uuid4()),
            message_type=CollaborationMessageType.DIRECT,
            from_agent_id=current_agent_id,
            from_agent_name=current_agent_name,
            to_agent_id=target_agent_id,
            to_agent_name=normalize_agent_type(target_agent_name),
            chatroom_id=chatroom_id,
            content=message
        )
        
        # Route message
        await self.coordinator.route_message(direct_msg)
        
        return f"[Direct Message] Sent to {normalize_agent_type(target_agent_name)}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "Name of the agent to send message to"
                },
                "message": {
                    "type": "string",
                    "description": "The message content"
                }
            },
            "required": ["target_agent_name", "message"]
        }


class ListAgentsTool(BaseTool):
    """Tool for listing agents that are NOT in the current room"""

    name = "list_agents"
    description = (
        "List agents that are NOT currently in this room. "
        "Use this to find agents you can invite into the current project."
    )

    async def execute(self, **kwargs) -> str:
        chatroom_id = kwargs.get('chatroom_id', 0)

        from models.database import get_db, Agent as DBAgent, Chatroom, AgentAssignment
        db = next(get_db())
        try:
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom or not chatroom.project_id:
                return "[Directory] Error: No project associated with this chatroom"

            # 当前房间内的 agent ids
            assignments = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == chatroom.project_id
            ).all()
            room_agent_ids = {a.agent_id for a in assignments}

            # 系统中所有活跃 agent，排除已在房间内的
            all_agents = db.query(DBAgent).filter(DBAgent.is_active == True).all()
            external = [a for a in all_agents if a.id not in room_agent_ids]

            if not external:
                return "[Directory] All system agents are already in this room."

            result = f"[Directory] {len(external)} agent(s) available to invite:\n"
            for a in external:
                result += f"  - **{a.agent_type or a.name}** ({agent_name_of(a)}, role: {a.role})\n"
            result += "\nUse invite_agent(agent_name) to add one to this room."
            return result
        finally:
            db.close()

    def _get_parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}


class InviteAgentTool(BaseTool):
    """Tool for inviting an agent to join the current room"""

    name = "invite_agent"
    description = (
        "Invite an agent to join the current room/project. "
        "The agent will be added to the team and available for collaboration."
    )

    async def execute(self, agent_name: str, **kwargs) -> str:
        chatroom_id = kwargs.get('chatroom_id', 0)
        target_agent_type = normalize_agent_type(agent_name)

        from models.database import get_db, Agent as DBAgent, Chatroom, AgentAssignment
        db = next(get_db())
        try:
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom or not chatroom.project_id:
                return "[Invite] Error: No project associated with this chatroom"

            # 查找目标 agent
            target = db.query(DBAgent).filter(
                DBAgent.agent_type == target_agent_type, DBAgent.is_active == True
            ).first()
            if not target:
                return f"[Invite] Error: Agent '{target_agent_type}' not found in the system."

            # 检查是否已在房间内
            existing = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == chatroom.project_id,
                AgentAssignment.agent_id == target.id
            ).first()
            if existing:
                return f"[Invite] Agent '{target_agent_type}' is already in this room."

            # 创建分配
            assignment = AgentAssignment(project_id=chatroom.project_id, agent_id=target.id)
            db.add(assignment)
            db.commit()

            # 注册到协作协调器
            try:
                from agents.collaboration import collaboration_coordinator, AgentCollaborator
                collaborator = AgentCollaborator(
                    agent_id=target.id,
                    agent_name=target.agent_type or target.name,
                    chatroom_id=chatroom_id
                )
                collaboration_coordinator.register_collaborator(collaborator)
            except Exception:
                pass

            return f"[Invite] ✅ Agent '{target_agent_type}' ({agent_name_of(target)}, role: {target.role}) has joined this room."
        finally:
            db.close()

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to invite (e.g. 'security_auditor')"
                }
            },
            "required": ["agent_name"]
        }
