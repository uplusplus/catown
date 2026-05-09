from services.evaluation_result_policy import (
    validate_and_project_evaluation_result_for_policy,
    validate_evaluation_result_for_policy,
)
from services.runner_policy import compile_workflow_run_policy
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def _policy():
    workflow_spec = compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default workflow",
            "stages": [
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "evaluation_rubrics": ["rubric-testing"],
                },
                {
                    "name": "release",
                    "display_name": "Release",
                    "agent": "release",
                    "gate": "manual",
                },
            ],
        },
    )
    return compile_workflow_run_policy(workflow_spec=workflow_spec, project_id=7)


def _result(*, rubric_id: str = "rubric-testing", stage_id: str = "testing"):
    return {
        "kind": "evaluation_result",
        "version": 1,
        "result_id": "eval-test-1",
        "rubric_id": rubric_id,
        "target": {
            "artifact_type": "document.test_report",
            "file_path": "test_report.md",
            "workflow_id": "default",
            "stage_id": stage_id,
        },
        "reviewer": {
            "name": "Tester",
            "agent_type": "tester",
            "owner": "agent",
        },
        "overall_status": "passed",
        "criterion_results": [
            {
                "criterion_id": "blocker_absent",
                "status": "passed",
            }
        ],
    }


def test_evaluation_result_policy_accepts_stage_rubric_ref():
    decision = validate_evaluation_result_for_policy(
        result=_result(),
        policy=_policy(),
    )

    assert decision.accepted is True
    assert decision.stage_name == "testing"
    assert decision.violations == []
    assert decision.to_payload()["metadata"]["pipeline_name"] == "default"


def test_evaluation_result_policy_rejects_wrong_rubric_for_stage():
    decision = validate_evaluation_result_for_policy(
        result=_result(rubric_id="rubric-release"),
        policy=_policy(),
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == [
        "rubric_not_allowed_for_stage"
    ]


def test_evaluation_result_policy_rejects_unknown_stage():
    decision = validate_evaluation_result_for_policy(
        result=_result(stage_id="security"),
        policy=_policy(),
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == [
        "unknown_result_stage"
    ]


def test_evaluation_result_policy_accepts_stage_without_rubric_policy():
    decision = validate_evaluation_result_for_policy(
        result=_result(rubric_id="rubric-release", stage_id="release"),
        policy=_policy(),
    )

    assert decision.accepted is True
    assert decision.stage_name == "release"


def test_evaluation_result_policy_result_projects_policy_decision():
    result = validate_and_project_evaluation_result_for_policy(
        result=_result(rubric_id="rubric-release"),
        policy=_policy(),
    )

    payload = result.to_payload()
    assert payload["result"]["result_id"] == "eval-test-1"
    assert payload["decision"]["accepted"] is False
    assert payload["policy_decision"]["subject"] == {
        "kind": "evaluation_result",
        "id": "eval-test-1",
        "type": None,
    }
    assert payload["policy_decision_event_payload"]["event_kind"] == "policy_decision_recorded"
    assert payload["policy_decision_gate_result"]["blocked"] is True
    assert payload["policy_decision_gate_result"]["blocked_kind"] == "policy_decision"
