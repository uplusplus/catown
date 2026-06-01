# -*- coding: utf-8 -*-
"""
Agent Tools Module
"""
import os
from .base import ToolRegistry, BaseTool
from .web_search import WebSearchTool
from .web_fetch import WebFetchTool
from .execute_code import ExecuteCodeTool
from .run_shell import RunShellTool
from .retrieve_memory import RetrieveMemoryTool
from .file_operations import (
    ReadFileTool, 
    WriteFileTool, 
    ListFilesTool, 
    DeleteFileTool, 
    SearchFilesTool
)
from . import collaboration_tools
from .collaboration_tools import (
    DelegateTaskTool,
    BroadcastMessageTool,
    CheckTaskStatusTool,
    ListCollaboratorsTool,
    SendDirectMessageTool,
    ListAgentsTool,
    InviteAgentTool
)
from .consult_agent import ConsultAgentTool
from .github_manager import GitHubManagerTool
from .screenshot import ScreenshotTool
from .browser import BrowserTool
from .skill_manager import SkillManagerTool
from .user_file_interaction import OpenFileForUserTool
from .knowledge_graph import KnowledgeGraphTool
from .analyze_image import AnalyzeImageTool
from .analyze_document import AnalyzeDocumentTool
from .screenshot_compare import ScreenshotCompareTool
from .chrome_devtools import ChromeDevtoolsTool

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
tool_registry.register(WebFetchTool())
tool_registry.register(ExecuteCodeTool(workspace=WORKSPACE))
tool_registry.register(RunShellTool(workspace=WORKSPACE))
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
tool_registry.register(ConsultAgentTool())
tool_registry.register_alias("query_agent", "consult_agent")
tool_registry.register(ListAgentsTool())
tool_registry.register(InviteAgentTool())
tool_registry.register(GitHubManagerTool())
tool_registry.register(ScreenshotTool())
tool_registry.register(BrowserTool())
tool_registry.register(SkillManagerTool())
tool_registry.register(OpenFileForUserTool())
tool_registry.register(KnowledgeGraphTool())
tool_registry.register(AnalyzeImageTool())
tool_registry.register(AnalyzeDocumentTool())
tool_registry.register(ScreenshotCompareTool())
tool_registry.register(ChromeDevtoolsTool())

def init_collaboration_tools(coordinator):
    """Initialize collaboration tools with coordinator"""
    for tool_name in ['delegate_task', 'broadcast_message', 'check_task_status', 'list_collaborators', 'send_direct_message', 'consult_agent']:
        tool = tool_registry.get(tool_name)
        if tool:
            tool.coordinator = coordinator

__all__ = [
    'tool_registry', 
    'ToolRegistry', 
    'BaseTool', 
    'WebSearchTool', 
    'ExecuteCodeTool', 
    'RunShellTool',
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
    'ConsultAgentTool',
    'InviteAgentTool',
    'ListAgentsTool',
    'GitHubManagerTool',
    'ScreenshotTool',
    'BrowserTool',
    'SkillManagerTool',
    'OpenFileForUserTool',
    'KnowledgeGraphTool',
    'AnalyzeImageTool',
    'AnalyzeDocumentTool',
    'ScreenshotCompareTool',
    'ChromeDevtoolsTool',
    'set_collaboration_coordinator',
    'init_collaboration_tools'
]
