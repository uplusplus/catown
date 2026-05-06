from pydantic import ValidationError

from services.artifact_contracts import dump_artifact_contract, parse_artifact_contract


def test_parse_workspace_file_artifact_contract():
    contract = parse_artifact_contract(
        {
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-prd-file-1",
            "artifact_type": "document.prd_file",
            "title": "PRD file",
            "producer": {
                "agent_name": "Analyst",
                "agent_type": "analyst",
                "stage_name": "analysis",
            },
            "mode": "workspace_file",
            "file_path": "PRD.md",
            "media_type": "text/markdown",
        }
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "workspace_file"
    assert dumped["file_path"] == "PRD.md"


def test_parse_document_artifact_contract():
    contract = parse_artifact_contract(
        {
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "artifact-prd-doc-1",
            "artifact_type": "document.prd",
            "title": "PRD draft",
            "summary": "Structured product requirements.",
            "producer": {
                "agent_name": "Analyst",
                "agent_type": "analyst",
                "stage_name": "analysis",
                "task_run_id": 12,
            },
            "mode": "document",
            "format": "mixed",
            "file_path": "PRD.md",
            "content_markdown": "# PRD\n\n## Stories",
            "content_json": {"stories": 5, "acceptance_criteria": 12},
        }
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "document"
    assert dumped["content_json"]["stories"] == 5


def test_parse_structured_asset_artifact_contract():
    contract = parse_artifact_contract(
        {
            "kind": "artifact_contract",
            "version": 1,
            "artifact_id": "asset-design-token-1",
            "artifact_type": "design.tokens",
            "title": "Design tokens",
            "producer": {
                "agent_name": "Designer",
                "stage_name": "design",
                "stage_run_id": 9,
            },
            "mode": "structured_asset",
            "schema_name": "design.tokens.v1",
            "storage_path": "assets/design-tokens.json",
            "content_json": {"color.primary": "#223344"},
        }
    )

    dumped = dump_artifact_contract(contract)
    assert dumped["mode"] == "structured_asset"
    assert dumped["schema_name"] == "design.tokens.v1"


def test_document_artifact_requires_some_content():
    try:
        parse_artifact_contract(
            {
                "kind": "artifact_contract",
                "version": 1,
                "artifact_id": "artifact-empty-doc-1",
                "artifact_type": "document.empty",
                "title": "Empty document",
                "mode": "document",
                "format": "markdown",
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError for empty document artifact contract.")
