# -*- coding: utf-8 -*-
"""Tool capability profile resolution for prompt/tool-schema budgeting."""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

from config import settings

logger = logging.getLogger("catown.tool_capability_profiles")


@dataclass(frozen=True)
class ToolCapabilitySelection:
    """Result of applying a capability profile to an agent tool whitelist."""

    profile_name: str
    original_tools: list[str]
    active_tools: list[str]
    excluded_tools: list[str]
    activated_groups: list[str]
    mode: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "original_tool_count": len(self.original_tools),
            "active_tool_count": len(self.active_tools),
            "excluded_tool_count": len(self.excluded_tools),
            "active_tools": list(self.active_tools),
            "excluded_tools": list(self.excluded_tools),
            "activated_groups": list(self.activated_groups),
            "mode": self.mode,
        }


_DEFAULT_TOOL_CAPABILITY_PROFILES: dict[str, dict[str, Any]] = {
    "interactive_chat": {
        "always_tools": [
            "read_file",
            "write_file",
            "list_files",
            "search_files",
            "run_shell",
            "execute_code",
            "consult_agent",
            "delegate_task",
            "check_task_status",
        ],
        "conditional_groups": {
            "collaboration": {
                "tools": ["list_agents", "list_collaborators"],
                "keywords": [
                    "agent",
                    "agents",
                    "collaborator",
                    "collaborators",
                    "team",
                    "teammate",
                    "sidecar",
                    "delegate",
                    "handoff",
                    "invite",
                    "who can help",
                ],
            },
            "browser": {
                "tools": ["browser", "screenshot", "screenshot_compare", "chrome_devtools"],
                "keywords": [
                    "browser",
                    "browse",
                    "click",
                    "navigate",
                    "web page",
                    "screenshot",
                    "playwright",
                    "chrome",
                    "devtools",
                ],
            },
            "web": {
                "tools": ["web_search", "web_fetch"],
                "keywords": ["web search", "search web", "internet", "online", "latest", "recent", "url", "http://", "https://"],
            },
            "github": {
                "tools": ["github_manager"],
                "keywords": ["github", "pull request", "pr ", "branch", "commit", "release", "issue"],
            },
            "memory": {
                "tools": ["retrieve_memory", "save_memory", "knowledge_graph"],
                "keywords": ["memory", "remember", "knowledge graph", "graphify"],
            },
            "skills": {
                "tools": ["skill_manager"],
                "keywords": ["skill", "marketplace", "install skill", "enable skill"],
            },
            "document": {
                "tools": ["analyze_document", "analyze_image"],
                "keywords": ["pdf", "document", "docx", "image", "screenshot", "attachment", "attached file", "image_url"],
            },
        },
    },
    "code_debug": {
        "always_tools": [
            "read_file",
            "write_file",
            "list_files",
            "search_files",
            "run_shell",
            "execute_code",
            "consult_agent",
            "delegate_task",
            "check_task_status",
        ],
        "conditional_groups": {
            "browser": {
                "tools": ["browser", "screenshot", "screenshot_compare", "chrome_devtools"],
                "keywords": ["browser", "click", "navigate", "screenshot", "playwright", "chrome", "devtools", "visual"],
            },
            "web": {
                "tools": ["web_search", "web_fetch"],
                "keywords": ["web search", "search web", "internet", "online", "latest", "recent", "url", "http://", "https://"],
            },
            "github": {
                "tools": ["github_manager"],
                "keywords": ["github", "pull request", "pr ", "branch", "commit", "release", "issue"],
            },
            "memory": {
                "tools": ["retrieve_memory", "save_memory", "knowledge_graph"],
                "keywords": ["memory", "remember", "knowledge graph", "graphify"],
            },
            "skills": {
                "tools": ["skill_manager"],
                "keywords": ["skill", "marketplace", "install skill", "enable skill"],
            },
            "document": {
                "tools": ["analyze_document", "analyze_image"],
                "keywords": ["pdf", "document", "docx", "image", "attachment", "attached file", "image_url"],
            },
        },
    },
    "browser": {
        "always_tools": [
            "browser",
            "screenshot",
            "screenshot_compare",
            "chrome_devtools",
            "web_search",
            "web_fetch",
            "read_file",
            "write_file",
            "list_files",
            "search_files",
            "run_shell",
        ],
        "conditional_groups": {},
    },
    "document": {
        "always_tools": [
            "read_file",
            "write_file",
            "list_files",
            "search_files",
            "analyze_document",
            "analyze_image",
            "web_search",
            "web_fetch",
            "consult_agent",
        ],
        "conditional_groups": {},
    },
    "orchestration": {
        "always_tools": [
            "delegate_task",
            "consult_agent",
            "check_task_status",
            "list_agents",
            "list_collaborators",
            "broadcast_message",
            "send_direct_message",
            "send_message",
            "invite_agent",
        ],
        "conditional_groups": {},
    },
    "consult": {
        "always_tools": [
            "read_file",
            "list_files",
            "search_files",
            "retrieve_memory",
            "consult_agent",
        ],
        "conditional_groups": {
            "web": {
                "tools": ["web_search", "web_fetch"],
                "keywords": ["web search", "search web", "internet", "online", "latest", "recent", "url", "http://", "https://"],
            },
            "document": {
                "tools": ["analyze_document", "analyze_image"],
                "keywords": ["pdf", "document", "docx", "image", "attachment", "attached file", "image_url"],
            },
        },
    },
}

_AGENT_TYPE_PROFILE_HINTS = {
    "developer": "code_debug",
    "tester": "code_debug",
}

_SELECTOR_PROFILE_HINTS = {
    "consult_agent": "consult",
    "fallback_chat": "interactive_chat",
    "chat_interactive": "interactive_chat",
}

_TOOL_CAPABILITY_PROFILE_FIELDS = {"always_tools", "conditional_groups", "include_all"}


def default_tool_capability_profiles() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(_DEFAULT_TOOL_CAPABILITY_PROFILES)


def effective_tool_capability_profiles(config_data: Optional[dict[str, Any]] = None) -> dict[str, dict[str, Any]]:
    profiles = default_tool_capability_profiles()
    payload = config_data if config_data is not None else _load_agent_config_data()
    context_data = payload.get("context") if isinstance(payload, dict) else None
    overrides = (
        context_data.get("tool_capability_profiles")
        if isinstance(context_data, dict) and isinstance(context_data.get("tool_capability_profiles"), dict)
        else {}
    )
    for raw_name, raw_profile in overrides.items():
        if not isinstance(raw_profile, dict):
            continue
        profile_name = str(raw_name or "").strip()
        if not profile_name:
            continue
        base = copy.deepcopy(profiles.get(profile_name) or {})
        for key, value in raw_profile.items():
            if key in _TOOL_CAPABILITY_PROFILE_FIELDS:
                base[key] = value
        profiles[profile_name] = _normalize_tool_capability_profile(base)
    return profiles


def resolve_tool_capability_profile_name(
    *,
    explicit_profile: Any = None,
    selector_profile: str = "",
    agent: Any = None,
    user_message: Any = "",
    mode: str = "",
) -> str:
    profiles = effective_tool_capability_profiles()
    explicit = str(explicit_profile or "").strip()
    if explicit in profiles:
        return explicit

    text = tool_capability_context_text(user_message).lower()
    if _matches_any(text, ["browser", "browse", "click", "navigate", "playwright", "chrome", "devtools"]):
        return "browser"
    if _matches_any(text, ["pdf", "document", "docx", "image_url", "attachment", "attached file"]):
        return "document"

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode in profiles:
        return normalized_mode

    normalized_selector = str(selector_profile or "").strip()
    if normalized_selector in _SELECTOR_PROFILE_HINTS:
        return _SELECTOR_PROFILE_HINTS[normalized_selector]

    agent_type = _agent_type(agent)
    return _AGENT_TYPE_PROFILE_HINTS.get(agent_type, "interactive_chat")


def select_tools_for_capability_profile(
    tool_names: list[str],
    *,
    profile_name: str,
    user_message: Any = "",
    mode: str = "",
) -> ToolCapabilitySelection:
    original_tools = _dedupe_tool_names(tool_names)
    profiles = effective_tool_capability_profiles()
    profile = profiles.get(profile_name) or profiles["interactive_chat"]
    if bool(profile.get("include_all")):
        return ToolCapabilitySelection(
            profile_name=profile_name,
            original_tools=original_tools,
            active_tools=original_tools,
            excluded_tools=[],
            activated_groups=[],
            mode=str(mode or "").strip() or "default",
        )

    active_set = set(_dedupe_tool_names(profile.get("always_tools") if isinstance(profile.get("always_tools"), list) else []))
    activated_groups: list[str] = []
    text = tool_capability_context_text(user_message).lower()
    conditional_groups = profile.get("conditional_groups") if isinstance(profile.get("conditional_groups"), dict) else {}
    for group_name, raw_group in conditional_groups.items():
        if not isinstance(raw_group, dict):
            continue
        keywords = [str(item or "").strip().lower() for item in raw_group.get("keywords") or [] if str(item or "").strip()]
        if keywords and not _matches_any(text, keywords):
            continue
        group_tools = _dedupe_tool_names(raw_group.get("tools") if isinstance(raw_group.get("tools"), list) else [])
        if not group_tools:
            continue
        active_set.update(group_tools)
        activated_groups.append(str(group_name or "").strip() or "group")

    active_tools = [tool_name for tool_name in original_tools if tool_name in active_set]
    excluded_tools = [tool_name for tool_name in original_tools if tool_name not in set(active_tools)]
    return ToolCapabilitySelection(
        profile_name=profile_name,
        original_tools=original_tools,
        active_tools=active_tools,
        excluded_tools=excluded_tools,
        activated_groups=activated_groups,
        mode=str(mode or "").strip() or "default",
    )


def tool_capability_context_text(user_message: Any) -> str:
    if isinstance(user_message, str):
        return user_message
    if isinstance(user_message, list):
        parts: list[str] = []
        for item in user_message:
            if not isinstance(item, Mapping):
                parts.append(str(item))
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type:
                parts.append(item_type)
            text = item.get("text")
            if text:
                parts.append(str(text))
            image_url = item.get("image_url")
            if image_url:
                parts.append("image image_url attachment")
        return "\n".join(parts)
    return str(user_message or "")


@lru_cache(maxsize=8)
def _load_agent_config_snapshot(config_path: str, modified_ns: int) -> dict[str, Any]:
    with open(config_path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _load_agent_config_data() -> dict[str, Any]:
    config_path = Path(settings.AGENT_CONFIG_FILE)
    if not config_path.exists():
        return {}
    try:
        stat = config_path.stat()
        return _load_agent_config_snapshot(str(config_path.resolve()), stat.st_mtime_ns)
    except Exception as exc:
        logger.warning(f"Failed to load agent config for tool capability profiles: {exc}")
        return {}


def _normalize_tool_capability_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    normalized["always_tools"] = _dedupe_tool_names(profile.get("always_tools") if isinstance(profile.get("always_tools"), list) else [])
    normalized["include_all"] = bool(profile.get("include_all"))
    normalized_groups: dict[str, dict[str, list[str]]] = {}
    conditional_groups = profile.get("conditional_groups") if isinstance(profile.get("conditional_groups"), dict) else {}
    for raw_name, raw_group in conditional_groups.items():
        if not isinstance(raw_group, Mapping):
            continue
        group_name = str(raw_name or "").strip()
        if not group_name:
            continue
        tools = _dedupe_tool_names(raw_group.get("tools") if isinstance(raw_group.get("tools"), list) else [])
        keywords = [str(item or "").strip() for item in raw_group.get("keywords") or [] if str(item or "").strip()]
        if tools:
            normalized_groups[group_name] = {"tools": tools, "keywords": keywords}
    normalized["conditional_groups"] = normalized_groups
    return normalized


def _dedupe_tool_names(tool_names: Any) -> list[str]:
    if not isinstance(tool_names, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in tool_names:
        tool_name = str(raw_name or "").strip()
        if not tool_name or tool_name in seen:
            continue
        normalized.append(tool_name)
        seen.add(tool_name)
    return normalized


def _agent_type(agent: Any) -> str:
    for attr in ("agent_type", "type", "name"):
        value = getattr(agent, attr, None)
        if value:
            return str(value).strip().lower()
    return ""


def _matches_any(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    for keyword in keywords:
        if not keyword:
            continue
        if re.search(r"\w$", keyword):
            pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
            if re.search(pattern, text):
                return True
            continue
        if keyword in text:
            return True
    return False
