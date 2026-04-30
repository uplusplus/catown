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
