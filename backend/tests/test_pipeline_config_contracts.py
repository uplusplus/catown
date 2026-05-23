import json

from pipeline.config import PipelineConfigManager


def test_pipeline_config_manager_exports_canonical_workflow_spec(tmp_path):
    config_file = tmp_path / "pipelines.json"
    config_file.write_text(
        json.dumps(
            {
                "default": {
                    "name": "Standard software delivery workflow",
                    "description": "analysis -> architecture -> development",
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
                        },
                        {
                            "name": "development",
                            "display_name": "Development",
                            "agent": "developer",
                            "gate": "auto",
                            "timeout_minutes": 60,
                            "expected_artifacts": ["src/"],
                            "context_prompt": "Write code.",
                            "rollback_on_blocker": True,
                            "max_rollback_count": 2,
                            "rollback_target": "architecture",
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
    assert workflow_spec.name == "Standard software delivery workflow"
    assert len(workflow_spec.stages) == 3
    assert workflow_spec.stages[0].stage_id == "analysis"
    assert workflow_spec.stages[0].gate == "manual"
    assert workflow_spec.stages[0].delivery.expected_artifacts == ["docs/prd/"]
    assert workflow_spec.stages[1].stage_id == "architecture"
    assert workflow_spec.stages[1].delivery.expected_artifacts == ["docs/specs/"]
    assert workflow_spec.stages[2].rollback.enabled is True
    assert workflow_spec.stages[2].rollback.target_stage_name == "architecture"
    assert workflow_spec.stages[2].skills.hint_only == ["debugging"]

    report = manager.get_workflow_spec_report("default")
    assert report is not None
    assert report.executable is True
    assert report.to_payload()["metadata"]["stage_count"] == 3


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
                            "display_name": "Testing",
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
