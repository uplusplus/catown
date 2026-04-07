"""
单元测试 - 工具模块
"""
import pytest
import asyncio
import sys
import os

# 添加 backend 目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.base import BaseTool, ToolRegistry
from tools.web_search import WebSearchTool
from tools.execute_code import ExecuteCodeTool
from tools.retrieve_memory import RetrieveMemoryTool


# ==================== BaseTool & ToolRegistry ====================

class TestToolRegistry:
    """测试工具注册表"""

    def test_register_tool(self):
        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        assert "web_search" in registry.list_tools()

    def test_get_tool(self):
        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        assert registry.get("web_search") is tool
        assert registry.get("nonexistent") is None

    def test_get_schemas(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(ExecuteCodeTool())
        schemas = registry.get_schemas()
        assert len(schemas) == 2
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "web_search"

    def test_get_schemas_filtered(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(ExecuteCodeTool())
        schemas = registry.get_schemas(["web_search"])
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "web_search"


# ==================== WebSearchTool ====================

class TestWebSearchTool:
    """测试 Web 搜索工具"""

    def test_schema(self):
        tool = WebSearchTool()
        schema = tool.get_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "web_search"
        assert "query" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_execute_returns_string(self):
        tool = WebSearchTool()
        result = await tool.execute(query="Python")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_execute_with_empty_query(self):
        tool = WebSearchTool()
        result = await tool.execute(query="")
        assert isinstance(result, str)


# ==================== ExecuteCodeTool ====================

class TestExecuteCodeTool:
    """测试代码执行工具"""

    def test_schema(self):
        tool = ExecuteCodeTool()
        schema = tool.get_schema()
        assert schema["function"]["name"] == "execute_code"
        assert "code" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_execute_simple_code(self):
        tool = ExecuteCodeTool()
        result = await tool.execute(code="print('hello')")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_execute_math_code(self):
        tool = ExecuteCodeTool()
        result = await tool.execute(code="print(2 + 3)")
        assert "5" in result

    @pytest.mark.asyncio
    async def test_execute_error_code(self):
        tool = ExecuteCodeTool()
        result = await tool.execute(code="print(1/0)")
        assert "Error" in result or "ZeroDivisionError" in result

    @pytest.mark.asyncio
    async def test_execute_unsupported_language(self):
        tool = ExecuteCodeTool()
        result = await tool.execute(code="console.log('hi')", language="javascript")
        assert "not supported" in result.lower()


# ==================== RetrieveMemoryTool ====================

class TestRetrieveMemoryTool:
    """测试记忆检索工具"""

    def test_schema(self):
        tool = RetrieveMemoryTool()
        schema = tool.get_schema()
        assert schema["function"]["name"] == "retrieve_memory"
        assert "query" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_execute_returns_string(self):
        tool = RetrieveMemoryTool()
        result = await tool.execute(query="test")
        assert isinstance(result, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
