import pytest

from services.evaluation_action_requests import (
    build_and_validate_rollback_request_from_evaluation_result,
    build_rollback_policy_result_from_evaluation_result,
    build_report_blocker_request_from_evaluation_result,
    build_suggest_rollback_request_from_evaluation_result,
)
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def _failed_result():
    return {
        "kind": "evaluation_result",
        "version": 1,
        "result_id": "eval-test-1",
        "rubric_id": "rubric-testing",
        "target": {
            "artifact_type": "document.test_report",
            "file_path": "test_report.md",
            "workflow_id": "default",
            "stage_id": "testing",
            "task_run_id": 21,
            "pipeline_run_id": 5,
            "pipeline_stage_id": 9,
        },
        "reviewer": {
            "name": "Tester",
            "agent_type": "tester",
            "owner": "agent",
        },
        "overall_status": "failed",
        "criterion_results": [
            {
                "criterion_id": "blocker_absent",
                "status": "failed",
                "score": 0,
                "rationale": "A blocker was found in auth checks.",
                "evidence_refs": ["test_report.md"],
            }
        ],
        "summary": "Testing failed.",
    }


def _needs_review_result():
    result = _failed_result()
    result["result_id"] = "eval-design-1"
    result["overall_status"] = "needs_review"
    result["criterion_results"][0]["status"] = "needs_review"
    result["criterion_results"][0]["rationale"] = "Visual hierarchy needs human review."
    return result


def _workflow_spec():
    return compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default workflow",
            "stages": [
                {
                    "name": "development",
                    "display_name": "Development",
                    "agent": "developer",
                    "gate": "auto",
                },
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "rollback_on_blocker": True,
                    "max_rollback_count": 3,
                    "rollback_target": "development",
                },
            ],
        },
    )


def test_failed_evaluation_result_builds_report_blocker_request():
    request = build_report_blocker_request_from_evaluation_result(
        result=_failed_result(),
        request_id="req-blocker-1",
        agent_name="Tester",
    )

    assert request["type"] == "report_blocker"
    assert request["source"]["stage_name"] == "testing"
    assert request["source"]["agent_type"] == "tester"
    assert request["payload"]["severity"] == "blocker"
    assert request["payload"]["blocker_code"] == "eval-test-1"
    assert "auth checks" in request["payload"]["reason"]
    assert request["metadata"]["evaluation_result_id"] == "eval-test-1"


def test_needs_review_evaluation_result_builds_major_blocker_request():
    request = build_report_blocker_request_from_evaluation_result(
        result=_needs_review_result(),
        request_id="req-review-1",
        agent_name="Designer",
        agent_type="designer",
    )

    assert request["type"] == "report_blocker"
    assert request["payload"]["severity"] == "major"
    assert request["source"]["agent_type"] == "designer"
    assert request["metadata"]["overall_status"] == "needs_review"


def test_failed_evaluation_result_builds_suggest_rollback_request():
    request = build_suggest_rollback_request_from_evaluation_result(
        result=_failed_result(),
        request_id="req-rollback-1",
        agent_name="Tester",
        target_stage_name="development",
    )

    assert request["type"] == "suggest_rollback"
    assert request["payload"]["target_stage_name"] == "development"
    assert request["payload"]["blocker_code"] == "eval-test-1"
    assert request["source"]["pipeline_stage_id"] == 9


def test_failed_evaluation_result_rollback_request_can_be_validated_against_workflow():
    request, decision = build_and_validate_rollback_request_from_evaluation_result(
        result=_failed_result(),
        request_id="req-rollback-validated-1",
        agent_name="Tester",
        target_stage_name="development",
        workflow_spec=_workflow_spec(),
    )

    assert request["type"] == "suggest_rollback"
    assert decision.accepted is True
    assert decision.stage_name == "testing"
    assert decision.violations == []


def test_evaluation_rollback_request_policy_rejects_wrong_target():
    request, decision = build_and_validate_rollback_request_from_evaluation_result(
        result=_failed_result(),
        request_id="req-rollback-validated-2",
        agent_name="Tester",
        target_stage_name="analysis",
        workflow_spec=_workflow_spec(),
    )

    assert request["type"] == "suggest_rollback"
    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == [
        "unknown_rollback_target"
    ]


def test_evaluation_rollback_policy_result_projects_policy_decision():
    result = build_rollback_policy_result_from_evaluation_result(
        result=_failed_result(),
        request_id="req-rollback-result-1",
        agent_name="Tester",
        target_stage_name="development",
        workflow_spec=_workflow_spec(),
    )

    payload = result.to_payload()
    assert payload["request"]["type"] == "suggest_rollback"
    assert payload["decision"]["accepted"] is True
    assert payload["policy_decision"]["subject"] == {
        "kind": "action_request",
        "id": "req-rollback-result-1",
        "type": "suggest_rollback",
    }
    assert payload["policy_decision_event_payload"]["event_kind"] == "policy_decision_recorded"
    assert payload["policy_decision_gate_result"]["allowed"] is True


def test_passed_evaluation_result_does_not_build_blocker_request():
    result = _failed_result()
    result["overall_status"] = "passed"
    result["criterion_results"][0]["status"] = "passed"

    with pytest.raises(ValueError):
        build_report_blocker_request_from_evaluation_result(
            result=result,
            request_id="req-blocker-2",
            agent_name="Tester",
        )


def test_needs_review_result_does_not_build_rollback_request():
    with pytest.raises(ValueError):
        build_suggest_rollback_request_from_evaluation_result(
            result=_needs_review_result(),
            request_id="req-rollback-2",
            agent_name="Designer",
            target_stage_name="design",
        )
