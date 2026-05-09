from pydantic import ValidationError

from services.evaluation_result_contracts import (
    dump_evaluation_result,
    parse_evaluation_result,
)


def test_parse_passed_evaluation_result():
    result = parse_evaluation_result(
        {
            "kind": "evaluation_result",
            "version": 1,
            "result_id": "eval-prd-1",
            "rubric_id": "rubric-analysis-prd",
            "target": {
                "artifact_id": "artifact-prd-1",
                "artifact_type": "document.prd",
                "file_path": "PRD.md",
                "workflow_id": "default",
                "stage_id": "analysis",
                "task_run_id": 12,
            },
            "reviewer": {
                "name": "Analyst",
                "agent_type": "analyst",
                "owner": "agent",
            },
            "overall_status": "passed",
            "criterion_results": [
                {
                    "criterion_id": "stories",
                    "status": "passed",
                    "score": 1,
                    "rationale": "Stories and acceptance criteria are present.",
                    "evidence_refs": ["artifact-prd-1"],
                }
            ],
            "summary": "PRD satisfies the stage rubric.",
        }
    )

    dumped = dump_evaluation_result(result)
    assert dumped["kind"] == "evaluation_result"
    assert dumped["overall_status"] == "passed"
    assert dumped["target"]["stage_id"] == "analysis"
    assert dumped["criterion_results"][0]["criterion_id"] == "stories"


def test_parse_failed_evaluation_result_can_reference_action_requests():
    result = parse_evaluation_result(
        {
            "kind": "evaluation_result",
            "version": 1,
            "result_id": "eval-test-1",
            "rubric_id": "rubric-testing",
            "target": {
                "artifact_type": "document.test_report",
                "file_path": "test_report.md",
                "stage_id": "testing",
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
            "recommended_action_request_ids": ["req-rollback-1"],
        }
    )

    dumped = dump_evaluation_result(result)
    assert dumped["overall_status"] == "failed"
    assert dumped["recommended_action_request_ids"] == ["req-rollback-1"]


def test_duplicate_criterion_result_ids_are_rejected():
    try:
        parse_evaluation_result(
            {
                "kind": "evaluation_result",
                "version": 1,
                "result_id": "eval-duplicate",
                "rubric_id": "rubric-duplicate",
                "overall_status": "needs_review",
                "criterion_results": [
                    {"criterion_id": "fit", "status": "needs_review"},
                    {"criterion_id": "fit", "status": "needs_review"},
                ],
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError for duplicate criterion result ids.")


def test_passed_result_cannot_include_failed_criteria():
    try:
        parse_evaluation_result(
            {
                "kind": "evaluation_result",
                "version": 1,
                "result_id": "eval-inconsistent",
                "rubric_id": "rubric-test",
                "overall_status": "passed",
                "criterion_results": [
                    {
                        "criterion_id": "blocker_absent",
                        "status": "failed",
                        "rationale": "Blocker found.",
                    }
                ],
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError for inconsistent passed result.")
