from services.runner_policy import compile_workflow_run_policy
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def test_compile_workflow_run_policy_preserves_stage_governance_shape():
    workflow_spec = compile_pipeline_template_to_workflow_spec(
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
    assert payload["stage_count"] == 2
    assert payload["metadata"]["workflow_name"] == "标准软件开发流水线"
    assert payload["metadata"]["workflow_domain"] == "software_delivery"

    analysis = payload["stages"][0]
    assert analysis["stage_name"] == "analysis"
    assert analysis["agent_name"] == "analyst"
    assert analysis["approval"]["required"] is True
    assert analysis["delivery"]["expected_artifacts"] == ["PRD.md"]
    assert analysis["metadata"]["tool_policy_summary"]["tool_count"] == 1

    testing = payload["stages"][1]
    assert testing["stage_name"] == "testing"
    assert testing["rollback"]["enabled"] is True
    assert testing["rollback"]["max_attempts"] == 3
    assert testing["rollback"]["target_stage"] == "development"
    assert testing["hint_only_skills"] == ["security-testing"]
