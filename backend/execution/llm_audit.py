# -*- coding: utf-8 -*-
"""Shared LLM-call audit helpers for legacy and project-first runtimes."""
import json
import time

from models.audit import LLMCall


def start_llm_call(*, run_id: int | None, stage_id: int | None, agent_name: str, turn_index: int, model: str, system_prompt: str | None, messages: list[dict]) -> tuple[LLMCall, float]:
    """Create an in-memory LLMCall record and its start timestamp."""
    record = LLMCall(
        run_id=run_id,
        stage_id=stage_id,
        agent_name=agent_name,
        turn_index=turn_index,
        model=model,
        system_prompt=system_prompt[:50000] if system_prompt else None,
        messages=json.dumps(messages[-10:], ensure_ascii=False)[:100000],
    )
    return record, time.time()


def fail_llm_call(db, record: LLMCall, started_at: float, error: Exception) -> None:
    """Persist a failed LLM call."""
    record.error = str(error)[:5000]
    record.duration_ms = int((time.time() - started_at) * 1000)
    db.add(record)
    db.commit()


def finalize_llm_call(db, record: LLMCall, started_at: float, *, content: str, tool_calls: list | None, usage: dict | None) -> LLMCall:
    """Persist a completed LLM call and return the flushed row."""
    record.response_content = content[:100000] if content else None
    record.response_tool_calls = json.dumps(
        [{"id": tc.get("id"), "function": tc.get("function")} for tc in tool_calls],
        ensure_ascii=False,
    ) if tool_calls else None
    record.duration_ms = int((time.time() - started_at) * 1000)
    if usage:
        record.token_input = usage.get("prompt_tokens", 0)
        record.token_output = usage.get("completion_tokens", 0)
    db.add(record)
    db.flush()
    return record
