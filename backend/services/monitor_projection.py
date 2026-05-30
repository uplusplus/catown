from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import ApprovalQueueItem, Chatroom, Project, TaskRun, TaskRunEvent
from services.approval_queue import serialize_approval_queue_item
from services.context_compaction_summary import build_context_compaction_projection
from services.policy_decision_contracts import summarize_policy_decision


INPUT_PRICE_PER_1K = 0.03
OUTPUT_PRICE_PER_1K = 0.06


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_runtime_card_task_run_id(
    *,
    card: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    metadata = metadata or {}
    card = card or {}
    for candidate in (
        card.get("task_run_id"),
        card.get("run_id"),
        metadata.get("task_run_id"),
        metadata.get("run_id"),
    ):
        coerced = _coerce_int(candidate)
        if coerced and coerced > 0:
            return coerced
    return None


def extract_runtime_card_message_payload(message: Any) -> tuple[dict[str, Any] | None, int | None, dict[str, Any]]:
    metadata = parse_metadata(getattr(message, "metadata_json", None))
    card = metadata.get("card") if isinstance(metadata.get("card"), dict) else None
    task_run_id = extract_runtime_card_task_run_id(card=card, metadata=metadata)
    return card, task_run_id, metadata


def get_runtime_card_projection_health(db: Session) -> dict[str, int]:
    from models.database import Message, RuntimeCardProjection

    runtime_card_count = int(
        db.query(func.count(Message.id))
        .filter(Message.message_type == "runtime_card")
        .scalar()
        or 0
    )
    projection_count = int(
        db.query(func.count(RuntimeCardProjection.id)).scalar() or 0
    )
    missing_count = int(
        db.query(func.count(Message.id))
        .outerjoin(RuntimeCardProjection, RuntimeCardProjection.message_id == Message.id)
        .filter(
            Message.message_type == "runtime_card",
            RuntimeCardProjection.message_id.is_(None),
        )
        .scalar()
        or 0
    )
    return {
        "runtime_cards": runtime_card_count,
        "projections": projection_count,
        "missing": missing_count,
    }


def compact_preview(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    compact = " ".join(text.strip().split())
    return compact[:limit]


def format_runtime_detail_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def metadata_client_turn_id(metadata: dict[str, Any]) -> str | None:
    client_turn_id = metadata.get("client_turn_id")
    if isinstance(client_turn_id, str) and client_turn_id:
        return client_turn_id

    card = metadata.get("card")
    if isinstance(card, dict):
        card_turn_id = card.get("client_turn_id")
        if isinstance(card_turn_id, str) and card_turn_id:
            return card_turn_id
    return None


def runtime_entities(card: dict[str, Any]) -> tuple[str | None, str | None]:
    card_type = str(card.get("type") or "runtime")
    agent = card.get("agent")
    from_agent = card.get("from_agent")
    to_agent = card.get("to_agent")

    if card_type == "llm_call":
        return (str(agent) if agent else "agent", "LLM")
    if card_type == "tool_call":
        return (str(agent) if agent else "agent", str(card.get("tool") or "tool"))
    if card_type == "agent_message":
        return (str(from_agent) if from_agent else "agent", str(to_agent) if to_agent else "team")
    if card_type == "boss_instruction":
        return ("Boss", str(agent) if agent else "agent")
    return (str(agent) if agent else None, None)


def extract_prompt_preview(card: dict[str, Any]) -> str:
    prompt_messages = card.get("prompt_messages")
    if not isinstance(prompt_messages, str) or not prompt_messages.strip():
        return ""

    try:
        parsed = json.loads(prompt_messages)
    except json.JSONDecodeError:
        return compact_preview(prompt_messages)

    if isinstance(parsed, list):
        for message in reversed(parsed):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return compact_preview(content)
            if isinstance(content, list):
                chunks: list[str] = []
                for chunk in content:
                    if not isinstance(chunk, dict):
                        continue
                    text = chunk.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
                if chunks:
                    return compact_preview(" ".join(chunks))

    return compact_preview(parsed)


def build_runtime_title(card: dict[str, Any]) -> str:
    card_type = str(card.get("type") or "runtime")
    agent = str(card.get("agent") or card.get("from_agent") or "system")

    if card_type == "llm_call":
        return f"{agent} -> LLM"
    if card_type == "tool_call":
        tool_name = str(card.get("tool") or "tool")
        return f"{agent} used {tool_name}"
    if card_type == "agent_error":
        return f"{agent} stream failed"
    if card_type == "stage_started":
        return f"{agent} started {card.get('display_name') or card.get('stage') or 'stage'}"
    if card_type == "stage_completed":
        return f"{agent} finished {card.get('display_name') or card.get('stage') or 'stage'}"
    if card_type == "gate_blocked":
        return f"Gate blocked at {card.get('stage') or 'pipeline'}"
    if card_type == "gate_rejected":
        return f"Gate rejected by {agent}"
    if card_type == "gate_approved":
        return f"Gate approved by {agent}"
    if card_type == "skill_inject":
        return f"{agent} loaded skills"
    if card_type == "agent_message":
        to_agent = card.get("to_agent")
        if to_agent:
            return f"{agent} -> {to_agent}"
        return f"Agent message from {agent}"
    if card_type == "boss_instruction":
        return f"Boss instruction for {agent}"
    return f"{agent} / {card_type}"


def build_runtime_operation_label(card: dict[str, Any]) -> str:
    card_type = str(card.get("type") or "runtime")
    if card_type == "llm_call":
        return "llm"
    if card_type == "tool_call":
        return str(card.get("tool") or "tool")
    if card_type == "agent_error":
        return "error"
    if card_type in {"stage_started", "stage_completed"}:
        return str(card.get("display_name") or card.get("stage") or "stage")
    return str(card.get("stage") or card.get("display_name") or card_type.replace("_", " "))


def build_runtime_brain_events(
    *,
    runtime_id: int,
    card: dict[str, Any],
    from_entity: str | None,
    to_entity: str | None,
    operation_label: str,
    title: str,
    preview: str,
    prompt_preview: str,
    response_preview: str,
    arguments_preview: str,
) -> list[dict[str, Any]]:
    source_entity = from_entity or str(card.get("agent") or card.get("from_agent") or "runtime")
    target_entity = to_entity or None
    card_type = str(card.get("type") or "runtime")

    if card_type == "llm_call":
        llm_target = "LLM"
        return [
            {
                "id": f"runtime-{runtime_id}-outbound",
                "phase": "outbound",
                "category": "llm",
                "tone": "neutral",
                "from_entity": source_entity,
                "to_entity": llm_target,
                "operation_label": operation_label,
                "label": f"{source_entity} -> {llm_target}",
                "detail": prompt_preview or preview or "Prompt payload captured.",
            },
            {
                "id": f"runtime-{runtime_id}-inbound",
                "phase": "inbound",
                "category": "llm",
                "tone": "success",
                "from_entity": llm_target,
                "to_entity": source_entity,
                "operation_label": operation_label,
                "label": f"{llm_target} -> {source_entity}",
                "detail": response_preview or preview or "Model response captured.",
            },
        ]

    if card_type == "tool_call":
        tool_target = target_entity or str(card.get("tool") or "tool")
        success = card.get("success")
        return [
            {
                "id": f"runtime-{runtime_id}-outbound",
                "phase": "outbound",
                "category": "tool",
                "tone": "neutral",
                "from_entity": source_entity,
                "to_entity": tool_target,
                "operation_label": operation_label,
                "label": f"{source_entity} -> {tool_target}",
                "detail": arguments_preview or preview or "Tool call issued.",
            },
            {
                "id": f"runtime-{runtime_id}-inbound",
                "phase": "inbound",
                "category": "tool",
                "tone": "error" if success is False else "success",
                "from_entity": tool_target,
                "to_entity": source_entity,
                "operation_label": operation_label,
                "label": f"{tool_target} -> {source_entity}",
                "detail": response_preview or preview or "Tool output returned.",
            },
        ]

    if card_type == "agent_error":
        return [
            {
                "id": f"runtime-{runtime_id}-error",
                "phase": "inbound",
                "category": "runtime",
                "tone": "error",
                "from_entity": source_entity,
                "to_entity": "User",
                "operation_label": operation_label,
                "label": f"{source_entity} -> User",
                "detail": response_preview or preview or "Agent stream failed before a final reply was saved.",
            }
        ]

    return [
        {
            "id": f"runtime-{runtime_id}",
            "phase": "state",
            "category": "runtime",
            "tone": "error" if card.get("success") is False else "neutral",
            "from_entity": source_entity,
            "to_entity": target_entity,
            "operation_label": operation_label,
            "label": target_entity and f"{source_entity} -> {target_entity}" or title,
            "detail": preview or title,
        }
    ]


def build_runtime_detail_sections(
    *,
    runtime_id: int,
    card: dict[str, Any],
    operation_label: str,
    from_entity: str | None,
    to_entity: str | None,
    client_turn_id: str | None,
) -> list[dict[str, Any]]:
    card_type = str(card.get("type") or "runtime")
    sections: list[dict[str, Any]] = []

    def append_section(
        *,
        phase: str,
        label: str,
        content: Any,
        tone: str,
        format_name: str,
        variant: str,
    ) -> None:
        normalized_content = format_runtime_detail_value(content)
        if not normalized_content:
            return
        sections.append(
            {
                "id": f"runtime-{runtime_id}-section-{len(sections) + 1}",
                "phase": phase,
                "label": label,
                "content": normalized_content,
                "tone": tone,
                "format": format_name,
                "variant": variant,
            }
        )

    if card_type == "llm_call":
        append_section(
            phase="outbound",
            label="System Prompt",
            content=card.get("system_prompt"),
            tone="accent",
            format_name="text",
            variant="meta",
        )
        append_section(
            phase="outbound",
            label="Raw Prompt Payload",
            content=card.get("prompt_messages"),
            tone="accent",
            format_name="json",
            variant="raw",
        )
        append_section(
            phase="outbound",
            label="Planned Tools",
            content=card.get("tool_calls"),
            tone="warning",
            format_name="json",
            variant="raw",
        )
        append_section(
            phase="inbound",
            label="Response",
            content=card.get("response"),
            tone="success",
            format_name="text",
            variant="result",
        )
        append_section(
            phase="inbound",
            label="Raw LLM Response",
            content=card.get("raw_response"),
            tone="neutral",
            format_name="json",
            variant="raw",
        )
    elif card_type == "tool_call":
        append_section(
            phase="outbound",
            label="Raw Tool Input",
            content=card.get("arguments"),
            tone="accent",
            format_name="json",
            variant="raw",
        )
        append_section(
            phase="inbound",
            label="Tool Error" if card.get("success") is False else "Tool Output",
            content=card.get("result"),
            tone="error" if card.get("success") is False else "success",
            format_name="json" if isinstance(card.get("result"), (dict, list)) else "text",
            variant="result",
        )
    elif card_type == "agent_error":
        append_section(
            phase="inbound",
            label="Failure Summary",
            content=card.get("summary"),
            tone="warning",
            format_name="text",
            variant="result",
        )
        append_section(
            phase="inbound",
            label="Error",
            content=card.get("error"),
            tone="error",
            format_name="text",
            variant="result",
        )
        append_section(
            phase="inbound",
            label="Failure Detail",
            content=card.get("content"),
            tone="error",
            format_name="text",
            variant="raw",
        )
    elif card_type == "consult_call":
        append_section(
            phase="outbound",
            label="Consult Request",
            content=card.get("question") or card.get("question_preview") or card.get("arguments"),
            tone="accent",
            format_name="text",
            variant="result",
        )
        append_section(
            phase="inbound",
            label="Consult Response",
            content=card.get("response") or card.get("response_preview") or card.get("summary_text"),
            tone="success",
            format_name="text",
            variant="result",
        )
    elif card_type == "agent_message":
        append_section(
            phase="state",
            label="Agent Message",
            content=card.get("content") or card.get("content_preview") or card.get("summary"),
            tone="neutral",
            format_name="text",
            variant="result",
        )
    elif card_type == "boss_instruction":
        append_section(
            phase="state",
            label="Boss Instruction",
            content=card.get("content") or card.get("instruction") or card.get("summary"),
            tone="warning",
            format_name="text",
            variant="result",
        )
    elif card_type in {"stage_started", "stage_completed"}:
        append_section(
            phase="state",
            label="Stage Status",
            content=card.get("summary") or card.get("content") or card.get("display_name") or card.get("stage"),
            tone="success" if card_type == "stage_completed" else "neutral",
            format_name="text",
            variant="result",
        )
    else:
        for label, value, tone, format_name, variant in [
            ("Content", card.get("content"), "neutral", "text", "result"),
            ("Preview", card.get("content_preview"), "neutral", "text", "result"),
            ("Summary", card.get("summary"), "neutral", "text", "result"),
            ("Arguments", card.get("arguments"), "accent", "json", "raw"),
            ("Result", card.get("result"), "success", "text", "result"),
        ]:
            append_section(
                phase="state",
                label=label,
                content=value,
                tone=tone,
                format_name=format_name,
                variant=variant,
            )

    append_section(
        phase="all",
        label="Exchange Meta",
        content={
            "from": from_entity,
            "to": to_entity,
            "type": card_type,
            "model": card.get("model"),
            "tool": card.get("tool"),
            "turn": card.get("turn"),
            "tokens_in": card.get("tokens_in"),
            "tokens_out": card.get("tokens_out"),
            "duration_ms": card.get("duration_ms"),
            "success": card.get("success"),
            "client_turn_id": client_turn_id or card.get("client_turn_id"),
            "operation_label": operation_label,
        },
        tone="neutral",
        format_name="json",
        variant="meta",
    )
    append_section(
        phase="all",
        label="Raw Event Payload",
        content=card,
        tone="neutral",
        format_name="json",
        variant="raw",
    )
    return sections


def build_runtime_preview(card: dict[str, Any]) -> str:
    candidates = [
        card.get("error"),
        card.get("response"),
        card.get("result"),
        card.get("content_preview"),
        card.get("content"),
        card.get("summary"),
        card.get("arguments"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            compact = " ".join(candidate.strip().split())
            return compact[:240]
    return ""


def resolve_chatroom_project(db: Session, chatroom: Chatroom | None) -> Project | None:
    current = chatroom
    visited_ids: set[int] = set()
    while current:
        if current.project_id:
            return db.query(Project).filter(Project.id == current.project_id).first()
        if not current.source_chatroom_id or current.source_chatroom_id in visited_ids:
            break
        visited_ids.add(current.source_chatroom_id)
        current = db.query(Chatroom).filter(Chatroom.id == current.source_chatroom_id).first()
    return None


def serialize_monitor_message_item(
    *,
    message_id: int,
    chatroom_id: int,
    chat_title: str,
    project_id: int | None,
    project_name: str | None,
    agent_name: str | None,
    content: str,
    message_type: str,
    created_at: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    normalized_content = content or ""
    normalized_metadata = metadata or {}
    return {
        "id": message_id,
        "chatroom_id": chatroom_id,
        "chat_title": chat_title,
        "project_id": project_id,
        "project_name": project_name,
        "agent_name": agent_name,
        "content": normalized_content,
        "content_preview": " ".join(normalized_content.split())[:220],
        "message_type": message_type,
        "created_at": created_value,
        "client_turn_id": metadata_client_turn_id(normalized_metadata),
    }


def serialize_monitor_runtime_item(
    *,
    runtime_message_id: int,
    chatroom_id: int,
    chat_title: str,
    project_id: int | None,
    project_name: str | None,
    card: dict[str, Any],
    created_at: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    normalized_metadata = metadata or {}
    from_entity, to_entity = runtime_entities(card)
    title = build_runtime_title(card)
    operation_label = build_runtime_operation_label(card)
    preview = build_runtime_preview(card)
    prompt_preview = extract_prompt_preview(card)
    response_preview = compact_preview(card.get("response") or card.get("result"))
    arguments_preview = compact_preview(card.get("arguments"))
    return {
        "id": runtime_message_id,
        "type": str(card.get("type") or "runtime"),
        "title": title,
        "operation_label": operation_label,
        "preview": preview,
        "created_at": created_value,
        "chatroom_id": chatroom_id,
        "chat_title": chat_title,
        "project_id": project_id,
        "project_name": project_name,
        "agent": card.get("agent") or card.get("from_agent"),
        "from_entity": from_entity,
        "to_entity": to_entity,
        "model": card.get("model"),
        "tool_name": card.get("tool"),
        "tool_call_id": card.get("tool_call_id"),
        "success": card.get("success"),
        "tokens_in": int(card.get("tokens_in") or 0),
        "tokens_out": int(card.get("tokens_out") or 0),
        "duration_ms": int(card.get("duration_ms") or 0),
        "turn": int(card.get("turn") or 0) or None,
        "client_turn_id": metadata_client_turn_id(normalized_metadata),
        "prompt_preview": prompt_preview,
        "response_preview": response_preview,
        "arguments_preview": arguments_preview,
        "stage": card.get("stage") or card.get("display_name"),
        "brain_events": build_runtime_brain_events(
            runtime_id=runtime_message_id,
            card=card,
            from_entity=from_entity,
            to_entity=to_entity,
            operation_label=operation_label,
            title=title,
            preview=preview,
            prompt_preview=prompt_preview,
            response_preview=response_preview,
            arguments_preview=arguments_preview,
        ),
    }


def serialize_monitor_runtime_detail(
    *,
    runtime_message_id: int,
    chatroom_id: int,
    chat_title: str,
    project_id: int | None,
    project_name: str | None,
    card: dict[str, Any],
    created_at: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = serialize_monitor_runtime_item(
        runtime_message_id=runtime_message_id,
        chatroom_id=chatroom_id,
        chat_title=chat_title,
        project_id=project_id,
        project_name=project_name,
        card=card,
        created_at=created_at,
        metadata=metadata,
    )
    payload["card"] = card
    payload["detail_sections"] = build_runtime_detail_sections(
        runtime_id=runtime_message_id,
        card=card,
        operation_label=str(payload.get("operation_label") or ""),
        from_entity=payload.get("from_entity") if isinstance(payload.get("from_entity"), str) else None,
        to_entity=payload.get("to_entity") if isinstance(payload.get("to_entity"), str) else None,
        client_turn_id=payload.get("client_turn_id") if isinstance(payload.get("client_turn_id"), str) else None,
    )
    return payload


def serialize_monitor_approval_queue_item(
    item: ApprovalQueueItem,
    *,
    chat_title: str | None = None,
    project_name: str | None = None,
    task_run: TaskRun | None = None,
) -> dict[str, Any]:
    payload = serialize_approval_queue_item(item)
    request_payload = payload.get("request_payload") if isinstance(payload.get("request_payload"), dict) else {}
    resolution_payload = payload.get("resolution_payload") if isinstance(payload.get("resolution_payload"), dict) else {}
    latest_event_type = task_run.events[-1].event_type if task_run and task_run.events else None

    request_preview = compact_preview(
        item.summary
        or request_payload.get("blocked_reason")
        or request_payload.get("request_payload")
        or request_payload
    )
    resolution_preview = compact_preview(
        item.resolution_note
        or resolution_payload.get("replay_result_preview")
        or resolution_payload.get("followup_error")
        or resolution_payload.get("request_payload")
        or resolution_payload
    )

    payload.update(
        {
            "chat_title": chat_title,
            "project_name": project_name,
            "task_run_title": task_run.title if task_run else None,
            "task_run_status": task_run.status if task_run else None,
            "run_kind": task_run.run_kind if task_run else None,
            "latest_event_type": latest_event_type,
            "request_preview": request_preview,
            "resolution_preview": resolution_preview,
            "resume_supported": bool(request_payload.get("resume_supported")),
            "action_taken": resolution_payload.get("action_taken"),
            "replay_status": resolution_payload.get("replay_status"),
            "replay_success": resolution_payload.get("replay_success"),
            "followup_attempted": resolution_payload.get("followup_attempted"),
            "followup_status": resolution_payload.get("followup_status"),
            "followup_reason": resolution_payload.get("followup_reason"),
            "followup_error": resolution_payload.get("followup_error"),
            "followup_message_id": resolution_payload.get("followup_message_id"),
        }
    )
    return payload


def serialize_monitor_compaction_item(
    event: TaskRunEvent,
    *,
    task_run: TaskRun | None = None,
    chat_title: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    payload = parse_metadata(event.payload_json)
    diagnostics = payload.get("selector_diagnostics") if isinstance(payload.get("selector_diagnostics"), dict) else {}
    projection = build_context_compaction_projection(diagnostics, fallback_summary=event.summary)
    return {
        "id": event.id,
        "task_run_id": event.task_run_id,
        "chatroom_id": task_run.chatroom_id if task_run else None,
        "project_id": task_run.project_id if task_run else None,
        "chat_title": chat_title,
        "project_name": project_name,
        "run_kind": task_run.run_kind if task_run else None,
        "task_run_title": task_run.title if task_run else None,
        "task_run_status": task_run.status if task_run else None,
        "agent_name": event.agent_name,
        "event_type": event.event_type,
        "summary": event.summary,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        **projection,
        "developer": diagnostics.get("developer") if isinstance(diagnostics.get("developer"), dict) else {},
        "user": diagnostics.get("user") if isinstance(diagnostics.get("user"), dict) else {},
        "payload": payload,
    }


def serialize_monitor_policy_decision_item(
    event: TaskRunEvent,
    *,
    task_run: TaskRun | None = None,
    chat_title: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    payload = parse_metadata(event.payload_json)
    contract = payload.get("policy_decision")
    summary_payload = payload.get("policy_decision_summary")
    if isinstance(contract, dict) and contract.get("kind") == "policy_decision":
        decision_summary = summarize_policy_decision(contract)
        decision_payload = contract
    elif isinstance(summary_payload, dict):
        decision_summary = summarize_policy_decision(summary_payload)
        decision_payload = None
    else:
        decision_summary = {}
        decision_payload = None

    return {
        "id": event.id,
        "task_run_id": event.task_run_id,
        "chatroom_id": task_run.chatroom_id if task_run else None,
        "project_id": task_run.project_id if task_run else None,
        "chat_title": chat_title,
        "project_name": project_name,
        "run_kind": task_run.run_kind if task_run else None,
        "task_run_title": task_run.title if task_run else None,
        "task_run_status": task_run.status if task_run else None,
        "agent_name": event.agent_name,
        "event_index": event.event_index,
        "event_type": event.event_type,
        "event_summary": event.summary,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "accepted": decision_summary.get("accepted"),
        "decision_type": decision_summary.get("decision_type"),
        "subject_kind": decision_summary.get("subject_kind"),
        "subject_id": decision_summary.get("subject_id"),
        "stage_name": decision_summary.get("stage_name"),
        "policy_decision_summary": decision_summary,
        "policy_decision": decision_payload,
    }


def upsert_runtime_card_projection(
    db: Session,
    *,
    message_id: int,
    chatroom_id: int,
    task_run_id: int | None,
    card: dict[str, Any],
    created_at: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write a structured projection row alongside the runtime_card message."""
    from models.database import RuntimeCardProjection

    projection_card = dict(card)
    client_turn_id = metadata_client_turn_id(metadata or {})
    if client_turn_id and not projection_card.get("client_turn_id"):
        projection_card["client_turn_id"] = client_turn_id

    card_type = str(card.get("type") or "runtime")
    agent_name = str(card.get("agent") or card.get("from_agent") or "system")
    tool_name = str(card.get("tool")) if card.get("tool") else None
    model_name = str(card.get("model")) if card.get("model") else None
    turn = int(card.get("turn") or 0) or None
    tokens_in = int(card.get("tokens_in") or 0)
    tokens_out = int(card.get("tokens_out") or 0)
    duration_ms = int(card.get("duration_ms") or 0)
    success = card.get("success")
    if success is not None:
        success = bool(success)

    title = build_runtime_title(card)
    preview = build_runtime_preview(card)
    prompt_preview = extract_prompt_preview(card)
    response_preview = compact_preview(card.get("response") or card.get("result"))

    projection = RuntimeCardProjection(
        message_id=message_id,
        chatroom_id=chatroom_id,
        task_run_id=task_run_id,
        card_type=card_type,
        agent_name=agent_name,
        tool_name=tool_name,
        model_name=model_name,
        turn=turn,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        success=success,
        title=title,
        preview=preview,
        prompt_preview=prompt_preview,
        response_preview=response_preview,
        card_json=json.dumps(projection_card, ensure_ascii=False) if projection_card else None,
        created_at=created_at or datetime.now(),
    )
    db.add(projection)


def backfill_runtime_card_projections(db: Session) -> int:
    """Backfill projection table from existing messages. Returns count of rows inserted."""
    from models.database import Message, RuntimeCardProjection

    existing_ids = {
        row[0]
        for row in db.query(RuntimeCardProjection.message_id).all()
    }

    rows = (
        db.query(Message)
        .filter(Message.message_type == "runtime_card")
        .order_by(Message.id.asc())
        .all()
    )

    inserted = 0
    for message in rows:
        if message.id in existing_ids:
            continue
        card, task_run_id, metadata = extract_runtime_card_message_payload(message)
        if not card:
            continue
        upsert_runtime_card_projection(
            db,
            message_id=message.id,
            chatroom_id=message.chatroom_id,
            task_run_id=task_run_id,
            card=card,
            created_at=message.created_at,
            metadata=metadata,
        )
        inserted += 1
        if inserted % 200 == 0:
            db.flush()

    if inserted > 0:
        db.commit()
    return inserted
