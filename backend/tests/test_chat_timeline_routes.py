import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_app(tmp_path):
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

    modules_to_clear = [
        "main",
        "config",
        "models.database",
        "agents.registry",
        "agents.collaboration",
        "tools",
        "llm.client",
        "chatrooms.manager",
        "routes.api",
        "routes.websocket",
        "pipeline.engine",
        "routes.pipeline",
        "services.run_ledger",
        "services.task_activity_projection",
        "services.chat_timeline_projection",
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    import llm.client as llm_mod

    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(return_value={"content": "Mocked agent response.", "tool_calls": None})

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
def client(tmp_path):
    from fastapi.testclient import TestClient

    return TestClient(_make_app(tmp_path), base_url="http://testserver", headers={"X-Catown-Client": "test"})


def test_task_run_timeline_route_returns_canonical_steps(client):
    from models.database import Chatroom, SessionLocal, TaskRun
    from services.run_ledger import append_task_event

    db = SessionLocal()
    try:
        chatroom = Chatroom(title="Timeline route")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="running",
            title="Timeline route run",
            client_turn_id="turn-route-1",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        append_task_event(
            db,
            task_run,
            "llm_request_created",
            agent_name="Valet",
            summary="Request created.",
            payload={"turn": 1, "occurred_at": "2026-05-15T10:00:02"},
        )
        append_task_event(
            db,
            task_run,
            "llm_response_completed",
            agent_name="Valet",
            summary="Response completed.",
            payload={"turn": 1, "occurred_at": "2026-05-15T10:00:01"},
        )
        task_run_id = task_run.id
    finally:
        db.close()

    response = client.get(f"/api/task-runs/{task_run_id}/timeline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "task_run"
    assert payload["version"] == 2
    assert [step["event_type"] for step in payload["steps"]] == [
        "llm_request_created",
        "llm_response_completed",
    ]
    assert [step["sequence"] for step in payload["steps"]] == [1, 2]


def test_chatroom_timeline_route_returns_aggregated_steps(client):
    from models.database import Chatroom, SessionLocal, TaskRun
    from services.run_ledger import append_task_event

    db = SessionLocal()
    try:
        chatroom = Chatroom(title="Chat timeline route")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = TaskRun(
            chatroom_id=chatroom.id,
            run_kind="chat_turn",
            status="running",
            title="Timeline route run",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        append_task_event(db, task_run, "agent_turn_started", agent_name="Valet", summary="Started.")
        chatroom_id = chatroom.id
    finally:
        db.close()

    response = client.get(f"/api/chatrooms/{chatroom_id}/timeline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "chatroom"
    assert payload["chatroom_id"] == chatroom_id
    assert payload["steps"][0]["actor"] == "Valet"
    assert payload["steps"][0]["scope_sequence"] == 1
