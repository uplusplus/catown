# -*- coding: utf-8 -*-
"""
Command executor for chat input commands.

Read-only commands that query system state without modifying anything.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("catown.commands")

# Command definitions loaded from configs/commands.json
_COMMANDS: List[Dict[str, Any]] = []
_COMMAND_MAP: Dict[str, Dict[str, Any]] = {}
_ALIAS_MAP: Dict[str, str] = {}


def _config_path() -> Path:
    backend_dir = Path(__file__).resolve().parent.parent
    return backend_dir / "configs" / "commands.json"


def load_commands() -> List[Dict[str, Any]]:
    """Load command definitions from configs/commands.json."""
    global _COMMANDS, _COMMAND_MAP, _ALIAS_MAP
    path = _config_path()
    if not path.exists():
        logger.warning(f"Commands config not found: {path}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _COMMANDS = data.get("commands", [])
    _COMMAND_MAP = {}
    _ALIAS_MAP = {}

    for cmd in _COMMANDS:
        name = cmd["command"]
        _COMMAND_MAP[name] = cmd
        for alias in cmd.get("aliases", []):
            _ALIAS_MAP[alias] = name

    return _COMMANDS


def get_command_definitions() -> List[Dict[str, Any]]:
    """Get all command definitions for autocomplete."""
    if not _COMMANDS:
        load_commands()
    return _COMMANDS


def resolve_command(input_text: str) -> Optional[Dict[str, Any]]:
    """Resolve user input to a command definition. Returns None if not a command."""
    if not _COMMANDS:
        load_commands()

    text = input_text.strip()
    if not text.startswith("/"):
        return None

    # Try exact match first
    if text in _COMMAND_MAP:
        return _COMMAND_MAP[text]

    # Try alias match
    parts = text.split(None, 1)
    if parts and parts[0] in _ALIAS_MAP:
        cmd_name = _ALIAS_MAP[parts[0]]
        cmd = _COMMAND_MAP.get(cmd_name)
        if cmd:
            return cmd

    # Try prefix match (e.g., "/skills list" matches "/skills list")
    for cmd_name, cmd in _COMMAND_MAP.items():
        if text.startswith(cmd_name):
            return cmd

    return None


def parse_command(input_text: str) -> tuple[str, Optional[str]]:
    """
    Parse command input into (command_name, args).

    Returns ("", None) if not a command.
    """
    text = input_text.strip()
    if not text.startswith("/"):
        return ("", None)

    parts = text.split(None, 1)
    if not parts:
        return ("", None)

    cmd_part = parts[0]
    args = parts[1] if len(parts) > 1 else None

    # Resolve alias
    if cmd_part in _ALIAS_MAP:
        cmd_part = _ALIAS_MAP[cmd_part]

    return (cmd_part, args)


async def execute_command(
    command_text: str,
    db: Session,
    chatroom_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Execute a read-only command and return the result.

    Returns:
        {
            "success": bool,
            "command": str,
            "category": str,
            "title": str,
            "content": str,  # markdown
            "error": str | None
        }
    """
    if not _COMMANDS:
        load_commands()

    cmd_name, args = parse_command(command_text)
    if not cmd_name:
        return {
            "success": False,
            "command": command_text,
            "category": "错误",
            "title": "未知指令",
            "content": f"无法识别的指令：`{command_text}`\n\n输入 `/help` 查看所有可用指令。",
            "error": "unknown_command",
        }

    cmd_def = _COMMAND_MAP.get(cmd_name)
    if not cmd_def:
        return {
            "success": False,
            "command": cmd_name,
            "category": "错误",
            "title": "未知指令",
            "content": f"指令 `{cmd_name}` 不存在。\n\n输入 `/help` 查看所有可用指令。",
            "error": "unknown_command",
        }

    try:
        result = await _dispatch_command(cmd_name, args, db, chatroom_id, project_id)
        return {
            "success": True,
            "command": cmd_name,
            "category": cmd_def.get("category", ""),
            "title": result.get("title", cmd_name),
            "content": result.get("content", ""),
            "error": None,
        }
    except Exception as e:
        logger.error(f"Command execution failed: {cmd_name} - {e}")
        return {
            "success": False,
            "command": cmd_name,
            "category": cmd_def.get("category", ""),
            "title": "执行失败",
            "content": f"指令 `{cmd_name}` 执行出错：{e}",
            "error": str(e),
        }


async def _dispatch_command(
    cmd_name: str,
    args: Optional[str],
    db: Session,
    chatroom_id: Optional[int],
    project_id: Optional[int],
) -> Dict[str, str]:
    """Dispatch command to handler. Returns {"title": str, "content": str}."""

    if cmd_name == "/help":
        return _cmd_help()
    elif cmd_name == "/skills list":
        return await _cmd_skills_list()
    elif cmd_name == "/skills info":
        return await _cmd_skills_info(args)
    elif cmd_name == "/tools list":
        return await _cmd_tools_list()
    elif cmd_name == "/tools info":
        return await _cmd_tools_info(args)
    elif cmd_name == "/agents list":
        return await _cmd_agents_list(db)
    elif cmd_name == "/agents info":
        return await _cmd_agents_info(args, db)
    elif cmd_name == "/config get":
        return await _cmd_config_get()
    elif cmd_name == "/pipeline status":
        return await _cmd_pipeline_status(db, project_id)
    else:
        return {"title": "未实现", "content": f"指令 `{cmd_name}` 尚未实现。"}


# ────────────────────────── Command Handlers ──────────────────────────


def _cmd_help() -> Dict[str, str]:
    lines = ["## 可用指令\n"]
    by_category: Dict[str, list] = {}
    for cmd in _COMMANDS:
        cat = cmd.get("category", "其他")
        by_category.setdefault(cat, []).append(cmd)

    for cat, cmds in by_category.items():
        lines.append(f"### {cat}\n")
        for cmd in cmds:
            name = cmd["command"]
            aliases = cmd.get("aliases", [])
            desc = cmd.get("description", "")
            args = cmd.get("args", "")
            alias_str = f" ({', '.join(aliases)})" if aliases else ""
            args_str = f" `{args}`" if args else ""
            lines.append(f"- `{name}{args_str}`{alias_str} — {desc}")
        lines.append("")

    return {"title": "帮助", "content": "\n".join(lines)}


async def _cmd_skills_list() -> Dict[str, str]:
    try:
        from skills import skill_registry
        skills = skill_registry.list_skills()
        if not skills:
            return {"title": "Skills 列表", "content": "当前没有已安装的 Skills。"}

        lines = ["## 已安装 Skills\n"]
        for s in skills:
            name = s.get("name", "unknown")
            desc = s.get("description", "")[:80]
            lines.append(f"- **{name}** — {desc}")
        return {"title": "Skills 列表", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "Skills 列表", "content": f"获取 Skills 列表失败：{e}"}


async def _cmd_skills_info(args: Optional[str]) -> Dict[str, str]:
    if not args:
        return {"title": "Skill 详情", "content": "用法：`/skills info <name>`"}

    try:
        from skills import skill_registry
        skill = skill_registry.get_skill(args.strip())
        if not skill:
            return {"title": "Skill 详情", "content": f"未找到 Skill：`{args.strip()}`"}

        lines = [f"## {skill.get('name', args)}\n"]
        lines.append(f"**描述**：{skill.get('description', '无')}")
        if skill.get("version"):
            lines.append(f"**版本**：{skill['version']}")
        if skill.get("tools"):
            lines.append(f"**工具**：{', '.join(skill['tools'])}")
        return {"title": f"Skill: {args}", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "Skill 详情", "content": f"获取失败：{e}"}


async def _cmd_tools_list() -> Dict[str, str]:
    try:
        from tools import tool_registry
        tools = tool_registry.list_tools()
        if not tools:
            return {"title": "工具列表", "content": "当前没有可用工具。"}

        lines = ["## 可用工具\n"]
        for t in tools:
            name = t.get("name", "unknown")
            desc = t.get("description", "")[:80]
            lines.append(f"- `{name}` — {desc}")
        return {"title": "工具列表", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "工具列表", "content": f"获取工具列表失败：{e}"}


async def _cmd_tools_info(args: Optional[str]) -> Dict[str, str]:
    if not args:
        return {"title": "工具详情", "content": "用法：`/tools info <name>`"}

    try:
        from tools import tool_registry
        tool = tool_registry.get_tool(args.strip())
        if not tool:
            return {"title": "工具详情", "content": f"未找到工具：`{args.strip()}`"}

        lines = [f"## {tool.get('name', args)}\n"]
        lines.append(f"**描述**：{tool.get('description', '无')}")
        if tool.get("parameters"):
            lines.append(f"**参数**：\n```json\n{json.dumps(tool['parameters'], indent=2, ensure_ascii=False)}\n```")
        return {"title": f"工具: {args}", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "工具详情", "content": f"获取失败：{e}"}


async def _cmd_agents_list(db: Session) -> Dict[str, str]:
    try:
        from models.database import Agent
        agents = db.query(Agent).filter(Agent.is_active == True).all()
        if not agents:
            return {"title": "Agent 列表", "content": "当前没有活跃的 Agent。"}

        lines = ["## Agent 角色\n"]
        for a in agents:
            lines.append(f"- **{a.name}** ({a.role}) — {'活跃' if a.is_active else '停用'}")
        return {"title": "Agent 列表", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "Agent 列表", "content": f"获取失败：{e}"}


async def _cmd_agents_info(args: Optional[str], db: Session) -> Dict[str, str]:
    if not args:
        return {"title": "Agent 详情", "content": "用法：`/agents info <name>`"}

    try:
        from models.database import Agent
        agent = db.query(Agent).filter(
            (Agent.name.ilike(f"%{args.strip()}%")) | (Agent.agent_type == args.strip())
        ).first()
        if not agent:
            return {"title": "Agent 详情", "content": f"未找到 Agent：`{args.strip()}`"}

        lines = [f"## {agent.name}\n"]
        lines.append(f"**角色**：{agent.role}")
        lines.append(f"**类型**：{agent.agent_type or '默认'}")
        lines.append(f"**状态**：{'活跃' if agent.is_active else '停用'}")
        if agent.tools:
            try:
                tools = json.loads(agent.tools)
                if tools:
                    lines.append(f"**工具**：{', '.join(tools[:10])}")
            except (json.JSONDecodeError, TypeError):
                pass
        return {"title": f"Agent: {agent.name}", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "Agent 详情", "content": f"获取失败：{e}"}


async def _cmd_config_get() -> Dict[str, str]:
    try:
        from config import settings
        lines = ["## 全局配置\n"]
        lines.append(f"**数据库**：`{settings.SQLALCHEMY_DATABASE_URL[:50]}...`" if len(str(settings.SQLALCHEMY_DATABASE_URL)) > 50 else f"**数据库**：`{settings.SQLALCHEMY_DATABASE_URL}`")
        lines.append(f"**日志级别**：{settings.LOG_LEVEL}")
        lines.append(f"**状态目录**：`{settings.STATE_DIR}`")
        return {"title": "全局配置", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "全局配置", "content": f"获取失败：{e}"}


async def _cmd_pipeline_status(db: Session, project_id: Optional[int]) -> Dict[str, str]:
    try:
        from models.database import Pipeline, PipelineRun
        if project_id:
            pipeline = db.query(Pipeline).filter(Pipeline.project_id == project_id).first()
            if not pipeline:
                return {"title": "Pipeline 状态", "content": "当前项目没有 Pipeline。"}

            latest_run = db.query(PipelineRun).filter(
                PipelineRun.pipeline_id == pipeline.id
            ).order_by(PipelineRun.run_number.desc()).first()

            lines = [f"## Pipeline 状态\n"]
            lines.append(f"**名称**：{pipeline.pipeline_name}")
            lines.append(f"**状态**：{pipeline.status}")
            lines.append(f"**当前阶段**：{pipeline.current_stage_index}")
            if latest_run:
                lines.append(f"**最近运行**：#{latest_run.run_number} ({latest_run.status})")
            return {"title": "Pipeline 状态", "content": "\n".join(lines)}
        else:
            pipelines = db.query(Pipeline).all()
            if not pipelines:
                return {"title": "Pipeline 状态", "content": "系统中没有 Pipeline。"}

            lines = ["## Pipeline 状态\n"]
            for p in pipelines:
                lines.append(f"- **{p.pipeline_name}** — {p.status} (阶段 {p.current_stage_index})")
            return {"title": "Pipeline 状态", "content": "\n".join(lines)}
    except Exception as e:
        return {"title": "Pipeline 状态", "content": f"获取失败：{e}"}
