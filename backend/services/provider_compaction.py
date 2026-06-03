# -*- coding: utf-8 -*-
"""Local provider compaction checkpoints for ADR-035 Phase 4."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.orm import Session

from config import settings
from models.database import LLMProviderSession, TaskRun
from services.provider_sessions import update_provider_session_response


LOCAL_COMPACTION_KIND = "local_structured_summary"
PROVIDER_NATIVE_COMPACTION_KIND = "provider_native_response_compaction"
PROVIDER_COMPACTION_EVENT_TYPE = "context_compaction"
PROVIDER_COMPACTION_EVENT_KIND = "semantic_compaction"
MODEL_WINDOW_PRESSURE_REASON = "model_window_pressure"
EXPLICIT_COMPACTION_REASONS = {
    "explicit_request",
    "explicit_user_request",
    "explicit_admin_request",
}


def build_compaction_context_messages(
    messages: list[dict[str, Any]] | None,
    *,
    response_content: Any = None,
    tool_calls: Any = None,
) -> list[dict[str, Any]]:
    """Append the latest assistant response to the outbound messages for compaction."""

    compact_messages = [dict(message) for message in messages or [] if isinstance(message, dict)]
    normalized_tool_calls = tool_calls if isinstance(tool_calls, list) and tool_calls else None
    if response_content or normalized_tool_calls:
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": response_content or "",
        }
        if normalized_tool_calls:
            assistant_message["tool_calls"] = normalized_tool_calls
        compact_messages.append(assistant_message)
    return compact_messages


async def maybe_create_provider_or_local_compaction_checkpoint(
    db: Session,
    *,
    provider_session: LLMProviderSession | None,
    task_run: TaskRun | None,
    llm_client: Any | None = None,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    checkpoint_snapshot: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    reason: str | None = None,
    agent_name: str | None = None,
    emit_event: bool = True,
) -> dict[str, Any] | None:
    """Create a provider-native compaction checkpoint when possible, otherwise fallback locally."""

    trigger_reason = local_compaction_trigger_reason(diagnostics=diagnostics, reason=reason)
    if not trigger_reason or provider_session is None or task_run is None:
        return None

    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, Mapping) else None
    if snapshot is None:
        from services.run_ledger import build_task_run_checkpoint_snapshot

        snapshot = build_task_run_checkpoint_snapshot(task_run)
    if (
        trigger_reason == MODEL_WINDOW_PRESSURE_REASON
        and _has_compaction_for_source(
            provider_session.metadata_json,
            trigger_reason=trigger_reason,
            source_context_budget_event_id=_source_context_budget_event_id(snapshot),
        )
    ):
        return None

    checkpoint: dict[str, Any] | None = None
    provider_error = ""
    if _can_use_provider_native_compaction(provider_session, llm_client, messages):
        try:
            checkpoint = await create_provider_native_compaction_checkpoint(
                db,
                provider_session=provider_session,
                task_run=task_run,
                llm_client=llm_client,
                messages=messages or [],
                tools=tools,
                checkpoint_snapshot=snapshot,
                reason=trigger_reason,
            )
        except Exception as exc:
            provider_error = f"{type(exc).__name__}: {exc}"

    if checkpoint is None:
        checkpoint = create_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            checkpoint_snapshot=snapshot,
            reason=trigger_reason,
        )
        if checkpoint is not None and provider_error:
            checkpoint["provider_native_error"] = provider_error
    if checkpoint is None:
        return None

    if emit_event:
        _append_provider_compaction_event(
            db,
            task_run=task_run,
            provider_session=provider_session,
            checkpoint=checkpoint,
            diagnostics=diagnostics,
            trigger_reason=trigger_reason,
            agent_name=agent_name,
        )
    return checkpoint


def maybe_create_local_compaction_checkpoint(
    db: Session,
    *,
    provider_session: LLMProviderSession | None,
    task_run: TaskRun | None,
    checkpoint_snapshot: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    reason: str | None = None,
    agent_name: str | None = None,
    emit_event: bool = True,
) -> dict[str, Any] | None:
    """Create a local compaction checkpoint when Phase 4 trigger rules allow it."""

    trigger_reason = local_compaction_trigger_reason(diagnostics=diagnostics, reason=reason)
    if not trigger_reason or provider_session is None or task_run is None:
        return None

    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, Mapping) else None
    if snapshot is None:
        from services.run_ledger import build_task_run_checkpoint_snapshot

        snapshot = build_task_run_checkpoint_snapshot(task_run)
    if (
        trigger_reason == MODEL_WINDOW_PRESSURE_REASON
        and _has_compaction_for_source(
            provider_session.metadata_json,
            trigger_reason=trigger_reason,
            source_context_budget_event_id=_source_context_budget_event_id(snapshot),
        )
    ):
        return None

    checkpoint = create_local_compaction_checkpoint(
        db,
        provider_session=provider_session,
        task_run=task_run,
        checkpoint_snapshot=snapshot,
        reason=trigger_reason,
    )
    if checkpoint is None:
        return None

    if emit_event:
        _append_provider_compaction_event(
            db,
            task_run=task_run,
            provider_session=provider_session,
            checkpoint=checkpoint,
            diagnostics=diagnostics,
            trigger_reason=trigger_reason,
            agent_name=agent_name,
        )
    return checkpoint


def local_compaction_trigger_reason(
    *,
    diagnostics: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> str | None:
    """Return the allowed local compaction trigger reason, or None when it should not run."""

    normalized_reason = _normalize_reason(reason)
    if normalized_reason in EXPLICIT_COMPACTION_REASONS:
        return normalized_reason

    payload = diagnostics if isinstance(diagnostics, Mapping) else {}
    context_pressure_kind = _normalize_reason(payload.get("context_pressure_kind"))
    if context_pressure_kind == MODEL_WINDOW_PRESSURE_REASON:
        return MODEL_WINDOW_PRESSURE_REASON
    return None


def create_local_compaction_checkpoint(
    db: Session,
    *,
    provider_session: LLMProviderSession | None,
    task_run: TaskRun | None,
    checkpoint_snapshot: Mapping[str, Any] | None,
    reason: str = "explicit_request",
) -> dict[str, Any] | None:
    """Persist a local structured compaction checkpoint and attach it to the provider session."""

    if provider_session is None:
        return None

    checkpoint_id = f"localcmp_{uuid.uuid4().hex}"
    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, Mapping) else {}
    payload = {
        "id": checkpoint_id,
        "kind": LOCAL_COMPACTION_KIND,
        "created_at": datetime.now().isoformat(),
        "reason": _compact_text(reason, 240) or "explicit_request",
        "provider_session": _provider_session_payload(provider_session),
        "task_run": _task_run_payload(task_run),
        "sections": build_local_compaction_sections(task_run=task_run, checkpoint_snapshot=snapshot),
        "source": _source_payload(snapshot),
    }
    path = _checkpoint_path(checkpoint_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    provider_session.metadata_json = _updated_metadata(
        provider_session.metadata_json,
        checkpoint_id=checkpoint_id,
        path=path,
        reason=str(payload.get("reason") or ""),
        source=payload.get("source") if isinstance(payload.get("source"), Mapping) else {},
    )
    provider_session = update_provider_session_response(
        db,
        provider_session,
        compact_checkpoint_id=checkpoint_id,
    )
    if provider_session is not None:
        db.refresh(provider_session)
    return {
        "compact_checkpoint_id": checkpoint_id,
        "kind": LOCAL_COMPACTION_KIND,
        "path": str(path),
        "summary": _checkpoint_summary(payload),
        "sections": payload["sections"],
    }


async def create_provider_native_compaction_checkpoint(
    db: Session,
    *,
    provider_session: LLMProviderSession | None,
    task_run: TaskRun | None,
    llm_client: Any,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    checkpoint_snapshot: Mapping[str, Any] | None,
    reason: str = "explicit_request",
) -> dict[str, Any] | None:
    """Persist the canonical output window returned by provider-native Responses compaction."""

    if provider_session is None:
        return None
    compact = await llm_client.compact_responses_context(messages, tools=tools)
    output = compact.get("output")
    if not isinstance(output, list) or not output:
        return None

    checkpoint_id = _provider_checkpoint_id(compact)
    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, Mapping) else {}
    source_last_response_id = getattr(provider_session, "last_response_id", None)
    payload = {
        "id": checkpoint_id,
        "kind": PROVIDER_NATIVE_COMPACTION_KIND,
        "created_at": datetime.now().isoformat(),
        "reason": _compact_text(reason, 240) or "explicit_request",
        "provider_session": _provider_session_payload(provider_session),
        "task_run": _task_run_payload(task_run),
        "source_last_response_id": source_last_response_id,
        "output": output,
        "output_item_count": len(output),
        "usage": compact.get("usage") if isinstance(compact.get("usage"), Mapping) else {},
        "request": compact.get("request") if isinstance(compact.get("request"), Mapping) else {},
        "provider_request": (
            compact.get("provider_request")
            if isinstance(compact.get("provider_request"), Mapping)
            else {}
        ),
        "response": compact.get("response") if isinstance(compact.get("response"), Mapping) else {},
        "compaction_item": (
            compact.get("compaction_item")
            if isinstance(compact.get("compaction_item"), Mapping)
            else {}
        ),
        "source": _source_payload(snapshot),
    }
    path = _checkpoint_path(checkpoint_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    provider_session.metadata_json = _updated_metadata(
        provider_session.metadata_json,
        checkpoint_id=checkpoint_id,
        kind=PROVIDER_NATIVE_COMPACTION_KIND,
        path=path,
        reason=str(payload.get("reason") or ""),
        source=payload.get("source") if isinstance(payload.get("source"), Mapping) else {},
        source_last_response_id=source_last_response_id,
        output_item_count=len(output),
        usage=payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {},
    )
    provider_session = update_provider_session_response(
        db,
        provider_session,
        compact_checkpoint_id=checkpoint_id,
    )
    if provider_session is not None:
        db.refresh(provider_session)

    return {
        "compact_checkpoint_id": checkpoint_id,
        "kind": PROVIDER_NATIVE_COMPACTION_KIND,
        "path": str(path),
        "summary": _provider_checkpoint_summary(payload),
        "output_item_count": len(output),
        "usage": payload["usage"],
        "source_last_response_id": source_last_response_id,
    }


def load_local_compaction_checkpoint(checkpoint_id: str) -> dict[str, Any] | None:
    """Load a previously persisted local compaction checkpoint by id."""

    normalized = _clean_checkpoint_id(checkpoint_id)
    if not normalized:
        return None
    path = _checkpoint_path(normalized)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_local_compaction_sections(
    *,
    task_run: TaskRun | None,
    checkpoint_snapshot: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Build the local fallback summary sections required by ADR-035 Phase 4."""

    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, Mapping) else {}
    latest_agent_turn = _mapping(snapshot.get("latest_agent_turn"))
    turn_local_state = _mapping(snapshot.get("turn_local_state"))
    latest_context_budget = _mapping(snapshot.get("latest_context_budget_event"))
    continuation_cursor = _mapping(snapshot.get("continuation_cursor"))
    subagent_handles = _mapping(snapshot.get("subagent_handles"))

    return {
        "current_objective": _join_lines(
            [
                _task_run_objective(task_run),
                _prefixed("Latest response", latest_agent_turn.get("response_preview")),
            ],
            fallback="Continue the current Catown task.",
        ),
        "constraints_and_decisions": _join_lines(
            [
                _prefixed("Continuation", snapshot.get("continuation_state_summary")),
                _prefixed("Policy", snapshot.get("policy_decision_summary")),
                _prefixed("Scheduler", snapshot.get("scheduler_runtime_summary")),
            ],
            fallback="No explicit constraints or decisions were captured.",
        ),
        "files_and_artifacts": _join_lines(
            [
                _prefixed("Pipeline inbox", snapshot.get("pipeline_inbox_summary")),
                _prefixed("Handoff inbox", snapshot.get("orchestration_handoff_inbox_summary")),
                *_tool_artifact_lines(turn_local_state),
            ],
            fallback="No file or artifact references were captured.",
        ),
        "tool_outcomes": _join_lines(
            [
                *_prior_round_lines(turn_local_state),
                *_tool_result_lines(turn_local_state),
            ],
            fallback="No prior tool outcomes were captured.",
        ),
        "pending_steps": _join_lines(
            [
                _prefixed("Cursor", snapshot.get("continuation_cursor_summary")),
                _prefixed("Next action", continuation_cursor.get("next_action")),
                _prefixed("Subagents", subagent_handles.get("summary")),
            ],
            fallback="No pending continuation step was captured.",
        ),
        "risks_and_open_questions": _join_lines(
            [
                _prefixed("Context budget", latest_context_budget.get("reason_summary")),
                _prefixed("Usage", latest_context_budget.get("detail_summary")),
                _blocked_tool_line(turn_local_state),
            ],
            fallback="No explicit risks or open questions were captured.",
        ),
    }


def _checkpoint_path(checkpoint_id: str) -> Path:
    return settings.STATE_DIR / "provider_compaction" / f"{checkpoint_id}.json"


def _provider_session_payload(provider_session: LLMProviderSession) -> dict[str, Any]:
    return {
        "id": provider_session.id,
        "chatroom_id": provider_session.chatroom_id,
        "task_run_id": provider_session.task_run_id,
        "project_id": provider_session.project_id,
        "agent_name": provider_session.agent_name,
        "provider_mode": provider_session.provider_mode,
        "provider_host": provider_session.provider_host,
        "model_name": provider_session.model_name,
        "last_response_id": provider_session.last_response_id,
        "provider_conversation_id": provider_session.provider_conversation_id,
        "compact_checkpoint_id": provider_session.compact_checkpoint_id,
    }


def _task_run_payload(task_run: TaskRun | None) -> dict[str, Any]:
    if task_run is None:
        return {}
    return {
        "id": getattr(task_run, "id", None),
        "chatroom_id": getattr(task_run, "chatroom_id", None),
        "project_id": getattr(task_run, "project_id", None),
        "run_kind": getattr(task_run, "run_kind", None),
        "status": getattr(task_run, "status", None),
        "title": getattr(task_run, "title", None),
        "target_agent_name": getattr(task_run, "target_agent_name", None),
    }


def _source_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    latest_context_budget = _mapping(snapshot.get("latest_context_budget_event"))
    return {
        "event_count": snapshot.get("event_count"),
        "latest_event_type": snapshot.get("latest_event_type"),
        "latest_event_index": snapshot.get("latest_event_index"),
        "latest_context_budget_event_id": latest_context_budget.get("event_id"),
        "continuation_state_summary": snapshot.get("continuation_state_summary"),
        "continuation_cursor_summary": snapshot.get("continuation_cursor_summary"),
    }


def _updated_metadata(
    raw_metadata: Any,
    *,
    checkpoint_id: str,
    kind: str = LOCAL_COMPACTION_KIND,
    path: Path,
    reason: str,
    source: Mapping[str, Any],
    source_last_response_id: Any = None,
    output_item_count: int | None = None,
    usage: Mapping[str, Any] | None = None,
) -> str:
    try:
        metadata = json.loads(str(raw_metadata or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    list_key = (
        "provider_compaction_checkpoints"
        if kind == PROVIDER_NATIVE_COMPACTION_KIND
        else "local_compaction_checkpoints"
    )
    checkpoints = metadata.setdefault(list_key, [])
    if not isinstance(checkpoints, list):
        checkpoints = []
        metadata[list_key] = checkpoints
    item = {
        "id": checkpoint_id,
        "kind": kind,
        "path": str(path),
        "reason": reason,
        "source_context_budget_event_id": source.get("latest_context_budget_event_id"),
        "source_event_count": source.get("event_count"),
        "created_at": datetime.now().isoformat(),
    }
    if kind == PROVIDER_NATIVE_COMPACTION_KIND:
        item["source_last_response_id"] = source_last_response_id
        item["output_item_count"] = output_item_count
        item["usage"] = dict(usage or {})
        metadata["last_provider_compaction_checkpoint_id"] = checkpoint_id
    else:
        metadata["last_local_compaction_checkpoint_id"] = checkpoint_id
    checkpoints.append(item)
    return json.dumps(metadata, ensure_ascii=False, default=str)


def _checkpoint_summary(payload: Mapping[str, Any]) -> str:
    sections = _mapping(payload.get("sections"))
    objective = _compact_text(sections.get("current_objective"), 120)
    pending = _compact_text(sections.get("pending_steps"), 120)
    return " | ".join(part for part in (objective, pending) if part)


def _provider_checkpoint_summary(payload: Mapping[str, Any]) -> str:
    output_item_count = payload.get("output_item_count")
    usage = _mapping(payload.get("usage"))
    tokens = usage.get("total_tokens") or usage.get("prompt_tokens")
    parts = [f"{output_item_count} compacted output items" if output_item_count else ""]
    if tokens:
        parts.append(f"{tokens} compact tokens")
    return " | ".join(part for part in parts if part) or "Provider-native compaction checkpoint"


def _append_provider_compaction_event(
    db: Session,
    *,
    task_run: TaskRun,
    provider_session: LLMProviderSession,
    checkpoint: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
    trigger_reason: str,
    agent_name: str | None,
) -> None:
    from services.provider_sessions import serialize_provider_session
    from services.run_ledger import append_task_event

    checkpoint_id = _compact_text(checkpoint.get("compact_checkpoint_id"), 120)
    event_agent_name = (agent_name or provider_session.agent_name or "").strip() or None
    checkpoint_kind = _compact_text(checkpoint.get("kind") or LOCAL_COMPACTION_KIND, 120)
    kind_label = "provider-native" if checkpoint_kind == PROVIDER_NATIVE_COMPACTION_KIND else "local"
    event_summary = (
        f"{event_agent_name or 'agent'} created {kind_label} compaction checkpoint {checkpoint_id} "
        f"({trigger_reason})."
    )
    payload = {
        "event_kind": PROVIDER_COMPACTION_EVENT_KIND,
        "semantic_compaction": True,
        "context_pressure_kind": trigger_reason,
        "selector_diagnostics": _semantic_compaction_diagnostics(
            diagnostics=diagnostics,
            trigger_reason=trigger_reason,
        ),
        "provider_session": serialize_provider_session(provider_session),
        "provider_compaction": {
            "id": checkpoint_id,
            "kind": checkpoint_kind,
            "trigger_reason": trigger_reason,
            "path": checkpoint.get("path"),
            "summary": checkpoint.get("summary"),
            "sections": checkpoint.get("sections") if isinstance(checkpoint.get("sections"), dict) else {},
            "output_item_count": checkpoint.get("output_item_count"),
            "usage": checkpoint.get("usage") if isinstance(checkpoint.get("usage"), Mapping) else {},
            "provider_native_error": checkpoint.get("provider_native_error"),
        },
    }
    append_task_event(
        db,
        task_run,
        PROVIDER_COMPACTION_EVENT_TYPE,
        agent_name=event_agent_name,
        summary=event_summary,
        payload=payload,
    )


def _semantic_compaction_diagnostics(
    *,
    diagnostics: Mapping[str, Any] | None,
    trigger_reason: str,
) -> dict[str, Any]:
    payload = dict(diagnostics) if isinstance(diagnostics, Mapping) else {}
    summary = _mapping(payload.get("summary"))
    payload["selection_changed"] = bool(payload.get("selection_changed"))
    payload["semantic_compaction"] = True
    payload["event_kind"] = PROVIDER_COMPACTION_EVENT_KIND
    payload["context_pressure_kind"] = trigger_reason
    payload["summary"] = {
        **dict(summary),
        "dropped_count": int(summary.get("dropped_count") or 0),
        "truncated_count": int(summary.get("truncated_count") or 0),
    }
    return payload


def _task_run_objective(task_run: TaskRun | None) -> str:
    if task_run is None:
        return ""
    request = _compact_text(getattr(task_run, "user_request", None), 420)
    title = _compact_text(getattr(task_run, "title", None), 180)
    if request:
        return f"User request: {request}"
    if title:
        return f"Task: {title}"
    return ""


def _tool_artifact_lines(turn_local_state: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for result in _tool_results(turn_local_state):
        metadata = _mapping(result.get("metadata"))
        artifact = _mapping(metadata.get("tool_output_artifact"))
        if artifact.get("path"):
            lines.append(f"- {result.get('tool_name') or 'tool'} artifact: {_compact_text(artifact.get('path'), 260)}")
    return lines


def _prior_round_lines(turn_local_state: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    raw = turn_local_state.get("prior_round_summaries")
    if not isinstance(raw, list):
        return lines
    for item in raw[:12]:
        if isinstance(item, Mapping):
            text = item.get("summary") or item.get("assistant_content") or item
        else:
            text = item
        compact = _compact_text(text, 320)
        if compact:
            lines.append(f"- {compact}")
    return lines


def _tool_result_lines(turn_local_state: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for result in _tool_results(turn_local_state)[:12]:
        tool_name = _compact_text(result.get("tool_name") or result.get("name") or "tool", 80)
        status = _compact_text(result.get("status") or ("ok" if result.get("success", True) else "error"), 80)
        result_text = _compact_text(result.get("result") or result.get("content"), 320)
        lines.append(f"- {tool_name} [{status}]: {result_text}")
    return lines


def _blocked_tool_line(turn_local_state: Mapping[str, Any]) -> str:
    blocked = _mapping(turn_local_state.get("blocked_tool"))
    if not blocked:
        return ""
    tool_name = _compact_text(blocked.get("tool_name"), 80) or "tool"
    reason = _compact_text(blocked.get("blocked_reason") or blocked.get("reason"), 240)
    return f"Blocked {tool_name}: {reason}" if reason else f"Blocked {tool_name}"


def _tool_results(turn_local_state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_results = turn_local_state.get("tool_results")
    if not isinstance(raw_results, list):
        return []
    return [item for item in raw_results if isinstance(item, Mapping)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _prefixed(label: str, value: Any) -> str:
    text = _compact_text(value, 420)
    return f"{label}: {text}" if text else ""


def _join_lines(lines: list[str], *, fallback: str) -> str:
    cleaned = [line for line in lines if str(line or "").strip()]
    return "\n".join(cleaned) if cleaned else fallback


def _compact_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = " ".join(value.strip().split())
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
        text = " ".join(text.strip().split())
    if not text:
        return ""
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def _clean_checkpoint_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(("localcmp_", "providercmp_")):
        return ""
    return "".join(char for char in text if char.isalnum() or char in {"_", "-"})


def _has_compaction_for_source(
    raw_metadata: Any,
    *,
    trigger_reason: str,
    source_context_budget_event_id: Any,
) -> bool:
    if source_context_budget_event_id is None:
        return False
    try:
        metadata = json.loads(str(raw_metadata or "{}"))
    except json.JSONDecodeError:
        return False
    if not isinstance(metadata, dict):
        return False
    for list_key in ("local_compaction_checkpoints", "provider_compaction_checkpoints"):
        checkpoints = metadata.get(list_key)
        if not isinstance(checkpoints, list):
            continue
        if any(
            isinstance(item, Mapping)
            and item.get("reason") == trigger_reason
            and item.get("source_context_budget_event_id") == source_context_budget_event_id
            for item in checkpoints
        ):
            return True
    return False


def _source_context_budget_event_id(snapshot: Mapping[str, Any]) -> Any:
    latest_context_budget = _mapping(snapshot.get("latest_context_budget_event"))
    return latest_context_budget.get("event_id")


def _normalize_reason(value: Any) -> str:
    return str(value or "").strip().lower()


def _can_use_provider_native_compaction(
    provider_session: LLMProviderSession,
    llm_client: Any | None,
    messages: list[dict[str, Any]] | None,
) -> bool:
    if provider_session.provider_mode not in {"responses_http", "responses_websocket"}:
        return False
    if llm_client is None or not hasattr(llm_client, "compact_responses_context"):
        return False
    return bool(messages)


def _provider_checkpoint_id(compact: Mapping[str, Any]) -> str:
    raw_id = _compact_text(compact.get("id") or compact.get("response_id"), 120)
    clean_id = "".join(char for char in raw_id if char.isalnum() or char in {"_", "-"})
    if clean_id:
        return f"providercmp_{clean_id}"
    return f"providercmp_{uuid.uuid4().hex}"
