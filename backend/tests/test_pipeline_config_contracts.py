import json

from pipeline.config import PipelineConfigManager


def test_pipeline_config_manager_exports_canonical_workflow_spec(tmp_path):
    config_file = tmp_path / "pipelines.json"
    config_file.write_text(
        json.dumps(
            {
                "default": {
                    "name": "标准软件开发流水线",
                    "description": "需求分析 -> 架构设计 -> 开发",
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
                            "name": "development",
                            "display_name": "开发",
                            "agent": "developer",
                            "gate": "auto",
                            "timeout_minutes": 60,
                            "expected_artifacts": ["src/"],
                            "context_prompt": "Write code.",
                            "rollback_on_blocker": True,
                            "max_rollback_count": 2,
                            "rollback_target": "analysis",
                            "active_skills": ["code-generation"],
                            "hint_only_skills": ["debugging"],
                        },
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = PipelineConfigManager(config_file=str(config_file))
    configs = manager.load()
    assert "default" in configs

    workflow_spec = manager.get_workflow_spec("default")
    assert workflow_spec is not None
    assert workflow_spec.workflow_id == "default"
    assert workflow_spec.name == "标准软件开发流水线"
    assert len(workflow_spec.stages) == 2
    assert workflow_spec.stages[0].stage_id == "analysis"
    assert workflow_spec.stages[0].gate == "manual"
    assert workflow_spec.stages[0].delivery.expected_artifacts == ["PRD.md"]
    assert workflow_spec.stages[1].rollback.enabled is True
    assert workflow_spec.stages[1].rollback.target_stage_name == "analysis"
    assert workflow_spec.stages[1].skills.hint_only == ["debugging"]

    report = manager.get_workflow_spec_report("default")
    assert report is not None
    assert report.executable is True
    assert report.to_payload()["metadata"]["stage_count"] == 2


def test_pipeline_config_manager_exports_workflow_spec_diagnostics(tmp_path):
    config_file = tmp_path / "pipelines.json"
    config_file.write_text(
        json.dumps(
            {
                "broken": {
                    "name": "Broken workflow",
                    "description": "Invalid rollback target",
                    "stages": [
                        {
                            "name": "testing",
                            "display_name": "测试",
                            "agent": "tester",
                            "gate": "auto",
                            "timeout_minutes": 30,
                            "rollback_on_blocker": True,
                            "max_rollback_count": 2,
                            "rollback_target": "missing",
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = PipelineConfigManager(config_file=str(config_file))
    configs = manager.load()

    assert "broken" in configs
    report = manager.get_workflow_spec_report("broken")
    assert report is not None
    assert report.executable is False
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        "rollback_target_unknown"
    ]
