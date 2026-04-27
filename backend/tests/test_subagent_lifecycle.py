from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from services.subagent_lifecycle import build_subagent_lifecycle_from_events, summarize_subagent_lifecycle


def _event(event_type, payload, *, created_at=None):
    return SimpleNamespace(
        id=1,
        event_type=event_type,
        payload_json=json.dumps(payload),
        created_at=created_at or datetime.now(),
    )


def test_subagent_lifecycle_projects_scheduler_events():
    now = datetime.now()
    events = [
        _event(
            "scheduler_plan_created",
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
            },
            created_at=now,
        ),
        _event(
            "scheduler_step_dispatched",
            {"step_id": "step-1", "position": 1, "agent_name": "analyst", "dispatch_kind": "blocking"},
            created_at=now + timedelta(seconds=1),
        ),
        _event(
            "scheduler_step_completed",
            {"step_id": "step-1", "position": 1, "agent_name": "analyst", "dispatch_kind": "blocking"},
            created_at=now + timedelta(seconds=2),
        ),
        _event(
            "scheduler_step_resumed",
            {"step_id": "step-2", "position": 2, "agent_name": "developer", "dispatch_kind": "blocking"},
            created_at=now + timedelta(seconds=3),
        ),
    ]

    lifecycle = build_subagent_lifecycle_from_events(events)

    assert lifecycle["subagent_count"] == 2
    assert lifecycle["status_counts"] == {"completed": 1, "spawned": 1}
    assert lifecycle["subagents"][0]["status"] == "completed"
    assert lifecycle["subagents"][0]["started_at"] is not None
    assert lifecycle["subagents"][0]["completed_at"] is not None
    assert lifecycle["subagents"][1]["wait_for_step_id"] == "step-1"
    assert summarize_subagent_lifecycle(lifecycle) == "2 subagents · 1 spawned · 1 completed"


def test_task_checkpoint_includes_subagent_lifecycle(fresh_db):
    from services.run_ledger import build_task_run_checkpoint_snapshot

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Lifecycle checkpoint")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Lifecycle projection",
            user_request="Coordinate agents.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                fresh_db.TaskRunEvent(
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
                fresh_db.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_dispatched",
                    agent_name="analyst",
                    payload_json=json.dumps(
                        {"step_id": "step-1", "position": 1, "agent_name": "analyst", "dispatch_kind": "blocking"}
                    ),
                ),
            ]
        )
        db.commit()
        db.refresh(task_run)

        snapshot = build_task_run_checkpoint_snapshot(task_run)
        assert snapshot["subagent_lifecycle"]["status_counts"] == {"running": 1}
        assert snapshot["subagent_lifecycle_summary"] == "1 subagents · 1 running"
    finally:
        db.close()


def test_subagent_lifecycle_projects_failed_and_cancelled_steps():
    now = datetime.now()
    events = [
        _event(
            "scheduler_plan_created",
            {
                "steps": [
                    {"step_id": "step-1", "position": 1, "agent_name": "developer"},
                    {"step_id": "step-2", "position": 2, "agent_name": "tester"},
                ]
            },
            created_at=now,
        ),
        _event(
            "scheduler_step_failed",
            {"step_id": "step-1", "position": 1, "agent_name": "developer", "error": "LLM timeout"},
            created_at=now + timedelta(seconds=1),
        ),
        _event(
            "scheduler_step_cancelled",
            {"step_id": "step-2", "position": 2, "agent_name": "tester"},
            created_at=now + timedelta(seconds=2),
        ),
    ]

    lifecycle = build_subagent_lifecycle_from_events(events)

    assert lifecycle["status_counts"] == {"failed": 1, "cancelled": 1}
    assert lifecycle["subagents"][0]["error"] == "LLM timeout"
    assert summarize_subagent_lifecycle(lifecycle) == "2 subagents · 1 failed · 1 cancelled"
