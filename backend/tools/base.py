# -*- coding: utf-8 -*-
"""
Tool Base Classes and Registry
"""
import copy
import inspect
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
from abc import ABC, abstractmethod

from services.tool_governance import build_blocked_tool_result, tool_requires_manual_approval


class ToolSchema(BaseModel):
    """JSON Schema for a tool"""
    name: str
    description: str
    parameters: Dict[str, Any]


_DEFAULT_TOOL_POLICY_TEMPLATE: Dict[str, Any] = {
    "risk_level": "low",
    "approval": {
        "kind": "auto",
        "required": False,
        "notes": [],
    },
    "sandbox": {
        "mode": "none",
        "workspace_scope": "none",
        "network_access": "none",
        "notes": [],
    },
    "escalation": {
        "possible": False,
        "hint": None,
        "triggers": [],
    },
    "side_effect_scope": "none",
    "requires_credentials": False,
    "external_targets": [],
}


_DEFAULT_TOOL_POLICY_CATALOG: Dict[str, Dict[str, Any]] = {
    "read_file": {
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_read"},
        "side_effect_scope": "read_only",
    },
    "list_files": {
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_read"},
        "side_effect_scope": "read_only",
    },
    "search_files": {
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_read"},
        "side_effect_scope": "read_only",
    },
    "list_directory": {
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_read"},
        "side_effect_scope": "read_only",
    },
    "write_file": {
        "risk_level": "medium",
        "approval": {
            "kind": "sandbox_dependent",
            "notes": ["Workspace writes are allowed, external paths should be escalated."],
        },
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_write"},
        "escalation": {
            "possible": True,
            "hint": "Escalate when a requested write escapes the active workspace or touches protected metadata.",
            "triggers": ["outside_workspace", "protected_path"],
        },
        "side_effect_scope": "workspace_write",
    },
    "delete_file": {
        "risk_level": "high",
        "approval": {
            "kind": "manual",
            "required": True,
            "notes": ["Deleting files is destructive and should be explicitly approved."],
        },
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_write"},
        "escalation": {
            "possible": True,
            "hint": "Escalate for destructive deletes outside the approved workspace scope.",
            "triggers": ["destructive_delete", "outside_workspace"],
        },
        "side_effect_scope": "workspace_delete",
    },
    "execute_code": {
        "risk_level": "medium",
        "sandbox": {
            "mode": "language_sandbox",
            "workspace_scope": "active_workspace",
            "network_access": "blocked",
            "notes": [
                "Python and Node execution are wrapped by import/module deny-lists.",
                "Execution is time-boxed and output-capped.",
            ],
        },
        "side_effect_scope": "ephemeral_exec",
    },
    "web_search": {
        "sandbox": {"mode": "network_client", "network_access": "enabled"},
        "side_effect_scope": "network_read",
        "external_targets": ["web"],
    },
    "web_fetch": {
        "sandbox": {"mode": "network_client", "network_access": "enabled"},
        "side_effect_scope": "network_read",
        "external_targets": ["web"],
    },
    "browser": {
        "risk_level": "high",
        "approval": {
            "kind": "conditional",
            "notes": ["Navigation is read-mostly, but click/fill/evaluate actions may mutate remote state."],
        },
        "sandbox": {
            "mode": "browser_runtime",
            "workspace_scope": "temp_or_explicit_path",
            "network_access": "enabled",
        },
        "escalation": {
            "possible": True,
            "hint": "Escalate when browser actions may log in, submit forms, or write artifacts to protected paths.",
            "triggers": ["remote_mutation", "protected_output_path"],
        },
        "side_effect_scope": "browser_interaction",
        "external_targets": ["web"],
    },
    "screenshot": {
        "risk_level": "medium",
        "approval": {
            "kind": "conditional",
            "notes": ["Remote capture is read-only, but the tool writes local image artifacts."],
        },
        "sandbox": {
            "mode": "browser_runtime",
            "workspace_scope": "temp_or_explicit_path",
            "network_access": "enabled",
        },
        "escalation": {
            "possible": True,
            "hint": "Escalate when output paths fall outside the approved workspace or require browser setup.",
            "triggers": ["protected_output_path", "browser_dependency"],
        },
        "side_effect_scope": "artifact_capture",
        "external_targets": ["web"],
    },
    "github_manager": {
        "risk_level": "high",
        "approval": {
            "kind": "conditional",
            "notes": ["Read actions are safe, but branch/file/release mutations affect external GitHub state."],
        },
        "sandbox": {
            "mode": "network_and_workspace",
            "workspace_scope": "workspace_write",
            "network_access": "enabled",
        },
        "escalation": {
            "possible": True,
            "hint": "Escalate when cloning repos, mutating GitHub state, or using elevated credentials.",
            "triggers": ["remote_write", "clone_repo", "credential_use"],
        },
        "side_effect_scope": "external_mutation",
        "requires_credentials": True,
        "external_targets": ["github_api", "local_git"],
    },
    "retrieve_memory": {
        "sandbox": {"mode": "database_read", "workspace_scope": "none"},
        "side_effect_scope": "read_only",
    },
    "save_memory": {
        "risk_level": "medium",
        "sandbox": {"mode": "database_write", "workspace_scope": "none"},
        "side_effect_scope": "memory_write",
    },
    "delegate_task": {
        "risk_level": "medium",
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "runtime_dispatch",
    },
    "broadcast_message": {
        "risk_level": "medium",
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "runtime_dispatch",
    },
    "send_direct_message": {
        "risk_level": "medium",
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "runtime_dispatch",
    },
    "send_message": {
        "risk_level": "medium",
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "runtime_dispatch",
    },
    "invite_agent": {
        "risk_level": "medium",
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "runtime_dispatch",
    },
    "check_task_status": {
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "read_only",
    },
    "list_collaborators": {
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "read_only",
    },
    "query_agent": {
        "risk_level": "medium",
        "sandbox": {"mode": "runtime_internal"},
        "side_effect_scope": "agent_query",
    },
    "skill_manager": {
        "risk_level": "high",
        "approval": {
            "kind": "conditional",
            "notes": ["Listing is read-only, but install/import can download and write skill packages."],
        },
        "sandbox": {
            "mode": "workspace_and_marketplace",
            "workspace_scope": "workspace_write",
            "network_access": "conditional",
        },
        "escalation": {
            "possible": True,
            "hint": "Escalate when importing from marketplaces, remote sources, or protected skill directories.",
            "triggers": ["remote_install", "protected_skills_dir"],
        },
        "side_effect_scope": "workspace_write",
        "external_targets": ["skill_marketplace"],
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def build_tool_policy_payload(
    name: str,
    *,
    description: str = "",
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    policy = _deep_merge(_DEFAULT_TOOL_POLICY_TEMPLATE, _DEFAULT_TOOL_POLICY_CATALOG.get(name, {}))
    if override:
        policy = _deep_merge(policy, override)
    policy["name"] = str(name or "").strip()
    policy["description"] = str(description or "").strip()
    return policy


def summarize_tool_policy_payloads(tool_policies: List[Dict[str, Any]]) -> Dict[str, Any]:
    policies = list(tool_policies or [])
    return {
        "tool_count": len(policies),
        "approval_required_count": sum(
            1 for policy in policies if bool((policy.get("approval") or {}).get("required"))
        ),
        "conditional_approval_count": sum(
            1 for policy in policies if ((policy.get("approval") or {}).get("kind") == "conditional")
        ),
        "network_enabled_count": sum(
            1
            for policy in policies
            if ((policy.get("sandbox") or {}).get("network_access") not in {"none", "blocked", "", None})
        ),
        "workspace_write_count": sum(
            1
            for policy in policies
            if (
                "write" in str((policy.get("sandbox") or {}).get("workspace_scope") or "").lower()
                or str(policy.get("side_effect_scope") or "").lower()
                in {
                    "workspace_write",
                    "workspace_delete",
                    "memory_write",
                    "external_mutation",
                    "runtime_dispatch",
                    "artifact_capture",
                }
            )
        ),
        "escalation_possible_count": sum(
            1 for policy in policies if bool((policy.get("escalation") or {}).get("possible"))
        ),
        "credentialed_tool_count": sum(
            1 for policy in policies if bool(policy.get("requires_credentials"))
        ),
    }


def build_tool_policy_pack(
    tool_names: Optional[List[str]],
    *,
    description_map: Optional[Dict[str, str]] = None,
    override_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    normalized_names: List[str] = []
    seen = set()
    for tool_name in list(tool_names or []):
        normalized = str(tool_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_names.append(normalized)
    tool_policies = [
        build_tool_policy_payload(
            tool_name,
            description=(description_map or {}).get(tool_name, ""),
            override=(override_map or {}).get(tool_name),
        )
        for tool_name in normalized_names
    ]
    return {
        "tool_names": normalized_names,
        "tool_policies": tool_policies,
        "tool_policy_summary": summarize_tool_policy_payloads(tool_policies),
    }


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

    def get_policy_payload(self) -> Dict[str, Any]:
        """Return a structured approval/sandbox/escalation snapshot for this tool."""
        return build_tool_policy_payload(self.name, description=self.description)


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
        if tool_names is not None:
            return [self._tools[name].get_schema() for name in tool_names if name in self._tools]
        return [tool.get_schema() for tool in self._tools.values()]

    def get_policy_payloads(self, tool_names: List[str] = None) -> List[Dict[str, Any]]:
        """Get governance policy snapshots for specified tools (or all if not specified)."""
        tools = (
            [self._tools[name] for name in tool_names if name in self._tools]
            if tool_names is not None
            else list(self._tools.values())
        )
        return [tool.get_policy_payload() for tool in tools]

    def get_policy_pack(self, tool_names: List[str] = None) -> Dict[str, Any]:
        """Get a normalized tool policy pack with summary metadata."""
        tools = (
            [self._tools[name] for name in tool_names if name in self._tools]
            if tool_names is not None
            else list(self._tools.values())
        )
        normalized_names = [tool.name for tool in tools]
        tool_policies = [tool.get_policy_payload() for tool in tools]
        return {
            "tool_names": normalized_names,
            "tool_policies": tool_policies,
            "tool_policy_summary": summarize_tool_policy_payloads(tool_policies),
        }
    
    async def execute(self, tool_name: str, **kwargs) -> Any:
        """Execute a tool by name."""
        tool = self.get(tool_name)
        if not tool:
            raise ValueError(f"Tool not found: {tool_name}")
        approval_granted = bool(kwargs.pop("__catown_approval_granted", False))
        tool_policy = tool.get_policy_payload()
        if tool_requires_manual_approval(tool_policy) and not approval_granted:
            approval_notes = list((tool_policy.get("approval") or {}).get("notes") or [])
            reason = approval_notes[0] if approval_notes else "This tool requires manual approval before execution."
            return build_blocked_tool_result("approval_blocked", tool_name, reason)
        execute_fn = tool.execute
        parameters = inspect.signature(execute_fn).parameters.values()
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters):
            filtered_kwargs = kwargs
        else:
            allowed_names = {param.name for param in parameters}
            filtered_kwargs = {
                key: value for key, value in kwargs.items() if key in allowed_names
            }
        return await execute_fn(**filtered_kwargs)
