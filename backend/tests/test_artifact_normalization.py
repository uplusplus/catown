from datetime import datetime
from types import SimpleNamespace

import pytest

from services.artifact_contracts import dump_artifact_contract
from services.artifact_normalization import compile_stage_artifact_to_contract


def test_compile_stage_file_artifact_to_workspace_file_contract():
    run = SimpleNamespace(id=4, task_run_id=12)
    stage = SimpleNamespace(id=8, run_id=4, run=run, stage_name="testing", agent_name="Tester")
    artifact = SimpleNamespace(
        id=17,
        stage_id=8,
        stage=stage,
        artifact_type="file",
        file_path="reports/summary.md",
        summary="Regression summary.",
        created_at=datetime(2026, 5, 9, 10, 30, 0),
    )

    contract = compile_stage_artifact_to_contract(artifact)

    dumped = dump_artifact_contract(contract)
    assert dumped["artifact_id"] == "stage-artifact-17"
    assert dumped["artifact_type"] == "workspace.file"
    assert dumped["mode"] == "workspace_file"
    assert dumped["title"] == "summary.md"
    assert dumped["file_path"] == "reports/summary.md"
    assert dumped["media_type"] == "text/markdown"
    assert dumped["producer"]["agent_name"] == "Tester"
    assert dumped["producer"]["stage_name"] == "testing"
    assert dumped["producer"]["task_run_id"] == 12
    assert dumped["producer"]["pipeline_run_id"] == 4
    assert dumped["producer"]["pipeline_stage_id"] == 8
    assert dumped["source_input_refs"] == ["stage_artifact:17"]
    assert dumped["metadata"]["source_model"] == "StageArtifact"
    assert dumped["metadata"]["source_created_at"] == "2026-05-09T10:30:00"


def test_compile_stage_directory_artifact_to_workspace_directory_contract():
    artifact = {
        "stage_id": 5,
        "artifact_type": "directory",
        "file_path": "src",
        "summary": "12 files",
    }
    stage = {"id": 5, "run_id": 2, "stage_name": "development", "agent_name": "Developer"}

    contract = compile_stage_artifact_to_contract(artifact, stage=stage)

    dumped = dump_artifact_contract(contract)
    assert dumped["artifact_id"] == "stage-artifact-5-src"
    assert dumped["artifact_type"] == "workspace.directory"
    assert dumped["mode"] == "workspace_directory"
    assert dumped["directory_path"] == "src/"
    assert dumped["summary"] == "12 files"
    assert dumped["producer"]["pipeline_run_id"] == 2


def test_compile_stage_artifact_preserves_specific_artifact_type():
    contract = compile_stage_artifact_to_contract(
        {
            "id": 22,
            "stage_id": 9,
            "artifact_type": "document.test_report",
            "file_path": "test_report.md",
        },
        stage={"stage_name": "testing"},
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["artifact_type"] == "document.test_report"
    assert dumped["mode"] == "workspace_file"


def test_compile_stage_artifact_requires_file_path():
    with pytest.raises(ValueError):
        compile_stage_artifact_to_contract({"id": 1, "artifact_type": "file"})
