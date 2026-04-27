from types import SimpleNamespace

from services.approval_replay import (
    build_approval_queue_replay_round_payload,
    build_followup_continued_payload,
    build_followup_failed_payload,
    build_followup_skipped_payload,
    build_queue_replay_resolution_payload,
    replay_result_is_actionable,
)


def test_replay_result_is_actionable_requires_success_without_block():
    assert replay_result_is_actionable(SimpleNamespace(success=True, blocked=False)) is True
    assert replay_result_is_actionable(SimpleNamespace(success=False, blocked=False)) is False
    assert replay_result_is_actionable(SimpleNamespace(success=True, blocked=True)) is False


def test_followup_payload_helpers_share_resolution_shape():
    assert build_followup_skipped_payload("task_run_missing") == {
        "followup_attempted": False,
        "followup_status": "skipped",
        "followup_reason": "task_run_missing",
    }

    assert build_followup_continued_payload(followup_reason="pipeline_resumed", ignored=None) == {
        "followup_attempted": True,
        "followup_status": "continued",
        "followup_reason": "pipeline_resumed",
    }

    assert build_followup_failed_payload(RuntimeError("boom"), followup_message_id=42, ignored=None) == {
        "followup_attempted": True,
        "followup_status": "failed",
        "followup_error": "boom",
        "followup_message_id": 42,
    }


def test_queue_replay_resolution_payload_records_replay_outcome_and_preview():
    replay_result = SimpleNamespace(
        status="succeeded",
        success=True,
        blocked=False,
        blocked_kind=None,
        result="line one\nline two with enough content to truncate",
    )

    payload = build_queue_replay_resolution_payload(
        request_payload={"resume_supported": True, "tool_name": "read_file"},
        replay_result=replay_result,
        action_taken="tool_replayed",
        result_preview_limit=24,
    )

    assert payload == {
        "request_payload": {"resume_supported": True, "tool_name": "read_file"},
        "resume_supported": True,
        "action_taken": "tool_replayed",
        "replay_attempted": True,
        "replay_status": "succeeded",
        "replay_success": True,
        "replay_blocked": False,
        "replay_blocked_kind": None,
        "replay_result_preview": "line one line two with...",
    }


def test_approval_queue_replay_round_payload_carries_pipeline_cursor_only_when_present():
    item = SimpleNamespace(id=123)

    payload = build_approval_queue_replay_round_payload(
        item,
        {
            "pipeline_id": 1,
            "pipeline_run_id": 2,
            "pipeline_stage_id": None,
            "stage_name": "analysis",
            "display_name": "Analysis",
        },
    )

    assert payload == {
        "replay": True,
        "replay_of_queue_item_id": 123,
        "pipeline_id": 1,
        "pipeline_run_id": 2,
        "stage_name": "analysis",
        "display_name": "Analysis",
    }
