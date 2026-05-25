# -*- coding: utf-8 -*-
"""
Shared audit recording helpers for chat runtime paths.

Provides reusable callback factories that plug into the non-stream/stream
turn executor hooks, writing LLMCall / ToolCall / Event records to the
audit tables alongside the existing pipeline engine audit collection.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models.audit import Event, LLMCall, ToolCall

logger = logging.getLogger("catown.audit")


def _truncate(text: Any, limit: int = 100000) -> Optional[str]:
    if text is None:
        return None
    s = str(text)
    return s[:limit] if len(s) > limit else s


def _json_dumps_safe(value: Any, limit: int = 100000) -> Optional[str]:
    if value is None:
        return None
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        s = str(value)
    return s[:limit] if len(s) > limit else s


# ────────────────────────────── LLM Call Recording ──────────────────────────


def create_llm_call_record(
    *,
    db: Session,
    run_id: Optional[int],
    stage_id: Optional[int],
    agent_name: str,
    turn_index: int,
    model: str,
    system_prompt: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Create an LLMCall record before the LLM invocation.

    Returns a mutable state dict to pass through the turn executor callbacks.
    """
    record = LLMCall(
        run_id=run_id,
        stage_id=stage_id,
        agent_name=agent_name,
        turn_index=turn_index,
        model=model,
        system_prompt=_truncate(system_prompt, 50000),
        messages=_json_dumps_safe(messages[-10:] if messages else None, 100000),
    )
    db.add(record)
    db.flush()
    return {"record": record, "started_at": time.time()}


def finalize_llm_call_success(
    *,
    db: Session,
    state: Dict[str, Any],
    content: Optional[str],
    tool_calls: Optional[List[Dict[str, Any]]],
    usage: Optional[Dict[str, Any]],
    agent_name: str,
    stage_name: Optional[str] = None,
    run_id: Optional[int] = None,
) -> Optional[LLMCall]:
    """Finalize an LLMCall record after a successful LLM response."""
    record: Optional[LLMCall] = state.get("record")
    if record is None:
        return None

    started_at = state.get("started_at", time.time())
    record.response_content = _truncate(content, 100000)
    record.response_tool_calls = _json_dumps_safe(tool_calls, 100000)
    record.duration_ms = int((time.time() - started_at) * 1000)
    if usage:
        record.token_input = usage.get("prompt_tokens", 0)
        record.token_output = usage.get("completion_tokens", 0)

    db.add(record)
    db.flush()

    # Write an event for the LLM call
    db.add(Event(
        run_id=run_id or record.run_id,
        event_type="llm_call",
        agent_name=agent_name,
        stage_name=stage_name,
        summary=(
            f"LLM #{record.turn_index}: "
            f"{record.token_input}in/{record.token_output}out, "
            f"{record.duration_ms}ms"
        ),
        payload=_json_dumps_safe({
            "turn": record.turn_index,
            "model": record.model,
            "tokens_in": record.token_input,
            "tokens_out": record.token_output,
            "duration_ms": record.duration_ms,
            "content_preview": (content or "")[:200],
        }),
    ))
    db.flush()
    return record


def finalize_llm_call_error(
    *,
    db: Session,
    state: Dict[str, Any],
    error: Exception,
    agent_name: str,
    stage_name: Optional[str] = None,
    run_id: Optional[int] = None,
) -> None:
    """Finalize an LLMCall record after an error."""
    record: Optional[LLMCall] = state.get("record")
    if record is None:
        return

    started_at = state.get("started_at", time.time())
    record.error = str(error)[:5000]
    record.duration_ms = int((time.time() - started_at) * 1000)
    db.add(record)
    db.flush()

    db.add(Event(
        run_id=run_id or record.run_id,
        event_type="error",
        agent_name=agent_name,
        stage_name=stage_name,
        summary=f"LLM error: {str(error)[:200]}",
        payload=_json_dumps_safe({
            "turn": record.turn_index,
            "model": record.model,
            "error": str(error)[:2000],
            "duration_ms": record.duration_ms,
        }),
    ))
    db.flush()


# ────────────────────────────── Tool Call Recording ──────────────────────────


def record_tool_call(
    *,
    db: Session,
    llm_call_id: Optional[int],
    run_id: Optional[int],
    stage_id: Optional[int],
    agent_name: str,
    tool_name: str,
    arguments: str,
    result_summary: str,
    result_length: int,
    success: bool,
    duration_ms: int,
    stage_name: Optional[str] = None,
) -> ToolCall:
    """Record a completed tool call."""
    record = ToolCall(
        llm_call_id=llm_call_id,
        run_id=run_id,
        stage_id=stage_id,
        agent_name=agent_name,
        tool_name=tool_name,
        arguments=_truncate(arguments, 50000),
        result_summary=_truncate(result_summary, 500),
        result_length=result_length,
        success=success,
        duration_ms=duration_ms,
    )
    db.add(record)
    db.flush()

    db.add(Event(
        run_id=run_id,
        event_type="tool_call",
        agent_name=agent_name,
        stage_name=stage_name,
        summary=f"{tool_name}({'ok' if success else 'fail'}, {result_length} chars, {duration_ms}ms)",
        payload=_json_dumps_safe({
            "tool": tool_name,
            "success": success,
            "result_length": result_length,
            "result_preview": (result_summary or "")[:200],
            "duration_ms": duration_ms,
        }),
    ))
    db.flush()
    return record


# ────────────────────────────── Generic Event Recording ──────────────────────


def record_audit_event(
    *,
    db: Session,
    run_id: Optional[int],
    event_type: str,
    agent_name: Optional[str] = None,
    stage_name: Optional[str] = None,
    summary: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Event:
    """Record a generic audit event."""
    event = Event(
        run_id=run_id,
        event_type=event_type,
        agent_name=agent_name,
        stage_name=stage_name,
        summary=summary,
        payload=_json_dumps_safe(payload),
    )
    db.add(event)
    db.flush()
    return event


# ────────────────────────────── Non-Stream Turn Callbacks ────────────────────


def make_nonstream_audit_callbacks(
    *,
    db: Session,
    run_id: Optional[int],
    stage_id: Optional[int],
    agent_name: str,
    stage_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create callback dict for execute_non_stream_turn_loop that records audit data.

    Returns dict with keys: before_llm_call, on_llm_response, on_llm_error
    """
    _state: Dict[str, Any] = {}
    _tool_started_at: float = 0.0

    def before_llm_call(frame, turn_state):
        _state.clear()
        state = create_llm_call_record(
            db=db,
            run_id=run_id,
            stage_id=stage_id,
            agent_name=agent_name,
            turn_index=frame.turn_index,
            model=getattr(frame, "model", None) or "unknown",
            system_prompt=None,
            messages=frame.messages,
        )
        _state.update(state)
        return state

    async def on_llm_response(frame, turn_state):
        nonlocal _tool_started_at
        state = {**_state}
        record = finalize_llm_call_success(
            db=db,
            state=state,
            content=frame.content,
            tool_calls=[
                {"function": {"name": tc.get("function", {}).get("name"), "arguments": tc.get("function", {}).get("arguments", "")}}
                for tc in (frame.normalized_tool_calls or [])
            ] if frame.normalized_tool_calls else None,
            usage=frame.usage,
            agent_name=agent_name,
            stage_name=stage_name,
            run_id=run_id,
        )
        _state["llm_call_id"] = record.id if record else None
        _tool_started_at = time.time()
        db.commit()

    async def on_llm_error(frame, exc, turn_state):
        state = {**_state}
        finalize_llm_call_error(
            db=db,
            state=state,
            error=exc,
            agent_name=agent_name,
            stage_name=stage_name,
            run_id=run_id,
        )
        db.commit()

    async def _on_tool_round(frame, tool_results, turn_state):
        """Record tool calls to audit tables after each tool round."""
        nonlocal _tool_started_at
        llm_call_id = _state.get("llm_call_id")
        executed = frame.executed_tool_calls or []
        duration_ms = int((time.time() - _tool_started_at) * 1000) if _tool_started_at else 0

        for i, tool_call in enumerate(executed):
            fn_name = tool_call.get("function", {}).get("name", "")
            fn_args = tool_call.get("function", {}).get("arguments", "")
            result = tool_results[i] if i < len(tool_results) else None
            result_text = str(getattr(result, "result", "") or "") if result else ""
            success = getattr(result, "success", True) if result else True

            record_tool_call(
                db=db,
                llm_call_id=llm_call_id,
                run_id=run_id,
                stage_id=stage_id,
                agent_name=agent_name,
                tool_name=fn_name,
                arguments=fn_args,
                result_summary=result_text[:500],
                result_length=len(result_text),
                success=success,
                duration_ms=duration_ms,
                stage_name=stage_name,
            )
        _tool_started_at = time.time()
        db.commit()

    return {
        "before_llm_call": before_llm_call,
        "on_llm_response": on_llm_response,
        "on_llm_error": on_llm_error,
        "on_tool_round": _on_tool_round,
    }


# ────────────────────────────── Stream Turn Audit Hook ───────────────────────


def chain_before_event_callbacks(*callbacks):
    """
    Chain multiple before_event callbacks into one.
    Each callback is called in order. None callbacks are skipped.
    """
    valid = [cb for cb in callbacks if cb is not None]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]

    async def combined(frame, event, turn_state):
        for cb in valid:
            result = cb(frame, event, turn_state)
            if hasattr(result, "__await__"):
                await result

    return combined


def make_stream_audit_before_event(
    *,
    db: Session,
    run_id: Optional[int],
    stage_id: Optional[int],
    agent_name: str,
    stage_name: Optional[str] = None,
):
    """
    Create a before_event callback for iter_stream_turn_events that records
    LLM call and tool call audit data from streaming events.
    """
    _llm_state: Dict[str, Any] = {}
    _tool_started_at: float = 0.0

    async def before_event(frame, event, turn_state):
        nonlocal _tool_started_at
        event_type = event.get("type")

        if event_type == "agent_start":
            # Prepare LLM call record
            _llm_state.clear()
            state = create_llm_call_record(
                db=db,
                run_id=run_id,
                stage_id=stage_id,
                agent_name=agent_name,
                turn_index=frame.turn_index,
                model=getattr(frame, "model", None) or "unknown",
                system_prompt=frame.system_prompt,
                messages=frame.messages,
            )
            _llm_state.update(state)

        elif event_type == "done":
            # Finalize LLM call
            usage = event.get("usage")
            content = event.get("full_content") or frame.llm_content
            raw_tool_calls = event.get("tool_calls")
            state = {**_llm_state}
            finalize_llm_call_success(
                db=db,
                state=state,
                content=content,
                tool_calls=raw_tool_calls,
                usage=usage,
                agent_name=agent_name,
                stage_name=stage_name,
                run_id=run_id,
            )
            db.commit()
            # Store llm_call_id for tool call linking
            _llm_state["llm_call_id"] = state.get("record").id if state.get("record") else None

        elif event_type == "tool_start":
            _tool_started_at = time.time()

        elif event_type == "tool_result":
            tool_name = event.get("tool", "")
            success = event.get("success", True)
            result_preview = event.get("result", "")
            result_length = len(str(result_preview))
            duration_ms = int((time.time() - _tool_started_at) * 1000) if _tool_started_at else 0

            record_tool_call(
                db=db,
                llm_call_id=_llm_state.get("llm_call_id"),
                run_id=run_id,
                stage_id=stage_id,
                agent_name=agent_name,
                tool_name=tool_name,
                arguments=event.get("args", "{}"),
                result_summary=str(result_preview)[:500],
                result_length=result_length,
                success=success,
                duration_ms=duration_ms,
                stage_name=stage_name,
            )
            db.commit()

    return before_event
