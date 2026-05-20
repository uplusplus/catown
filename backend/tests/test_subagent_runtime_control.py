from types import SimpleNamespace

import pytest

from services.subagent_runtime_control import (
    SubagentRuntimeControlError,
    build_task_run_subagent_projection,
    cancel_task_run_subagent_handle,
    close_task_run_subagent_handle,
    observe_task_run_subagent,
)


def _task_run(*, status="running", events=None):
    return SimpleNamespace(
        id=7,
        status=status,
        run_kind="multi_agent_orchestration",
        events=list(events or []),
    )


def test_build_task_run_subagent_projection_returns_checkpoint_view(monkeypatch):
    monkeypatch.setattr(
        "services.run_ledger.build_task_run_checkpoint_snapshot",
        lambda task_run: {
            "subagent_lifecycle_summary": "1 subagents 路 1 running",
            "subagent_handles_summary": "1 handle 路 1 await completion 路 1 cancellable",
            "subagent_lifecycle": {"subagent_count": 1},
            "subagent_handles": {"handle_count": 1},
        },
    )

    projection = build_task_run_subagent_projection(_task_run())

    assert projection["task_run_id"] == 7
    assert "1 subagents" in projection["subagent_lifecycle_summary"]
    assert "1 handle" in projection["subagent_handles_summary"]


def test_observe_task_run_subagent_returns_wait_contract(monkeypatch):
    monkeypatch.setattr(
        "services.run_ledger.build_task_run_checkpoint_snapshot",
        lambda task_run: {
            "subagent_handles": {
                "entries": [
                    {
                        "step_id": "step-2",
                        "control_state": "await_dependency",
                        "terminal": False,
                        "awaitable": True,
                        "last_event_index": 1,
                    }
                ]
            }
        },
    )

    observed = observe_task_run_subagent(
        _task_run(events=[SimpleNamespace(event_index=1)]),
        "step-2",
        since_event_index=1,
    )

    assert observed["subagent_handle"]["control_state"] == "await_dependency"
    assert observed["wait_result"]["state_changed"] is False
    assert observed["wait_result"]["suggested_poll"] == "continue"


def test_cancel_task_run_subagent_handle_terminalizes_when_last_handle(monkeypatch):
    snapshots = [
        {
            "subagent_handles": {
                "entries": [
                    {"step_id": "step-1", "cancellable": True, "status": "running"},
                ]
            },
            "subagent_lifecycle": {
                "subagents": [{"step_id": "step-1", "status": "running"}],
            },
        },
        {
            "subagent_handles": {
                "entries": [
                    {"step_id": "step-1", "cancellable": False, "status": "cancelled"},
                ]
            }
        },
    ]

    def _snapshot(task_run):
        return snapshots.pop(0)

    recorded_events = []
    cancelled_steps = []

    monkeypatch.setattr("services.run_ledger.build_task_run_checkpoint_snapshot", _snapshot)
    monkeypatch.setattr(
        "services.orchestration_events.record_scheduler_step_cancelled",
        lambda db, task_run, subagent, **kwargs: cancelled_steps.append((subagent, kwargs)),
    )
    monkeypatch.setattr(
        "services.run_ledger.append_task_event",
        lambda db, task_run, event_type, **kwargs: recorded_events.append((event_type, kwargs)),
    )
    monkeypatch.setattr(
        "services.run_ledger.complete_task_run",
        lambda db, task_run, **kwargs: setattr(task_run, "status", kwargs["status"]),
    )
    monkeypatch.setattr(
        "services.run_ledger.serialize_task_run_detail",
        lambda task_run: {"status": task_run.status},
    )

    fake_db = SimpleNamespace(refresh=lambda task_run: None)
    result = cancel_task_run_subagent_handle(
        fake_db,
        _task_run(),
        step_id="step-1",
        cancelled_by="tester",
        note="Stop it.",
    )

    assert cancelled_steps[0][0]["step_id"] == "step-1"
    assert result["task_run_cancelled"] is True
    assert result["status"] == "cancelled"
    assert [event_type for event_type, _ in recorded_events] == [
        "task_run_subagent_cancelled",
        "task_run_cancelled",
    ]


def test_close_task_run_subagent_handle_marks_closed(monkeypatch):
    monkeypatch.setattr(
        "services.run_ledger.build_task_run_checkpoint_snapshot",
        lambda task_run: {
            "subagent_handles": {
                "entries": [
                    {
                        "step_id": "step-1",
                        "terminal": True,
                        "closed": False,
                        "status": "completed",
                        "control_state": "completed",
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        "services.run_ledger.append_task_event",
        lambda db, task_run, event_type, **kwargs: None,
    )
    monkeypatch.setattr(
        "services.run_ledger.serialize_task_run_detail",
        lambda task_run: {
            "checkpoint_snapshot": {
                "subagent_handles": {
                    "entries": [
                        {
                            "step_id": "step-1",
                            "closed": True,
                            "closed_by": "tester",
                            "close_note": "Archive it.",
                        }
                    ]
                }
            }
        },
    )

    fake_db = SimpleNamespace(refresh=lambda task_run: None)
    result = close_task_run_subagent_handle(
        fake_db,
        _task_run(status="completed"),
        step_id="step-1",
        closed_by="tester",
        note="Archive it.",
    )

    assert result["closed"] is True
    assert result["subagent_handle"]["closed"] is True
    assert result["subagent_handle"]["closed_by"] == "tester"


def test_close_task_run_subagent_handle_rejects_non_terminal_handle(monkeypatch):
    from services import subagent_runtime_control as runtime_control

    monkeypatch.setattr(
        "services.run_ledger.build_task_run_checkpoint_snapshot",
        lambda task_run: {
            "subagent_handles": {
                "entries": [
                    {"step_id": "step-1", "terminal": False, "closed": False},
                ]
            }
        },
    )

    with pytest.raises(runtime_control.SubagentRuntimeControlError) as excinfo:
        runtime_control.close_task_run_subagent_handle(
            SimpleNamespace(refresh=lambda task_run: None),
            _task_run(),
            step_id="step-1",
        )
    assert excinfo.value.status_code == 409
