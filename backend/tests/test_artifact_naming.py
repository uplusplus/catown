from datetime import datetime, timezone

from services.artifact_naming import build_timestamped_artifact_path, slug_artifact_subject


def test_build_timestamped_artifact_path_uses_directory_timestamp_refs_and_slug():
    path = build_timestamped_artifact_path(
        directory="reports/tests",
        subject="Backend Pytest",
        run_ref="run-17",
        task_ref="task-45",
        extension=".md",
        timestamp=datetime(2026, 5, 22, 14, 31, 22, 4981, tzinfo=timezone.utc),
    )

    assert path == "reports/tests/20260522T143122004981Z--run-17--task-45--backend-pytest.md"


def test_slug_artifact_subject_normalizes_free_form_text():
    assert slug_artifact_subject(" Project Browser / Artifact Handling ") == "project-browser-artifact-handling"
