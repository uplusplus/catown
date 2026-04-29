# -*- coding: utf-8 -*-
"""Shared helpers for extracting durable agent memories from turn responses."""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.identity import normalize_agent_type


logger = logging.getLogger("catown.memory")

_MEMORY_EXTRACTION_SYSTEM_PROMPT = (
    "You are a memory extraction system. Analyze the conversation and extract "
    "important information worth remembering. Return a JSON array of objects with fields: "
    "'content' (the memory text, concise), 'type' (one of: fact, preference, decision, context), "
    "'importance' (1-10).\n\n"
    "Rules:\n"
    "- Extract factual information, user preferences, decisions made, and important context\n"
    "- Skip greetings, small talk, simple confirmations, and generic Q&A\n"
    "- Each memory should be self-contained and meaningful\n"
    "- Max 3 memories per extraction\n"
    "- If nothing worth remembering, return an empty array []\n"
    "- Return ONLY the JSON array, no explanation"
)


def build_memory_extraction_messages(
    *,
    agent_type: str,
    user_message: str,
    agent_response: str,
) -> list[dict[str, str]]:
    """Build the prompt used to extract memories from one turn."""

    return [
        {
            "role": "system",
            "content": _MEMORY_EXTRACTION_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"User: {user_message[:500]}\n\n"
                f"Agent {agent_type}: {agent_response[:800]}"
            ),
        },
    ]


def parse_memory_extraction_response(result: str | None) -> list[dict[str, Any]]:
    """Parse one memory-extraction model response into a normalized list."""

    if not result:
        return []
    resolved = str(result).strip()
    if not resolved:
        return []
    if resolved.startswith("```"):
        resolved = resolved.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(resolved)
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def persist_extracted_memories(
    db: Any,
    *,
    memory_cls: Any,
    agent_id: int,
    memories: list[dict[str, Any]],
    limit: int = 3,
) -> int:
    """Persist validated memories and return the number of saved rows."""

    persisted = 0
    try:
        for memory in memories[:limit]:
            content = str(memory.get("content", "")).strip()
            if len(content) < 10:
                continue
            memory_type = str(memory.get("type", "context") or "context").strip() or "context"
            importance = min(max(int(memory.get("importance", 5)), 1), 10)
            db.add(
                memory_cls(
                    agent_id=agent_id,
                    memory_type=memory_type,
                    content=content,
                    importance=importance,
                )
            )
            persisted += 1
        db.commit()
        return persisted
    except Exception:
        db.rollback()
        raise


async def extract_agent_memories(
    agent_id: int,
    agent_type: str,
    user_message: str,
    agent_response: str,
) -> int:
    """Extract durable memories for one agent turn response."""

    try:
        from llm.client import get_llm_client_for_agent
        from models.database import Memory, get_db

        llm = get_llm_client_for_agent(normalize_agent_type(agent_type))
        result = await llm.chat(
            build_memory_extraction_messages(
                agent_type=agent_type,
                user_message=user_message,
                agent_response=agent_response,
            ),
            temperature=0.3,
            max_tokens=500,
        )
        memories = parse_memory_extraction_response(result)
        if not memories:
            return 0

        db = next(get_db())
        try:
            persisted = persist_extracted_memories(
                db,
                memory_cls=Memory,
                agent_id=agent_id,
                memories=memories,
            )
        finally:
            db.close()

        if persisted:
            logger.info("[Memory] Extracted %s memories for %s", persisted, agent_type)
        return persisted
    except Exception as exc:
        logger.debug("[Memory] Extraction failed: %s", exc)
        return 0
