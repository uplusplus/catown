import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_app(tmp_path):
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")
    os.environ["PIPELINE_CONFIG_FILE"] = str(tmp_path / "pipelines.json")

    with open(tmp_path / "pipelines.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "default": {
                    "name": "Standard software delivery workflow",
                    "description": "analysis -> architecture",
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
                    ],
                }
            },
            f,
            ensure_ascii=False,
        )

    modules_to_clear = [
        "main",
        "config",
        "models.database",
        "models.audit",
        "agents.registry",
        "agents.collaboration",
        "tools",
        "llm.client",
        "chatrooms.manager",
        "routes.api",
        "routes.audit",
        "routes.monitor",
        "routes.websocket",
        "pipeline.engine",
        "pipeline.config",
        "routes.pipeline",
    ]
    from tests.conftest import reset_app_modules

    reset_app_modules(modules_to_clear)

    import llm.client as llm_mod
    from unittest.mock import AsyncMock, MagicMock

    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(
        return_value={"content": "Mocked agent response.", "tool_calls": None}
    )

    async def mock_stream(messages, tools=None):
        yield {"type": "content", "delta": "Hello!"}
        yield {"type": "done", "full_content": "Hello!", "tool_calls": None}

    mock_llm.chat_stream = mock_stream
    llm_mod._llm_client = mock_llm

    import main as main_mod

    async def passthrough(self, request, call_next):
        return await call_next(request)

    main_mod.RateLimitMiddleware.dispatch = passthrough
    main_mod.RequestLoggingMiddleware.dispatch = passthrough
    return main_mod.app


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    return TestClient(
        _make_app(tmp_path),
        base_url="http://testserver",
        headers={"X-Catown-Client": "test"},
    )


def test_pipeline_template_workflow_spec_endpoint_exposes_canonical_schema(client):
    response = client.get("/api/pipelines/templates/default/workflow-spec")
    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "default"
    assert payload["name"] == "Standard software delivery workflow"
    assert payload["domain"] == "software_delivery"
    assert payload["stage_count"] == 2
    assert payload["payload"]["kind"] == "workflow_spec"
    assert payload["payload"]["version"] == 1
    assert payload["payload"]["stages"][0]["stage_id"] == "analysis"
    assert payload["payload"]["stages"][0]["delivery"]["expected_artifacts"] == ["docs/prd/"]
    assert payload["payload"]["stages"][1]["stage_id"] == "architecture"
    assert payload["payload"]["stages"][1]["delivery"]["expected_artifacts"] == ["docs/specs/"]


def test_pipeline_template_workflow_spec_report_endpoint_exposes_diagnostics(client):
    response = client.get("/api/pipelines/templates/default/workflow-spec/report")
    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "default"
    assert payload["executable"] is True
    assert payload["diagnostic_count"] == 0
    assert payload["payload"]["metadata"]["stage_count"] == 2


def test_workflow_spec_validate_endpoint_accepts_executable_spec(client):
    response = client.post(
        "/api/pipelines/workflow-spec/validate",
        json={
            "kind": "workflow_spec",
            "version": 1,
            "workflow_id": "generated",
            "name": "Generated workflow",
            "stages": [
                {
                    "stage_id": "analysis",
                    "display_name": "Analysis",
                    "agent_type": "analyst",
                    "gate": "manual",
                    "timeout_minutes": 30,
                    "delivery": {
                        "expected_artifacts": ["docs/prd/"],
                        "required": True,
                    },
                },
                {
                    "stage_id": "architecture",
                    "display_name": "Architecture",
                    "agent_type": "architect",
                    "gate": "auto",
                    "timeout_minutes": 45,
                    "delivery": {
                        "expected_artifacts": ["docs/specs/"],
                        "required": True,
                    },
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "generated"
    assert payload["executable"] is True
    assert payload["diagnostic_count"] == 0


def test_workflow_spec_validate_endpoint_reports_policy_errors(client):
    response = client.post(
        "/api/pipelines/workflow-spec/validate",
        json={
            "kind": "workflow_spec",
            "version": 1,
            "workflow_id": "broken",
            "name": "Broken workflow",
            "stages": [
                {
                    "stage_id": "testing",
                    "display_name": "Testing",
                    "agent_type": "tester",
                    "gate": "auto",
                    "timeout_minutes": 30,
                    "rollback": {
                        "enabled": True,
                        "max_attempts": 2,
                        "target_stage_name": "missing",
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "broken"
    assert payload["executable"] is False
    assert payload["diagnostic_count"] == 1
    assert payload["payload"]["diagnostics"][0]["code"] == "rollback_target_unknown"
