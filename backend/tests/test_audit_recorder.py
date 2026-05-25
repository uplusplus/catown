# -*- coding: utf-8 -*-
"""Tests for the shared audit recording service."""
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_db_session():
    """Create a mock DB session that tracks add/flush/commit calls."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.commit = MagicMock()
    return db


class TestCreateLLMCallRecord:
    def test_creates_record_with_correct_fields(self):
        from services.audit_recorder import create_llm_call_record

        db = _make_db_session()
        state = create_llm_call_record(
            db=db,
            run_id=42,
            stage_id=7,
            agent_name="developer",
            turn_index=2,
            model="gpt-4o",
            system_prompt="You are a developer.",
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert "record" in state
        assert "started_at" in state
        record = state["record"]
        assert record.run_id == 42
        assert record.stage_id == 7
        assert record.agent_name == "developer"
        assert record.turn_index == 2
        assert record.model == "gpt-4o"
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_truncates_long_system_prompt(self):
        from services.audit_recorder import create_llm_call_record

        db = _make_db_session()
        long_prompt = "x" * 60000
        state = create_llm_call_record(
            db=db,
            run_id=1,
            stage_id=None,
            agent_name="test",
            turn_index=0,
            model="m",
            system_prompt=long_prompt,
        )
        record = state["record"]
        assert len(record.system_prompt) <= 50000

    def test_handles_none_messages(self):
        from services.audit_recorder import create_llm_call_record

        db = _make_db_session()
        state = create_llm_call_record(
            db=db,
            run_id=1,
            stage_id=None,
            agent_name="test",
            turn_index=0,
            model="m",
            messages=None,
        )
        record = state["record"]
        assert record.messages is None


class TestFinalizeLLMCallSuccess:
    def test_sets_response_fields(self):
        from services.audit_recorder import create_llm_call_record, finalize_llm_call_success

        db = _make_db_session()
        state = create_llm_call_record(
            db=db, run_id=1, stage_id=None, agent_name="a",
            turn_index=0, model="m",
        )
        # Simulate some time passing
        state["started_at"] = time.time() - 1.0

        record = finalize_llm_call_success(
            db=db,
            state=state,
            content="Hello world",
            tool_calls=None,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            agent_name="a",
        )

        assert record.response_content == "Hello world"
        assert record.token_input == 100
        assert record.token_output == 50
        assert record.duration_ms >= 900  # ~1 second
        assert db.add.call_count >= 2  # LLMCall + Event
        assert db.flush.call_count >= 2

    def test_handles_none_usage(self):
        from services.audit_recorder import create_llm_call_record, finalize_llm_call_success

        db = _make_db_session()
        state = create_llm_call_record(
            db=db, run_id=1, stage_id=None, agent_name="a",
            turn_index=0, model="m",
        )

        record = finalize_llm_call_success(
            db=db, state=state, content="x", tool_calls=None, usage=None, agent_name="a",
        )
        assert record.token_input == 0
        assert record.token_output == 0


class TestFinalizeLLMCallError:
    def test_records_error(self):
        from services.audit_recorder import create_llm_call_record, finalize_llm_call_error

        db = _make_db_session()
        state = create_llm_call_record(
            db=db, run_id=1, stage_id=None, agent_name="a",
            turn_index=0, model="m",
        )
        state["started_at"] = time.time() - 0.5

        finalize_llm_call_error(
            db=db, state=state, error=ValueError("API timeout"), agent_name="a",
        )
        assert db.add.call_count >= 2  # LLMCall + Event
        # Check the LLMCall record has error set
        llm_call = db.add.call_args_list[0][0][0]
        assert "API timeout" in llm_call.error


class TestRecordToolCall:
    def test_creates_tool_call_and_event(self):
        from services.audit_recorder import record_tool_call

        db = _make_db_session()
        record = record_tool_call(
            db=db,
            llm_call_id=10,
            run_id=42,
            stage_id=7,
            agent_name="developer",
            tool_name="read_file",
            arguments='{"path": "test.py"}',
            result_summary="file contents here",
            result_length=100,
            success=True,
            duration_ms=250,
        )

        assert record.tool_name == "read_file"
        assert record.success is True
        assert record.duration_ms == 250
        assert db.add.call_count == 2  # ToolCall + Event
        assert db.flush.call_count == 2


class TestChainBeforeEventCallbacks:
    @pytest.mark.asyncio
    async def test_chains_multiple_callbacks(self):
        from services.audit_recorder import chain_before_event_callbacks

        calls = []

        async def cb1(frame, event, state):
            calls.append("cb1")

        async def cb2(frame, event, state):
            calls.append("cb2")

        combined = chain_before_event_callbacks(cb1, cb2)
        assert combined is not None

        await combined(None, None, None)
        assert calls == ["cb1", "cb2"]

    def test_returns_none_for_all_none(self):
        from services.audit_recorder import chain_before_event_callbacks

        result = chain_before_event_callbacks(None, None)
        assert result is None

    def test_returns_single_when_one_non_none(self):
        from services.audit_recorder import chain_before_event_callbacks

        async def cb1(frame, event, state):
            pass

        result = chain_before_event_callbacks(None, cb1)
        assert result is cb1


class TestMakeNonstreamAuditCallbacks:
    def test_returns_all_callbacks(self):
        from services.audit_recorder import make_nonstream_audit_callbacks

        db = _make_db_session()
        cbs = make_nonstream_audit_callbacks(
            db=db, run_id=1, stage_id=None, agent_name="a",
        )
        assert "before_llm_call" in cbs
        assert "on_llm_response" in cbs
        assert "on_llm_error" in cbs
        assert "on_tool_round" in cbs

    @pytest.mark.asyncio
    async def test_before_llm_call_creates_record(self):
        from services.audit_recorder import make_nonstream_audit_callbacks

        db = _make_db_session()
        cbs = make_nonstream_audit_callbacks(
            db=db, run_id=1, stage_id=None, agent_name="dev",
        )

        frame = MagicMock()
        frame.turn_index = 0
        frame.messages = [{"role": "user", "content": "hi"}]

        state = cbs["before_llm_call"](frame, None)
        assert "record" in state
        assert db.add.called


class TestRecordAuditEvent:
    def test_creates_event(self):
        from services.audit_recorder import record_audit_event

        db = _make_db_session()
        event = record_audit_event(
            db=db,
            run_id=1,
            event_type="stage_start",
            agent_name="dev",
            stage_name="development",
            summary="Stage started",
            payload={"key": "value"},
        )
        assert event.event_type == "stage_start"
        db.add.assert_called_once()
        db.flush.assert_called_once()
