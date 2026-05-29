import os

from fastapi import HTTPException


def test_read_project_workspace_file_returns_text(tmp_path):
    from routes.api import _read_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = _read_project_workspace_file(str(workspace), "src/app.py")

    assert result.path == "src/app.py"
    assert result.name == "app.py"
    assert result.content == "print('ok')\n".replace("\n", os.linesep)
    assert result.binary is False
    assert result.truncated is False


def test_read_project_workspace_file_rejects_escape(tmp_path):
    from routes.api import _read_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    try:
        _read_project_workspace_file(str(workspace), "../outside.txt")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "escapes workspace" in exc.detail
    else:
        raise AssertionError("Expected workspace escape to be rejected")


def test_read_project_workspace_file_rejects_directory(tmp_path):
    from routes.api import _read_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()

    try:
        _read_project_workspace_file(str(workspace), "src")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "not a file" in exc.detail
    else:
        raise AssertionError("Expected directory read to be rejected")


def test_write_project_workspace_file_saves_text(tmp_path):
    from routes.api import ProjectFileWriteRequest, _read_project_workspace_file, _write_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("old\n", encoding="utf-8")
    opened = _read_project_workspace_file(str(workspace), "notes.md")

    result = _write_project_workspace_file(
        str(workspace),
        ProjectFileWriteRequest(path="notes.md", content="new\n", expected_mtime=opened.mtime),
    )

    assert result.content == "new\n".replace("\n", os.linesep)
    assert target.read_text(encoding="utf-8") == "new\n"


def test_write_project_workspace_file_rejects_overwrite_without_explicit_allow(tmp_path):
    from routes.api import ProjectFileWriteRequest, _write_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("old\n", encoding="utf-8")

    try:
        _write_project_workspace_file(
            str(workspace),
            ProjectFileWriteRequest(path="notes.md", content="new\n"),
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "overwrite is disabled by default" in exc.detail.lower()
    else:
        raise AssertionError("Expected overwrite without allow signal to be rejected")

    assert target.read_text(encoding="utf-8") == "old\n"


def test_write_project_workspace_file_archives_overwritten_artifact(tmp_path):
    from routes.api import ProjectFileWriteRequest, _read_project_workspace_file, _write_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "PRD.md"
    target.write_text("old\n", encoding="utf-8")
    opened = _read_project_workspace_file(str(workspace), "PRD.md")

    _write_project_workspace_file(
        str(workspace),
        ProjectFileWriteRequest(path="PRD.md", content="new\n", expected_mtime=opened.mtime),
    )

    archived = list((workspace / ".catown" / "artifact-history").glob("*--PRD.md"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "old\n"
    assert target.read_text(encoding="utf-8") == "new\n"


def test_write_project_workspace_file_rejects_stale_mtime(tmp_path):
    from routes.api import ProjectFileWriteRequest, _read_project_workspace_file, _write_project_workspace_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("old\n", encoding="utf-8")
    opened = _read_project_workspace_file(str(workspace), "notes.md")
    target.write_text("external\n", encoding="utf-8")
    os.utime(target, (opened.mtime + 2, opened.mtime + 2))

    try:
        _write_project_workspace_file(
            str(workspace),
            ProjectFileWriteRequest(path="notes.md", content="new\n", expected_mtime=opened.mtime),
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "changed" in exc.detail
    else:
        raise AssertionError("Expected stale mtime save to be rejected")
