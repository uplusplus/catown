import pytest

from services.artifact_contracts import dump_artifact_contract
from services.artifact_publication import (
    compile_and_validate_publish_artifact_request_for_workflow,
    compile_publish_artifact_request_to_contract,
)
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def _publish_request(payload):
    return {
        "kind": "action_request",
        "version": 1,
        "request_id": "req-artifact-1",
        "type": "publish_artifact",
        "source": {
            "agent_name": "Tester",
            "agent_type": "tester",
            "stage_name": "testing",
            "task_run_id": 12,
            "pipeline_run_id": 4,
            "pipeline_stage_id": 8,
        },
        "summary": "Publish produced artifact.",
        "payload": payload,
    }


def _workflow_spec():
    return compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default delivery workflow",
            "description": "testing",
            "stages": [
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "expected_artifacts": ["test_report.md"],
                },
            ],
        },
    )


def test_compile_publish_artifact_request_to_document_contract():
    contract = compile_publish_artifact_request_to_contract(
        request=_publish_request(
            {
                "artifact_type": "document.test_report",
                "title": "Test report",
                "summary": "Regression results.",
                "file_path": "test_report.md",
                "content_markdown": "# Test Report",
                "content_json": {"passed": 12},
            }
        )
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["artifact_id"] == "artifact-req-artifact-1"
    assert dumped["mode"] == "document"
    assert dumped["format"] == "mixed"
    assert dumped["producer"]["stage_name"] == "testing"
    assert dumped["source_input_refs"] == ["req-artifact-1"]
    assert dumped["metadata"]["source_action_request_type"] == "publish_artifact"


def test_compile_publish_artifact_request_to_workspace_file_contract():
    contract = compile_publish_artifact_request_to_contract(
        request=_publish_request(
            {
                "artifact_type": "workspace.file",
                "title": "Implementation file",
                "file_path": "src/runtime/policy.py",
            }
        )
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "workspace_file"
    assert dumped["file_path"] == "src/runtime/policy.py"
    assert dumped["media_type"] is None


def test_compile_publish_artifact_request_to_workspace_directory_contract():
    contract = compile_publish_artifact_request_to_contract(
        request=_publish_request(
            {
                "artifact_type": "workspace.directory",
                "title": "Source tree",
                "file_path": "src/",
            }
        )
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "workspace_directory"
    assert dumped["directory_path"] == "src/"


def test_compile_publish_artifact_request_to_structured_asset_contract():
    contract = compile_publish_artifact_request_to_contract(
        request=_publish_request(
            {
                "artifact_type": "structured.design_tokens",
                "title": "Design tokens",
                "file_path": "assets/tokens.json",
                "content_json": {"color.primary": "#223344"},
            }
        )
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "structured_asset"
    assert dumped["schema_name"] == "structured.design_tokens"
    assert dumped["storage_path"] == "assets/tokens.json"
    assert dumped["content_json"]["color.primary"] == "#223344"


def test_compile_rejects_non_publish_artifact_requests():
    with pytest.raises(ValueError):
        compile_publish_artifact_request_to_contract(
            request={
                "kind": "action_request",
                "version": 1,
                "request_id": "req-tool-1",
                "type": "use_tool",
                "source": {"agent_name": "Developer"},
                "payload": {"tool_name": "read_file", "arguments": {}},
            }
        )


def test_compile_and_validate_publish_artifact_request_accepts_expected_artifact():
    result = compile_and_validate_publish_artifact_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request=_publish_request(
            {
                "artifact_type": "document.test_report",
                "title": "Test report",
                "file_path": "reports/test_report.md",
                "content_markdown": "# Test Report",
            }
        ),
    )

    payload = result.to_payload()
    assert result.decision.accepted is True
    assert payload["contract"]["artifact_id"] == "artifact-req-artifact-1"
    assert payload["decision"]["accepted"] is True
    assert payload["policy_decision"]["subject"]["kind"] == "artifact_contract"
    assert payload["policy_decision"]["subject"]["id"] == "artifact-req-artifact-1"
    assert payload["policy_decision_event_payload"]["event_kind"] == "policy_decision_recorded"


def test_compile_and_validate_publish_artifact_request_rejects_unexpected_artifact():
    result = compile_and_validate_publish_artifact_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request=_publish_request(
            {
                "artifact_type": "document.release_note",
                "title": "Release note",
                "file_path": "CHANGELOG.md",
                "content_markdown": "# Release",
            }
        ),
    )

    assert result.decision.accepted is False
    assert [violation.code for violation in result.decision.violations] == ["artifact_not_expected"]
    payload = result.to_payload()
    assert payload["policy_decision"]["accepted"] is False
    assert payload["policy_decision_event_payload"]["accepted"] is False
