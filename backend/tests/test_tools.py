"""
单元测试 - 工具模块
"""
import pytest
import asyncio
import subprocess
import sys
import os
from unittest.mock import patch

# 添加 backend 目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.base import BaseTool, ToolRegistry, build_tool_policy_pack
from tools.web_search import WebSearchTool
from tools.execute_code import ExecuteCodeTool
from tools.retrieve_memory import RetrieveMemoryTool
from tools.github_manager import GitHubManagerTool
from tools.skill_manager import SkillManagerTool
from tools.file_operations import DeleteFileTool


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

    @pytest.mark.asyncio
    async def test_execute_allows_payload_name_for_github_manager(self, monkeypatch):
        registry = ToolRegistry()
        tool = GitHubManagerTool()
        registry.register(tool)

        async def fake_repo_info(repo: str, **kwargs):
            assert repo == "owner/repo"
            return kwargs["name"]

        monkeypatch.setattr(tool, "_action_repo_info", fake_repo_info)

        result = await registry.execute(
            "github_manager",
            action="repo_info",
            repo="owner/repo",
            name="Release Title",
        )

        assert result == "Release Title"

    @pytest.mark.asyncio
    async def test_execute_filters_unknown_runtime_kwargs_for_strict_tools(self):
        registry = ToolRegistry()

        class StrictTool(BaseTool):
            name = "strict"
            description = "Strict tool"

            async def execute(self, value: str) -> str:
                return value

        registry.register(StrictTool())

        result = await registry.execute(
            "strict",
            value="ok",
            agent_id=7,
            agent_name="developer",
            chatroom_id=3,
        )

        assert result == "ok"

    def test_builtin_registry_includes_skill_manager(self):
        from tools import tool_registry

        assert "skill_manager" in tool_registry.list_tools()

    def test_get_policy_pack_surfaces_approval_sandbox_and_escalation(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(ExecuteCodeTool())
        registry.register(GitHubManagerTool())

        pack = registry.get_policy_pack(["execute_code", "web_search", "github_manager"])

        assert pack["tool_policy_summary"]["tool_count"] == 3
        assert pack["tool_policy_summary"]["network_enabled_count"] == 2
        assert pack["tool_policy_summary"]["escalation_possible_count"] == 1

        policies = {policy["name"]: policy for policy in pack["tool_policies"]}
        assert policies["execute_code"]["sandbox"]["mode"] == "language_sandbox"
        assert policies["execute_code"]["sandbox"]["network_access"] == "blocked"
        assert policies["web_search"]["sandbox"]["network_access"] == "enabled"
        assert policies["github_manager"]["approval"]["kind"] == "conditional"
        assert policies["github_manager"]["escalation"]["possible"] is True

    def test_build_tool_policy_pack_supports_runtime_only_tools(self):
        pack = build_tool_policy_pack(
            ["send_message", "write_file"],
            description_map={
                "send_message": "Send a pipeline message.",
                "write_file": "Write content to a file in workspace.",
            },
        )

        policies = {policy["name"]: policy for policy in pack["tool_policies"]}
        assert pack["tool_policy_summary"]["tool_count"] == 2
        assert policies["send_message"]["side_effect_scope"] == "runtime_dispatch"
        assert policies["write_file"]["sandbox"]["workspace_scope"] == "workspace_write"
        assert policies["write_file"]["escalation"]["possible"] is True

    @pytest.mark.asyncio
    async def test_execute_blocks_manual_approval_tools_in_registry(self, tmp_path):
        registry = ToolRegistry()
        registry.register(DeleteFileTool(workspace=str(tmp_path)))

        result = await registry.execute("delete_file", file_path="danger.txt")

        assert "Approval Blocked" in result
        assert "approved" in result


class TestSkillManagerTool:
    @pytest.mark.asyncio
    async def test_list_skills(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CATOWN_SKILLS_DIR", str(tmp_path / "skills"))
        tool = SkillManagerTool()

        result = await tool.execute(action="list")

        assert result["ok"] is True
        assert result["skills_dir"] == str(tmp_path / "skills")
        assert any(skill["id"] == "code-generation" for skill in result["skills"])

    @pytest.mark.asyncio
    async def test_install_local_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CATOWN_SKILLS_DIR", str(tmp_path / "skills"))
        source = tmp_path / "hub" / "demo-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            """---
name: demo-skill
description: Demo import.
---

# Demo Skill
""",
            encoding="utf-8",
        )
        tool = SkillManagerTool()

        result = await tool.execute(action="install", source=str(source))

        assert result["ok"] is True
        assert result["skill"]["id"] == "demo-skill"
        assert (tmp_path / "skills" / "demo-skill" / "skill.json").exists()

    @pytest.mark.asyncio
    async def test_list_marketplaces(self):
        tool = SkillManagerTool()

        result = await tool.execute(action="marketplaces")

        assert result["ok"] is True
        assert any(marketplace["id"] == "builtin" for marketplace in result["marketplaces"])
        assert any("command_available" in marketplace for marketplace in result["marketplaces"])

    def test_schema_guides_agents_to_use_marketplaces(self):
        tool = SkillManagerTool()

        schema = tool.get_schema()
        encoded = str(schema)

        assert "skillhub-cn" in encoded
        assert "graphify" in encoded
        assert "command_not_found" in encoded
        assert "marketplaces" in encoded


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
        result = await tool.execute(code="puts 'hi'", language="ruby")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_uses_active_workspace_as_cwd(self, tmp_path):
        from tools.file_operations import reset_active_workspace, set_active_workspace

        fallback = tmp_path / "fallback"
        project = tmp_path / "project"
        fallback.mkdir()
        project.mkdir()

        tool = ExecuteCodeTool(workspace=str(fallback))
        token = set_active_workspace(str(project))
        try:
            with patch("tools.execute_code.subprocess.run") as mocked_run:
                mocked_run.return_value = subprocess.CompletedProcess(
                    args=["python"],
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                )
                result = await tool.execute(code="print('ok')")
        finally:
            reset_active_workspace(token)

        assert "ok" in result
        assert mocked_run.call_args.kwargs["cwd"] == os.path.realpath(project)


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
