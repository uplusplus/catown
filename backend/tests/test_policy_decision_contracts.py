import pytest

from services.action_request_policy import validate_action_request_for_workflow
from services.artifact_contract_policy import validate_artifact_contract_for_workflow
from services.evaluation_result_policy import validate_evaluation_result_for_policy
from services.policy_decision_contracts import (
    dump_policy_decision,
    parse_policy_decision,
    project_policy_decision,
)
from services.runner_policy import compile_workflow_run_policy
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def _workflow_spec():
    return compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default workflow",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "Analysis",
                    "agent": "analyst",
                    "gate": "manual",
                    "expected_artifacts": ["PRD.md"],
                    "evaluation_rubrics": ["rubric-analysis"],
                },
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "expected_artifacts": ["test_report.md"],
                    "evaluation_rubrics": ["rubric-testing"],
                },
            ],
        },
    )


def test_parse_policy_decision_contract():
    decision = parse_policy_decision(
        {
            "kind": "policy_decision",
            "version": 1,
            "decision_id": "policy-decision-1",
            "decision_type": "action_request_policy",
            "subject": {
                "kind": "action_request",
                "id": "req-1",
                "type": "request_approval",
            },
            "accepted": True,
            "stage_name": "analysis",
            "policy_source": "workflow_spec",
            "pipeline_name": "default",
            "stage_count": 2,
        }
    )

    dumped = dump_policy_decision(decision)
    assert dumped["kind"] == "policy_decision"
    assert dumped["subject"]["id"] == "req-1"
    assert dumped["accepted"] is True


def test_project_action_request_policy_decision():
    decision = validate_action_request_for_workflow(
        workflow_spec=_workflow_spec(),
        request={
            "kind": "action_request",
            "version": 1,
            "request_id": "req-gate-1",
            "type": "request_approval",
            "source": {
                "agent_name": "Analyst",
                "agent_type": "analyst",
                "stage_name": "analysis",
            },
            "payload": {
                "target_kind": "pipeline_gate",
                "target_name": "analysis",
                "reason": "Manual gate.",
            },
        },
    )

    projected = project_policy_decision(decision)
    dumped = dump_policy_decision(projected)
    assert dumped["decision_id"] == "policy-decision-action-request-policy-req-gate-1"
    assert dumped["decision_type"] == "action_request_policy"
    assert dumped["subject"] == {
        "kind": "action_request",
        "id": "req-gate-1",
        "type": "request_approval",
    }
    assert dumped["accepted"] is True
    assert dumped["policy_source"] == "workflow_spec"
    assert dumped["pipeline_name"] == "default"


def test_project_artifact_policy_decision_with_violation():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-release-note-1",
            "artifact_type": "workspace.file",
            "title": "Release note",
            "producer": {"stage_name": "testing"},
            "mode": "workspace_file",
            "file_path": "CHANGELOG.md",
        },
    )

    dumped = dump_policy_decision(project_policy_decision(decision))
    assert dumped["decision_type"] == "artifact_contract_policy"
    assert dumped["subject"]["id"] == "artifact-release-note-1"
    assert dumped["subject"]["type"] == "workspace.file"
    assert dumped["accepted"] is False
    assert [violation["code"] for violation in dumped["violations"]] == ["artifact_not_expected"]


def test_project_evaluation_result_policy_decision():
    policy = compile_workflow_run_policy(workflow_spec=_workflow_spec(), project_id=7)
    decision = validate_evaluation_result_for_policy(
        policy=policy,
        result={
            "kind": "evaluation_result",
            "version": 1,
            "result_id": "eval-test-1",
            "rubric_id": "rubric-release",
            "target": {
                "workflow_id": "default",
                "stage_id": "testing",
            },
            "reviewer": {
                "name": "Tester",
                "owner": "agent",
            },
            "overall_status": "passed",
            "criterion_results": [
                {
                    "criterion_id": "blocker_absent",
                    "status": "passed",
                }
            ],
        },
    )

    dumped = dump_policy_decision(project_policy_decision(decision))
    assert dumped["decision_type"] == "evaluation_result_policy"
    assert dumped["subject"] == {
        "kind": "evaluation_result",
        "id": "eval-test-1",
        "type": None,
    }
    assert dumped["accepted"] is False
    assert dumped["violations"][0]["code"] == "rubric_not_allowed_for_stage"


def test_project_policy_decision_requires_identity_for_unknown_payload():
    with pytest.raises(ValueError):
        project_policy_decision({"accepted": True})
