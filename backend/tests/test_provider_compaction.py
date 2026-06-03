# -*- coding: utf-8 -*-
"""Tests for ADR-035 local provider compaction checkpoints."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_local_compaction_checkpoint_persists_sections_and_session_metadata(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_compaction import (
        LOCAL_COMPACTION_KIND,
        create_local_compaction_checkpoint,
        load_local_compaction_checkpoint,
    )
    from services.provider_sessions import ensure_provider_session

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Local Compaction Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            title="Continue ADR-035 Phase 4",
            user_request="Continue implementing provider compaction.",
            status="running",
            target_agent_name="Developer",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        client = SimpleNamespace(
            base_url="https://api.openai.com/v1",
            model="gpt-5.5",
            provider_mode="responses_http",
        )
        provider_session = ensure_provider_session(
            db,
            task_run=task_run,
            agent_name="Developer",
            llm_client=client,
        )
        checkpoint_snapshot = {
            "event_count": 12,
            "latest_event_type": "llm_call_completed",
            "latest_event_index": 12,
            "latest_agent_turn": {
                "response_preview": "Implemented the local fallback checkpoint writer.",
            },
            "continuation_state_summary": "Phase 4 is active after provider sessions landed.",
            "policy_decision_summary": "Do not claim previous_response_id reduces billed tokens.",
            "scheduler_runtime_summary": "No delegated work is pending.",
            "pipeline_inbox_summary": "No pipeline inbox entries.",
            "orchestration_handoff_inbox_summary": "No handoff entries.",
            "continuation_cursor_summary": "Run focused backend validation next.",
            "continuation_cursor": {"next_action": "Run provider compaction tests."},
            "subagent_handles": {"summary": "No active subagents."},
            "latest_context_budget_event": {
                "reason_summary": "Prompt budget is under model-window pressure threshold.",
                "detail_summary": "Tool-output summaries saved estimated tokens.",
            },
            "turn_local_state": {
                "prior_round_summaries": [{"summary": "Provider sessions are persisted."}],
                "tool_results": [
                    {
                        "tool_name": "run_shell",
                        "status": "succeeded",
                        "result": "pytest passed",
                        "metadata": {
                            "tool_output_artifact": {
                                "path": "state/tool_outputs/run-shell.json",
                            }
                        },
                    }
                ],
            },
        }

        checkpoint = create_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            checkpoint_snapshot=checkpoint_snapshot,
            reason="explicit_admin_request",
        )

        assert checkpoint is not None
        checkpoint_id = checkpoint["compact_checkpoint_id"]
        assert checkpoint_id.startswith("localcmp_")
        assert checkpoint["kind"] == LOCAL_COMPACTION_KIND

        checkpoint_path = Path(checkpoint["path"])
        assert checkpoint_path.exists()
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert load_local_compaction_checkpoint(checkpoint_id) == payload

        expected_sections = {
            "current_objective",
            "constraints_and_decisions",
            "files_and_artifacts",
            "tool_outcomes",
            "pending_steps",
            "risks_and_open_questions",
        }
        assert set(payload["sections"]) == expected_sections
        assert "Continue implementing provider compaction" in payload["sections"]["current_objective"]
        assert "previous_response_id" in payload["sections"]["constraints_and_decisions"]
        assert "state/tool_outputs/run-shell.json" in payload["sections"]["files_and_artifacts"]
        assert "Provider sessions are persisted" in payload["sections"]["tool_outcomes"]
        assert "Run provider compaction tests" in payload["sections"]["pending_steps"]
        assert "Prompt budget" in payload["sections"]["risks_and_open_questions"]

        refreshed_session = (
            db.query(fresh_db.LLMProviderSession)
            .filter(fresh_db.LLMProviderSession.id == provider_session.id)
            .one()
        )
        assert refreshed_session.compact_checkpoint_id == checkpoint_id
        metadata = json.loads(refreshed_session.metadata_json)
        assert metadata["last_local_compaction_checkpoint_id"] == checkpoint_id
        assert metadata["local_compaction_checkpoints"][-1]["id"] == checkpoint_id
        assert metadata["local_compaction_checkpoints"][-1]["path"] == str(checkpoint_path)
        assert metadata["local_compaction_checkpoints"][-1]["reason"] == "explicit_admin_request"
    finally:
        db.close()


def test_local_compaction_trigger_skips_prompt_budget_pressure(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_compaction import maybe_create_local_compaction_checkpoint
    from services.provider_sessions import ensure_provider_session

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Prompt Budget Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            title="Prompt budget only",
            user_request="Do not compact on selector budget pressure.",
            status="running",
            target_agent_name="Developer",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        provider_session = ensure_provider_session(
            db,
            task_run=task_run,
            agent_name="Developer",
            llm_client=SimpleNamespace(
                base_url="https://api.openai.com/v1",
                model="gpt-5.5",
                provider_mode="responses_http",
            ),
        )
        checkpoint = maybe_create_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            checkpoint_snapshot={"latest_agent_turn": {"response_preview": "No compaction."}},
            diagnostics={
                "event_kind": "selection_truncation",
                "context_pressure_kind": "prompt_budget_pressure",
                "summary": {"dropped_count": 1, "truncated_count": 1},
            },
            agent_name="Developer",
        )

        assert checkpoint is None
        assert db.query(fresh_db.TaskRunEvent).filter(fresh_db.TaskRunEvent.event_type == "context_compaction").count() == 0
    finally:
        db.close()


def test_local_compaction_trigger_emits_semantic_event_for_model_window_pressure(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_compaction import maybe_create_local_compaction_checkpoint
    from services.provider_sessions import ensure_provider_session

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Model Window Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            title="Model window pressure",
            user_request="Create a compaction checkpoint when the real model window is under pressure.",
            status="running",
            target_agent_name="Developer",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        provider_session = ensure_provider_session(
            db,
            task_run=task_run,
            agent_name="Developer",
            llm_client=SimpleNamespace(
                base_url="https://api.openai.com/v1",
                model="gpt-5.5",
                provider_mode="responses_http",
            ),
        )
        checkpoint_snapshot = {
            "latest_context_budget_event": {"event_id": 44},
            "latest_agent_turn": {"response_preview": "Ready to compact."},
            "continuation_cursor_summary": "Resume with the latest tool result.",
            "turn_local_state": {
                "tool_results": [
                    {
                        "tool_name": "run_shell",
                        "status": "succeeded",
                        "result": "validation passed",
                    }
                ]
            },
        }
        diagnostics = {
            "event_kind": "selection_truncation",
            "context_pressure_kind": "model_window_pressure",
            "selector": {"usage_band": {"band": "orange", "ratio": 0.75}},
            "summary": {"dropped_count": 2, "truncated_count": 1},
        }
        checkpoint = maybe_create_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            checkpoint_snapshot=checkpoint_snapshot,
            diagnostics=diagnostics,
            agent_name="Developer",
        )

        assert checkpoint is not None
        checkpoint_id = checkpoint["compact_checkpoint_id"]
        duplicate_checkpoint = maybe_create_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            checkpoint_snapshot=checkpoint_snapshot,
            diagnostics=diagnostics,
            agent_name="Developer",
        )
        assert duplicate_checkpoint is None
        event = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.event_type == "context_compaction")
            .one()
        )
        payload = json.loads(event.payload_json)
        assert event.summary == f"Developer created local compaction checkpoint {checkpoint_id} (model_window_pressure)."
        assert payload["semantic_compaction"] is True
        assert payload["event_kind"] == "semantic_compaction"
        assert payload["context_pressure_kind"] == "model_window_pressure"
        assert payload["selector_diagnostics"]["semantic_compaction"] is True
        assert payload["selector_diagnostics"]["summary"] == {"dropped_count": 2, "truncated_count": 1}
        assert payload["provider_compaction"]["id"] == checkpoint_id
        assert payload["provider_compaction"]["kind"] == "local_structured_summary"
        assert "validation passed" in payload["provider_compaction"]["sections"]["tool_outcomes"]

        refreshed_session = (
            db.query(fresh_db.LLMProviderSession)
            .filter(fresh_db.LLMProviderSession.id == provider_session.id)
            .one()
        )
        assert refreshed_session.compact_checkpoint_id == checkpoint_id
        metadata = json.loads(refreshed_session.metadata_json)
        assert metadata["local_compaction_checkpoints"][-1]["source_context_budget_event_id"] == 44
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_mode", ["responses_http", "responses_websocket"])
async def test_provider_native_compaction_persists_output_window_and_lineage(fresh_db, provider_mode):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_compaction import (
        PROVIDER_NATIVE_COMPACTION_KIND,
        maybe_create_provider_or_local_compaction_checkpoint,
    )
    from services.provider_sessions import ensure_provider_session, serialize_provider_session, update_provider_session_response

    class FakeResponsesClient:
        async def compact_responses_context(self, messages, tools=None):
            return {
                "id": "cmp_abc",
                "response_id": "resp_compact",
                "output": [
                    {"type": "compaction", "id": "cmp_abc", "summary": "Old context compacted."},
                    {"role": "assistant", "content": "Ready to continue."},
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 0, "total_tokens": 21},
                "request": {"model": "gpt-5.5", "input_item_count": len(messages), "tool_count": len(tools or [])},
                "provider_request": {"estimated_full_input_tokens": 100},
                "response": {"id": "resp_compact", "status": "completed"},
                "compaction_item": {"type": "compaction", "id": "cmp_abc"},
            }

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Provider Native Compaction")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            title="Provider-native compact",
            user_request="Compact with the Responses endpoint.",
            status="running",
            target_agent_name="Developer",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        provider_session = ensure_provider_session(
            db,
            task_run=task_run,
            agent_name="Developer",
            llm_client=SimpleNamespace(
                base_url="https://api.openai.com/v1",
                model="gpt-5.5",
                provider_mode=provider_mode,
            ),
        )
        provider_session = update_provider_session_response(db, provider_session, response_id="resp_before_compact")
        checkpoint = await maybe_create_provider_or_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            llm_client=FakeResponsesClient(),
            messages=[
                {"role": "system", "content": "System rules"},
                {"role": "user", "content": "Continue"},
                {"role": "assistant", "content": "Ready"},
            ],
            tools=[{"type": "function", "function": {"name": "run_shell"}}],
            checkpoint_snapshot={
                "event_count": 6,
                "latest_context_budget_event": {"event_id": 77},
            },
            diagnostics={
                "context_pressure_kind": "model_window_pressure",
                "summary": {"dropped_count": 0, "truncated_count": 0},
            },
            agent_name="Developer",
        )

        assert checkpoint is not None
        checkpoint_id = checkpoint["compact_checkpoint_id"]
        assert checkpoint_id == "providercmp_cmp_abc"
        assert checkpoint["kind"] == PROVIDER_NATIVE_COMPACTION_KIND
        assert checkpoint["output_item_count"] == 2
        payload = json.loads(Path(checkpoint["path"]).read_text(encoding="utf-8"))
        assert payload["output"][0]["type"] == "compaction"
        assert payload["source_last_response_id"] == "resp_before_compact"

        event = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.event_type == "context_compaction")
            .one()
        )
        assert event.summary == (
            f"Developer created provider-native compaction checkpoint {checkpoint_id} "
            "(model_window_pressure)."
        )
        event_payload = json.loads(event.payload_json)
        assert event_payload["provider_compaction"]["kind"] == PROVIDER_NATIVE_COMPACTION_KIND
        assert event_payload["provider_compaction"]["output_item_count"] == 2
        assert event_payload["provider_compaction"]["usage"]["total_tokens"] == 21

        refreshed_session = (
            db.query(fresh_db.LLMProviderSession)
            .filter(fresh_db.LLMProviderSession.id == provider_session.id)
            .one()
        )
        assert refreshed_session.compact_checkpoint_id == checkpoint_id
        metadata = json.loads(refreshed_session.metadata_json)
        assert metadata["last_provider_compaction_checkpoint_id"] == checkpoint_id
        assert metadata["provider_compaction_checkpoints"][-1]["source_last_response_id"] == "resp_before_compact"
        session_payload = serialize_provider_session(refreshed_session)
        assert session_payload["provider_compaction"]["ready_for_next_request"] is True

        refreshed_session = update_provider_session_response(db, refreshed_session, response_id="resp_after_compact")
        session_payload = serialize_provider_session(refreshed_session)
        assert session_payload["provider_compaction"]["ready_for_next_request"] is False
    finally:
        db.close()


@pytest.mark.asyncio
async def test_provider_native_failure_falls_back_to_local_checkpoint(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_compaction import LOCAL_COMPACTION_KIND, maybe_create_provider_or_local_compaction_checkpoint
    from services.provider_sessions import ensure_provider_session

    class FailingResponsesClient:
        async def compact_responses_context(self, messages, tools=None):
            raise RuntimeError("compact unavailable")

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Provider Fallback Compaction")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            title="Provider fallback",
            user_request="Fall back locally if provider compact fails.",
            status="running",
            target_agent_name="Developer",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        provider_session = ensure_provider_session(
            db,
            task_run=task_run,
            agent_name="Developer",
            llm_client=SimpleNamespace(
                base_url="https://api.openai.com/v1",
                model="gpt-5.5",
                provider_mode="responses_http",
            ),
        )
        checkpoint = await maybe_create_provider_or_local_compaction_checkpoint(
            db,
            provider_session=provider_session,
            task_run=task_run,
            llm_client=FailingResponsesClient(),
            messages=[{"role": "user", "content": "Continue"}],
            checkpoint_snapshot={
                "latest_agent_turn": {"response_preview": "Fallback target."},
                "latest_context_budget_event": {"event_id": 78},
            },
            diagnostics={
                "context_pressure_kind": "model_window_pressure",
                "summary": {"dropped_count": 0, "truncated_count": 1},
            },
            agent_name="Developer",
        )

        assert checkpoint is not None
        assert checkpoint["kind"] == LOCAL_COMPACTION_KIND
        assert checkpoint["provider_native_error"] == "RuntimeError: compact unavailable"
        event = (
            db.query(fresh_db.TaskRunEvent)
            .filter(fresh_db.TaskRunEvent.event_type == "context_compaction")
            .one()
        )
        payload = json.loads(event.payload_json)
        assert payload["provider_compaction"]["kind"] == LOCAL_COMPACTION_KIND
        assert payload["provider_compaction"]["provider_native_error"] == "RuntimeError: compact unavailable"
    finally:
        db.close()
