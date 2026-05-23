from services.artifact_history import (
    archive_workspace_artifact_snapshot,
    classify_workspace_artifact_path,
)


def test_classify_workspace_artifact_path_recognizes_named_deliverables():
    assert classify_workspace_artifact_path("PRD.md") == "PRD"
    assert classify_workspace_artifact_path("reports/tests/20260523T101010000000Z--task-45--backend-pytest.md") == "Test"
    assert classify_workspace_artifact_path("notes/todo.txt") is None


def test_archive_workspace_artifact_snapshot_copies_previous_version(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "PRD.md"
    target.write_text("old draft\n", encoding="utf-8")

    archive_path = archive_workspace_artifact_snapshot(workspace, "PRD.md", next_content="new draft\n")

    assert archive_path is not None
    archived_file = workspace / archive_path
    assert archived_file.exists()
    assert archived_file.read_text(encoding="utf-8") == "old draft\n"
    assert archived_file.parent == workspace / ".catown" / "artifact-history"


def test_archive_workspace_artifact_snapshot_skips_non_artifact_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "scratch.txt"
    target.write_text("old scratch\n", encoding="utf-8")

    archive_path = archive_workspace_artifact_snapshot(workspace, "scratch.txt", next_content="new scratch\n")

    assert archive_path is None
    assert not (workspace / ".catown").exists()


def test_archive_workspace_artifact_snapshot_skips_unchanged_content(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "CHANGELOG.md"
    target.write_text("same content\n", encoding="utf-8")

    archive_path = archive_workspace_artifact_snapshot(workspace, "CHANGELOG.md", next_content="same content\n")

    assert archive_path is None
    assert not (workspace / ".catown").exists()
