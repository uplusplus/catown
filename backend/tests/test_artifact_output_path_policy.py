from services.artifact_output_path_policy import validate_artifact_output_path


def test_semantic_prd_output_must_live_under_prd_directory():
    decision = validate_artifact_output_path("PRD.md")

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == [
        "artifact_path_noncanonical_directory"
    ]


def test_semantic_prd_output_accepts_canonical_directory():
    decision = validate_artifact_output_path("docs/prd/project-browser-artifact-lifecycle.md")

    assert decision.accepted is True


def test_test_report_output_requires_timestamped_filename():
    decision = validate_artifact_output_path("reports/tests/backend-pytest.md")

    assert decision.accepted is False
    assert [violation.code for violation in decision.violations] == [
        "artifact_path_timestamp_required"
    ]


def test_release_output_accepts_release_directory():
    decision = validate_artifact_output_path("reports/releases/changelog.md")

    assert decision.accepted is True
