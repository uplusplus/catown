from pydantic import ValidationError

from services.evaluation_rubric_contracts import (
    dump_evaluation_rubric,
    parse_evaluation_rubric,
)


def test_parse_stage_evaluation_rubric():
    rubric = parse_evaluation_rubric(
        {
            "kind": "evaluation_rubric",
            "version": 1,
            "rubric_id": "rubric-analysis-prd",
            "name": "PRD completeness rubric",
            "domain": "software_delivery",
            "applies_to": {
                "workflow_id": "default",
                "stage_id": "analysis",
                "agent_type": "analyst",
                "artifact_type": "document.prd",
            },
            "criteria": [
                {
                    "criterion_id": "stories",
                    "name": "User stories",
                    "description": "PRD includes user stories and acceptance criteria.",
                    "scale": "pass_fail",
                    "evaluator": "agent",
                    "required": True,
                    "acceptance_threshold": 1,
                },
                {
                    "criterion_id": "ambiguity",
                    "name": "Ambiguity reduction",
                    "scale": "score_1_5",
                    "evaluator": "hybrid",
                    "weight": 2,
                    "acceptance_threshold": 4,
                    "guidance": "Important assumptions should be explicit.",
                },
            ],
            "guidance": "Prefer concrete acceptance criteria over broad goals.",
        }
    )

    dumped = dump_evaluation_rubric(rubric)
    assert dumped["kind"] == "evaluation_rubric"
    assert dumped["applies_to"]["stage_id"] == "analysis"
    assert dumped["criteria"][1]["acceptance_threshold"] == 4


def test_parse_taste_heavy_rubric_uses_human_or_hybrid_evaluators():
    rubric = parse_evaluation_rubric(
        {
            "kind": "evaluation_rubric",
            "version": 1,
            "rubric_id": "rubric-ui-taste",
            "name": "UI taste rubric",
            "domain": "ui_design",
            "applies_to": {
                "stage_id": "design_review",
                "artifact_type": "structured.design_review",
            },
            "criteria": [
                {
                    "criterion_id": "visual_hierarchy",
                    "name": "Visual hierarchy",
                    "scale": "qualitative",
                    "evaluator": "hybrid",
                    "guidance": "Assess hierarchy, rhythm, contrast, and restraint.",
                },
                {
                    "criterion_id": "brand_fit",
                    "name": "Brand fit",
                    "scale": "qualitative",
                    "evaluator": "human",
                    "required": True,
                },
            ],
        }
    )

    dumped = dump_evaluation_rubric(rubric)
    assert dumped["domain"] == "ui_design"
    assert dumped["criteria"][0]["evaluator"] == "hybrid"
    assert dumped["criteria"][1]["evaluator"] == "human"


def test_duplicate_criterion_ids_are_rejected():
    try:
        parse_evaluation_rubric(
            {
                "kind": "evaluation_rubric",
                "version": 1,
                "rubric_id": "rubric-duplicate",
                "name": "Duplicate rubric",
                "criteria": [
                    {"criterion_id": "complete", "name": "Complete"},
                    {"criterion_id": "complete", "name": "Complete again"},
                ],
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError for duplicate criterion ids.")


def test_invalid_threshold_for_scale_is_rejected():
    try:
        parse_evaluation_rubric(
            {
                "kind": "evaluation_rubric",
                "version": 1,
                "rubric_id": "rubric-invalid-threshold",
                "name": "Invalid threshold",
                "criteria": [
                    {
                        "criterion_id": "taste",
                        "name": "Taste",
                        "scale": "qualitative",
                        "acceptance_threshold": 4,
                    }
                ],
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError for qualitative threshold.")
