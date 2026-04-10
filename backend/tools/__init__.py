# -*- coding: utf-8 -*-
"""
Agent Tools Module
"""
import os
from .base import ToolRegistry, BaseTool
from .web_search import WebSearchTool
from .execute_code import ExecuteCodeTool
from .retrieve_memory import RetrieveMemoryTool
from .file_operations import (
    ReadFileTool, 
    WriteFileTool, 
    ListFilesTool, 
    DeleteFileTool, 
    SearchFilesTool
)
from .collaboration_tools import (
    DelegateTaskTool,
    BroadcastMessageTool,
    CheckTaskStatusTool,
    ListCollaboratorsTool,
    SendDirectMessageTool,
    ListDirectoryTool,
    InviteAgentTool
)
from .query_agent import QueryAgentTool
from .github_manager import GitHubManagerTool

# Initialize tool registry
tool_registry = ToolRegistry()

# Define workspace for file operations (can be configured via environment)
WORKSPACE = os.environ.get('CATOWN_WORKSPACE', os.getcwd())

# Collaboration coordinator reference (will be set by main app)
_collaboration_coordinator = None

def set_collaboration_coordinator(coordinator):
    """Set the collaboration coordinator for collaboration tools"""
    global _collaboration_coordinator
    _collaboration_coordinator = coordinator

# Register built-in tools
tool_registry.register(WebSearchTool())
tool_registry.register(ExecuteCodeTool())
tool_registry.register(RetrieveMemoryTool())
from .save_memory import SaveMemoryTool
tool_registry.register(SaveMemoryTool())

# Register file operation tools
tool_registry.register(ReadFileTool(workspace=WORKSPACE))
tool_registry.register(WriteFileTool(workspace=WORKSPACE))
tool_registry.register(ListFilesTool(workspace=WORKSPACE))
tool_registry.register(DeleteFileTool(workspace=WORKSPACE))
tool_registry.register(SearchFilesTool(workspace=WORKSPACE))

# Register collaboration tools (coordinator will be set later)
tool_registry.register(DelegateTaskTool())
tool_registry.register(BroadcastMessageTool())
tool_registry.register(CheckTaskStatusTool())
tool_registry.register(ListCollaboratorsTool())
tool_registry.register(SendDirectMessageTool())
tool_registry.register(QueryAgentTool())
tool_registry.register(ListDirectoryTool())
tool_registry.register(InviteAgentTool())
tool_registry.register(GitHubManagerTool())

def init_collaboration_tools(coordinator):
    """Initialize collaboration tools with coordinator"""
    # Update existing collaboration tools with coordinator
    for tool_name in ['delegate_task', 'broadcast_message', 'check_task_status', 'list_collaborators', 'send_direct_message', 'query_agent']:
        tool = tool_registry.get(tool_name)
        if tool:
            tool.coordinator = coordinator

__all__ = [
    'tool_registry', 
    'ToolRegistry', 
    'BaseTool', 
    'WebSearchTool', 
    'ExecuteCodeTool', 
    'RetrieveMemoryTool',
    'ReadFileTool',
    'WriteFileTool',
    'ListFilesTool',
    'DeleteFileTool',
    'SearchFilesTool',
    'SaveMemoryTool',
    'DelegateTaskTool',
    'BroadcastMessageTool',
    'CheckTaskStatusTool',
    'ListCollaboratorsTool',
    'SendDirectMessageTool',
    'QueryAgentTool',
    'GitHubManagerTool',
    'set_collaboration_coordinator',
    'init_collaboration_tools'
]
