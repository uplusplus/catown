# -*- coding: utf-8 -*-
"""Tests for ADR-035 provider session state helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace


def test_provider_session_reuses_active_chatroom_agent_model_session(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_sessions import (
        ensure_provider_session,
        serialize_provider_session,
        touch_provider_session_request,
    )

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Provider Session Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        client = SimpleNamespace(
            base_url="https://api.openai.com/v1",
            model="gpt-5.5",
            provider_mode="responses_http",
        )
        first = ensure_provider_session(
            db,
            task_run=None,
            chatroom_id=chatroom.id,
            agent_name="Valet",
            llm_client=client,
        )
        first = touch_provider_session_request(db, first)
        second = ensure_provider_session(
            db,
            task_run=None,
            chatroom_id=chatroom.id,
            agent_name="Valet",
            llm_client=client,
        )

        assert first is not None
        assert second is not None
        assert second.id == first.id
        assert second.provider_mode == "responses_http"
        assert second.provider_host == "api.openai.com"
        assert second.model_name == "gpt-5.5"
        assert second.turn_count == 1
        assert serialize_provider_session(second)["state_reused"] is False
    finally:
        db.close()


def test_provider_session_records_response_state_for_next_turn(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_sessions import (
        ensure_provider_session,
        serialize_provider_session,
        touch_provider_session_request,
        update_provider_session_response,
    )

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Responses Session Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        client = SimpleNamespace(
            base_url="https://api.openai.com/v1",
            model="gpt-5.5",
            provider_mode="responses_http",
        )
        provider_session = ensure_provider_session(
            db,
            task_run=None,
            chatroom_id=chatroom.id,
            agent_name="Developer",
            llm_client=client,
        )
        provider_session = touch_provider_session_request(db, provider_session)
        provider_session = update_provider_session_response(
            db,
            provider_session,
            response_id="resp_123",
            provider_conversation_id="conv_123",
            compact_checkpoint_id="compact_123",
        )

        payload = serialize_provider_session(provider_session)
        assert payload["last_response_id"] == "resp_123"
        assert payload["previous_response_id"] == "resp_123"
        assert payload["provider_conversation_id"] == "conv_123"
        assert payload["compact_checkpoint_id"] == "compact_123"
        assert payload["state_reused"] is True
    finally:
        db.close()


def test_provider_session_reloaded_compaction_ready_until_response_advances(fresh_db, tmp_path):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    from services.provider_sessions import (
        ensure_provider_session,
        serialize_provider_session,
        update_provider_session_response,
    )

    compact_payload_path = tmp_path / "providercmp_cmp_reload.json"
    compact_payload_path.write_text(
        """
        {
          "id": "providercmp_cmp_reload",
          "kind": "provider_native_response_compaction",
          "output": [
            {"type": "compaction", "id": "cmp_reload", "summary": "Older context."}
          ]
        }
        """,
        encoding="utf-8",
    )

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Reloaded Responses Session Chat")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        client = SimpleNamespace(
            base_url="https://api.openai.com/v1",
            model="gpt-5.5",
            provider_mode="responses_http",
        )
        provider_session = ensure_provider_session(
            db,
            task_run=None,
            chatroom_id=chatroom.id,
            agent_name="Developer",
            llm_client=client,
        )
        provider_session = update_provider_session_response(
            db,
            provider_session,
            response_id="resp_before_compact",
            compact_checkpoint_id="providercmp_cmp_reload",
        )
        provider_session.metadata_json = json.dumps(
            {
                "provider_compaction_checkpoints": [
                    {
                        "id": "providercmp_cmp_reload",
                        "kind": "provider_native_response_compaction",
                        "path": str(compact_payload_path),
                        "source_last_response_id": "resp_before_compact",
                        "output_item_count": 1,
                        "source_context_budget_event_id": 88,
                    }
                ]
            }
        )
        db.add(provider_session)
        db.commit()
        session_id = provider_session.id
    finally:
        db.close()

    db = fresh_db.SessionLocal()
    try:
        reloaded_session = (
            db.query(fresh_db.LLMProviderSession)
            .filter(fresh_db.LLMProviderSession.id == session_id)
            .one()
        )
        payload = serialize_provider_session(reloaded_session)
        assert payload["provider_compaction"] == {
            "id": "providercmp_cmp_reload",
            "kind": "provider_native_response_compaction",
            "path": str(compact_payload_path),
            "source_last_response_id": "resp_before_compact",
            "output_item_count": 1,
            "source_context_budget_event_id": 88,
            "ready_for_next_request": True,
        }

        reloaded_session = update_provider_session_response(
            db,
            reloaded_session,
            response_id="resp_after_compact",
        )
        payload = serialize_provider_session(reloaded_session)
        assert payload["provider_compaction"]["ready_for_next_request"] is False
    finally:
        db.close()
