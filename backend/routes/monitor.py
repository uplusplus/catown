# -*- coding: utf-8 -*-
"""Catown backstage monitoring routes."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from agents.collaboration import collaboration_coordinator
from models.database import Agent, Chatroom, Message, Project, get_db
from monitoring import monitor_log_buffer

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

INPUT_PRICE_PER_1K = 0.03
OUTPUT_PRICE_PER_1K = 0.06
LOG_STREAM_POLL_INTERVAL = 0.75
LOG_STREAM_LIMIT = 200

monitor_log_buffer.install()


def _parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _compact_preview(value: Any, limit: int = 220) -> str:
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


def _metadata_client_turn_id(metadata: dict[str, Any]) -> str | None:
    client_turn_id = metadata.get("client_turn_id")
    if isinstance(client_turn_id, str) and client_turn_id:
        return client_turn_id

    card = metadata.get("card")
    if isinstance(card, dict):
        card_turn_id = card.get("client_turn_id")
        if isinstance(card_turn_id, str) and card_turn_id:
            return card_turn_id
    return None


def _runtime_entities(card: dict[str, Any]) -> tuple[str | None, str | None]:
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


def _extract_prompt_preview(card: dict[str, Any]) -> str:
    prompt_messages = card.get("prompt_messages")
    if not isinstance(prompt_messages, str) or not prompt_messages.strip():
        return ""

    try:
        parsed = json.loads(prompt_messages)
    except json.JSONDecodeError:
        return _compact_preview(prompt_messages)

    if isinstance(parsed, list):
        for message in reversed(parsed):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return _compact_preview(content)
            if isinstance(content, list):
                chunks: list[str] = []
                for chunk in content:
                    if not isinstance(chunk, dict):
                        continue
                    text = chunk.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
                if chunks:
                    return _compact_preview(" ".join(chunks))

    return _compact_preview(parsed)



def _build_runtime_title(card: dict[str, Any]) -> str:
    card_type = str(card.get("type") or "runtime")
    agent = str(card.get("agent") or card.get("from_agent") or "system")

    if card_type == "llm_call":
        return f"{agent} -> LLM"
    if card_type == "tool_call":
        tool_name = str(card.get("tool") or "tool")
        return f"{agent} used {tool_name}"
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



def _build_runtime_preview(card: dict[str, Any]) -> str:
    candidates = [
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


def _normalize_log_level(level: str | None) -> str:
    normalized = (level or "all").strip().lower()
    if normalized == "warn":
        return "warning"
    return normalized


def _serialize_runtime_card_detail(message: Message, chatroom: Chatroom, project: Project | None, card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": message.id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "chatroom_id": chatroom.id,
        "chat_title": chatroom.title,
        "project_id": project.id if project else None,
        "project_name": project.name if project else None,
        "card": card,
    }


@router.get("/logs")
async def get_monitor_logs(
    limit: int = Query(200, ge=20, le=1000),
    level: str = Query("all"),
    query: str | None = Query(None, max_length=200),
):
    entries = monitor_log_buffer.list_entries(
        limit=limit,
        level=_normalize_log_level(level),
        query=query,
    )
    return {
        "captured_at": datetime.now().isoformat(),
        "latest_id": monitor_log_buffer.latest_id(),
        "entries": entries,
    }


@router.get("/logs/stream")
async def stream_monitor_logs(
    request: Request,
    cursor: int = Query(0, ge=0),
    level: str = Query("all"),
    query: str | None = Query(None, max_length=200),
    once: bool = Query(False),
):
    normalized_level = _normalize_log_level(level)

    async def event_generator():
        last_seen_id = cursor
        idle_ticks = 0

        while True:
            if await request.is_disconnected():
                break

            entries = monitor_log_buffer.list_entries(
                limit=LOG_STREAM_LIMIT,
                after_id=last_seen_id,
                level=normalized_level,
                query=query,
            )
            if entries:
                for entry in entries:
                    last_seen_id = max(last_seen_id, int(entry["id"]))
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                idle_ticks = 0
                if once:
                    break
                continue

            idle_ticks += 1
            if once:
                break
            if idle_ticks >= int(15 / LOG_STREAM_POLL_INTERVAL):
                yield ": ping\n\n"
                idle_ticks = 0

            await asyncio.sleep(LOG_STREAM_POLL_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runtime-cards/{message_id}")
async def get_monitor_runtime_card_detail(message_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(Message, Chatroom, Project)
        .join(Chatroom, Message.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .filter(Message.id == message_id, Message.message_type == "runtime_card")
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Runtime card not found")

    message, chatroom, project = row
    metadata = _parse_metadata(message.metadata_json)
    card = metadata.get("card") if isinstance(metadata.get("card"), dict) else None
    if not card:
        raise HTTPException(status_code=404, detail="Runtime card payload is unavailable")

    return _serialize_runtime_card_detail(message, chatroom, project, card)


@router.get("/overview")
async def get_monitor_overview(
    runtime_limit: int = Query(24, ge=6, le=80),
    summary_window: int = Query(240, ge=40, le=1000),
    message_limit: int = Query(18, ge=6, le=60),
    db: Session = Depends(get_db),
):
    active_agent_count = db.query(Agent).filter(Agent.is_active.is_(True)).count()
    agent_count = db.query(Agent).count()
    project_count = db.query(Project).count()
    chatroom_count = db.query(Chatroom).count()
    visible_chat_count = db.query(Chatroom).filter(Chatroom.is_visible_in_chat_list.is_(True)).count()
    message_count = db.query(Message).filter(Message.message_type != "runtime_card").count()
    runtime_card_count = db.query(Message).filter(Message.message_type == "runtime_card").count()
    latest_message = db.query(Message).order_by(desc(Message.created_at)).first()

    summary_rows = (
        db.query(Message, Chatroom, Project)
        .join(Chatroom, Message.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .filter(Message.message_type == "runtime_card")
        .order_by(desc(Message.created_at))
        .limit(summary_window)
        .all()
    )

    recent_rows = summary_rows[:runtime_limit]
    recent_runtime: list[dict[str, Any]] = []
    tool_summary: dict[str, dict[str, float]] = defaultdict(lambda: {"call_count": 0, "failure_count": 0, "total_duration_ms": 0.0})
    agent_summary: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "llm_calls": 0,
            "tool_calls": 0,
            "errors": 0,
            "token_input": 0,
            "token_output": 0,
        }
    )

    llm_calls = 0
    tool_calls = 0
    tool_errors = 0
    input_tokens = 0
    output_tokens = 0

    for message, chatroom, project in summary_rows:
        metadata = _parse_metadata(message.metadata_json)
        card = metadata.get("card") if isinstance(metadata.get("card"), dict) else None
        if not card:
            continue

        card_type = str(card.get("type") or message.content or "runtime")
        agent_name = card.get("agent") or card.get("from_agent") or "system"

        if card_type == "llm_call":
            llm_calls += 1
            token_in = int(card.get("tokens_in") or 0)
            token_out = int(card.get("tokens_out") or 0)
            input_tokens += token_in
            output_tokens += token_out
            agent_summary[str(agent_name)]["llm_calls"] += 1
            agent_summary[str(agent_name)]["token_input"] += token_in
            agent_summary[str(agent_name)]["token_output"] += token_out
        elif card_type == "tool_call":
            tool_calls += 1
            tool_name = str(card.get("tool") or "tool")
            duration_ms = float(card.get("duration_ms") or 0)
            success = bool(card.get("success", True))
            tool_summary[tool_name]["call_count"] += 1
            tool_summary[tool_name]["total_duration_ms"] += duration_ms
            agent_summary[str(agent_name)]["tool_calls"] += 1
            if not success:
                tool_errors += 1
                tool_summary[tool_name]["failure_count"] += 1
                agent_summary[str(agent_name)]["errors"] += 1
        elif card_type in {"gate_rejected", "error"}:
            agent_summary[str(agent_name)]["errors"] += 1

    estimated_cost = round((input_tokens / 1000 * INPUT_PRICE_PER_1K) + (output_tokens / 1000 * OUTPUT_PRICE_PER_1K), 4)

    top_tools = []
    for tool_name, metrics in sorted(tool_summary.items(), key=lambda item: (-item[1]["call_count"], item[0]))[:8]:
        count = int(metrics["call_count"])
        top_tools.append(
            {
                "tool_name": tool_name,
                "call_count": count,
                "failure_count": int(metrics["failure_count"]),
                "avg_duration_ms": round(metrics["total_duration_ms"] / count, 1) if count else 0,
            }
        )

    by_agent = []
    for agent_name, metrics in sorted(agent_summary.items(), key=lambda item: (-(item[1]["llm_calls"] + item[1]["tool_calls"]), item[0])):
        token_total = int(metrics["token_input"] + metrics["token_output"])
        estimated_agent_cost = round(
            (metrics["token_input"] / 1000 * INPUT_PRICE_PER_1K) + (metrics["token_output"] / 1000 * OUTPUT_PRICE_PER_1K),
            4,
        )
        by_agent.append(
            {
                "agent_name": agent_name,
                "llm_calls": int(metrics["llm_calls"]),
                "tool_calls": int(metrics["tool_calls"]),
                "errors": int(metrics["errors"]),
                "token_input": int(metrics["token_input"]),
                "token_output": int(metrics["token_output"]),
                "token_total": token_total,
                "estimated_cost_usd": estimated_agent_cost,
            }
        )

    for message, chatroom, project in recent_rows:
        metadata = _parse_metadata(message.metadata_json)
        card = metadata.get("card") if isinstance(metadata.get("card"), dict) else None
        if not card:
            continue
        from_entity, to_entity = _runtime_entities(card)
        recent_runtime.append(
            {
                "id": message.id,
                "type": str(card.get("type") or message.content or "runtime"),
                "title": _build_runtime_title(card),
                "preview": _build_runtime_preview(card),
                "created_at": message.created_at.isoformat() if message.created_at else None,
                "chatroom_id": chatroom.id,
                "chat_title": chatroom.title,
                "project_id": project.id if project else None,
                "project_name": project.name if project else None,
                "agent": card.get("agent") or card.get("from_agent"),
                "from_entity": from_entity,
                "to_entity": to_entity,
                "model": card.get("model"),
                "tool_name": card.get("tool"),
                "success": card.get("success"),
                "tokens_in": int(card.get("tokens_in") or 0),
                "tokens_out": int(card.get("tokens_out") or 0),
                "duration_ms": int(card.get("duration_ms") or 0),
                "turn": int(card.get("turn") or 0) or None,
                "client_turn_id": _metadata_client_turn_id(metadata),
                "prompt_preview": _extract_prompt_preview(card),
                "response_preview": _compact_preview(card.get("response") or card.get("result")),
                "arguments_preview": _compact_preview(card.get("arguments")),
                "stage": card.get("stage") or card.get("display_name"),
            }
        )

    recent_message_rows = (
        db.query(Message, Chatroom, Project)
        .join(Chatroom, Message.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .filter(Message.message_type != "runtime_card")
        .order_by(desc(Message.created_at))
        .limit(message_limit)
        .all()
    )
    agent_name_by_id = {agent.id: agent.name for agent in db.query(Agent).all()}
    recent_messages = [
        {
            "id": message.id,
            "chatroom_id": chatroom.id,
            "chat_title": chatroom.title,
            "project_id": project.id if project else None,
            "project_name": project.name if project else None,
            "agent_name": agent_name_by_id.get(message.agent_id),
            "content": message.content or "",
            "content_preview": " ".join((message.content or "").split())[:220],
            "message_type": message.message_type,
            "created_at": message.created_at.isoformat() if message.created_at else None,
            "client_turn_id": _metadata_client_turn_id(_parse_metadata(message.metadata_json)),
        }
        for message, chatroom, project in recent_message_rows
    ]

    return {
        "captured_at": datetime.now().isoformat(),
        "system": {
            "status": "healthy",
            "version": "1.0.0",
            "stats": {
                "agents": agent_count,
                "active_agents": active_agent_count,
                "projects": project_count,
                "chatrooms": chatroom_count,
                "visible_chats": visible_chat_count,
                "messages": message_count,
                "runtime_cards": runtime_card_count,
            },
            "features": {
                "llm_enabled": True,
                "websocket_enabled": True,
                "tools_enabled": True,
                "memory_enabled": True,
            },
            "collaboration": {
                "active_collaborators": len(collaboration_coordinator.collaborators),
                "chatrooms": len(collaboration_coordinator.chatroom_agents),
                "pending_tasks": len(collaboration_coordinator.task_registry),
                "status": "active",
            },
            "last_message_at": latest_message.created_at.isoformat() if latest_message and latest_message.created_at else None,
        },
        "usage_window": {
            "runtime_cards_considered": len(summary_rows),
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": estimated_cost,
            "pricing": {
                "input_per_1k": INPUT_PRICE_PER_1K,
                "output_per_1k": OUTPUT_PRICE_PER_1K,
            },
            "by_agent": by_agent,
            "top_tools": top_tools,
        },
        "recent_runtime": recent_runtime,
        "recent_messages": recent_messages,
    }
