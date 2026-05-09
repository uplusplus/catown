import pytest

from services.evaluation_action_requests import (
    build_report_blocker_request_from_evaluation_result,
    build_suggest_rollback_request_from_evaluation_result,
)


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
