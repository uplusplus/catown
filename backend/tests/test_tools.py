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
from services.tool_governance import assess_run_shell_command, classify_tool_result
from services.tool_execution_preferences import (
    AUTH_DECISION_ALLOW,
    AUTH_DECISION_DENY,
    AUTH_MATCHER_COMMAND_FINGERPRINT,
    AUTH_MATCHER_TOOL_TARGET,
    AUTH_PREFERENCE_KIND,
    build_run_shell_timeout_preference_key,
    build_run_shell_command_matcher_value,
    build_tool_target_matcher_value,
    save_wait_forever_preference,
    upsert_authorization_rule,
)
from tools.web_search import WebSearchTool
from tools.execute_code import ExecuteCodeTool
from tools.run_shell import RunShellTool
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
        registry.register(RunShellTool())
        schemas = registry.get_schemas()
        assert len(schemas) == 3
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "web_search"

    def test_get_schemas_filtered(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(ExecuteCodeTool())
        registry.register(RunShellTool())
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

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert result["result"] == "Release Title"

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

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert result["result"] == "ok"

    def test_builtin_registry_includes_skill_manager(self):
        from tools import tool_registry

        assert "skill_manager" in tool_registry.list_tools()
        assert "run_shell" in tool_registry.list_tools()

    def test_get_policy_pack_surfaces_approval_sandbox_and_escalation(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(ExecuteCodeTool())
        registry.register(RunShellTool())
        registry.register(GitHubManagerTool())

        pack = registry.get_policy_pack(["execute_code", "run_shell", "web_search", "github_manager"])

        assert pack["tool_policy_summary"]["tool_count"] == 4
        assert pack["tool_policy_summary"]["network_enabled_count"] == 3
        assert pack["tool_policy_summary"]["escalation_possible_count"] == 2

        policies = {policy["name"]: policy for policy in pack["tool_policies"]}
        assert policies["execute_code"]["sandbox"]["mode"] == "language_sandbox"
        assert policies["execute_code"]["sandbox"]["network_access"] == "blocked"
        assert policies["run_shell"]["approval"]["kind"] == "conditional"
        assert policies["run_shell"]["approval"]["required"] is False
        assert policies["run_shell"]["sandbox"]["mode"] == "workspace_shell"
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

        assert result["success"] is False
        assert result["status"] == "approval_blocked"
        assert result["blocked"] is True
        assert result["blocked_kind"] == "approval"
        assert "approved" in result["result"]

    @pytest.mark.asyncio
    async def test_run_shell_executes_after_approval(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        result = await registry.execute(
            "run_shell",
            command="printf ok",
            __catown_approval_granted=True,
        )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert "ok" in result["result"]

    @pytest.mark.asyncio
    async def test_run_shell_read_only_command_executes_without_approval(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        result = await registry.execute(
            "run_shell",
            command="pwd && printf ok",
        )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert "ok" in result["result"]

    @pytest.mark.asyncio
    async def test_run_shell_mutating_command_still_requires_approval(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        result = await registry.execute(
            "run_shell",
            command="touch created.txt",
        )

        assert result["success"] is False
        assert result["status"] == "approval_blocked"
        assert result["blocked"] is True
        assert result["blocked_kind"] == "approval"
        assert "requires approval" in result["result"]

    @pytest.mark.asyncio
    async def test_run_shell_timeout_requests_continue_waiting(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        with patch("tools.run_shell.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="python", timeout=1)):
            result = await registry.execute(
                "run_shell",
                command="cat /dev/zero",
                timeout_seconds=1,
                chatroom_id=1,
            )

        assert result["success"] is False
        assert result["status"] == "timeout_waiting"
        assert result["blocked"] is True
        assert result["blocked_kind"] == "timeout"
        assert "continue without a timeout" in result["result"]

    @pytest.mark.asyncio
    async def test_run_shell_saved_wait_preference_disables_timeout(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Timeout preference chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            chatroom_id = chatroom.id
            preference_key = build_run_shell_timeout_preference_key("cat sample.txt", ".")
            save_wait_forever_preference(
                db,
                tool_name="run_shell",
                preference_key=preference_key,
                command_preview="cat sample.txt @ .",
                chatroom_id=chatroom_id,
            )
        finally:
            db.close()

        def fake_run(*args, **kwargs):
            assert "timeout" not in kwargs
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok\n", stderr="")

        with patch("tools.run_shell.subprocess.run", side_effect=fake_run):
            result = await registry.execute(
                "run_shell",
                command="cat sample.txt",
                timeout_seconds=1,
                chatroom_id=chatroom_id,
            )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert "ok" in result["result"]

    @pytest.mark.asyncio
    async def test_saved_allow_rule_bypasses_run_shell_approval(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Allow rule chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="chatroom",
                matcher_type=AUTH_MATCHER_COMMAND_FINGERPRINT,
                matcher_value=build_run_shell_command_matcher_value("touch created.txt", "."),
                decision_kind=AUTH_DECISION_ALLOW,
                chatroom_id=chatroom.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="granted",
                command_preview="touch created.txt @ .",
            )
            chatroom_id = chatroom.id
        finally:
            db.close()

        result = await registry.execute(
            "run_shell",
            command="touch created.txt",
            chatroom_id=chatroom_id,
        )

        assert result["success"] is True
        assert (tmp_path / "created.txt").exists()

    @pytest.mark.asyncio
    async def test_saved_deny_rule_blocks_tool_even_if_normally_allowed(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Deny rule chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="chatroom",
                matcher_type=AUTH_MATCHER_TOOL_TARGET,
                matcher_value=build_tool_target_matcher_value("run_shell"),
                decision_kind=AUTH_DECISION_DENY,
                chatroom_id=chatroom.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="denied",
                command_preview="run_shell",
            )
            chatroom_id = chatroom.id
        finally:
            db.close()

        result = await registry.execute(
            "run_shell",
            command="pwd",
            chatroom_id=chatroom_id,
        )

        assert result["success"] is False
        assert result["status"] == "approval_blocked"
        assert "denies" in result["result"]

    def test_classify_tool_result_does_not_treat_embedded_approval_text_as_blocked(self):
        result = classify_tool_result(
            "read_file",
            "[Read File] Content of '/tmp/test_report.md':\n[Approval Blocked] Tool 'run_shell' was blocked: Shell command `python` may mutate the workspace or external systems and requires approval.",
            success=True,
        )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert result["blocked"] is False

    def test_assess_run_shell_command_allows_read_only_git_chain(self):
        assessment = assess_run_shell_command("git status --short --branch && echo '---' && git log --oneline -5")

        assert assessment["requires_approval"] is False

    def test_assess_run_shell_command_blocks_redirection_and_mutation(self):
        redirection = assess_run_shell_command("printf ok > note.txt")
        mutation = assess_run_shell_command("git commit -m 'ship it'")

        assert redirection["requires_approval"] is True
        assert mutation["requires_approval"] is True


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
