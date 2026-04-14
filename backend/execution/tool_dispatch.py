# -*- coding: utf-8 -*-
"""Shared legacy tool registry + dispatch helpers."""
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from execution.workspace_guard import ensure_workspace_path, is_catown_protected, validate_workspace_target

logger = logging.getLogger("catown.pipeline.engine")

ToolHandler = Callable[[Path, Any], str]
AsyncSendMessageHandler = Callable[[str, Any, dict[str, str], Any, int | None], Awaitable[str]]

TOOLS_DIR = Path(__file__).parent.parent / "tools"
AGENT_TOOLS: dict[str, list[str]] = {}


def load_agent_tools() -> None:
    """Load per-agent tool allowlists from agents.json."""
    global AGENT_TOOLS
    config_file = os.environ.get("AGENT_CONFIG_FILE", "configs/agents.json")
    if not os.path.exists(config_file):
        return
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, cfg in data.get("agents", {}).items():
            AGENT_TOOLS[name] = cfg.get("tools", [])
    except Exception as exc:
        logger.warning(f"Failed to load agent tools: {exc}")


def get_workspace(run) -> Path:
    return ensure_workspace_path(run.workspace_path, run.id)


def validate_path(workspace: Path, target: Path, allow_catown: bool = False) -> str | None:
    return validate_workspace_target(workspace, target, allow_catown=allow_catown)


def tool_read_file(workspace: Path, file_path: str) -> str:
    target = workspace / file_path
    err = validate_path(workspace, target)
    if err:
        return err
    if not target.exists():
        return f"Error: file not found: {file_path}"
    if not target.is_file():
        return f"Error: not a file: {file_path}"
    return target.read_text(encoding="utf-8", errors="replace")


def tool_write_file(workspace: Path, file_path: str, content: str) -> str:
    target = workspace / file_path
    err = validate_path(workspace, target)
    if err:
        return err
    parent_err = validate_path(workspace, target.parent)
    if parent_err:
        return parent_err
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {file_path} ({len(content)} chars)"


def tool_list_files(workspace: Path, dir_path: str = ".") -> str:
    target = workspace / dir_path
    err = validate_path(workspace, target)
    if err:
        return err
    if not target.exists():
        return f"Error: directory not found: {dir_path}"
    if not target.is_dir():
        return f"Error: not a directory: {dir_path}"
    entries = []
    for path in sorted(target.iterdir()):
        if is_catown_protected(workspace, path.resolve()):
            continue
        rel = path.relative_to(workspace)
        entries.append(f"{'[DIR]' if path.is_dir() else '[FILE]'} {rel}")
    return "\n".join(entries) or "(empty)"


def tool_execute_code(workspace: Path, code: str, language: str = "python") -> str:
    if language != "python":
        return f"Error: unsupported language: {language}"
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=str(workspace)) as file_obj:
        file_obj.write(code)
        tmp_path = file_obj.name
    try:
        err = validate_path(workspace, Path(tmp_path))
        if err:
            os.unlink(tmp_path)
            return err
        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workspace),
        )
        output = ""
        if result.stdout:
            output += f"stdout:\n{result.stdout}"
        if result.stderr:
            output += f"\nstderr:\n{result.stderr}"
        output += f"\nexit_code: {result.returncode}"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "Error: execution timed out (30s)"
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def tool_web_search(workspace: Path, query: str) -> str:
    import urllib.parse
    import urllib.request

    try:
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        })
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        results = []
        if data.get("Abstract"):
            results.append(f"**Summary**: {data['Abstract']}")
            if data.get("AbstractURL"):
                results.append(f"Source: {data['AbstractURL']}")
        if data.get("RelatedTopics"):
            results.append("\n**Related Topics**:")
            for i, topic in enumerate(data["RelatedTopics"][:5]):
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(f"  {i + 1}. {topic['Text'][:200]}")
        if data.get("Answer"):
            results.append(f"\n**Answer**: {data['Answer']}")
        if results:
            return "\n".join(results)
        return f"[Web Search] No instant answer found for '{query}'. Try a more specific query."
    except Exception as exc:
        return f"[Web Search] Error: {str(exc)}"


def tool_send_message_placeholder(workspace: Path, **kwargs) -> str:
    return "Error: send_message should be handled by _handle_send_message"


TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "read_file": {"fn": tool_read_file, "params": ["file_path"], "desc": "Read a file from workspace"},
    "write_file": {"fn": tool_write_file, "params": ["file_path", "content"], "desc": "Write content to a file in workspace"},
    "list_files": {"fn": tool_list_files, "params": ["dir_path?"], "desc": "List files in workspace directory"},
    "execute_code": {"fn": tool_execute_code, "params": ["code", "language?"], "desc": "Execute code (python)"},
    "web_search": {"fn": tool_web_search, "params": ["query"], "desc": "Search the web"},
    "send_message": {"fn": tool_send_message_placeholder, "params": ["to_agent", "content", "message_type?"], "desc": "Send a message to another agent in this pipeline (e.g. ask architect for clarification)"},
}


def build_tools_for_agent(agent_name: str) -> list[dict]:
    allowed = AGENT_TOOLS.get(agent_name, list(TOOL_REGISTRY.keys()))
    tools = []
    for tool_name in allowed:
        info = TOOL_REGISTRY.get(tool_name)
        if not info:
            continue
        properties = {}
        required = []
        for param_name in info["params"]:
            optional = param_name.endswith("?")
            clean_name = param_name.rstrip("?")
            properties[clean_name] = {"type": "string", "description": clean_name}
            if not optional:
                required.append(clean_name)
        tools.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": info["desc"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return tools


async def execute_tool(agent_name: str, run, tool_name: str, arguments: dict[str, str], *, send_message_handler: AsyncSendMessageHandler, db=None, stage_id: int | None = None) -> str:
    if agent_name in AGENT_TOOLS:
        allowed_tools = AGENT_TOOLS[agent_name]
        if tool_name not in allowed_tools:
            logger.warning(f"Agent '{agent_name}' attempted unauthorized tool: '{tool_name}' (allowed: {allowed_tools})")
            return f"Error: Agent '{agent_name}' is not authorized to use tool '{tool_name}'. Allowed tools: {allowed_tools}"
    if tool_name == "send_message":
        return await send_message_handler(agent_name, run, arguments, db, stage_id)
    info = TOOL_REGISTRY.get(tool_name)
    if not info:
        return f"Error: unknown tool: {tool_name}"
    workspace = get_workspace(run)
    try:
        return info["fn"](workspace, **arguments)
    except Exception as exc:
        return f"Tool error ({tool_name}): {exc}"
