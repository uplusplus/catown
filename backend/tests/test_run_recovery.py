"""Recovery tests for interrupted orchestration task runs."""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

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


def _seed_interrupted_orchestration_run(db, *, client_turn_id: str):
    from models.database import Message
    from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule
    from services.run_ledger import append_task_event, create_task_run
    from services.session_service import SessionService

    session_service = SessionService(db)
    project, chatroom, agents = session_service.create_project_directly(
        name=f"Recovery Project {client_turn_id}",
        description="Recovery test project",
        agent_names=["analyst", "developer", "tester"],
    )
    analyst = next(agent for agent in agents if (agent.agent_type or "").lower() == "analyst")
    requested_names = ["analyst", "developer", "tester"]
    targets = [(name, next(agent for agent in agents if (agent.agent_type or "").lower() == name)) for name in requested_names]
    plan = build_orchestration_schedule(targets, sidecar_agent_types={"tester"})
    queue = OrchestrationRuntimeQueue(plan)
    first_step = queue.pop_ready()
    assert first_step is not None

    task_run = create_task_run(
        db,
        chatroom_id=chatroom.id,
        project_id=project.id,
        origin_message_id=None,
        client_turn_id=client_turn_id,
        run_kind="multi_agent_orchestration",
        user_request="@analyst @developer @tester resume interrupted work",
    )
    append_task_event(
        db,
        task_run,
        "runtime_mode_selected",
        summary="Selected project multi-agent orchestration mode.",
        payload={"agents": requested_names, "project_id": project.id},
    )
    append_task_event(
        db,
        task_run,
        "orchestration_started",
        summary="Multi-agent orchestration started.",
        payload={
            "requested_agents": requested_names,
            "resolved_agents": [agent.name for agent in agents],
            "project_id": project.id,
        },
    )
    append_task_event(
        db,
        task_run,
        "scheduler_plan_created",
        summary="Built a blocking-chain orchestration schedule with sidecars.",
        payload={**plan.to_payload(), "runtime": queue.runtime_snapshot_payload()},
    )
    append_task_event(
        db,
        task_run,
        "scheduler_step_dispatched",
        agent_name=analyst.name,
        summary=f"Scheduler dispatched {first_step.dispatch_kind} work to {analyst.name}.",
        payload={
            **first_step.to_payload(),
            "step_state": queue.runtime_state_payload_for_step(first_step.step_id),
            "runtime": queue.runtime_snapshot_payload(),
        },
    )
    append_task_event(
        db,
        task_run,
        "agent_turn_started",
        agent_name=analyst.name,
        summary=f"{analyst.name} started an orchestrated turn.",
        payload={"client_turn_id": task_run.client_turn_id, "inter_agent_message_count": 0},
    )

    saved_message = Message(
        chatroom_id=chatroom.id,
        agent_id=analyst.id,
        content="Analyst checkpoint before restart.",
        message_type="text",
    )
    db.add(saved_message)
    db.commit()
    db.refresh(saved_message)

    append_task_event(
        db,
        task_run,
        "agent_turn_completed",
        agent_name=analyst.name,
        message_id=saved_message.id,
        summary=f"{analyst.name} completed the orchestrated turn.",
        payload={"response_preview": "Analyst checkpoint before restart.", "message_id": saved_message.id},
    )

    released_steps = queue.mark_completed(first_step.step_id)
    append_task_event(
        db,
        task_run,
        "scheduler_step_completed",
        agent_name=analyst.name,
        summary=f"Scheduler marked {analyst.name} complete and released {len(released_steps)} waiting step(s).",
        payload={
            **first_step.to_payload(),
            "step_state": queue.runtime_state_payload_for_step(first_step.step_id),
            "runtime": queue.runtime_snapshot_payload(),
            "released_step_ids": [step.step_id for step in released_steps],
            "released_step_count": len(released_steps),
            "completed_with_output": True,
        },
    )
    for next_step in released_steps:
        append_task_event(
            db,
            task_run,
            "scheduler_step_resumed",
            agent_name=next_step.agent_name,
            summary=f"Scheduler resumed {next_step.agent_name} after {analyst.name}.",
            payload={
                **next_step.to_payload(),
                "step_state": queue.runtime_state_payload_for_step(next_step.step_id),
                "runtime": queue.runtime_snapshot_payload(),
                "resumed_by_step_id": first_step.step_id,
                "resumed_by_agent": analyst.name,
            },
        )
    return task_run.id, chatroom.id


def test_startup_recovers_interrupted_orchestration_run(tmp_path):
    from fastapi.testclient import TestClient

    _make_app(tmp_path)

    from models.database import SessionLocal

    db = SessionLocal()
    try:
        task_run_id, chatroom_id = _seed_interrupted_orchestration_run(
            db,
            client_turn_id="turn-recovery-startup",
        )
    finally:
        db.close()

    recovered_app = _make_app(tmp_path)
    with TestClient(recovered_app, base_url="http://testserver", headers={"X-Catown-Client": "test"}) as client:
        detail = client.get(f"/api/task-runs/{task_run_id}").json()
        assert detail["status"] == "completed"
        assert detail["summary"] == "Mocked agent response."

        event_types = [event["event_type"] for event in detail["events"]]
        assert "task_run_recovery_started" in event_types
        assert "scheduler_recovery_state_rebuilt" in event_types
        assert "task_run_recovery_completed" in event_types
        assert event_types.count("scheduler_step_dispatched") == 3
        assert event_types.count("scheduler_step_completed") == 3
        assert event_types.count("scheduler_step_resumed") == 2

        recovery_state_event = next(event for event in detail["events"] if event["event_type"] == "scheduler_recovery_state_rebuilt")
        assert recovery_state_event["payload"]["runtime"]["completed_step_count"] == 1
        assert recovery_state_event["payload"]["runtime"]["ready_step_count"] == 1
        recovery_started_event = next(event for event in detail["events"] if event["event_type"] == "task_run_recovery_started")
        assert recovery_started_event["payload"]["trigger"] == "startup"

        messages = client.get(f"/api/chatrooms/{chatroom_id}/messages").json()
        assistant_messages = [message["content"] for message in messages if message.get("agent_name")]
        assert "Analyst checkpoint before restart." in assistant_messages
        assert assistant_messages.count("Mocked agent response.") == 2


def test_manual_resume_endpoint_recovers_interrupted_orchestration_run(tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(tmp_path)

    with TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"}) as client:
        from models.database import SessionLocal

        db = SessionLocal()
        try:
            task_run_id, chatroom_id = _seed_interrupted_orchestration_run(
                db,
                client_turn_id="turn-recovery-manual",
            )
        finally:
            db.close()

        response = client.post(f"/api/task-runs/{task_run_id}/resume")
        assert response.status_code == 200
        payload = response.json()
        assert payload["resumed"] is True
        assert payload["status"] == "completed"
        assert payload["task_run_id"] == task_run_id
        assert payload["detail"]["summary"] == "Mocked agent response."

        event_types = [event["event_type"] for event in payload["detail"]["events"]]
        assert "task_run_manual_resume_requested" in event_types
        assert "task_run_recovery_started" in event_types
        recovery_started_event = next(event for event in payload["detail"]["events"] if event["event_type"] == "task_run_recovery_started")
        assert recovery_started_event["payload"]["trigger"] == "manual"

        messages = client.get(f"/api/chatrooms/{chatroom_id}/messages").json()
        assistant_messages = [message["content"] for message in messages if message.get("agent_name")]
        assert "Analyst checkpoint before restart." in assistant_messages
        assert assistant_messages.count("Mocked agent response.") == 2

        second_response = client.post(f"/api/task-runs/{task_run_id}/resume")
        assert second_response.status_code == 409
        assert "running task runs" in second_response.json()["detail"]


def test_startup_recovery_skips_task_run_claimed_by_another_instance(tmp_path):
    _make_app(tmp_path)

    from models.database import SessionLocal, TaskRun
    import routes.api as api_routes

    db = SessionLocal()
    try:
        task_run_id, _ = _seed_interrupted_orchestration_run(
            db,
            client_turn_id="turn-recovery-leased-startup",
        )
        task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
        assert task_run is not None
        task_run.recovery_owner = "peer-instance"
        task_run.recovery_claimed_at = datetime.now()
        task_run.recovery_lease_expires_at = datetime.now() + timedelta(minutes=5)
        db.add(task_run)
        db.commit()
    finally:
        db.close()

    summary = asyncio.run(api_routes.recover_interrupted_task_runs(limit=10))
    assert summary["detected"] == 1
    assert summary["recovered"] == 0
    assert summary["skipped"] == 1
    assert summary["failed"] == 0

    db = SessionLocal()
    try:
        detail = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
        assert detail is not None
        assert detail.status == "running"
        assert detail.recovery_owner == "peer-instance"
        assert not any(event.event_type == "task_run_recovery_started" for event in detail.events)
    finally:
        db.close()


def test_manual_resume_rejects_task_run_claimed_by_another_instance(tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(tmp_path)

    with TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"}) as client:
        from models.database import SessionLocal, TaskRun

        db = SessionLocal()
        try:
            task_run_id, _ = _seed_interrupted_orchestration_run(
                db,
                client_turn_id="turn-recovery-leased-manual",
            )
            task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
            assert task_run is not None
            task_run.recovery_owner = "peer-instance"
            task_run.recovery_claimed_at = datetime.now()
            task_run.recovery_lease_expires_at = datetime.now() + timedelta(minutes=5)
            db.add(task_run)
            db.commit()
        finally:
            db.close()

        response = client.post(f"/api/task-runs/{task_run_id}/resume")
        assert response.status_code == 409
        assert "already being recovered" in response.json()["detail"]
