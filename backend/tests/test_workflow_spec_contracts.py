from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def test_compile_pipeline_template_to_workflow_spec_preserves_current_stage_shape():
    spec = compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Standard software delivery workflow",
            "description": "analysis -> architecture -> development -> testing -> release",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "Analysis",
                    "agent": "analyst",
                    "gate": "manual",
                    "timeout_minutes": 30,
                    "expected_artifacts": ["docs/prd/"],
                    "context_prompt": "Write a PRD.",
                    "active_skills": ["document-analysis"],
                    "hint_only_skills": [],
                    "evaluation_rubrics": ["rubric-analysis-prd"],
                },
                {
                    "name": "architecture",
                    "display_name": "Architecture",
                    "agent": "architect",
                    "gate": "auto",
                    "timeout_minutes": 45,
                    "expected_artifacts": ["docs/specs/"],
                    "context_prompt": "Write a technical specification.",
                    "active_skills": ["architecture-design"],
                    "hint_only_skills": ["knowledge-graph"],
                    "evaluation_rubrics": ["rubric-architecture-spec"],
                },
                {
                    "name": "development",
                    "display_name": "Development",
                    "agent": "developer",
                    "gate": "auto",
                    "timeout_minutes": 60,
                    "expected_artifacts": ["src/"],
                    "context_prompt": "Write code.",
                    "active_skills": ["code-generation"],
                    "hint_only_skills": ["debugging"],
                },
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "timeout_minutes": 30,
                    "expected_artifacts": ["reports/tests/"],
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
    assert spec.name == "Standard software delivery workflow"
    assert len(spec.stages) == 4

    analysis = spec.stages[0]
    assert analysis.stage_id == "analysis"
    assert analysis.agent_type == "analyst"
    assert analysis.gate == "manual"
    assert analysis.delivery.expected_artifacts == ["docs/prd/"]
    assert analysis.delivery.required is True
    assert analysis.skills.active == ["document-analysis"]
    assert analysis.evaluation.rubric_refs == ["rubric-analysis-prd"]
    assert analysis.evaluation.required is True
    assert analysis.rollback.enabled is False

    architecture = spec.stages[1]
    assert architecture.stage_id == "architecture"
    assert architecture.agent_type == "architect"
    assert architecture.delivery.expected_artifacts == ["docs/specs/"]
    assert architecture.skills.hint_only == ["knowledge-graph"]
    assert architecture.evaluation.rubric_refs == ["rubric-architecture-spec"]

    development = spec.stages[2]
    assert development.stage_id == "development"
    assert development.agent_type == "developer"
    assert development.delivery.expected_artifacts == ["src/"]
    assert development.skills.hint_only == ["debugging"]

    testing = spec.stages[3]
    assert testing.stage_id == "testing"
    assert testing.agent_type == "tester"
    assert testing.rollback.enabled is True
    assert testing.rollback.max_attempts == 3
    assert testing.rollback.target_stage_name == "development"
    assert testing.delivery.expected_artifacts == ["reports/tests/"]
    assert testing.skills.hint_only == ["security-testing"]
    assert testing.evaluation.rubric_refs == ["rubric-testing"]
