# -*- coding: utf-8 -*-
"""
Agent Tools Module
"""
from .base import ToolRegistry, BaseTool
from .web_search import WebSearchTool
from .execute_code import ExecuteCodeTool
from .retrieve_memory import RetrieveMemoryTool

# Initialize tool registry
tool_registry = ToolRegistry()

# Register built-in tools
tool_registry.register(WebSearchTool())
tool_registry.register(ExecuteCodeTool())
tool_registry.register(RetrieveMemoryTool())

__all__ = ['tool_registry', 'ToolRegistry', 'BaseTool', 'WebSearchTool', 'ExecuteCodeTool', 'RetrieveMemoryTool']
