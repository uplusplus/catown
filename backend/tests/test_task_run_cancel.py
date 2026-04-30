import json

import pytest


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_api_routes import _make_app

    app = _make_app(tmp_path)
    return TestClient(app, base_url="http://testserver", headers={"X-Catown-Client": "test"})


def test_cancel_task_run_terminalizes_active_subagents(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Cancelable Run", "agent_names": ["analyst", "developer"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Cancelable orchestration",
            user_request="Coordinate and then cancel.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        plan_payload = {
            "steps": [
                {
                    "step_id": "step-1",
                    "position": 1,
                    "agent_name": "analyst",
                    "agent_type": "analyst",
                    "dispatch_kind": "blocking",
                },
                {
                    "step_id": "step-2",
                    "position": 2,
                    "agent_name": "developer",
                    "agent_type": "developer",
                    "dispatch_kind": "blocking",
                    "wait_for_step_id": "step-1",
                },
            ]
        }
        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(plan_payload),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(plan_payload["steps"][0]),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    cancelled = client.post(
        f"/api/task-runs/{task_run_id}/cancel",
        json={"note": "Stop this run.", "cancelled_by": "tester"},
    )

    assert cancelled.status_code == 200
    payload = cancelled.json()
    assert payload["status"] == "cancelled"
    assert payload["cancelled_subagent_count"] == 2
    assert payload["detail"]["status"] == "cancelled"
    event_types = [event["event_type"] for event in payload["detail"]["events"]]
    assert event_types.count("scheduler_step_cancelled") == 2
    assert event_types[-1] == "task_run_cancelled"
    lifecycle = payload["detail"]["checkpoint_snapshot"]["subagent_lifecycle"]
    assert lifecycle["status_counts"] == {"cancelled": 2}
    assert lifecycle["subagents"][0]["cancelled_by"] == "tester"
    assert lifecycle["subagents"][0]["previous_status"] == "running"
    assert lifecycle["subagents"][1]["cancelled_by"] == "tester"
    assert lifecycle["subagents"][1]["previous_status"] == "spawned"
    handles = payload["detail"]["checkpoint_snapshot"]["subagent_handles"]
    assert handles["control_state_counts"] == {"cancelled": 2}
    assert handles["cancellable_count"] == 0
    assert handles["entries"][0]["available_actions"] == []
    assert payload["detail"]["subagent_handles_summary"] == "2 handles · 2 cancelled"


def test_cancel_task_run_rejects_non_running_runs(client):
    from models.database import SessionLocal, TaskRun

    response = client.post("/api/projects", json={"name": "Completed Run", "agent_names": ["analyst"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="completed",
            title="Completed orchestration",
            user_request="Already done.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        task_run_id = task_run.id
    finally:
        db.close()

    cancelled = client.post(f"/api/task-runs/{task_run_id}/cancel", json={"note": "Too late."})
    assert cancelled.status_code == 409


def test_list_task_run_subagent_handles(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Subagent Handle Run", "agent_names": ["analyst", "developer"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Subagent handles",
            user_request="Inspect subagent handles.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(
                        {
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "agent_name": "analyst",
                                    "agent_type": "analyst",
                                    "dispatch_kind": "blocking",
                                },
                                {
                                    "step_id": "step-2",
                                    "position": 2,
                                    "agent_name": "developer",
                                    "agent_type": "developer",
                                    "dispatch_kind": "blocking",
                                    "wait_for_step_id": "step-1",
                                },
                            ]
                        }
                    ),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    listed = client.get(f"/api/task-runs/{task_run_id}/subagents")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["task_run_id"] == task_run_id
    assert payload["subagent_lifecycle_summary"] == "2 subagents · 1 spawned · 1 running"
    assert payload["subagent_handles_summary"] == "2 handles · 1 await dependency · 1 await completion · 2 cancellable"
    handles = payload["subagent_handles"]
    assert handles["handle_count"] == 2
    assert handles["cancellable_count"] == 2
    assert handles["entries"][0]["control_state"] == "await_completion"
    assert handles["entries"][0]["available_actions"] == ["wait", "cancel"]
    assert handles["entries"][1]["control_state"] == "await_dependency"
    assert handles["entries"][1]["dependency_step_id"] == "step-1"


def test_cancel_single_task_run_subagent(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Partial Cancel Run", "agent_names": ["analyst", "developer"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Partial subagent cancel",
            user_request="Cancel one child only.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(
                        {
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "agent_name": "analyst",
                                    "agent_type": "analyst",
                                    "dispatch_kind": "blocking",
                                },
                                {
                                    "step_id": "step-2",
                                    "position": 2,
                                    "agent_name": "developer",
                                    "agent_type": "developer",
                                    "dispatch_kind": "blocking",
                                    "wait_for_step_id": "step-1",
                                },
                            ]
                        }
                    ),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    cancelled = client.post(
        f"/api/task-runs/{task_run_id}/subagents/step-2/cancel",
        json={"note": "Stop the dependent child only.", "cancelled_by": "tester"},
    )
    assert cancelled.status_code == 200
    payload = cancelled.json()
    assert payload["cancelled"] is True
    assert payload["task_run_cancelled"] is False
    assert payload["status"] == "running"
    assert payload["remaining_cancellable_subagent_count"] == 1
    detail = payload["detail"]
    event_types = [event["event_type"] for event in detail["events"]]
    assert "task_run_subagent_cancelled" in event_types
    assert event_types[-1] == "task_run_subagent_cancelled"
    handles = detail["checkpoint_snapshot"]["subagent_handles"]
    assert handles["control_state_counts"] == {"await_completion": 1, "cancelled": 1}
    cancelled_handle = next(item for item in handles["entries"] if item["step_id"] == "step-2")
    assert cancelled_handle["available_actions"] == []
    assert cancelled_handle["terminal"] is True
    assert detail["subagent_handles_summary"] == "2 handles · 1 await completion · 1 cancelled · 1 cancellable"


def test_wait_task_run_subagent_reports_poll_contract(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Wait Handle Run", "agent_names": ["analyst", "developer"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Wait subagent handle",
            user_request="Observe child wait contract.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(
                        {
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "agent_name": "analyst",
                                    "agent_type": "analyst",
                                    "dispatch_kind": "blocking",
                                },
                                {
                                    "step_id": "step-2",
                                    "position": 2,
                                    "agent_name": "developer",
                                    "agent_type": "developer",
                                    "dispatch_kind": "blocking",
                                    "wait_for_step_id": "step-1",
                                },
                            ]
                        }
                    ),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    waiting = client.get(f"/api/task-runs/{task_run_id}/subagents/step-2/wait?since_event_index=2")
    assert waiting.status_code == 200
    payload = waiting.json()
    assert payload["subagent_handle"]["control_state"] == "await_dependency"
    wait_result = payload["wait_result"]
    assert wait_result["awaitable"] is True
    assert wait_result["terminal"] is False
    assert wait_result["state_changed"] is False
    assert wait_result["suggested_poll"] == "continue"
    assert wait_result["last_event_index"] == 1
    assert wait_result["since_event_index"] == 2


def test_wait_task_run_subagent_reports_immediate_when_terminal(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Wait Terminal Run", "agent_names": ["analyst"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Wait terminal handle",
            user_request="Observe finished child.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(
                        {
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "agent_name": "analyst",
                                    "agent_type": "analyst",
                                    "dispatch_kind": "blocking",
                                }
                            ]
                        }
                    ),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_completed",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "step_state": {
                                "status": "completed",
                                "dispatch_count": 1,
                                "completion_count": 1,
                            },
                        }
                    ),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    waiting = client.get(f"/api/task-runs/{task_run_id}/subagents/step-1/wait?since_event_index=1")
    assert waiting.status_code == 200
    payload = waiting.json()
    wait_result = payload["wait_result"]
    assert wait_result["terminal"] is True
    assert wait_result["state_changed"] is True
    assert wait_result["suggested_poll"] == "immediate"
    assert wait_result["last_event_index"] == 2


def test_close_terminal_task_run_subagent_handle(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Close Handle Run", "agent_names": ["analyst"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Close terminal handle",
            user_request="Archive finished child handle.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(
                        {
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "agent_name": "analyst",
                                    "agent_type": "analyst",
                                    "dispatch_kind": "blocking",
                                }
                            ]
                        }
                    ),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_completed",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "step_state": {
                                "status": "completed",
                                "dispatch_count": 1,
                                "completion_count": 1,
                            },
                        }
                    ),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    closed = client.post(
        f"/api/task-runs/{task_run_id}/subagents/step-1/close",
        json={"note": "Archive this finished handle.", "cancelled_by": "tester"},
    )
    assert closed.status_code == 200
    payload = closed.json()
    assert payload["closed"] is True
    handle = payload["subagent_handle"]
    assert handle["closed"] is True
    assert handle["closed_by"] == "tester"
    assert handle["close_note"] == "Archive this finished handle."
    assert handle["available_actions"] == []
    assert payload["detail"]["checkpoint_snapshot"]["subagent_handles"]["entries"][0]["closed_at"] is not None
    event_types = [event["event_type"] for event in payload["detail"]["events"]]
    assert event_types[-1] == "subagent_handle_closed"


def test_close_task_run_subagent_rejects_non_terminal_handle(client):
    from models.database import SessionLocal, TaskRun, TaskRunEvent

    response = client.post("/api/projects", json={"name": "Close Active Handle Run", "agent_names": ["analyst"]})
    assert response.status_code == 200
    chatroom_id = response.json()["chatroom_id"]

    db = SessionLocal()
    try:
        task_run = TaskRun(
            chatroom_id=chatroom_id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Close active handle",
            user_request="Do not close active child.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_plan_created",
                    payload_json=json.dumps(
                        {
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "position": 1,
                                    "agent_name": "analyst",
                                    "agent_type": "analyst",
                                    "dispatch_kind": "blocking",
                                }
                            ]
                        }
                    ),
                ),
                TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                ),
            ]
        )
        db.commit()
        task_run_id = task_run.id
    finally:
        db.close()

    closed = client.post(
        f"/api/task-runs/{task_run_id}/subagents/step-1/close",
        json={"note": "Should fail."},
    )
    assert closed.status_code == 409
