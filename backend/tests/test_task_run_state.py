from dataclasses import dataclass

from services.task_run_state import derive_task_run_status


@dataclass
class _Event:
    event_type: str
    payload_json: str | None = None
    created_at: object | None = None


def test_recovery_completed_does_not_reopen_failed_task_run():
    events = [
        _Event("task_run_created"),
        _Event("tool_round_recorded"),
        _Event("task_run_failed"),
        _Event("task_run_recovery_completed"),
    ]

    assert derive_task_run_status(events) == "failed"


def test_recovery_completed_preserves_completed_task_run():
    events = [
        _Event("task_run_created"),
        _Event("agent_turn_started"),
        _Event("agent_turn_completed"),
        _Event("task_run_recovery_completed"),
    ]

    assert derive_task_run_status(events) == "completed"


def test_orchestration_turn_completion_does_not_finish_run_when_scheduler_resumes_next_step():
    events = [
        _Event("task_run_created"),
        _Event("scheduler_step_dispatched"),
        _Event("agent_turn_started"),
        _Event("agent_turn_completed"),
        _Event("scheduler_step_completed"),
        _Event("scheduler_step_resumed"),
    ]

    assert derive_task_run_status(events) == "running"


def test_tool_call_blocked_keeps_task_run_paused_after_queue_item_created():
    events = [
        _Event("task_run_created"),
        _Event("approval_queue_item_created"),
        _Event("tool_call_blocked"),
    ]

    assert derive_task_run_status(events) == "paused"


def test_subagent_cancelled_event_does_not_cancel_whole_task_run():
    events = [
        _Event("task_run_created"),
        _Event("task_run_subagent_cancelled"),
    ]

    assert derive_task_run_status(events) == "running"
