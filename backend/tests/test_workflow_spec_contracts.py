from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def test_compile_pipeline_template_to_workflow_spec_preserves_current_stage_shape():
    spec = compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "标准软件开发流水线",
            "description": "需求分析 -> 架构设计 -> 开发 -> 测试 -> 发布",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "需求分析",
                    "agent": "analyst",
                    "gate": "manual",
                    "timeout_minutes": 30,
                    "expected_artifacts": ["PRD.md"],
                    "context_prompt": "Write a PRD.",
                    "active_skills": ["document-analysis"],
                    "hint_only_skills": [],
                    "evaluation_rubrics": ["rubric-analysis-prd"],
                },
                {
                    "name": "testing",
                    "display_name": "测试",
                    "agent": "tester",
                    "gate": "auto",
                    "timeout_minutes": 30,
                    "expected_artifacts": ["test_report.md"],
                    "context_prompt": "Run tests.",
                    "rollback_on_blocker": True,
                    "max_rollback_count": 3,
                    "rollback_target": "development",
                    "active_skills": ["test-generation"],
                    "hint_only_skills": ["security-testing"],
                    "evaluation_rubrics": ["rubric-testing"],
                },
            ],
        },
    )

    assert spec.kind == "workflow_spec"
    assert spec.version == 1
    assert spec.workflow_id == "default"
    assert spec.name == "标准软件开发流水线"
    assert len(spec.stages) == 2

    analysis = spec.stages[0]
    assert analysis.stage_id == "analysis"
    assert analysis.agent_type == "analyst"
    assert analysis.gate == "manual"
    assert analysis.delivery.expected_artifacts == ["PRD.md"]
    assert analysis.delivery.required is True
    assert analysis.skills.active == ["document-analysis"]
    assert analysis.evaluation.rubric_refs == ["rubric-analysis-prd"]
    assert analysis.evaluation.required is True
    assert analysis.rollback.enabled is False

    testing = spec.stages[1]
    assert testing.stage_id == "testing"
    assert testing.agent_type == "tester"
    assert testing.rollback.enabled is True
    assert testing.rollback.max_attempts == 3
    assert testing.rollback.target_stage_name == "development"
    assert testing.skills.hint_only == ["security-testing"]
    assert testing.evaluation.rubric_refs == ["rubric-testing"]
