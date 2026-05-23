from services.runner_policy import compile_workflow_run_policy
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def test_compile_workflow_run_policy_preserves_stage_governance_shape():
    workflow_spec = compile_pipeline_template_to_workflow_spec(
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

    policy = compile_workflow_run_policy(
        workflow_spec=workflow_spec,
        project_id=7,
        stage_tool_packs={"analysis": {"tool_names": ["read_file"]}},
    )

    payload = policy.to_payload()
    assert payload["mode"] == "pipeline_governance"
    assert payload["source"] == "workflow_spec"
    assert payload["pipeline_name"] == "default"
    assert payload["project_id"] == 7
    assert payload["stage_count"] == 4
    assert payload["metadata"]["workflow_name"] == "Standard software delivery workflow"
    assert payload["metadata"]["workflow_domain"] == "software_delivery"

    analysis = payload["stages"][0]
    assert analysis["stage_name"] == "analysis"
    assert analysis["agent_name"] == "analyst"
    assert analysis["approval"]["required"] is True
    assert analysis["delivery"]["expected_artifacts"] == ["docs/prd/"]
    assert analysis["metadata"]["tool_policy_summary"]["tool_count"] == 1
    assert analysis["metadata"]["evaluation_policy"] == {
        "rubric_refs": ["rubric-analysis-prd"],
        "required": True,
    }

    architecture = payload["stages"][1]
    assert architecture["stage_name"] == "architecture"
    assert architecture["delivery"]["expected_artifacts"] == ["docs/specs/"]
    assert architecture["metadata"]["evaluation_policy"] == {
        "rubric_refs": ["rubric-architecture-spec"],
        "required": True,
    }

    development = payload["stages"][2]
    assert development["stage_name"] == "development"
    assert development["delivery"]["expected_artifacts"] == ["src/"]
    assert development["hint_only_skills"] == ["debugging"]

    testing = payload["stages"][3]
    assert testing["stage_name"] == "testing"
    assert testing["delivery"]["expected_artifacts"] == ["reports/tests/"]
    assert testing["rollback"]["enabled"] is True
    assert testing["rollback"]["max_attempts"] == 3
    assert testing["rollback"]["target_stage"] == "development"
    assert testing["hint_only_skills"] == ["security-testing"]
    assert testing["metadata"]["evaluation_policy"]["rubric_refs"] == ["rubric-testing"]
