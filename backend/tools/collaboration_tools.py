# -*- coding: utf-8 -*-
"""
Collaboration tools.

Tools that enable agents to collaborate with each other:
- delegate_task: Delegate a task to another agent
- broadcast_message: Send a message to all agents
- check_task_status: Check status of a delegated task
- list_collaborators: List available collaborators
"""

from __future__ import annotations

import asyncio

from .base import BaseTool
from services.agent_action_runtime import (
    run_broadcast_agent_action,
    run_check_task_status_action,
    run_delegate_agent_action,
    run_direct_message_agent_action,
    run_invite_agent_action,
    run_list_agents_action,
    run_list_collaborators_action,
)
from chatrooms.manager import chatroom_manager
from services.chat_publish import publish_saved_chat_message
from services.stream_runtime_persistence import store_runtime_card


class DelegateTaskTool(BaseTool):
    """Tool for delegating tasks to other agents."""

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
        **kwargs,
    ) -> str:
        """Delegate one tracked task to another agent."""
        current_agent_id = kwargs.get("agent_id", 0)
        current_agent_name = kwargs.get("agent_name", "unknown")
        chatroom_id = kwargs.get("chatroom_id", 0)
        task_run_id = kwargs.get("task_run_id")

        return await run_delegate_agent_action(
            coordinator=self.coordinator,
            target_agent_name=target_agent_name,
            task_title=task_title,
            task_description=task_description,
            context=context,
            current_agent_id=current_agent_id,
            current_agent_name=current_agent_name,
            chatroom_id=chatroom_id,
            task_run_id=task_run_id,
            store_runtime_card_fn=store_runtime_card,
            send_message_fn=chatroom_manager.send_message,
            publish_saved_chat_message_fn=publish_saved_chat_message,
            trigger_agent_response_fn=__import__(
                "routes.api",
                fromlist=["trigger_agent_response"],
            ).trigger_agent_response,
            create_task_fn=asyncio.create_task,
        )

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "Type of the agent to delegate to (analyst, architect, developer, tester, release, valet)",
                },
                "task_title": {
                    "type": "string",
                    "description": "Short title for the delegated task",
                },
                "task_description": {
                    "type": "string",
                    "description": "Detailed description of what needs to be done",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context or data for the task",
                    "default": "",
                },
            },
            "required": ["target_agent_name", "task_title", "task_description"],
        }


class BroadcastMessageTool(BaseTool):
    """Tool for broadcasting messages to all agents."""

    name = "broadcast_message"
    description = "Send a message to all agents in the current chatroom. Use for sharing information or requesting help from any available agent."

    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator

    async def execute(self, message: str, **kwargs) -> str:
        """Broadcast one message to all agents in the room."""
        current_agent_id = kwargs.get("agent_id", 0)
        current_agent_name = kwargs.get("agent_name", "unknown")
        chatroom_id = kwargs.get("chatroom_id", 0)

        return await run_broadcast_agent_action(
            coordinator=self.coordinator,
            message=message,
            current_agent_id=current_agent_id,
            current_agent_name=current_agent_name,
            chatroom_id=chatroom_id,
        )

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to broadcast to all agents",
                }
            },
            "required": ["message"],
        }


class CheckTaskStatusTool(BaseTool):
    """Tool for checking status of delegated tasks."""

    name = "check_task_status"
    description = "Check the status of a delegated task. Use this to see if a task you delegated has been completed."

    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator

    async def execute(self, task_id: str, **kwargs) -> str:
        """Return the current task status text for one delegated task."""
        return run_check_task_status_action(coordinator=self.coordinator, task_id=task_id)

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to check",
                }
            },
            "required": ["task_id"],
        }


class ListCollaboratorsTool(BaseTool):
    """Tool for listing available collaborators."""

    name = "list_collaborators"
    description = (
        "List all available agents for collaboration in the current chatroom, plus guidance on when to use "
        "delegate_task, consult_agent, or @mentions."
    )

    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator

    async def execute(self, **kwargs) -> str:
        """List available collaborators and a short selection guide."""
        chatroom_id = kwargs.get("chatroom_id", 0)
        return run_list_collaborators_action(
            coordinator=self.coordinator,
            chatroom_id=chatroom_id,
        )

    def _get_parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}


class SendDirectMessageTool(BaseTool):
    """Tool for sending direct messages to specific agents."""

    name = "send_direct_message"
    system_only = True
    description = (
        "Send a one-way direct message to a specific agent without creating a tracked task. "
        "Reserved for backend system calls; agent messages must be routed through chat."
    )

    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator

    async def execute(self, target_agent_name: str, message: str, **kwargs) -> str:
        """Send one direct message to a specific agent."""
        current_agent_id = kwargs.get("agent_id", 0)
        current_agent_name = kwargs.get("agent_name", "unknown")
        chatroom_id = kwargs.get("chatroom_id", 0)

        return await run_direct_message_agent_action(
            coordinator=self.coordinator,
            target_agent_name=target_agent_name,
            message=message,
            current_agent_id=current_agent_id,
            current_agent_name=current_agent_name,
            chatroom_id=chatroom_id,
        )

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "Name of the agent to send message to",
                },
                "message": {
                    "type": "string",
                    "description": "The message content",
                },
            },
            "required": ["target_agent_name", "message"],
        }


class ListAgentsTool(BaseTool):
    """Tool for listing agents that are not in the current room."""

    name = "list_agents"
    description = (
        "List agents that are NOT currently in this room. "
        "Use this to find agents you can invite into the current project."
    )

    async def execute(self, **kwargs) -> str:
        chatroom_id = kwargs.get("chatroom_id", 0)
        return run_list_agents_action(chatroom_id=chatroom_id)

    def _get_parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}


class InviteAgentTool(BaseTool):
    """Tool for inviting an agent to join the current room."""

    name = "invite_agent"
    description = (
        "Invite an agent to join the current room/project. "
        "The agent will be added to the team and available for collaboration."
    )

    async def execute(self, agent_name: str, **kwargs) -> str:
        chatroom_id = kwargs.get("chatroom_id", 0)
        return run_invite_agent_action(agent_name=agent_name, chatroom_id=chatroom_id)

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to invite (e.g. 'security_auditor')",
                }
            },
            "required": ["agent_name"],
        }
