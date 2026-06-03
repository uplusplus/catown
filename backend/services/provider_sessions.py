# -*- coding: utf-8 -*-
"""Provider conversation/session state for ADR-035 stateful LLM runtimes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from models.database import LLMProviderSession, TaskRun


PROVIDER_MODE_CHAT_COMPLETIONS = "chat_completions"
PROVIDER_MODE_RESPONSES_HTTP = "responses_http"
PROVIDER_MODE_RESPONSES_WEBSOCKET = "responses_websocket"

SUPPORTED_PROVIDER_MODES = {
    PROVIDER_MODE_CHAT_COMPLETIONS,
    PROVIDER_MODE_RESPONSES_HTTP,
    PROVIDER_MODE_RESPONSES_WEBSOCKET,
}


def provider_mode_for_client(llm_client: Any) -> str:
    """Return the configured runtime mode for an LLM client."""

    raw_mode = (
        getattr(llm_client, "provider_mode", None)
        or getattr(llm_client, "runtime_provider_mode", None)
        or PROVIDER_MODE_CHAT_COMPLETIONS
    )
    normalized_mode = str(raw_mode or "").strip().lower()
    if normalized_mode in SUPPORTED_PROVIDER_MODES:
        return normalized_mode
    return PROVIDER_MODE_CHAT_COMPLETIONS


def provider_host_for_client(llm_client: Any) -> str | None:
    """Return a non-secret provider endpoint label for diagnostics."""

    base_url = str(getattr(llm_client, "base_url", "") or "").strip()
    if not base_url:
        return None
    parsed = urlparse(base_url)
    host = parsed.netloc or parsed.path or base_url
    return host[:240] if host else None


def ensure_provider_session(
    db: Session,
    *,
    task_run: TaskRun | None,
    chatroom_id: int | None = None,
    project_id: int | None = None,
    agent_name: str,
    llm_client: Any,
    provider_mode: str | None = None,
) -> LLMProviderSession | None:
    """Find or create the active provider session for a chatroom/agent/model."""

    resolved_chatroom_id = chatroom_id or getattr(task_run, "chatroom_id", None)
    if not resolved_chatroom_id:
        return None

    resolved_project_id = project_id
    if resolved_project_id is None and task_run is not None:
        resolved_project_id = getattr(task_run, "project_id", None)

    mode = _normalize_provider_mode(provider_mode or provider_mode_for_client(llm_client))
    provider_host = provider_host_for_client(llm_client)
    model_name = str(getattr(llm_client, "model", "") or "").strip() or None
    normalized_agent_name = str(agent_name or "").strip() or "agent"

    query = db.query(LLMProviderSession).filter(
        LLMProviderSession.chatroom_id == int(resolved_chatroom_id),
        LLMProviderSession.agent_name == normalized_agent_name,
        LLMProviderSession.provider_mode == mode,
        LLMProviderSession.status == "active",
    )
    if model_name is None:
        query = query.filter(LLMProviderSession.model_name.is_(None))
    else:
        query = query.filter(LLMProviderSession.model_name == model_name)
    if provider_host is None:
        query = query.filter(LLMProviderSession.provider_host.is_(None))
    else:
        query = query.filter(LLMProviderSession.provider_host == provider_host)

    provider_session = (
        query.order_by(
            LLMProviderSession.last_used_at.desc().nullslast(),
            LLMProviderSession.id.desc(),
        )
        .first()
    )
    if provider_session is None:
        provider_session = LLMProviderSession(
            chatroom_id=int(resolved_chatroom_id),
            project_id=resolved_project_id,
            task_run_id=getattr(task_run, "id", None) if task_run is not None else None,
            agent_name=normalized_agent_name,
            provider_mode=mode,
            provider_host=provider_host,
            model_name=model_name,
            status="active",
            turn_count=0,
            metadata_json=_dump_metadata(
                {
                    "billing_note": (
                        "Provider state reuse may reduce repeated transport and assembly cost; "
                        "referenced previous input can still be billed by the provider."
                    )
                }
            ),
        )
        db.add(provider_session)
        db.commit()
        db.refresh(provider_session)
        return provider_session

    changed = False
    task_run_id = getattr(task_run, "id", None) if task_run is not None else None
    if task_run_id is not None and provider_session.task_run_id != task_run_id:
        provider_session.task_run_id = task_run_id
        changed = True
    if resolved_project_id is not None and provider_session.project_id != resolved_project_id:
        provider_session.project_id = resolved_project_id
        changed = True
    if changed:
        provider_session.updated_at = datetime.now()
        db.add(provider_session)
        db.commit()
        db.refresh(provider_session)
    return provider_session


def touch_provider_session_request(
    db: Session,
    provider_session: LLMProviderSession | None,
) -> LLMProviderSession | None:
    """Mark a provider session as used by one outbound LLM request."""

    if provider_session is None:
        return None
    provider_session.turn_count = int(provider_session.turn_count or 0) + 1
    now = datetime.now()
    provider_session.last_used_at = now
    provider_session.updated_at = now
    db.add(provider_session)
    db.commit()
    db.refresh(provider_session)
    return provider_session


def update_provider_session_response(
    db: Session,
    provider_session: LLMProviderSession | None,
    *,
    response_id: str | None = None,
    provider_conversation_id: str | None = None,
    compact_checkpoint_id: str | None = None,
) -> LLMProviderSession | None:
    """Persist provider response/checkpoint identifiers after a model response."""

    if provider_session is None:
        return None
    changed = False
    normalized_response_id = _clean_optional_id(response_id)
    if normalized_response_id and provider_session.last_response_id != normalized_response_id:
        provider_session.last_response_id = normalized_response_id
        changed = True
    normalized_conversation_id = _clean_optional_id(provider_conversation_id)
    if normalized_conversation_id and provider_session.provider_conversation_id != normalized_conversation_id:
        provider_session.provider_conversation_id = normalized_conversation_id
        changed = True
    normalized_checkpoint_id = _clean_optional_id(compact_checkpoint_id)
    if normalized_checkpoint_id and provider_session.compact_checkpoint_id != normalized_checkpoint_id:
        provider_session.compact_checkpoint_id = normalized_checkpoint_id
        changed = True
    if changed:
        provider_session.updated_at = datetime.now()
        db.add(provider_session)
        db.commit()
        db.refresh(provider_session)
    return provider_session


def serialize_provider_session(
    provider_session: LLMProviderSession | None,
    *,
    previous_response_id: str | None = None,
) -> dict[str, Any]:
    """Return provider session diagnostics safe for task events and Monitor."""

    if provider_session is None:
        return {
            "provider_mode": PROVIDER_MODE_CHAT_COMPLETIONS,
            "state_reused": False,
        }

    previous_id = _clean_optional_id(previous_response_id)
    if previous_id is None:
        previous_id = _clean_optional_id(provider_session.last_response_id)
    provider_mode = _normalize_provider_mode(provider_session.provider_mode)
    provider_compaction = _pending_provider_compaction(provider_session)
    return {
        "id": provider_session.id,
        "chatroom_id": provider_session.chatroom_id,
        "task_run_id": provider_session.task_run_id,
        "project_id": provider_session.project_id,
        "agent_name": provider_session.agent_name,
        "provider_mode": provider_mode,
        "provider_host": provider_session.provider_host,
        "model_name": provider_session.model_name,
        "provider_conversation_id": provider_session.provider_conversation_id,
        "previous_response_id": previous_id,
        "last_response_id": provider_session.last_response_id,
        "compact_checkpoint_id": provider_session.compact_checkpoint_id,
        "provider_compaction": provider_compaction,
        "status": provider_session.status,
        "turn_count": int(provider_session.turn_count or 0),
        "state_reused": bool(previous_id and provider_mode != PROVIDER_MODE_CHAT_COMPLETIONS),
        "last_used_at": provider_session.last_used_at.isoformat() if provider_session.last_used_at else None,
        "updated_at": provider_session.updated_at.isoformat() if provider_session.updated_at else None,
    }


def extract_response_state(event: dict[str, Any]) -> dict[str, str | None]:
    """Extract provider response identifiers from a stream/non-stream event payload."""

    response_payload = event.get("response") if isinstance(event.get("response"), dict) else {}
    return {
        "response_id": (
            _clean_optional_id(event.get("response_id"))
            or _clean_optional_id(event.get("id"))
            or _clean_optional_id(response_payload.get("id"))
        ),
        "provider_conversation_id": (
            _clean_optional_id(event.get("provider_conversation_id"))
            or _clean_optional_id(response_payload.get("conversation_id"))
            or _clean_optional_id(response_payload.get("conversation"))
        ),
        "compact_checkpoint_id": (
            _clean_optional_id(event.get("compact_checkpoint_id"))
            or _clean_optional_id(response_payload.get("compact_checkpoint_id"))
        ),
    }


def _normalize_provider_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_PROVIDER_MODES:
        return normalized
    return PROVIDER_MODE_CHAT_COMPLETIONS


def _clean_optional_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dump_metadata(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return "{}"


def _load_metadata(raw_metadata: Any) -> dict[str, Any]:
    try:
        metadata = json.loads(str(raw_metadata or "{}"))
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _pending_provider_compaction(provider_session: LLMProviderSession) -> dict[str, Any]:
    checkpoint_id = _clean_optional_id(provider_session.compact_checkpoint_id)
    if checkpoint_id is None:
        return {}
    metadata = _load_metadata(provider_session.metadata_json)
    checkpoints = metadata.get("provider_compaction_checkpoints")
    if not isinstance(checkpoints, list):
        return {}
    for item in reversed(checkpoints):
        if not isinstance(item, dict) or item.get("id") != checkpoint_id:
            continue
        source_last_response_id = _clean_optional_id(item.get("source_last_response_id"))
        ready_for_next_request = bool(
            source_last_response_id
            and source_last_response_id == _clean_optional_id(provider_session.last_response_id)
            and item.get("path")
        )
        return {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "path": item.get("path"),
            "source_last_response_id": source_last_response_id,
            "output_item_count": item.get("output_item_count"),
            "source_context_budget_event_id": item.get("source_context_budget_event_id"),
            "ready_for_next_request": ready_for_next_request,
        }
    return {}
