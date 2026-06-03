"""
单元测试 - 工具模块
"""
import pytest
import asyncio
import json
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
    AUTH_DECISION_REQUIRE_APPROVAL,
    AUTH_MATCHER_ALL_TOOLS,
    AUTH_MATCHER_COMMAND_FINGERPRINT,
    AUTH_MATCHER_SHELL_BIN,
    AUTH_MATCHER_TOOL_TARGET,
    AUTH_PREFERENCE_KIND,
    build_all_tools_matcher_value,
    build_run_shell_timeout_preference_key,
    build_run_shell_command_matcher_value,
    build_shell_bin_matcher_value,
    build_tool_target_matcher_value,
    shell_bins_for_command,
    save_wait_forever_preference,
    upsert_authorization_rule,
)
from tools.web_search import WebSearchTool
from tools.execute_code import ExecuteCodeTool
from tools.run_shell import RunShellTool
from tools.retrieve_memory import RetrieveMemoryTool
from tools.github_manager import GitHubManagerTool
from tools.skill_manager import SkillManagerTool
from tools.knowledge_graph import KnowledgeGraphTool
from tools.file_operations import DeleteFileTool
from tools.user_file_interaction import OpenFileForUserTool
from services.chat_runtime import merge_tool_execution_kwargs


def _read_only_probe_command() -> str:
    return "echo ok"


def _mutating_probe_command() -> str:
    if os.name == "nt":
        return "mkdir created-dir"
    return "touch created.txt"


def _python_command(code: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", code])


def _shell_path(path) -> str:
    return subprocess.list2cmdline([str(path)])


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

    def test_get_tool_by_alias(self):
        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        registry.register_alias("search_web", "web_search")

        assert registry.get("search_web") is tool

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

    def test_system_only_tools_are_not_agent_visible(self):
        registry = ToolRegistry()

        class SystemOnlyTool(BaseTool):
            name = "system_only_tool"
            description = "Backend only"
            system_only = True

            async def execute(self) -> str:
                return "ok"

        registry.register(WebSearchTool())
        registry.register(SystemOnlyTool())

        assert "system_only_tool" in registry.list_tools()
        assert "system_only_tool" in registry.list_system_tools()
        assert "system_only_tool" not in registry.list_agent_tools()
        assert registry.get("system_only_tool").get_policy_payload()["system_only"] is True

    @pytest.mark.asyncio
    async def test_execute_blocks_system_only_tools_without_system_marker(self):
        registry = ToolRegistry()

        class SystemOnlyTool(BaseTool):
            name = "system_only_tool"
            description = "Backend only"
            system_only = True

            async def execute(self) -> str:
                return "ok"

        registry.register(SystemOnlyTool())

        result = await registry.execute("system_only_tool")

        assert result["success"] is False
        assert result["status"] == "system_tool_only"
        assert result["blocked"] is True
        assert result["blocked_kind"] == "tool_scope"

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

    @pytest.mark.asyncio
    async def test_execute_adds_consult_agent_references_from_result(self):
        registry = ToolRegistry()

        class ConsultTool(BaseTool):
            name = "consult_agent"
            description = "Consult another agent"

            async def execute(self, **kwargs) -> str:
                return "[Response from analyst (Analyst)]:\nDetailed answer\n[consult_step_id] consult-analyst-1"

        registry.register(ConsultTool())

        result = await registry.execute(
            "consult_agent",
            target_agent="analyst",
            question="What is the risk?",
            task_run_id=42,
            client_turn_id="turn-42",
        )

        assert result["success"] is True
        assert result["metadata"]["consult_agent"] == {
            "consult_step_id": "consult-analyst-1",
            "task_run_id": 42,
            "client_turn_id": "turn-42",
        }

    @pytest.mark.asyncio
    async def test_execute_persists_long_non_shell_tool_output_reference(self, tmp_path, monkeypatch):
        catown_home = tmp_path / "catown-home"
        monkeypatch.setenv("CATOWN_HOME", str(catown_home))
        monkeypatch.setenv("CATOWN_STATE_DIR", str(catown_home / "state"))
        registry = ToolRegistry()

        class BrowserLikeTool(BaseTool):
            name = "browser"
            description = "Browser-like tool"

            async def execute(self, **kwargs) -> str:
                return "browser output\n" + ("x" * 2500)

        registry.register(BrowserLikeTool())

        result = await registry.execute(
            "browser",
            action="get_text",
            task_run_id=42,
            tool_call_id="call-browser",
        )

        artifact = result["metadata"]["tool_output_artifact"]
        artifact_path = artifact["path"]
        assert result["success"] is True
        assert artifact["tool_name"] == "browser"
        assert artifact["chars"] == len(result["result"])
        assert artifact["task_run_id"] == 42
        assert artifact["tool_call_id"] == "call-browser"
        assert os.path.exists(artifact_path)
        assert open(artifact_path, encoding="utf-8").read() == result["result"]

    def test_builtin_registry_includes_skill_manager(self):
        from tools import tool_registry

        assert "skill_manager" in tool_registry.list_tools()
        assert "run_shell" in tool_registry.list_tools()
        assert "open_file_for_user" in tool_registry.list_tools()
        assert "analyze_document" in tool_registry.list_tools()

    @pytest.mark.asyncio
    async def test_open_file_for_user_returns_interactive_file_payload(self):
        tool = OpenFileForUserTool()

        result = await tool.execute(path="./docs/ADR-017-chat-file-reader.md", mode="edit", project_id=12)

        assert result["success"] is True
        payload = json.loads(result["result"])
        assert payload["catown_interactive_tool"] == "file_reader_editor"
        assert payload["path"] == "docs/ADR-017-chat-file-reader.md"
        assert payload["mode"] == "edit"
        assert payload["project_id"] == 12

    @pytest.mark.asyncio
    async def test_open_file_for_user_rejects_workspace_escape(self):
        tool = OpenFileForUserTool()

        result = await tool.execute(path="../outside.txt")

        assert result["success"] is False
        assert result["status"] == "failed"

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

    def test_merge_tool_execution_kwargs_preserves_tool_agent_id_and_runtime_caller_identity(self):
        merged = merge_tool_execution_kwargs(
            {"agent_id": 99, "query": "memory lookup"},
            {"agent_id": 7, "agent_name": "developer", "chatroom_id": 3},
            turn=2,
        )

        assert merged["agent_id"] == 99
        assert merged["caller_agent_id"] == 7
        assert merged["caller_agent_name"] == "developer"
        assert merged["chatroom_id"] == 3
        assert merged["turn"] == 2

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
            command=_read_only_probe_command(),
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
            command=_read_only_probe_command(),
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
            command=_mutating_probe_command(),
        )

        assert result["success"] is False
        assert result["status"] == "approval_blocked"
        assert result["blocked"] is True
        assert result["blocked_kind"] == "approval"
        assert "requires approval" in result["result"]

    @pytest.mark.asyncio
    async def test_auto_approve_all_bypasses_run_shell_mutation_approval(self, tmp_path, monkeypatch):
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "permissions": {
                        "allow_read_only_tools_without_approval": True,
                        "auto_approve_all": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_path))

        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        result = await registry.execute(
            "run_shell",
            command="touch created.txt",
        )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert (tmp_path / "created.txt").exists()

    @pytest.mark.asyncio
    async def test_run_shell_foreground_wait_elapsed_returns_background_running(self, fresh_db, tmp_path):
        fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        result = await registry.execute(
            "run_shell",
            command=_python_command("import time; time.sleep(2)"),
            timeout_seconds=1,
            chatroom_id=1,
            __catown_approval_granted=True,
        )

        assert result["success"] is False
        assert result["status"] == "background_running"
        assert result["blocked"] is False
        assert result["blocked_kind"] is None
        assert "still running in the background" in result["result"]

    @pytest.mark.asyncio
    async def test_run_shell_saved_wait_preference_disables_timeout(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))
        (tmp_path / "sample.txt").write_text("ok\n", encoding="utf-8")

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
    async def test_run_shell_wait_forever_emits_progress(self, fresh_db, tmp_path, monkeypatch):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))
        command = _python_command(
            "import time; print('line 1', flush=True); time.sleep(0.3); print('line 2', flush=True)"
        )
        monkeypatch.setattr("tools.run_shell.TAIL_PROGRESS_INTERVAL_SECONDS", 0.1)

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Progress chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            chatroom_id = chatroom.id
            preference_key = build_run_shell_timeout_preference_key(command, ".")
            save_wait_forever_preference(
                db,
                tool_name="run_shell",
                preference_key=preference_key,
                command_preview=f"{command} @ .",
                chatroom_id=chatroom_id,
            )
        finally:
            db.close()

        progress_updates = []

        async def progress_callback(payload):
            progress_updates.append(payload)

        result = await registry.execute(
            "run_shell",
            command=command,
            timeout_seconds=1,
            chatroom_id=chatroom_id,
            __catown_approval_granted=True,
            progress_callback=progress_callback,
        )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert "line 1" in result["result"]
        assert progress_updates
        assert any("line 1" in str(update.get("tail_output") or "") for update in progress_updates)

    @pytest.mark.asyncio
    async def test_run_shell_progress_callback_failure_does_not_fail_process(self, fresh_db, tmp_path, monkeypatch):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))
        command = _python_command(
            "import time; print('line 1', flush=True); time.sleep(0.2); print('line 2', flush=True)"
        )
        monkeypatch.setattr("tools.run_shell.TAIL_PROGRESS_INTERVAL_SECONDS", 0.1)

        calls = 0

        async def progress_callback(_payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("publish failed")

        result = await registry.execute(
            "run_shell",
            command=command,
            timeout_seconds=2,
            __catown_approval_granted=True,
            progress_callback=progress_callback,
        )

        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert "line 2" in result["result"]
        assert calls >= 1

    @pytest.mark.asyncio
    async def test_run_shell_progress_tails_redirected_output(self, fresh_db, tmp_path, monkeypatch):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))
        output_path = tmp_path / "pytest.log"
        command = (
            _python_command(
                "import time; print('redirected line 1', flush=True); "
                "time.sleep(0.3); print('redirected line 2', flush=True)"
            )
            + f" > {_shell_path(output_path)} 2>&1"
        )
        monkeypatch.setattr("tools.run_shell.TAIL_PROGRESS_INTERVAL_SECONDS", 0.1)

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Redirect progress chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            chatroom_id = chatroom.id
            preference_key = build_run_shell_timeout_preference_key(command, ".")
            save_wait_forever_preference(
                db,
                tool_name="run_shell",
                preference_key=preference_key,
                command_preview=f"{command} @ .",
                chatroom_id=chatroom_id,
            )
        finally:
            db.close()

        progress_updates = []

        async def progress_callback(payload):
            progress_updates.append(payload)

        result = await registry.execute(
            "run_shell",
            command=command,
            timeout_seconds=1,
            chatroom_id=chatroom_id,
            __catown_approval_granted=True,
            progress_callback=progress_callback,
        )

        assert result["success"] is True
        assert "redirected line 1" in result["result"]
        assert progress_updates
        assert any("redirected line 1" in str(update.get("tail_output") or "") for update in progress_updates)

    @pytest.mark.asyncio
    async def test_run_shell_background_running_returns_tracked_process_metadata(self, fresh_db, tmp_path):
        fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        result = await registry.execute(
            "run_shell",
            command=_python_command("import time; time.sleep(2); print('done')"),
            timeout_seconds=1,
            __catown_approval_granted=True,
        )

        assert result["success"] is False
        assert result["status"] == "background_running"
        tracked_process = result.get("metadata", {}).get("tracked_process")
        assert isinstance(tracked_process, dict)
        assert tracked_process.get("token")

    def test_tracked_run_shell_bad_state_file_is_ignored(self, tmp_path, monkeypatch):
        from services import run_shell_processes

        monkeypatch.setattr(run_shell_processes.settings, "STATE_DIR", tmp_path / "state")
        state_dir = run_shell_processes.run_shell_process_state_dir()
        (state_dir / "bad-token.json").write_text("", encoding="utf-8")

        assert run_shell_processes.load_tracked_run_shell_handle({"token": "bad-token"}) is None

    def test_tracked_run_shell_log_is_bounded(self, tmp_path):
        from services.run_shell_processes import _append_bounded_log

        log_path = tmp_path / "tracked.log"

        for index in range(10):
            _append_bounded_log(log_path, f"chunk-{index}-".encode() * 40, max_bytes=256)

        payload = log_path.read_bytes()
        assert len(payload) <= 256 + (len("chunk-9-") * 40)
        assert b"chunk-9-" in payload
        assert b"chunk-0-" not in payload

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
        db = fresh_db.SessionLocal()
        try:
            audit_row = (
                db.query(fresh_db.ApprovalAuditLog)
                .filter(fresh_db.ApprovalAuditLog.event_kind == "authorization_rule_matched")
                .filter(fresh_db.ApprovalAuditLog.decision == "approve")
                .filter(fresh_db.ApprovalAuditLog.tool_name == "run_shell")
                .first()
            )
            assert audit_row is not None
            assert audit_row.chatroom_id == chatroom_id
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_saved_allow_rule_matches_project_workspace_cwd_alias(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        db = fresh_db.SessionLocal()
        try:
            project = fresh_db.Project(name="Allow rule workspace alias", workspace_path=str(tmp_path))
            db.add(project)
            db.commit()
            db.refresh(project)
            chatroom = fresh_db.Chatroom(project_id=project.id, title="Allow rule workspace alias chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="project",
                matcher_type=AUTH_MATCHER_COMMAND_FINGERPRINT,
                matcher_value=build_run_shell_command_matcher_value("touch alias-created.txt", str(tmp_path)),
                decision_kind=AUTH_DECISION_ALLOW,
                project_id=project.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="granted",
                command_preview=f"touch alias-created.txt @ {tmp_path}",
            )
            project_id = project.id
            chatroom_id = chatroom.id
        finally:
            db.close()

        result = await registry.execute(
            "run_shell",
            command="touch alias-created.txt",
            cwd=".",
            project_id=project_id,
            chatroom_id=chatroom_id,
            __catown_workspace_path=str(tmp_path),
        )

        assert result["success"] is True
        assert (tmp_path / "alias-created.txt").exists()

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

    @pytest.mark.asyncio
    async def test_saved_shell_bin_allow_rule_bypasses_run_shell_approval(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))
        command = _mutating_probe_command()
        parsed_shell_bins = shell_bins_for_command(command)
        assert parsed_shell_bins

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Shell bin allow chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="chatroom",
                matcher_type=AUTH_MATCHER_SHELL_BIN,
                matcher_value=build_shell_bin_matcher_value(parsed_shell_bins[0]),
                decision_kind=AUTH_DECISION_ALLOW,
                chatroom_id=chatroom.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="granted",
                command_preview=parsed_shell_bins[0],
            )
            chatroom_id = chatroom.id
        finally:
            db.close()

        result = await registry.execute(
            "run_shell",
            command=command,
            chatroom_id=chatroom_id,
        )

        assert result["success"] is True
        if os.name == "nt":
            assert (tmp_path / "created-dir").is_dir()
        else:
            assert (tmp_path / "created.txt").is_file()

    @pytest.mark.asyncio
    async def test_saved_all_tools_allow_rule_bypasses_run_shell_approval(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="All tools allow chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="chatroom",
                matcher_type=AUTH_MATCHER_ALL_TOOLS,
                matcher_value=build_all_tools_matcher_value(),
                decision_kind=AUTH_DECISION_ALLOW,
                chatroom_id=chatroom.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="granted",
                command_preview="all tools",
            )
            chatroom_id = chatroom.id
        finally:
            db.close()

        result = await registry.execute(
            "run_shell",
            command="touch all-tools-created.txt",
            chatroom_id=chatroom_id,
        )

        assert result["success"] is True
        assert (tmp_path / "all-tools-created.txt").exists()

    @pytest.mark.asyncio
    async def test_require_approval_rule_overrides_broader_allow(self, fresh_db, tmp_path):
        registry = ToolRegistry()
        registry.register(RunShellTool(workspace=str(tmp_path)))

        db = fresh_db.SessionLocal()
        try:
            chatroom = fresh_db.Chatroom(title="Require approval chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="chatroom",
                matcher_type=AUTH_MATCHER_ALL_TOOLS,
                matcher_value=build_all_tools_matcher_value(),
                decision_kind=AUTH_DECISION_ALLOW,
                chatroom_id=chatroom.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="granted",
                command_preview="all tools",
            )
            upsert_authorization_rule(
                db,
                tool_name="run_shell",
                scope="chatroom",
                matcher_type=AUTH_MATCHER_SHELL_BIN,
                matcher_value=build_shell_bin_matcher_value("python3"),
                decision_kind=AUTH_DECISION_REQUIRE_APPROVAL,
                chatroom_id=chatroom.id,
                preference_kind=AUTH_PREFERENCE_KIND,
                preference_value="approval_required",
                command_preview="python3",
            )
            chatroom_id = chatroom.id
        finally:
            db.close()

        result = await registry.execute(
            "run_shell",
            command="python3 -c 'print(123)'",
            chatroom_id=chatroom_id,
        )

        assert result["success"] is False
        assert result["status"] == "approval_blocked"
        assert "requires approval" in result["result"]

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


class TestKnowledgeGraphTool:
    @pytest.mark.asyncio
    async def test_check_update_uses_local_mtimes(self, tmp_path):
        from tools.file_operations import reset_active_workspace, set_active_workspace

        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        graph_json = graph_dir / "graph.json"
        graph_json.write_text("{}", encoding="utf-8")
        source = tmp_path / "backend" / "app.py"
        source.parent.mkdir()
        source.write_text("print('newer')\n", encoding="utf-8")
        os.utime(graph_json, (1000, 1000))
        os.utime(source, (2000, 2000))

        token = set_active_workspace(str(tmp_path))
        try:
            result = await KnowledgeGraphTool().execute(action="check_update")
        finally:
            reset_active_workspace(token)

        assert result["success"] is True
        assert result["status"] == "needs_update"
        assert result["metadata"]["needs_update"] is True
        assert result["metadata"]["latest_source_path"] == "backend/app.py"


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
            with patch.object(tool, "_run_subprocess") as mocked_run:
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
