"""Utilities for stable agent identifiers vs. display names."""
from __future__ import annotations

from typing import Any, Iterable, Optional


DEFAULT_AGENT_TYPE = "valet"
LEGACY_AGENT_TYPE_ALIASES = {
    "assistant": DEFAULT_AGENT_TYPE,
    "bot": DEFAULT_AGENT_TYPE,
    "arch": "architect",
    "dev": "developer",
    "qa": "tester",
    "rel": "release",
}

LEGACY_DEFAULT_AGENT_NAMES = {
    "architect": {"arch"},
    "developer": {"dev"},
    "tester": {"qa"},
    "release": {"rel"},
    DEFAULT_AGENT_TYPE: {"assistant", "bot"},
}


def normalize_agent_type(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_AGENT_TYPE
    normalized = raw.lower()
    return LEGACY_AGENT_TYPE_ALIASES.get(normalized, normalized)


def default_agent_name(agent_type: Optional[str]) -> str:
    normalized = normalize_agent_type(agent_type)
    return f"{normalized[:1].upper()}{normalized[1:]}" if normalized else "Agent"


def legacy_default_agent_names(agent_type: Optional[str]) -> set[str]:
    normalized = normalize_agent_type(agent_type)
    values = {default_agent_name(normalized).lower(), normalized}
    values.update(LEGACY_DEFAULT_AGENT_NAMES.get(normalized, set()))
    return values


def is_legacy_default_agent_name(name: Optional[str], agent_type: Optional[str]) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return True
    return lowered in legacy_default_agent_names(agent_type)


def agent_type_of(agent: Any) -> str:
    if agent is None:
        return DEFAULT_AGENT_TYPE

    explicit_type = getattr(agent, "agent_type", None) or getattr(agent, "type", None)
    if isinstance(explicit_type, str) and explicit_type.strip():
        return normalize_agent_type(explicit_type)

    name = getattr(agent, "name", None)
    if isinstance(name, str) and name.strip():
        return normalize_agent_type(name)

    return DEFAULT_AGENT_TYPE


def agent_name_of(agent: Any) -> str:
    if agent is None:
        return default_agent_name(DEFAULT_AGENT_TYPE)

    name = getattr(agent, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()

    return default_agent_name(agent_type_of(agent))


def agent_matches_type(agent: Any, agent_type: Optional[str]) -> bool:
    target = normalize_agent_type(agent_type)
    if agent is None:
        return False

    explicit_type = getattr(agent, "agent_type", None) or getattr(agent, "type", None)
    if isinstance(explicit_type, str) and explicit_type.strip():
        return normalize_agent_type(explicit_type) == target

    name = getattr(agent, "name", None)
    if isinstance(name, str) and name.strip():
        lowered = name.strip().lower()
        if normalize_agent_type(lowered) == target:
            return True
        if lowered == default_agent_name(target).lower():
            return True

    return False


def find_agent_by_type(agents: Iterable[Any], agent_type: Optional[str]) -> Any | None:
    target = normalize_agent_type(agent_type)
    for agent in agents:
        if agent_matches_type(agent, target):
            return agent
    return None
