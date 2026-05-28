from services.action_request_policy import (
    validate_action_request_for_workflow,
    validate_and_project_action_request_for_workflow,
)
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def _workflow_spec():
    return compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default delivery workflow",
            "description": "analysis -> development -> testing -> release",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "Analysis",
                    "agent": "analyst",
                    "gate": "manual",
                    "expected_artifacts": ["docs/prd/"],
                },
                {
                    "name": "architecture",
                    "display_name": "Architecture",
                    "agent": "architect",
                    "gate": "auto",
                    "expected_artifacts": ["docs/specs/"],
                },
                {
                    "name": "development",
                    "display_name": "Development",
                    "agent": "developer",
                    "gate": "auto",
                    "expected_artifacts": ["src/"],
                },
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "expected_artifacts": ["reports/tests/"],
                    "rollback_on_blocker": True,
                    "max_rollback_count": 3,
                    "rollback_target": "development",
                },
                {
                    "name": "release",
                    "display_name": "Release",
                    "agent": "release",
                    "gate": "manual",
                    "expected_artifacts": ["reports/releases/"],
                },
            ],
        },
    )


def _source(stage_name: str, agent_type: str):
    return {
        "agent_name": agent_type.title(),
        "agent_type": agent_type,
        "stage_name": stage_name,
        "task_run_id": 10,
    }


def test_manual_gate_approval_request_is_accepted():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-gate-1",
            "type": "request_approval",
            "source": _source("analysis", "analyst"),
            "payload": {
                "queue_kind": "approval",
                "target_kind": "pipeline_gate",
                "target_name": "analysis",
                "reason": "Analysis gate requires human confirmation.",
                "resume_supported": True,
            },
        },
    )

    assert decision.accepted is True
    assert decision.violations == []
    assert decision.to_payload()["metadata"]["pipeline_name"] == "default"


def test_action_request_policy_result_projects_policy_decision():
    result = validate_and_project_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-gate-projected-1",
            "type": "request_approval",
            "source": _source("analysis", "analyst"),
            "payload": {
                "queue_kind": "approval",
                "target_kind": "pipeline_gate",
                "target_name": "analysis",
                "reason": "Analysis gate requires human confirmation.",
            },
        },
    )

    payload = result.to_payload()
    assert payload["request"]["request_id"] == "req-gate-projected-1"
    assert payload["decision"]["accepted"] is True
    assert payload["policy_decision"]["subject"] == {
        "kind": "action_request",
        "id": "req-gate-projected-1",
        "type": "request_approval",
    }
    assert payload["policy_decision_event_payload"]["event_kind"] == "policy_decision_recorded"
    assert payload["policy_decision_gate_result"]["allowed"] is True


def test_auto_gate_approval_request_is_rejected():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-gate-2",
            "type": "request_approval",
            "source": _source("development", "developer"),
            "payload": {
                "queue_kind": "approval",
                "target_kind": "pipeline_gate",
                "target_name": "development",
                "reason": "Development is done.",
                "resume_supported": True,
            },
        },
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == ["stage_gate_not_configured"]


def test_source_agent_mismatch_is_rejected():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-tool-1",
            "type": "use_tool",
            "source": _source("testing", "developer"),
            "payload": {
                "tool_name": "run_tests",
                "arguments": {},
            },
        },
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == ["source_agent_mismatch"]


def test_rollback_target_must_match_workflow_policy():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-rollback-1",
            "type": "suggest_rollback",
            "source": _source("testing", "tester"),
            "payload": {
                "target_stage_name": "analysis",
                "reason": "Blocker found in acceptance tests.",
                "blocker_code": "auth-001",
            },
        },
    )

    assert decision.accepted is False
    assert "rollback_target_mismatch" in [violation.code for violation in decision.violations]


def test_publish_artifact_is_checked_against_stage_delivery_contract():
    accepted = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-artifact-1",
            "type": "publish_artifact",
            "source": _source("testing", "tester"),
            "payload": {
                "artifact_type": "document.test_report",
                "title": "Test report",
                "file_path": "reports/tests/20260522T143122004981Z--task-45--backend-pytest.md",
            },
        },
    )
    rejected = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-artifact-2",
            "type": "publish_artifact",
            "source": _source("testing", "tester"),
            "payload": {
                "artifact_type": "document.release_note",
                "title": "Release note",
                "file_path": "reports/releases/changelog.md",
            },
        },
    )

    assert accepted.accepted is True
    assert rejected.accepted is False
    assert [violation.code for violation in rejected.violations] == ["artifact_not_expected"]


def test_publish_artifact_rejects_non_timestamped_test_report_even_in_canonical_directory():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-artifact-5",
            "type": "publish_artifact",
            "source": _source("testing", "tester"),
            "payload": {
                "artifact_type": "document.test_report",
                "title": "Test report",
                "file_path": "reports/tests/backend-pytest.md",
            },
        },
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == [
        "artifact_path_timestamp_required"
    ]


def test_directory_artifact_expectation_accepts_nested_files():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-artifact-3",
            "type": "publish_artifact",
            "source": _source("development", "developer"),
            "payload": {
                "artifact_type": "workspace_file",
                "title": "Implementation",
                "file_path": "src/features/runtime_policy.py",
            },
        },
    )

    assert decision.accepted is True


def test_semantic_spec_artifact_expectation_accepts_nested_files():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-artifact-4",
            "type": "publish_artifact",
            "source": _source("architecture", "architect"),
            "payload": {
                "artifact_type": "document.spec",
                "title": "Architecture spec",
                "file_path": "docs/specs/project-browser-artifact-storage.md",
            },
        },
    )

    assert decision.accepted is True
