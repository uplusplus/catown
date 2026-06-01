# -*- coding: utf-8 -*-
"""
Tool Base Classes and Registry
"""
import copy
import inspect
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
from abc import ABC, abstractmethod

from services.tool_governance import build_blocked_tool_result, build_structured_tool_result, classify_tool_result, tool_manual_approval_reason
from services.tool_execution_preferences import (
    AUTH_DECISION_ALLOW,
    AUTH_DECISION_DENY,
    AUTH_DECISION_REQUIRE_APPROVAL,
    authorization_matchers_for_tool,
    resolve_authorization_rule,
)


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
    "list_agents": {
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
    "run_shell": {
        "risk_level": "high",
        "approval": {
            "kind": "conditional",
            "required": False,
            "notes": [
                "Read-only shell inspection commands can run automatically. Commands that mutate the workspace, change git state, install packages, or touch external systems still require explicit approval.",
            ],
        },
        "sandbox": {
            "mode": "workspace_shell",
            "workspace_scope": "workspace_write",
            "network_access": "host_inherited",
            "notes": [
                "Commands run inside the active workspace only.",
                "Output is capped and execution is time-boxed, but the host shell semantics still apply.",
            ],
        },
        "escalation": {
            "possible": True,
            "hint": "Escalate only after reviewing whether the command mutates git state, installs packages, or touches external systems.",
            "triggers": ["shell_mutation", "package_install", "external_command"],
        },
        "side_effect_scope": "workspace_command",
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
    "consult_agent": {
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
    "open_file_for_user": {
        "sandbox": {"mode": "workspace_guarded", "workspace_scope": "workspace_read"},
        "side_effect_scope": "user_interaction",
    },
    "analyze_image": {
        "risk_level": "low",
        "sandbox": {
            "mode": "workspace_guarded",
            "workspace_scope": "workspace_read",
            "network_access": "enabled",
            "notes": [
                "Reads an image file from the workspace and sends it to the LLM for analysis.",
                "The image is sent as base64 in the LLM request body.",
            ],
        },
        "side_effect_scope": "network_read",
        "external_targets": ["llm_api"],
    },
    "analyze_document": {
        "risk_level": "low",
        "sandbox": {
            "mode": "workspace_guarded",
            "workspace_scope": "workspace_read",
            "network_access": "enabled",
            "notes": [
                "Reads a PDF file from the workspace and extracts text locally.",
                "When a prompt is provided, extracted text is sent to the LLM for analysis.",
            ],
        },
        "side_effect_scope": "network_read",
        "external_targets": ["llm_api"],
    },
    "screenshot_compare": {
        "risk_level": "medium",
        "approval": {
            "kind": "conditional",
            "notes": [
                "Capture actions invoke the screenshot tool (browser dependency). "
                "Compare is a local file operation.",
            ],
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
    "chrome_devtools": {
        "risk_level": "high",
        "approval": {
            "kind": "conditional",
            "notes": [
                "Read-only metrics collection is safe. "
                "Page interactions (click/fill/type) may mutate remote state. "
                "Launching Chrome spawns a local process.",
            ],
        },
        "sandbox": {
            "mode": "browser_runtime",
            "workspace_scope": "temp_or_explicit_path",
            "network_access": "enabled",
        },
        "escalation": {
            "possible": True,
            "hint": "Escalate when page interactions may submit forms or write data.",
            "triggers": ["remote_mutation", "chrome_process_launch"],
        },
        "side_effect_scope": "browser_interaction",
        "external_targets": ["web", "chrome_devtools"],
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


def _agent_config_file_path() -> Path:
    configured_file = os.getenv("AGENT_CONFIG_FILE")
    if configured_file:
        return Path(configured_file)
    return Path(os.getenv("CATOWN_CONFIG_DIR", str(Path.home() / ".catown" / "config"))) / "agents.json"


def _load_permissions_override() -> Dict[str, Any]:
    config_file = _agent_config_file_path()
    if not config_file.exists():
        return {}
    try:
        with config_file.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        permissions = payload.get("permissions")
        return permissions if isinstance(permissions, dict) else {}
    except Exception:
        return {}


def permissions_auto_approve_all_enabled() -> bool:
    permissions = _load_permissions_override()
    return bool(permissions.get("auto_approve_all", False))


def _dynamic_policy_override(name: str) -> Dict[str, Any]:
    permissions = _load_permissions_override()
    if bool(permissions.get("auto_approve_all", False)):
        return {
            "approval": {
                "kind": "auto",
                "required": False,
                "notes": ["Auto-approve all approvals is enabled in runtime permissions."],
            }
        }

    allow_read_only = bool(permissions.get("allow_read_only_tools_without_approval", True))
    if not allow_read_only:
        return {}

    if name in {"read_file", "list_files", "search_files", "list_directory", "list_agents", "retrieve_memory", "open_file_for_user"}:
        return {
            "approval": {
                "kind": "auto",
                "required": False,
                "notes": [],
            }
        }
    return {}


def build_tool_policy_payload(
    name: str,
    *,
    description: str = "",
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    policy = _deep_merge(_DEFAULT_TOOL_POLICY_TEMPLATE, _DEFAULT_TOOL_POLICY_CATALOG.get(name, {}))
    dynamic_override = _dynamic_policy_override(name)
    if dynamic_override:
        policy = _deep_merge(policy, dynamic_override)
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
    system_only: bool = False
    
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
        payload = build_tool_policy_payload(self.name, description=self.description)
        payload["system_only"] = bool(self.system_only)
        return payload


class ToolRegistry:
    """Registry for managing tools"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._aliases: Dict[str, str] = {}
    
    def register(self, tool: BaseTool):
        """Register a tool"""
        self._tools[tool.name] = tool

    def register_alias(self, alias: str, canonical_name: str):
        """Register a compatibility alias for an existing tool."""
        normalized_alias = str(alias or "").strip()
        normalized_canonical = str(canonical_name or "").strip()
        if not normalized_alias or not normalized_canonical:
            return
        self._aliases[normalized_alias] = normalized_canonical

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        canonical_name = self._aliases.get(name, name)
        return self._tools.get(canonical_name)
    
    def list_tools(self) -> List[str]:
        """List all registered tool names"""
        return list(self._tools.keys())

    def list_agent_tools(self) -> List[str]:
        """List tools that can be exposed to agent LLM calls."""
        return [name for name, tool in self._tools.items() if not bool(getattr(tool, "system_only", False))]

    def list_system_tools(self) -> List[str]:
        """List tools reserved for backend/system use."""
        return [name for name, tool in self._tools.items() if bool(getattr(tool, "system_only", False))]

    def is_system_tool(self, name: str) -> bool:
        """Return whether a tool is reserved for backend/system use."""
        tool = self.get(name)
        return bool(tool and getattr(tool, "system_only", False))
    
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
        system_tool_call = bool(kwargs.pop("__catown_system_tool_call", False))
        if bool(getattr(tool, "system_only", False)) and not system_tool_call:
            result_text = build_blocked_tool_result(
                "system_tool_only",
                tool_name,
                f"Tool '{tool_name}' is reserved for backend system calls and is not available to agents.",
            )
            return build_structured_tool_result(
                tool_name=tool_name,
                result_text=result_text,
                success=False,
                status="system_tool_only",
                blocked=True,
                blocked_kind="tool_scope",
                blocked_reason=result_text,
            )
        approval_granted = bool(kwargs.pop("__catown_approval_granted", False))
        project_id = kwargs.get("project_id")
        chatroom_id = kwargs.get("chatroom_id")
        agent_name = kwargs.get("agent_name")

        from models.database import SessionLocal

        db = SessionLocal()
        try:
            authorization_rule = resolve_authorization_rule(
                db,
                tool_name=tool_name,
                matcher_pairs=authorization_matchers_for_tool(tool_name, kwargs),
                project_id=project_id if isinstance(project_id, int) else None,
                chatroom_id=chatroom_id if isinstance(chatroom_id, int) else None,
                agent_name=str(agent_name or "").strip() or None,
                decision_kinds=[AUTH_DECISION_ALLOW, AUTH_DECISION_DENY, AUTH_DECISION_REQUIRE_APPROVAL],
            )
        finally:
            db.close()

        authorization_decision = str(getattr(authorization_rule, "decision_kind", "") or "").strip().lower()
        if authorization_rule is not None and authorization_decision == AUTH_DECISION_DENY:
            self._record_authorization_rule_audit(
                authorization_rule,
                decision="deny",
                source="remembered_rule",
                tool_name=tool_name,
                arguments=kwargs,
            )
            result_text = build_blocked_tool_result(
                "approval_blocked",
                tool_name,
                f"Saved authorization rule denies this {tool_name} invocation.",
            )
            return build_structured_tool_result(
                tool_name=tool_name,
                result_text=result_text,
                success=False,
                status="approval_blocked",
                blocked=True,
                blocked_kind="approval",
                blocked_reason=result_text,
            )
        if authorization_rule is not None and authorization_decision == AUTH_DECISION_REQUIRE_APPROVAL:
            self._record_authorization_rule_audit(
                authorization_rule,
                decision="require_approval",
                source="remembered_rule",
                tool_name=tool_name,
                arguments=kwargs,
            )
            result_text = build_blocked_tool_result(
                "approval_blocked",
                tool_name,
                f"Saved authorization rule requires approval for this {tool_name} invocation.",
            )
            return build_structured_tool_result(
                tool_name=tool_name,
                result_text=result_text,
                success=False,
                status="approval_blocked",
                blocked=True,
                blocked_kind="approval",
                blocked_reason=result_text,
            )
        if authorization_rule is not None and authorization_decision == AUTH_DECISION_ALLOW:
            self._record_authorization_rule_audit(
                authorization_rule,
                decision="approve",
                source="remembered_rule",
                tool_name=tool_name,
                arguments=kwargs,
            )
            approval_granted = True

        tool_policy = tool.get_policy_payload()
        approval_reason = tool_manual_approval_reason(
            tool_policy,
            tool_name=tool_name,
            arguments=kwargs,
        )
        if approval_reason and not approval_granted:
            reason = approval_reason
            result_text = build_blocked_tool_result("approval_blocked", tool_name, reason)
            return build_structured_tool_result(
                tool_name=tool_name,
                result_text=result_text,
                success=False,
                status="approval_blocked",
                blocked=True,
                blocked_kind="approval",
                blocked_reason=result_text,
            )
        execute_fn = tool.execute
        parameters = inspect.signature(execute_fn).parameters.values()
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters):
            filtered_kwargs = kwargs
        else:
            allowed_names = {param.name for param in parameters}
            filtered_kwargs = {
                key: value for key, value in kwargs.items() if key in allowed_names
            }
        result = await execute_fn(**filtered_kwargs)
        if isinstance(result, dict) and result.get("__catown_tool_result__") is True:
            return result
        classification = classify_tool_result(tool_name, result)
        return build_structured_tool_result(
            tool_name=tool_name,
            result_text=result,
            success=bool(classification.get("success")),
            status=str(classification.get("status") or "succeeded"),
            blocked=bool(classification.get("blocked")),
            blocked_kind=classification.get("blocked_kind"),
            blocked_reason=classification.get("blocked_reason"),
        )

    @staticmethod
    def _record_authorization_rule_audit(
        authorization_rule: Any,
        *,
        decision: str,
        source: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> None:
        try:
            from models.database import SessionLocal
            from services.approval_audit import record_approval_audit

            db = SessionLocal()
            try:
                record_approval_audit(
                    db,
                    event_kind="authorization_rule_matched",
                    decision=decision,
                    source=source,
                    preference=authorization_rule,
                    task_run_id=arguments.get("task_run_id") if isinstance(arguments.get("task_run_id"), int) else None,
                    chatroom_id=arguments.get("chatroom_id") if isinstance(arguments.get("chatroom_id"), int) else getattr(authorization_rule, "chatroom_id", None),
                    project_id=arguments.get("project_id") if isinstance(arguments.get("project_id"), int) else getattr(authorization_rule, "project_id", None),
                    agent_name=str(arguments.get("agent_name") or getattr(authorization_rule, "agent_name", "") or "").strip() or None,
                    target_kind="tool",
                    target_name=tool_name,
                    tool_name=tool_name,
                    command_preview=getattr(authorization_rule, "command_preview", None),
                    reason="Matched saved authorization rule.",
                    request_payload={"arguments": arguments},
                    resolution_payload={"preference_id": getattr(authorization_rule, "id", None)},
                )
            finally:
                db.close()
        except Exception:
            return
