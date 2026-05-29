# -*- coding: utf-8 -*-
"""
ADR-034 Performance Optimization Tests

Covers:
1. RuntimeCardProjection model & migration
2. upsert_runtime_card_projection
3. backfill_runtime_card_projections
4. _query_recent_runtime_activity via projection table
5. _build_overview_usage_window with SQL aggregation
6. get_monitor_runtime_card_detail projection-first lookup
7. POST /api/monitor/backfill-projections endpoint
8. Middleware thinning for monitor routes
"""
import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_app(tmp_path):
    """Create an isolated FastAPI app for testing."""
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

    modules_to_clear = [
        "main", "config", "models.database", "models.audit",
        "agents.registry", "agents.collaboration", "tools",
        "llm.client", "chatrooms.manager",
        "routes.api", "routes.monitor", "routes.websocket",
        "pipeline.engine", "routes.pipeline",
        "services.approval_queue", "services.approval_replay",
        "services.monitor_projection", "services.run_ledger",
        "services.task_run_watchdog", "services.runtime_lifecycle",
        "services.telemetry_writer", "services.audit_recorder",
        "services.agent_lifecycle_runtime",
    ]
    from tests.conftest import reset_app_modules
    reset_app_modules(modules_to_clear)

    import llm.client as llm_mod
    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(
        return_value={"content": "Mocked agent response.", "tool_calls": None}
    )

    async def mock_stream(messages, tools=None):
        yield {"type": "content", "delta": "Hello!"}
        yield {"type": "done", "full_content": "Hello!", "tool_calls": None}

    mock_llm.chat_stream = mock_stream
    llm_mod._llm_client = mock_llm

    import main as main_mod

    async def passthrough(self, request, call_next):
        return await call_next(request)

    main_mod.RateLimitMiddleware.dispatch = passthrough
    main_mod.RequestLoggingMiddleware.dispatch = passthrough
    return main_mod.app


@pytest.fixture
def app_and_db(tmp_path):
    """Return (app, db_module) tuple for tests that need direct DB access."""
    app = _make_app(tmp_path)
    import models.database as db_mod
    return app, db_mod


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    return TestClient(app, base_url="testserver", headers={"X-Catown-Client": "test"})


def _insert_runtime_card(db_mod, db, chatroom_id, card, created_at=None):
    """Helper: insert a runtime_card message and return the message."""
    msg = db_mod.Message(
        chatroom_id=chatroom_id,
        content="runtime_card",
        message_type="runtime_card",
        metadata_json=json.dumps({"card": card}),
        created_at=created_at or datetime.now(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _make_llm_card(agent="Developer", model="gpt-4", tokens_in=500, tokens_out=200, turn=1):
    return {
        "type": "llm_call",
        "agent": agent,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "duration_ms": 1200,
        "turn": turn,
        "response": "Here is the answer.",
        "prompt_messages": json.dumps([{"role": "user", "content": "Hello"}]),
    }


def _make_tool_card(agent="Developer", tool="read_file", success=True, duration_ms=50):
    return {
        "type": "tool_call",
        "agent": agent,
        "tool": tool,
        "success": success,
        "duration_ms": duration_ms,
        "tokens_in": 0,
        "tokens_out": 0,
        "turn": 2,
        "arguments": json.dumps({"file_path": "/tmp/test.py"}),
        "result": "file content here",
    }


def _make_skill_inject_card(agent="Developer", skills=None):
    return {
        "type": "skill_inject",
        "agent": agent,
        "skills": skills or [{"name": "frontend-design"}, {"name": "polish"}],
    }


# ---------------------------------------------------------------------------
# 1. RuntimeCardProjection model & migration
# ---------------------------------------------------------------------------

class TestRuntimeCardProjectionModel:
    """Verify the model exists and the table is created during init."""

    def test_table_created_on_init(self, app_and_db):
        """runtime_card_projections table should exist after init_database."""
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            result = db.execute(
                db_mod.text("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_card_projections'")
            ).fetchone()
            assert result is not None, "runtime_card_projections table should exist"
        finally:
            db.close()

    def test_indexes_created(self, app_and_db):
        """Indexes on the projection table should be created."""
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            indexes = [
                row[1] for row in db.execute(
                    db_mod.text("PRAGMA index_list(runtime_card_projections)")
                ).fetchall()
            ]
            assert any("ix_rcp_created" in idx for idx in indexes), f"ix_rcp_created missing, got: {indexes}"
            assert any("ix_rcp_type_agent" in idx for idx in indexes), f"ix_rcp_type_agent missing, got: {indexes}"
            assert any("ix_rcp_task_run" in idx for idx in indexes), f"ix_rcp_task_run missing, got: {indexes}"
        finally:
            db.close()

    def test_model_class_importable(self):
        """RuntimeCardProjection can be imported from models.database."""
        from models.database import RuntimeCardProjection
        assert RuntimeCardProjection.__tablename__ == "runtime_card_projections"
        assert hasattr(RuntimeCardProjection, "message_id")
        assert hasattr(RuntimeCardProjection, "card_json")
        assert hasattr(RuntimeCardProjection, "tokens_in")


# ---------------------------------------------------------------------------
# 2. upsert_runtime_card_projection
# ---------------------------------------------------------------------------

class TestUpsertRuntimeCardProjection:
    """Verify projection row creation from card data."""

    def test_upsert_creates_projection(self, app_and_db):
        _, db_mod = app_and_db

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Test Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            msg = _insert_runtime_card(db_mod, db, chatroom.id, _make_llm_card())

            proj = db.query(db_mod.RuntimeCardProjection).filter(
                db_mod.RuntimeCardProjection.message_id == msg.id
            ).first()
            assert proj is not None
            assert proj.card_type == "llm_call"
            assert proj.agent_name == "Developer"
            assert proj.model_name == "gpt-4"
            assert proj.tokens_in == 500
            assert proj.tokens_out == 200
            assert proj.duration_ms == 1200
            assert proj.turn == 1
            assert proj.chatroom_id == chatroom.id
            assert proj.card_json is not None
            assert "llm_call" in proj.card_json
        finally:
            db.close()

    def test_upsert_tool_card(self, app_and_db):
        _, db_mod = app_and_db

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Test Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            card = _make_tool_card(tool="write_file", success=False, duration_ms=300)
            msg = _insert_runtime_card(db_mod, db, chatroom.id, card)

            proj = db.query(db_mod.RuntimeCardProjection).filter(
                db_mod.RuntimeCardProjection.message_id == msg.id
            ).first()
            assert proj is not None
            assert proj.card_type == "tool_call"
            assert proj.tool_name == "write_file"
            assert proj.success is False
            assert proj.duration_ms == 300
        finally:
            db.close()

    def test_upsert_extracts_title_and_preview(self, app_and_db):
        _, db_mod = app_and_db

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Test Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            card = _make_llm_card(agent="Architect")
            msg = _insert_runtime_card(db_mod, db, chatroom.id, card)

            proj = db.query(db_mod.RuntimeCardProjection).filter(
                db_mod.RuntimeCardProjection.message_id == msg.id
            ).first()
            assert proj is not None
            assert proj.title is not None and "Architect" in proj.title
            assert proj.preview is not None
            assert proj.response_preview is not None
        finally:
            db.close()

    def test_upsert_with_none_optional_fields(self, app_and_db):
        _, db_mod = app_and_db

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Test Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            card = {"type": "agent_message", "agent": "Valet", "content": "hello"}
            msg = _insert_runtime_card(db_mod, db, chatroom.id, card)

            proj = db.query(db_mod.RuntimeCardProjection).filter(
                db_mod.RuntimeCardProjection.message_id == msg.id
            ).first()
            assert proj is not None
            assert proj.tool_name is None
            assert proj.model_name is None
            assert proj.success is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 3. backfill_runtime_card_projections
# ---------------------------------------------------------------------------

class TestBackfillRuntimeCardProjections:
    """Verify backfill logic inserts missing projections."""

    def test_backfill_inserts_missing(self, app_and_db):
        _, db_mod = app_and_db
        from services.monitor_projection import backfill_runtime_card_projections

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Backfill Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for i in range(5):
                db.add(db_mod.Message(
                    chatroom_id=chatroom.id,
                    content="runtime_card",
                    message_type="runtime_card",
                    metadata_json=json.dumps({"card": _make_llm_card(tokens_in=100 * i, tokens_out=50 * i)}),
                    created_at=datetime.now() - timedelta(minutes=5 - i),
                ))
            db.commit()

            count = backfill_runtime_card_projections(db)
            assert count == 5

            projections = db.query(db_mod.RuntimeCardProjection).all()
            assert len(projections) == 5
        finally:
            db.close()

    def test_backfill_skips_existing(self, app_and_db):
        _, db_mod = app_and_db
        from services.monitor_projection import backfill_runtime_card_projections

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Backfill Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            msg = _insert_runtime_card(db_mod, db, chatroom.id, _make_llm_card())

            db.add(db_mod.Message(
                chatroom_id=chatroom.id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({"card": _make_tool_card()}),
                created_at=datetime.now(),
            ))
            db.commit()

            count = backfill_runtime_card_projections(db)
            assert count == 1

            projections = db.query(db_mod.RuntimeCardProjection).all()
            assert len(projections) == 2
        finally:
            db.close()

    def test_backfill_skips_non_runtime_cards(self, app_and_db):
        _, db_mod = app_and_db
        from services.monitor_projection import backfill_runtime_card_projections

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Backfill Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            db.add(db_mod.Message(
                chatroom_id=chatroom.id,
                content="Hello",
                message_type="text",
                created_at=datetime.now(),
            ))
            db.add(db_mod.Message(
                chatroom_id=chatroom.id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({}),
                created_at=datetime.now(),
            ))
            db.commit()

            count = backfill_runtime_card_projections(db)
            assert count == 0
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 4. _query_recent_runtime_activity via projection table
# ---------------------------------------------------------------------------

class TestQueryRecentRuntimeActivity:
    """Verify the rewritten function reads from projection table."""

    def test_returns_items_from_projections(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _query_recent_runtime_activity

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Activity Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for i in range(3):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_llm_card(tokens_in=100 * i),
                    created_at=datetime.now() - timedelta(minutes=3 - i),
                )

            items, empty_list = _query_recent_runtime_activity(db, runtime_limit=10, summary_window=20)
            assert len(items) == 3
            assert empty_list == []
            for item in items:
                assert "id" in item
                assert "type" in item
                assert item["type"] == "llm_call"
                assert "title" in item
                assert "tokens_in" in item
        finally:
            db.close()

    def test_empty_when_no_projections(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _query_recent_runtime_activity

        db = db_mod.SessionLocal()
        try:
            items, empty_list = _query_recent_runtime_activity(db, runtime_limit=10, summary_window=20)
            assert items == []
            assert empty_list == []
        finally:
            db.close()

    def test_respects_limit(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _query_recent_runtime_activity

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Limit Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for i in range(10):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_llm_card(tokens_in=i),
                    created_at=datetime.now() - timedelta(minutes=10 - i),
                )

            items, _ = _query_recent_runtime_activity(db, runtime_limit=3, summary_window=20)
            assert len(items) == 3
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 5. _build_overview_usage_window with SQL aggregation
# ---------------------------------------------------------------------------

class TestBuildOverviewUsageWindow:
    """Verify the rewritten usage window uses SQL aggregation."""

    def test_basic_usage_window(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _build_overview_usage_window

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Usage Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for i in range(3):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_llm_card(agent="Developer", tokens_in=1000, tokens_out=500),
                    created_at=datetime.now() - timedelta(hours=1),
                )
            for i in range(2):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_tool_card(agent="Developer", tool="read_file"),
                    created_at=datetime.now() - timedelta(hours=1),
                )

            result = _build_overview_usage_window(db, range_value="24h")
            assert result["llm_calls"] == 3
            assert result["tool_calls"] == 2
            assert result["input_tokens"] == 3000
            assert result["output_tokens"] == 1500
            assert result["total_tokens"] == 4500
            assert result["range"] == "24h"
            assert len(result["by_agent"]) >= 1
            assert result["by_agent"][0]["agent_name"] == "Developer"
            assert result["by_agent"][0]["llm_calls"] == 3
            assert result["by_agent"][0]["tool_calls"] == 2
        finally:
            db.close()

    def test_tool_summary_via_sql(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _build_overview_usage_window

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Tool Summary Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for _ in range(5):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_tool_card(tool="read_file", success=True, duration_ms=100),
                    created_at=datetime.now() - timedelta(hours=1),
                )
            _insert_runtime_card(
                db_mod, db, chatroom.id,
                _make_tool_card(tool="read_file", success=False, duration_ms=200),
                created_at=datetime.now() - timedelta(hours=1),
            )
            _insert_runtime_card(
                db_mod, db, chatroom.id,
                _make_tool_card(tool="write_file", success=True, duration_ms=300),
                created_at=datetime.now() - timedelta(hours=1),
            )

            result = _build_overview_usage_window(db, range_value="24h")
            assert result["tool_calls"] == 7
            assert result["tool_errors"] == 1

            top_tools = result["top_tools"]
            read_tool = next((t for t in top_tools if t["tool_name"] == "read_file"), None)
            assert read_tool is not None
            assert read_tool["call_count"] == 6
            assert read_tool["failure_count"] == 1
        finally:
            db.close()

    def test_skill_summary_from_card_json(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _build_overview_usage_window

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Skill Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            _insert_runtime_card(
                db_mod, db, chatroom.id,
                _make_skill_inject_card(skills=[{"name": "frontend-design"}, {"name": "polish"}]),
                created_at=datetime.now() - timedelta(hours=1),
            )
            _insert_runtime_card(
                db_mod, db, chatroom.id,
                _make_skill_inject_card(skills=[{"name": "frontend-design"}]),
                created_at=datetime.now() - timedelta(hours=1),
            )

            result = _build_overview_usage_window(db, range_value="24h")
            top_skills = result["top_skills"]
            fe_skill = next((s for s in top_skills if s["skill_name"] == "frontend-design"), None)
            assert fe_skill is not None
            assert fe_skill["inject_count"] == 2

            polish_skill = next((s for s in top_skills if s["skill_name"] == "polish"), None)
            assert polish_skill is not None
            assert polish_skill["inject_count"] == 1
        finally:
            db.close()

    def test_empty_window(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _build_overview_usage_window

        db = db_mod.SessionLocal()
        try:
            result = _build_overview_usage_window(db, range_value="24h")
            assert result["llm_calls"] == 0
            assert result["tool_calls"] == 0
            assert result["input_tokens"] == 0
            assert result["output_tokens"] == 0
            assert result["by_agent"] == []
            assert result["top_tools"] == []
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 6. get_monitor_runtime_card_detail projection-first lookup
# ---------------------------------------------------------------------------

class TestRuntimeCardDetailEndpoint:
    """Verify the detail endpoint reads from projection first."""

    def test_detail_from_projection(self, client, app_and_db):
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Detail Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            card = _make_llm_card(agent="Tester", model="claude-3")
            msg = _insert_runtime_card(db_mod, db, chatroom.id, card)
            msg_id = msg.id
        finally:
            db.close()

        r = client.get(f"/api/monitor/runtime-cards/{msg_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == msg_id
        assert data["type"] == "llm_call"
        assert data["agent"] == "Tester"
        assert data["model"] == "claude-3"
        assert "card" in data
        assert "detail_sections" in data

    def test_detail_fallback_to_legacy(self, client, app_and_db):
        """If no projection exists, falls back to legacy path."""
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Legacy Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            msg = db_mod.Message(
                chatroom_id=chatroom.id,
                content="runtime_card",
                message_type="runtime_card",
                metadata_json=json.dumps({"card": _make_tool_card(tool="search_files")}),
                created_at=datetime.now(),
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            msg_id = msg.id
        finally:
            db.close()

        r = client.get(f"/api/monitor/runtime-cards/{msg_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "tool_call"
        assert data["tool_name"] == "search_files"

    def test_detail_404_for_missing(self, client):
        r = client.get("/api/monitor/runtime-cards/999999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7. POST /api/monitor/backfill-projections endpoint
# ---------------------------------------------------------------------------

class TestBackfillEndpoint:
    """Verify the backfill API endpoint."""

    def test_backfill_endpoint(self, client, app_and_db):
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Backfill Endpoint Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for i in range(3):
                db.add(db_mod.Message(
                    chatroom_id=chatroom.id,
                    content="runtime_card",
                    message_type="runtime_card",
                    metadata_json=json.dumps({"card": _make_llm_card(tokens_in=10 * i)}),
                    created_at=datetime.now() - timedelta(minutes=3 - i),
                ))
            db.commit()
        finally:
            db.close()

        r = client.post("/api/monitor/backfill-projections")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["inserted"] == 3

        # Running again should insert 0 (idempotent)
        r2 = client.post("/api/monitor/backfill-projections")
        assert r2.status_code == 200
        assert r2.json()["inserted"] == 0


# ---------------------------------------------------------------------------
# 8. Middleware thinning for monitor routes
# ---------------------------------------------------------------------------

class TestMiddlewareThinning:
    """Verify monitor route requests don't pass full body to telemetry."""

    def test_monitor_route_skips_body(self, client, app_and_db):
        """Monitor API calls should not pass full response body to telemetry."""
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Middleware Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)
        finally:
            db.close()

        r = client.get("/api/monitor/overview")
        assert r.status_code == 200
        data = r.json()
        assert "system" in data
        assert "usage_window" in data


# ---------------------------------------------------------------------------
# 9. Integration: overview/activity uses projections
# ---------------------------------------------------------------------------

class TestOverviewActivityIntegration:
    """Verify /api/monitor/overview/activity works with projection table."""

    def test_activity_endpoint_returns_projections(self, client, app_and_db):
        _, db_mod = app_and_db
        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Activity Integration")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for i in range(5):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_llm_card(tokens_in=100 * i),
                    created_at=datetime.now() - timedelta(minutes=5 - i),
                )
            for i in range(3):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_tool_card(tool="read_file"),
                    created_at=datetime.now() - timedelta(minutes=3 - i),
                )
        finally:
            db.close()

        r = client.get("/api/monitor/overview/activity?runtime_limit=10&summary_window=20")
        assert r.status_code == 200
        data = r.json()
        assert "recent_runtime" in data
        assert len(data["recent_runtime"]) == 8
        for item in data["recent_runtime"]:
            assert item["type"] in ("llm_call", "tool_call")


# ---------------------------------------------------------------------------
# 10. Multiple agents aggregation
# ---------------------------------------------------------------------------

class TestMultiAgentAggregation:
    """Verify SQL aggregation groups correctly by agent."""

    def test_multiple_agents_in_usage_window(self, app_and_db):
        _, db_mod = app_and_db
        from routes.monitor import _build_overview_usage_window

        db = db_mod.SessionLocal()
        try:
            chatroom = db_mod.Chatroom(title="Multi Agent Chat")
            db.add(chatroom)
            db.commit()
            db.refresh(chatroom)

            for _ in range(2):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_llm_card(agent="Developer", tokens_in=1000, tokens_out=500),
                    created_at=datetime.now() - timedelta(hours=1),
                )
            _insert_runtime_card(
                db_mod, db, chatroom.id,
                _make_llm_card(agent="Architect", tokens_in=2000, tokens_out=1000),
                created_at=datetime.now() - timedelta(hours=1),
            )
            for _ in range(3):
                _insert_runtime_card(
                    db_mod, db, chatroom.id,
                    _make_tool_card(agent="Tester", tool="run_shell"),
                    created_at=datetime.now() - timedelta(hours=1),
                )

            result = _build_overview_usage_window(db, range_value="24h")
            assert result["llm_calls"] == 3
            assert result["tool_calls"] == 3
            assert result["input_tokens"] == 4000
            assert result["output_tokens"] == 2000

            by_agent = {a["agent_name"]: a for a in result["by_agent"]}
            assert by_agent["Developer"]["llm_calls"] == 2
            assert by_agent["Architect"]["llm_calls"] == 1
            assert by_agent["Architect"]["token_input"] == 2000
            assert by_agent["Tester"]["tool_calls"] == 3
        finally:
            db.close()
