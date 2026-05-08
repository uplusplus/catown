# -*- coding: utf-8 -*-
"""Persistent authorization and execution-preference rules for tool invocations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

AUTH_SCOPE_PROJECT = "project"
AUTH_SCOPE_CHATROOM = "chatroom"
AUTH_SCOPE_GLOBAL = "global"

AUTH_DECISION_ALLOW = "allow"
AUTH_DECISION_DENY = "deny"
AUTH_DECISION_ALLOW_NO_TIMEOUT = "allow_no_timeout"

AUTH_MATCHER_COMMAND_FINGERPRINT = "command_fingerprint"
AUTH_MATCHER_TOOL_TARGET = "tool_target"

AUTH_PREFERENCE_KIND = "authorization_rule"
TIMEOUT_BEHAVIOR_KIND = "timeout_behavior"
TIMEOUT_BEHAVIOR_WAIT_FOREVER = "wait_forever"


def _tool_execution_preference_model():
    from models.database import ToolExecutionPreference

    return ToolExecutionPreference


def build_run_shell_timeout_preference_key(command: str, cwd: str) -> str:
    return build_run_shell_command_matcher_value(command, cwd)


def build_run_shell_command_matcher_value(command: str, cwd: str) -> str:
    normalized_command = " ".join(str(command or "").strip().split())
    normalized_cwd = str(cwd or ".").strip() or "."
    raw = f"run_shell|command={normalized_command}|cwd={normalized_cwd}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_tool_target_matcher_value(tool_name: str) -> str:
    return str(tool_name or "").strip().lower()


def normalize_authorization_scope(
    requested_scope: str | None,
    *,
    project_id: int | None = None,
    chatroom_id: int | None = None,
) -> str:
    normalized = str(requested_scope or "").strip().lower()
    if normalized == AUTH_SCOPE_GLOBAL:
        return AUTH_SCOPE_GLOBAL
    if normalized == AUTH_SCOPE_CHATROOM:
        return AUTH_SCOPE_CHATROOM if chatroom_id is not None else AUTH_SCOPE_GLOBAL
    if normalized == AUTH_SCOPE_PROJECT:
        if project_id is not None:
            return AUTH_SCOPE_PROJECT
        if chatroom_id is not None:
            return AUTH_SCOPE_CHATROOM
        return AUTH_SCOPE_GLOBAL
    if project_id is not None:
        return AUTH_SCOPE_PROJECT
    if chatroom_id is not None:
        return AUTH_SCOPE_CHATROOM
    return AUTH_SCOPE_GLOBAL


def authorization_matchers_for_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    normalized_tool_name = str(tool_name or "").strip().lower()
    arguments = arguments if isinstance(arguments, dict) else {}
    matchers: list[tuple[str, str]] = []
    if normalized_tool_name == "run_shell":
        command = str(arguments.get("command") or "").strip()
        if command:
            cwd = str(arguments.get("cwd") or ".").strip() or "."
            matchers.append((AUTH_MATCHER_COMMAND_FINGERPRINT, build_run_shell_command_matcher_value(command, cwd)))
    if normalized_tool_name:
        matchers.append((AUTH_MATCHER_TOOL_TARGET, build_tool_target_matcher_value(normalized_tool_name)))
    return matchers


def _scope_matches(
    rule: Any,
    *,
    project_id: int | None,
    chatroom_id: int | None,
) -> bool:
    scope = str(rule.scope or "").strip().lower()
    if scope == AUTH_SCOPE_PROJECT:
        return project_id is not None and rule.project_id == project_id
    if scope == AUTH_SCOPE_CHATROOM:
        return chatroom_id is not None and rule.chatroom_id == chatroom_id
    if scope == AUTH_SCOPE_GLOBAL:
        return True
    return False


def _scope_rank(rule: ToolExecutionPreference) -> int:
    scope = str(rule.scope or "").strip().lower()
    if scope == AUTH_SCOPE_PROJECT:
        return 0
    if scope == AUTH_SCOPE_CHATROOM:
        return 1
    return 2


def _rule_is_active(rule: Any) -> bool:
    now = datetime.now()
    if rule.revoked_at is not None:
        return False
    if rule.expires_at is not None and rule.expires_at <= now:
        return False
    return True


def get_tool_execution_preference(
    db: Session,
    *,
    tool_name: str,
    preference_key: str,
    preference_kind: str = TIMEOUT_BEHAVIOR_KIND,
    project_id: int | None = None,
    chatroom_id: int | None = None,
) -> Optional[Any]:
    try:
        ToolExecutionPreference = _tool_execution_preference_model()
        query = (
            db.query(ToolExecutionPreference)
            .filter(ToolExecutionPreference.tool_name == str(tool_name or "").strip())
            .filter(ToolExecutionPreference.preference_key == str(preference_key or "").strip())
            .filter(ToolExecutionPreference.preference_kind == str(preference_kind or "").strip())
            .filter(ToolExecutionPreference.revoked_at.is_(None))
        )
        if project_id is not None:
            query = query.filter(ToolExecutionPreference.project_id == project_id)
        elif chatroom_id is not None:
            query = query.filter(
                ToolExecutionPreference.project_id.is_(None),
                ToolExecutionPreference.chatroom_id == chatroom_id,
            )
        else:
            query = query.filter(
                ToolExecutionPreference.project_id.is_(None),
                ToolExecutionPreference.chatroom_id.is_(None),
            )
        return query.order_by(ToolExecutionPreference.updated_at.desc(), ToolExecutionPreference.id.desc()).first()
    except Exception:
        return None


def list_authorization_rules(
    db: Session,
    *,
    project_id: int | None = None,
    chatroom_id: int | None = None,
    tool_name: str | None = None,
    include_revoked: bool = False,
) -> list[Any]:
    ToolExecutionPreference = _tool_execution_preference_model()
    query = db.query(ToolExecutionPreference).order_by(
        ToolExecutionPreference.updated_at.desc(),
        ToolExecutionPreference.id.desc(),
    )
    if not include_revoked:
        query = query.filter(ToolExecutionPreference.revoked_at.is_(None))
    if tool_name:
        query = query.filter(ToolExecutionPreference.tool_name == str(tool_name).strip())
    if project_id is not None:
        query = query.filter(ToolExecutionPreference.project_id == project_id)
    elif chatroom_id is not None:
        query = query.filter(
            ToolExecutionPreference.project_id.is_(None),
            ToolExecutionPreference.chatroom_id == chatroom_id,
        )
    return [rule for rule in query.all() if _rule_is_active(rule) or include_revoked]


def serialize_authorization_rule(rule: Any) -> dict[str, Any]:
    return {
        "id": rule.id,
        "project_id": rule.project_id,
        "chatroom_id": rule.chatroom_id,
        "agent_name": rule.agent_name,
        "tool_name": rule.tool_name,
        "scope": rule.scope,
        "matcher_type": rule.matcher_type,
        "matcher_value": rule.matcher_value,
        "decision_kind": rule.decision_kind,
        "preference_key": rule.preference_key,
        "preference_kind": rule.preference_kind,
        "preference_value": rule.preference_value,
        "constraints": _load_json(rule.constraints_json),
        "command_preview": rule.command_preview,
        "expires_at": rule.expires_at.isoformat() if rule.expires_at else None,
        "revoked_at": rule.revoked_at.isoformat() if rule.revoked_at else None,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def revoke_authorization_rule(db: Session, rule: Any | None) -> Any | None:
    if rule is None:
        return None
    if rule.revoked_at is None:
        rule.revoked_at = datetime.now()
        db.add(rule)
        db.commit()
        db.refresh(rule)
    return rule


def resolve_authorization_rule(
    db: Session,
    *,
    tool_name: str,
    matcher_pairs: list[tuple[str, str]],
    project_id: int | None = None,
    chatroom_id: int | None = None,
    agent_name: str | None = None,
    decision_kinds: list[str] | None = None,
) -> Optional[Any]:
    normalized_tool_name = str(tool_name or "").strip()
    normalized_agent_name = str(agent_name or "").strip()
    if not normalized_tool_name or not matcher_pairs:
        return None

    ToolExecutionPreference = _tool_execution_preference_model()
    rules = (
        db.query(ToolExecutionPreference)
        .filter(ToolExecutionPreference.tool_name == normalized_tool_name)
        .all()
    )

    normalized_decisions = {str(value).strip().lower() for value in (decision_kinds or []) if str(value).strip()}
    matcher_lookup = {(str(matcher_type).strip().lower(), str(matcher_value).strip()) for matcher_type, matcher_value in matcher_pairs}

    candidates: list[Any] = []
    for rule in rules:
        if not _rule_is_active(rule):
            continue
        if normalized_decisions and str(rule.decision_kind or "").strip().lower() not in normalized_decisions:
            continue
        if not _scope_matches(rule, project_id=project_id, chatroom_id=chatroom_id):
            continue
        if rule.agent_name and normalized_agent_name and str(rule.agent_name).strip() != normalized_agent_name:
            continue
        if rule.agent_name and not normalized_agent_name:
            continue
        matcher_key = (str(rule.matcher_type or "").strip().lower(), str(rule.matcher_value or "").strip())
        if matcher_key not in matcher_lookup:
            continue
        candidates.append(rule)

    if not candidates:
        return None
    candidates.sort(key=lambda rule: (_scope_rank(rule), -(rule.updated_at.timestamp() if rule.updated_at else 0), -int(rule.id or 0)))
    return candidates[0]


def upsert_authorization_rule(
    db: Session,
    *,
    tool_name: str,
    scope: str,
    matcher_type: str,
    matcher_value: str,
    decision_kind: str,
    project_id: int | None = None,
    chatroom_id: int | None = None,
    agent_name: str | None = None,
    preference_kind: str = AUTH_PREFERENCE_KIND,
    preference_value: str = "granted",
    constraints: dict[str, Any] | None = None,
    command_preview: str | None = None,
) -> Any:
    ToolExecutionPreference = _tool_execution_preference_model()
    normalized_scope = normalize_authorization_scope(scope, project_id=project_id, chatroom_id=chatroom_id)
    resolved_project_id = project_id if normalized_scope == AUTH_SCOPE_PROJECT else None
    resolved_chatroom_id = chatroom_id if normalized_scope == AUTH_SCOPE_CHATROOM else None
    normalized_tool_name = str(tool_name or "").strip()
    normalized_matcher_type = str(matcher_type or "").strip()
    normalized_matcher_value = str(matcher_value or "").strip()
    normalized_agent_name = str(agent_name or "").strip() or None

    existing = (
        db.query(ToolExecutionPreference)
        .filter(ToolExecutionPreference.tool_name == normalized_tool_name)
        .filter(ToolExecutionPreference.scope == normalized_scope)
        .filter(ToolExecutionPreference.matcher_type == normalized_matcher_type)
        .filter(ToolExecutionPreference.preference_key == normalized_matcher_value)
        .filter(ToolExecutionPreference.preference_kind == preference_kind)
        .filter(ToolExecutionPreference.revoked_at.is_(None))
        .filter(ToolExecutionPreference.agent_name == normalized_agent_name)
        .filter(ToolExecutionPreference.project_id == resolved_project_id)
        .filter(ToolExecutionPreference.chatroom_id == resolved_chatroom_id)
        .first()
    )
    if existing is None:
        existing = ToolExecutionPreference(
            project_id=resolved_project_id,
            chatroom_id=resolved_chatroom_id,
            agent_name=normalized_agent_name,
            tool_name=normalized_tool_name,
            scope=normalized_scope,
            matcher_type=normalized_matcher_type,
            matcher_value=normalized_matcher_value,
            preference_key=normalized_matcher_value,
            preference_kind=preference_kind,
        )
    existing.matcher_value = normalized_matcher_value
    existing.decision_kind = str(decision_kind or AUTH_DECISION_ALLOW).strip()
    existing.preference_value = str(preference_value or "granted").strip() or "granted"
    existing.constraints_json = _dump_json(constraints or {})
    existing.command_preview = (command_preview or "").strip() or None
    existing.revoked_at = None
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def save_wait_forever_preference(
    db: Session,
    *,
    tool_name: str,
    preference_key: str,
    command_preview: str | None = None,
    project_id: int | None = None,
    chatroom_id: int | None = None,
) -> Any:
    return upsert_authorization_rule(
        db,
        tool_name=tool_name,
        scope=normalize_authorization_scope(None, project_id=project_id, chatroom_id=chatroom_id),
        matcher_type=AUTH_MATCHER_COMMAND_FINGERPRINT,
        matcher_value=preference_key,
        decision_kind=AUTH_DECISION_ALLOW_NO_TIMEOUT,
        project_id=project_id,
        chatroom_id=chatroom_id,
        preference_kind=TIMEOUT_BEHAVIOR_KIND,
        preference_value=TIMEOUT_BEHAVIOR_WAIT_FOREVER,
        command_preview=command_preview,
        constraints={"allow_timeout_bypass": True},
    )


def prefers_wait_forever(
    db: Session,
    *,
    tool_name: str,
    preference_key: str,
    project_id: int | None = None,
    chatroom_id: int | None = None,
) -> bool:
    rule = resolve_authorization_rule(
        db,
        tool_name=tool_name,
        matcher_pairs=[(AUTH_MATCHER_COMMAND_FINGERPRINT, preference_key)],
        project_id=project_id,
        chatroom_id=chatroom_id,
        decision_kinds=[AUTH_DECISION_ALLOW_NO_TIMEOUT],
    )
    return bool(rule is not None)


def _dump_json(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return "{}"


def _load_json(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        loaded = json.loads(payload_json)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}
