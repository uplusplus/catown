from services.artifact_contract_policy import validate_artifact_contract_for_workflow
from services.workflow_spec_contracts import compile_pipeline_template_to_workflow_spec


def _workflow_spec():
    return compile_pipeline_template_to_workflow_spec(
        "default",
        {
            "name": "Default delivery workflow",
            "description": "analysis -> development -> testing -> release",
            "stages": [
                {
                    "name": "analysis",
                    "display_name": "Analysis",
                    "agent": "analyst",
                    "gate": "manual",
                    "expected_artifacts": ["docs/prd/"],
                },
                {
                    "name": "architecture",
                    "display_name": "Architecture",
                    "agent": "architect",
                    "gate": "auto",
                    "expected_artifacts": ["docs/specs/"],
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
                },
            ],
        },
    )


def test_workspace_file_artifact_matches_stage_delivery_contract():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-test-report-1",
            "artifact_type": "workspace.file",
            "title": "Test report",
            "producer": {
                "stage_name": "testing",
                "agent_name": "Tester",
            },
            "mode": "workspace_file",
            "file_path": "reports/tests/20260522T143122004981Z--task-45--backend-pytest.md",
        },
    )

    assert decision.accepted is True
    assert decision.violations == []
    assert decision.to_payload()["metadata"]["artifact_mode"] == "workspace_file"


def test_unexpected_artifact_path_is_rejected():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-release-note-1",
            "artifact_type": "workspace.file",
            "title": "Release note",
            "producer": {
                "stage_name": "testing",
                "agent_name": "Tester",
            },
            "mode": "workspace_file",
            "file_path": "CHANGELOG.md",
        },
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == ["artifact_not_expected"]


def test_directory_delivery_accepts_nested_workspace_file():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-source-file-1",
            "artifact_type": "workspace.file",
            "title": "Runtime policy",
            "producer": {
                "stage_name": "development",
                "agent_name": "Developer",
            },
            "mode": "workspace_file",
            "file_path": "src/runtime/policy.py",
        },
    )

    assert decision.accepted is True


def test_spec_delivery_accepts_nested_workspace_file():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-spec-file-1",
            "artifact_type": "workspace.file",
            "title": "Architecture spec",
            "producer": {
                "stage_name": "architecture",
                "agent_name": "Architect",
            },
            "mode": "workspace_file",
            "file_path": "docs/specs/project-browser-artifact-storage.md",
        },
    )

    assert decision.accepted is True


def test_missing_artifact_stage_is_rejected():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-prd-1",
            "artifact_type": "document.prd",
            "title": "PRD",
            "mode": "document",
            "format": "markdown",
            "file_path": "PRD.md",
        },
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == ["missing_artifact_stage"]


def test_document_artifact_without_path_cannot_satisfy_delivery_contract():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-prd-2",
            "artifact_type": "document.prd",
            "title": "PRD",
            "producer": {
                "stage_name": "analysis",
                "agent_name": "Analyst",
            },
            "mode": "document",
            "format": "markdown",
            "content_markdown": "# PRD",
        },
    )

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == ["artifact_path_missing"]


def test_document_artifact_requires_required_output_header_fields():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-test-report-header-1",
            "artifact_type": "document.test_report",
            "title": "Test report",
            "producer": {
                "stage_name": "testing",
                "agent_name": "Tester",
            },
            "mode": "document",
            "format": "markdown",
            "file_path": "reports/tests/20260528T110000000000Z--header-check.md",
            "content_markdown": "# Test Report\n\nMissing header",
        },
    )

    assert decision.accepted is False
    codes = {violation.code for violation in decision.violations}
    assert "output_header_purpose_missing" in codes
    assert "output_header_author_missing" in codes
    assert "output_header_created_at_missing" in codes
    assert "output_header_modification_log_missing" in codes


def test_document_artifact_accepts_required_output_header_fields():
    decision = validate_artifact_contract_for_workflow(
        workflow_spec=_workflow_spec(),
        contract={
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-test-report-header-2",
            "artifact_type": "document.test_report",
            "title": "Test report",
            "producer": {
                "stage_name": "testing",
                "agent_name": "Tester",
            },
            "mode": "document",
            "format": "markdown",
            "file_path": "reports/tests/20260528T110000000000Z--header-check.md",
            "content_markdown": (
                "Purpose: Regression verification\n"
                "Overview: Summarizes the latest backend test run\n"
                "Author: Tester\n"
                "Created At: 2026-05-28 11:00\n"
                "Modification Log:\n"
                "- 2026-05-28 11:00 Created the report\n\n"
                "# Test Report\n"
            ),
        },
    )

    assert decision.accepted is True
