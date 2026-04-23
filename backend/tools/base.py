# -*- coding: utf-8 -*-
"""
Tool Base Classes and Registry
"""
from typing import Dict, List, Any, Optional, Callable
from pydantic import BaseModel
from abc import ABC, abstractmethod


class ToolSchema(BaseModel):
    """JSON Schema for a tool"""
    name: str
    description: str
    parameters: Dict[str, Any]


class BaseTool(ABC):
    """Base class for all tools"""
    
    name: str = ""
    description: str = ""
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool"""
        pass
    
    def get_schema(self) -> Dict[str, Any]:
        """Get OpenAI-compatible tool schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._get_parameters_schema()
            }
        }
    
    def _get_parameters_schema(self) -> Dict[str, Any]:
        """Override this to define parameters schema"""
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


class ToolRegistry:
    """Registry for managing tools"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool):
        """Register a tool"""
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        return self._tools.get(name)
    
    def list_tools(self) -> List[str]:
        """List all registered tool names"""
        return list(self._tools.keys())
    
    def get_schemas(self, tool_names: List[str] = None) -> List[Dict[str, Any]]:
        """Get schemas for specified tools (or all if not specified)"""
        if tool_names:
            return [self._tools[name].get_schema() for name in tool_names if name in self._tools]
        return [tool.get_schema() for tool in self._tools.values()]
    
    async def execute(self, tool_name: str, **kwargs) -> Any:
        """Execute a tool by name."""
        tool = self.get(tool_name)
        if not tool:
            raise ValueError(f"Tool not found: {tool_name}")
        return await tool.execute(**kwargs)
