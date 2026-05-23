from services.workflow_spec_contracts import (
    WorkflowDeliverySpec,
    WorkflowRollbackSpec,
    WorkflowSkillSpec,
    WorkflowSpec,
    WorkflowStageSpec,
    compile_pipeline_template_to_workflow_spec,
)
from services.workflow_spec_policy import (
    compile_pipeline_template_with_policy_report,
    project_workflow_spec_policy_report,
    validate_and_project_workflow_spec_for_execution,
    validate_workflow_spec_for_execution,
)


def test_valid_pipeline_template_workflow_is_executable():
    spec = compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default workflow",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "Analysis",
                    "agent": "analyst",
                    "gate": "manual",
                    "expected_artifacts": ["PRD.md"],
                },
                {
                    "name": "development",
                    "display_name": "Development",
                    "agent": "developer",
                    "gate": "auto",
                    "expected_artifacts": ["src/"],
                },
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "gate": "auto",
                    "expected_artifacts": ["reports/tests/"],
                    "rollback_on_blocker": True,
                    "max_rollback_count": 3,
                    "rollback_target": "development",
                },
            ],
        },
    )

    report = validate_workflow_spec_for_execution(spec)

    assert report.executable is True
    assert report.diagnostics == []
    assert report.to_payload()["metadata"]["stage_count"] == 3


def test_compile_pipeline_template_with_policy_report_returns_spec_and_diagnostics():
    result = compile_pipeline_template_with_policy_report(
        "default",
        {
            "name": "Default workflow",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "Analysis",
                    "agent": "analyst",
                    "gate": "manual",
                    "expected_artifacts": ["PRD.md"],
                }
            ],
        },
    )

    payload = result.to_payload()
    assert result.executable is True
    assert result.workflow_spec.workflow_id == "default"
    assert payload["workflow_spec"]["stages"][0]["stage_id"] == "analysis"
    assert payload["policy_report"]["executable"] is True


def test_compile_pipeline_template_with_policy_report_surfaces_template_errors():
    result = compile_pipeline_template_with_policy_report(
        "broken",
        {
            "name": "Broken workflow",
            "stages": [
                {
                    "name": "testing",
                    "display_name": "Testing",
                    "agent": "tester",
                    "rollback_on_blocker": True,
                    "max_rollback_count": 3,
                    "rollback_target": "missing",
                }
            ],
        },
    )

    assert result.executable is False
    assert [diagnostic.code for diagnostic in result.policy_report.diagnostics] == [
        "rollback_target_unknown"
    ]


def test_workflow_spec_policy_report_projects_policy_decision():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="empty",
            name="Empty",
            stages=[],
        )
    )

    policy_decision = project_workflow_spec_policy_report(report)
    dumped = policy_decision.model_dump(mode="json")
    assert dumped["decision_type"] == "workflow_spec_policy"
    assert dumped["subject"] == {
        "kind": "workflow_spec",
        "id": "empty",
        "type": None,
    }
    assert dumped["accepted"] is False
    assert dumped["policy_source"] == "workflow_spec_policy"
    assert dumped["stage_count"] == 0
    assert [violation["code"] for violation in dumped["violations"]] == ["workflow_has_no_stages"]


def test_validate_and_project_workflow_spec_builds_ledger_payload():
    result = validate_and_project_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="valid",
            name="Valid",
            stages=[
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="Analysis",
                    agent_type="analyst",
                )
            ],
        )
    )

    payload = result.to_payload()
    assert payload["policy_report"]["executable"] is True
    assert payload["policy_decision"]["accepted"] is True
    assert payload["policy_decision_event_payload"]["event_kind"] == "policy_decision_recorded"
    assert payload["policy_decision_gate_result"]["allowed"] is True


def test_workflow_requires_at_least_one_stage():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="empty",
            name="Empty",
            stages=[],
        )
    )

    assert report.executable is False
    assert [diagnostic.code for diagnostic in report.diagnostics] == ["workflow_has_no_stages"]


def test_duplicate_stage_ids_are_rejected():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="duplicate",
            name="Duplicate",
            stages=[
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="Analysis",
                    agent_type="analyst",
                ),
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="Analysis copy",
                    agent_type="analyst",
                ),
            ],
        )
    )

    assert report.executable is False
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        "duplicate_stage_id",
        "duplicate_stage_id",
    ]


def test_stage_agent_and_timeout_are_required_for_execution():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="invalid-stage",
            name="Invalid stage",
            stages=[
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="Analysis",
                    agent_type="",
                    timeout_minutes=0,
                )
            ],
        )
    )

    assert report.executable is False
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        "stage_agent_missing",
        "stage_timeout_invalid",
    ]


def test_required_delivery_must_declare_expected_artifacts():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="delivery",
            name="Delivery",
            stages=[
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="Analysis",
                    agent_type="analyst",
                    delivery=WorkflowDeliverySpec(required=True),
                )
            ],
        )
    )

    assert report.executable is False
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        "required_delivery_has_no_artifacts"
    ]


def test_enabled_rollback_must_target_previous_existing_stage():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="rollback",
            name="Rollback",
            stages=[
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="Analysis",
                    agent_type="analyst",
                ),
                WorkflowStageSpec(
                    stage_id="testing",
                    display_name="Testing",
                    agent_type="tester",
                    rollback=WorkflowRollbackSpec(
                        enabled=True,
                        max_attempts=3,
                        target_stage_name="release",
                    ),
                ),
                WorkflowStageSpec(
                    stage_id="release",
                    display_name="Release",
                    agent_type="release",
                ),
            ],
        )
    )

    assert report.executable is False
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        "rollback_target_not_predecessor"
    ]


def test_condition_gate_and_skill_overlap_are_warnings():
    report = validate_workflow_spec_for_execution(
        WorkflowSpec(
            workflow_id="warnings",
            name="Warnings",
            stages=[
                WorkflowStageSpec(
                    stage_id="analysis",
                    display_name="",
                    agent_type="analyst",
                    gate="condition",
                    skills=WorkflowSkillSpec(
                        active=["requirements"],
                        hint_only=["requirements"],
                    ),
                )
            ],
        )
    )

    assert report.executable is True
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        "stage_display_name_missing",
        "condition_gate_has_no_expression",
        "skill_declared_active_and_hint_only",
    ]
    assert all(diagnostic.severity == "warning" for diagnostic in report.diagnostics)
