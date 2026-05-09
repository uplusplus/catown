from datetime import datetime
from types import SimpleNamespace

import pytest

from services.artifact_contracts import dump_artifact_contract
from services.artifact_normalization import (
    compile_asset_to_contract,
    compile_stage_artifact_to_contract,
)


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


def test_compile_document_asset_to_document_contract():
    stage_run = SimpleNamespace(id=3, stage_type="analysis")
    asset = SimpleNamespace(
        id=44,
        project_id=7,
        asset_type="document.prd",
        title="PRD",
        summary="Product requirements.",
        content_json='{"stories": 5}',
        content_markdown="# PRD",
        version=2,
        status="approved",
        is_current=True,
        owner_agent="Analyst",
        produced_by_stage_run_id=3,
        produced_by_stage_run=stage_run,
        supersedes_asset_id=12,
        approval_decision_id=31,
        source_input_refs_json='["requirement:1"]',
        storage_path="docs/PRD.md",
        created_at=datetime(2026, 5, 9, 11, 0, 0),
    )

    contract = compile_asset_to_contract(asset)

    dumped = dump_artifact_contract(contract)
    assert dumped["artifact_id"] == "asset-44"
    assert dumped["artifact_type"] == "document.prd"
    assert dumped["mode"] == "document"
    assert dumped["format"] == "mixed"
    assert dumped["file_path"] == "docs/PRD.md"
    assert dumped["content_json"]["stories"] == 5
    assert dumped["producer"]["agent_name"] == "Analyst"
    assert dumped["producer"]["stage_name"] == "analysis"
    assert dumped["producer"]["stage_run_id"] == 3
    assert dumped["source_input_refs"] == ["requirement:1"]
    assert dumped["metadata"]["source_model"] == "Asset"
    assert dumped["metadata"]["source_asset_id"] == 44
    assert dumped["metadata"]["status"] == "approved"


def test_compile_structured_asset_to_structured_asset_contract():
    contract = compile_asset_to_contract(
        {
            "project_id": 7,
            "asset_type": "design.tokens",
            "title": "Design tokens",
            "content_json": {"color.primary": "#223344"},
            "storage_path": "assets/tokens.json",
            "source_input_refs_json": [],
        }
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["artifact_id"] == "asset-7-Design-tokens"
    assert dumped["mode"] == "structured_asset"
    assert dumped["schema_name"] == "design.tokens"
    assert dumped["storage_path"] == "assets/tokens.json"
    assert dumped["content_json"]["color.primary"] == "#223344"


def test_compile_workspace_asset_to_workspace_file_contract():
    contract = compile_asset_to_contract(
        {
            "id": 55,
            "asset_type": "workspace.file",
            "title": "Run log",
            "storage_path": "logs/run.txt",
            "content_json": "{}",
            "source_input_refs_json": "[]",
        }
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "workspace_file"
    assert dumped["file_path"] == "logs/run.txt"
    assert dumped["media_type"] == "text/plain"


def test_compile_asset_rejects_invalid_content_json():
    with pytest.raises(ValueError):
        compile_asset_to_contract(
            {
                "id": 1,
                "asset_type": "design.tokens",
                "title": "Invalid tokens",
                "content_json": "not-json",
            }
        )
