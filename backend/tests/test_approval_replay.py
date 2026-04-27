from types import SimpleNamespace

from services.approval_replay import (
    blocked_tool_queue_kind,
    blocked_tool_queue_title,
    blocked_tool_resume_supported,
    build_approval_queue_item_created_event_payload,
    build_approval_queue_item_resolved_event_payload,
    build_approval_queue_replay_round_payload,
    build_blocked_tool_request_key,
    build_blocked_tool_request_payload,
    build_followup_continued_payload,
    build_followup_failed_payload,
    build_followup_skipped_payload,
    build_pipeline_gate_request_key,
    build_pipeline_gate_request_payload,
    build_pipeline_gate_resolution_payload,
    build_queue_replay_resolution_payload,
    build_queue_rejection_resolution_payload,
    build_tool_replay_followup_context,
    replay_result_is_actionable,
)


def test_blocked_tool_queue_helpers_preserve_request_semantics():
    blocked_tool = {
        "tool_name": "delete_file",
        "arguments": '{"path": "tmp.txt"}',
        "status": "approval_blocked",
        "blocked_kind": "approval",
        "blocked_reason": "delete_file requires approval",
    }

    assert blocked_tool_queue_kind("approval") == "approval"
    assert blocked_tool_queue_kind("sandbox") == "escalation"
    assert blocked_tool_queue_title("delete_file", queue_kind="approval") == "Approval needed for delete_file"
    assert blocked_tool_queue_title("delete_file", queue_kind="escalation") == "Escalation needed for delete_file"
    assert blocked_tool_resume_supported(blocked_kind="approval", blocked_reason="delete_file requires approval") is True
    assert blocked_tool_resume_supported(blocked_kind="sandbox", blocked_reason="sandbox blocked") is False
    assert blocked_tool_resume_supported(blocked_kind="approval", blocked_reason="unauthorized tool") is False

    request_key = build_blocked_tool_request_key(
        task_run_id=7,
        agent_name="analyst",
        blocked_tool=blocked_tool,
    )
    assert request_key == "b608248210cb9b1168efe8dc7867c7fc62193def"

    assert build_blocked_tool_request_payload(
        turn=2,
        blocked_tool=blocked_tool,
        resume_supported=True,
        runtime_payload={
            "pipeline_id": 11,
            "pipeline_run_id": 12,
            "pipeline_stage_id": 13,
            "stage_name": "analysis",
            "display_name": "Analysis",
        },
    ) == {
        "turn": 2,
        "tool_name": "delete_file",
        "arguments": '{"path": "tmp.txt"}',
        "status": "approval_blocked",
        "blocked_kind": "approval",
        "blocked_reason": "delete_file requires approval",
        "resume_supported": True,
        "pipeline_id": 11,
        "pipeline_run_id": 12,
        "pipeline_stage_id": 13,
        "stage_name": "analysis",
        "display_name": "Analysis",
    }


def test_pipeline_gate_payload_helpers_preserve_gate_cursor():
    stage_policy = SimpleNamespace(to_payload=lambda: {"stage_name": "qa_gate", "gate": "manual"})

    assert build_pipeline_gate_request_key(pipeline_run_id=42, stage_name=" qa_gate ") == "pipeline_gate:42:qa_gate"
    assert build_pipeline_gate_request_payload(
        pipeline_id=7,
        pipeline_run_id=42,
        pipeline_stage_id=99,
        stage_name="qa_gate",
        display_name="QA Gate",
        stage_policy=stage_policy,
    ) == {
        "pipeline_id": 7,
        "pipeline_run_id": 42,
        "pipeline_stage_id": 99,
        "stage_name": "qa_gate",
        "display_name": "QA Gate",
        "resume_supported": True,
        "stage_policy": {"stage_name": "qa_gate", "gate": "manual"},
    }
    assert build_pipeline_gate_resolution_payload(
        pipeline_run_id=42,
        pipeline_stage_id=99,
        stage_name="qa_gate",
        display_name="QA Gate",
    ) == {
        "pipeline_run_id": 42,
        "pipeline_stage_id": 99,
        "stage_name": "qa_gate",
        "display_name": "QA Gate",
    }


def test_queue_resolution_event_payload_helpers_share_ledger_shape():
    item = SimpleNamespace(
        id=55,
        queue_kind="approval",
        target_kind="tool",
        target_name="read_file",
        status="approved",
        source="tool_call_blocked",
        resolved_by="user",
    )

    assert build_approval_queue_item_created_event_payload(item) == {
        "queue_item_id": 55,
        "queue_kind": "approval",
        "target_kind": "tool",
        "target_name": "read_file",
        "status": "approved",
        "source": "tool_call_blocked",
    }
    assert build_approval_queue_item_resolved_event_payload(
        item,
        status="approved",
        request_payload={"resume_supported": True},
        resolution_payload={
            "action_taken": "tool_replayed",
            "replay_status": "succeeded",
            "replay_success": True,
            "replay_blocked": False,
        },
    ) == {
        "queue_item_id": 55,
        "queue_kind": "approval",
        "target_kind": "tool",
        "target_name": "read_file",
        "status": "approved",
        "resolved_by": "user",
        "resume_supported": True,
        "action_taken": "tool_replayed",
        "replay_status": "succeeded",
        "replay_success": True,
        "replay_blocked": False,
    }


def test_queue_rejection_resolution_payload_omits_absent_rollback():
    assert build_queue_rejection_resolution_payload(request_payload={"resume_supported": False}) == {
        "request_payload": {"resume_supported": False},
        "resume_supported": False,
        "action_taken": "queue_resolved_only",
    }
    assert build_queue_rejection_resolution_payload(
        request_payload={"resume_supported": True},
        rollback_to="analysis",
    )["rollback_to"] == "analysis"


def test_tool_replay_followup_context_preserves_status_and_truncated_result():
    item = SimpleNamespace(target_name="read_file")
    replay_result = SimpleNamespace(
        tool_name=None,
        status="succeeded",
        result="first line\nsecond line that should be truncated",
    )

    context = build_tool_replay_followup_context(item, replay_result, result_preview_limit=24)

    assert "- Tool: read_file" in context
    assert "- Status: succeeded" in context
    assert "- Result: first line second line..." in context
    assert "Do not rerun the same tool call" in context


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
