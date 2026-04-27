from types import SimpleNamespace

from services.approval_replay import (
    build_followup_continued_payload,
    build_followup_failed_payload,
    build_followup_skipped_payload,
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
