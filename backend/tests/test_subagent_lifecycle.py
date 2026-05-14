from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from services.subagent_lifecycle import (
    build_subagent_lifecycle_from_events,
    build_subagent_runtime_handles,
    summarize_subagent_lifecycle,
    summarize_subagent_runtime_handles,
)


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
            {
                "step_id": "step-1",
                "position": 1,
                "agent_name": "analyst",
                "dispatch_kind": "blocking",
                "step_state": {
                    "status": "running",
                    "dispatch_count": 1,
                    "completion_count": 0,
                },
            },
            created_at=now + timedelta(seconds=1),
        ),
        _event(
            "scheduler_step_completed",
            {
                "step_id": "step-1",
                "position": 1,
                "agent_name": "analyst",
                "dispatch_kind": "blocking",
                "step_state": {
                    "status": "completed",
                    "dispatch_count": 1,
                    "completion_count": 1,
                },
            },
            created_at=now + timedelta(seconds=2),
        ),
        _event(
            "scheduler_step_resumed",
            {
                "step_id": "step-2",
                "position": 2,
                "agent_name": "developer",
                "dispatch_kind": "blocking",
                "resumed_by_step_id": "step-1",
                "resumed_by_agent": "analyst",
                "step_state": {
                    "status": "ready",
                    "released_by_step_id": "step-1",
                    "dispatch_count": 0,
                    "completion_count": 0,
                },
            },
            created_at=now + timedelta(seconds=3),
        ),
    ]

    lifecycle = build_subagent_lifecycle_from_events(events)

    assert lifecycle["subagent_count"] == 2
    assert lifecycle["status_counts"] == {"completed": 1, "spawned": 1}
    assert lifecycle["subagents"][0]["status"] == "completed"
    assert lifecycle["subagents"][0]["scheduler_status"] == "completed"
    assert lifecycle["subagents"][0]["dispatch_count"] == 1
    assert lifecycle["subagents"][0]["completion_count"] == 1
    assert lifecycle["subagents"][0]["started_at"] is not None
    assert lifecycle["subagents"][0]["completed_at"] is not None
    assert lifecycle["subagents"][1]["wait_for_step_id"] == "step-1"
    assert lifecycle["subagents"][1]["scheduler_status"] == "ready"
    assert lifecycle["subagents"][1]["released_by_step_id"] == "step-1"
    assert lifecycle["subagents"][1]["resumed_by_step_id"] == "step-1"
    assert lifecycle["subagents"][1]["resumed_by_agent"] == "analyst"
    assert summarize_subagent_lifecycle(lifecycle) == "2 subagents · 1 spawned · 1 completed"
    handles = build_subagent_runtime_handles(lifecycle)
    assert handles["handle_count"] == 2
    assert handles["cancellable_count"] == 1
    assert handles["control_state_counts"] == {"completed": 1, "await_dispatch": 1}
    assert handles["entries"][0]["terminal"] is True
    assert handles["entries"][0]["available_actions"] == ["close"]
    assert handles["entries"][1]["awaitable"] is True


def test_subagent_lifecycle_projects_consult_dispatch_events():
    now = datetime.now()
    events = [
        _event(
            "scheduler_step_dispatched",
            {
                "step_id": "consult-1",
                "position": 0,
                "agent_name": "Analyst",
                "agent_type": "analyst",
                "dispatch_kind": "consult",
                "source": "consult_agent",
                "step_state": {
                    "status": "running",
                    "dispatch_count": 1,
                    "completion_count": 0,
                },
            },
            created_at=now,
        ),
        _event(
            "scheduler_step_completed",
            {
                "step_id": "consult-1",
                "position": 0,
                "agent_name": "Analyst",
                "agent_type": "analyst",
                "dispatch_kind": "consult",
                "source": "consult_agent",
                "step_state": {
                    "status": "completed",
                    "dispatch_count": 1,
                    "completion_count": 1,
                },
            },
            created_at=now + timedelta(seconds=1),
        ),
    ]

    lifecycle = build_subagent_lifecycle_from_events(events)

    assert lifecycle["subagent_count"] == 1
    assert lifecycle["status_counts"] == {"completed": 1}
    assert lifecycle["subagents"][0]["dispatch_kind"] == "consult"
    assert lifecycle["subagents"][0]["source"] == "consult_agent"
    handles = build_subagent_runtime_handles(lifecycle)
    assert handles["handle_count"] == 1
    assert handles["cancellable_count"] == 0
    assert handles["entries"][0]["dispatch_kind"] == "consult"
    assert handles["entries"][0]["control_state"] == "completed"
    assert handles["entries"][0]["available_actions"] == ["close"]
    assert summarize_subagent_runtime_handles(handles) == "1 handle · 1 completed"


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
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "agent_name": "analyst",
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
        db.refresh(task_run)

        snapshot = build_task_run_checkpoint_snapshot(task_run)
        assert snapshot["subagent_lifecycle"]["status_counts"] == {"running": 1}
        assert snapshot["subagent_lifecycle"]["subagents"][0]["scheduler_status"] == "running"
        assert snapshot["subagent_lifecycle"]["subagents"][0]["dispatch_count"] == 1
        assert snapshot["subagent_lifecycle_summary"] == "1 subagents · 1 running"
        assert snapshot["subagent_handles"]["control_state_counts"] == {"await_completion": 1}
        assert snapshot["subagent_handles"]["entries"][0]["available_actions"] == ["wait", "cancel"]
        assert snapshot["subagent_handles_summary"] == "1 handle · 1 await completion · 1 cancellable"
    finally:
        db.close()


def test_task_checkpoint_marks_consult_subagent_in_continuation_state(fresh_db):
    from services.run_ledger import build_task_run_checkpoint_snapshot

    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)

    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Consult continuation")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent",
            status="running",
            title="Consult projection",
            user_request="Ask analyst for help.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        db.add_all(
            [
                fresh_db.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=1,
                    event_type="scheduler_step_dispatched",
                    agent_name="Analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "consult-1",
                            "position": 0,
                            "agent_name": "Analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "consult",
                            "source": "consult_agent",
                            "step_state": {
                                "status": "running",
                                "dispatch_count": 1,
                                "completion_count": 0,
                            },
                        }
                    ),
                ),
                fresh_db.TaskRunEvent(
                    task_run_id=task_run.id,
                    event_index=2,
                    event_type="scheduler_step_completed",
                    agent_name="Analyst",
                    payload_json=json.dumps(
                        {
                            "step_id": "consult-1",
                            "position": 0,
                            "agent_name": "Analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "consult",
                            "source": "consult_agent",
                            "response_preview": "Short consult answer",
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
        db.refresh(task_run)

        snapshot = build_task_run_checkpoint_snapshot(task_run)
        assert snapshot["latest_subagent_step"]["dispatch_kind"] == "consult"
        assert snapshot["latest_subagent_step"]["status"] == "completed"
        assert snapshot["latest_subagent_step"]["response_preview"] == "Short consult answer"
        assert snapshot["continuation_state"]["latest_subagent_dispatch_kind"] == "consult"
        assert snapshot["continuation_state"]["latest_subagent_status"] == "completed"
        assert "consult_subagent" in snapshot["continuation_state"]["consumed_layers"]
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


def test_subagent_lifecycle_rebuilds_runtime_state_from_recovery_snapshot():
    now = datetime.now()
    events = [
        _event(
            "scheduler_recovery_state_rebuilt",
            {
                "steps": [
                    {
                        "step_id": "step-1",
                        "position": 1,
                        "requested_name": "analyst",
                        "agent_id": 1,
                        "agent_name": "Analyst",
                        "agent_type": "analyst",
                        "dispatch_kind": "blocking",
                        "source": "user_mentions",
                    },
                    {
                        "step_id": "step-2",
                        "position": 2,
                        "requested_name": "developer",
                        "agent_id": 2,
                        "agent_name": "Developer",
                        "agent_type": "developer",
                        "dispatch_kind": "blocking",
                        "wait_for_step_id": "step-1",
                        "source": "user_mentions",
                    },
                ],
                "runtime": {
                    "steps": [
                        {
                            "step_id": "step-1",
                            "position": 1,
                            "requested_name": "analyst",
                            "agent_id": 1,
                            "agent_name": "Analyst",
                            "agent_type": "analyst",
                            "dispatch_kind": "blocking",
                            "source": "user_mentions",
                            "status": "completed",
                            "dispatch_count": 1,
                            "completion_count": 1,
                        },
                        {
                            "step_id": "step-2",
                            "position": 2,
                            "requested_name": "developer",
                            "agent_id": 2,
                            "agent_name": "Developer",
                            "agent_type": "developer",
                            "dispatch_kind": "blocking",
                            "wait_for_step_id": "step-1",
                            "source": "user_mentions",
                            "status": "ready",
                            "released_by_step_id": "step-1",
                            "dispatch_count": 0,
                            "completion_count": 0,
                        },
                    ]
                },
            },
            created_at=now,
        )
    ]

    lifecycle = build_subagent_lifecycle_from_events(events)

    assert lifecycle["status_counts"] == {"completed": 1, "spawned": 1}
    assert lifecycle["subagents"][0]["requested_name"] == "analyst"
    assert lifecycle["subagents"][0]["agent_id"] == 1
    assert lifecycle["subagents"][0]["source"] == "user_mentions"
    assert lifecycle["subagents"][0]["scheduler_status"] == "completed"
    assert lifecycle["subagents"][1]["scheduler_status"] == "ready"
    assert lifecycle["subagents"][1]["released_by_step_id"] == "step-1"
    handles = build_subagent_runtime_handles(lifecycle)
    assert handles["control_state_counts"] == {"completed": 1, "await_dispatch": 1}
    assert handles["entries"][1]["control_state"] == "await_dispatch"
    assert handles["entries"][1]["available_actions"] == ["wait", "cancel"]
