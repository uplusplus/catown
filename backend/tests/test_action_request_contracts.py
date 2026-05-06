from pydantic import ValidationError

from services.action_request_contracts import dump_action_request, parse_action_request


def test_parse_use_tool_action_request():
    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": "req-tool-1",
            "type": "use_tool",
            "source": {
                "agent_name": "Developer",
                "agent_type": "developer",
                "stage_name": "development",
                "task_run_id": 12,
                "turn_index": 3,
            },
            "summary": "Read the API route file.",
            "payload": {
                "tool_name": "read_file",
                "arguments": {"file_path": "backend/routes/api.py"},
                "reason": "Need to inspect the current endpoint wiring.",
            },
        }
    )

    dumped = dump_action_request(request)
    assert dumped["type"] == "use_tool"
    assert dumped["payload"]["tool_name"] == "read_file"
    assert dumped["source"]["stage_name"] == "development"


def test_parse_request_approval_action_request():
    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": "req-approval-1",
            "type": "request_approval",
            "source": {
                "agent_name": "Developer",
                "agent_type": "developer",
                "task_run_id": 21,
            },
            "payload": {
                "queue_kind": "approval",
                "target_kind": "tool",
                "target_name": "delete_file",
                "reason": "Deleting generated fixtures needs explicit confirmation.",
                "resume_supported": True,
                "request_payload": {"tool_name": "delete_file", "arguments": {"file_path": "tmp.txt"}},
            },
        }
    )

    dumped = dump_action_request(request)
    assert dumped["payload"]["target_kind"] == "tool"
    assert dumped["payload"]["resume_supported"] is True


def test_parse_publish_artifact_action_request():
    request = parse_action_request(
        {
            "kind": "action_request",
            "version": 1,
            "request_id": "req-artifact-1",
            "type": "publish_artifact",
            "source": {
                "agent_name": "Analyst",
                "agent_type": "analyst",
                "stage_name": "analysis",
            },
            "payload": {
                "artifact_type": "document.prd",
                "title": "PRD draft",
                "summary": "Structured product requirements.",
                "file_path": "PRD.md",
                "content_json": {"stories": 5, "acceptance_criteria": 12},
            },
        }
    )

    dumped = dump_action_request(request)
    assert dumped["payload"]["artifact_type"] == "document.prd"
    assert dumped["payload"]["file_path"] == "PRD.md"


def test_unknown_action_request_type_is_rejected():
    try:
        parse_action_request(
            {
                "kind": "action_request",
                "version": 1,
                "request_id": "req-unknown-1",
                "type": "close_handle",
                "source": {"agent_name": "Developer"},
                "payload": {"step_id": "step-1"},
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError for unsupported action request type.")
