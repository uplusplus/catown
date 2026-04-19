import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from http_client import SyncASGITestClient


def _make_app(tmp_path):
    os.environ["LLM_API_KEY"] = "test-key"
    os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")

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
        "routes.websocket",
        "services.project_service",
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    import llm.client as llm_mod

    mock_llm = MagicMock()
    mock_llm.base_url = "http://localhost:9999/v1"
    mock_llm.model = "test-model"
    mock_llm.chat = AsyncMock(return_value="Mocked response.")
    mock_llm.chat_with_tools = AsyncMock(return_value={"content": "Mocked agent response.", "tool_calls": None})

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
    app = _make_app(tmp_path)
    with SyncASGITestClient(app, base_url="http://testserver") as client:
        yield client


def test_create_project_v2_bootstraps_brief_and_decision(client):
    r = client.post(
        "/api/v2/projects",
        json={
            "name": "FitPet",
            "one_line_vision": "Help pet owners manage feeding and exercise",
            "target_platforms": ["ios", "android"],
            "target_users": ["pet owners"],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["project"]["status"] == "draft"
    assert data["project"]["current_stage"] == "briefing"

    project_id = data["project"]["id"]
    assets = client.get(f"/api/v2/projects/{project_id}/assets")
    assert assets.status_code == 200
    assert len(assets.json()) == 1
    assert assets.json()[0]["asset_type"] == "project_brief"

    decisions = client.get(f"/api/v2/projects/{project_id}/decisions")
    assert decisions.status_code == 200
    assert len(decisions.json()) == 1
    assert decisions.json()[0]["decision_type"] == "scope_confirmation"


def test_resolve_scope_confirmation_and_continue_project(client):
    project = client.post(
        "/api/v2/projects",
        json={"name": "CatPlan", "one_line_vision": "Ship an MVP cat social planner"},
    ).json()["project"]
    project_id = project["id"]
    decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]

    resolved = client.post(
        f"/api/v2/decisions/{decision['id']}/resolve",
        json={"resolution": "approved", "selected_option": "approve_brief_v1"},
    )
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["project"]["status"] == "brief_confirmed"

    continued = client.post(f"/api/v2/projects/{project_id}/continue")
    assert continued.status_code == 200
    continue_payload = continued.json()
    assert continue_payload["stage_run"]["stage_type"] == "product_definition"
    assert continue_payload["stage_run"]["status"] == "completed"

    overview = client.get(f"/api/v2/projects/{project_id}/overview")
    assert overview.status_code == 200
    overview_data = overview.json()
    assert "prd" in overview_data["assets_by_type"]
    assert "ux_blueprint" in overview_data["assets_by_type"]
    assert "tech_spec" in overview_data["assets_by_type"]


def test_dashboard_v2_returns_project_first_shape(client):
    client.post(
        "/api/v2/projects",
        json={"name": "Dash", "one_line_vision": "Validate dashboard aggregation"},
    )
    r = client.get("/api/v2/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert "projects" in data
    assert "project_cards" in data
    assert "summary" in data
    assert "pending_decisions" in data


def test_stage_run_detail_and_instruction_event(client):
    project = client.post(
        "/api/v2/projects",
        json={"name": "StageEvents", "one_line_vision": "Track stage instructions and execution events"},
    ).json()["project"]
    project_id = project["id"]
    decision = client.get(f"/api/v2/projects/{project_id}/decisions").json()[0]
    client.post(f"/api/v2/decisions/{decision['id']}/resolve", json={"resolution": "approved"})
    client.post(f"/api/v2/projects/{project_id}/continue")

    stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs").json()
    product_definition = next(item for item in stage_runs if item["stage_type"] == "product_definition")

    instruction = client.post(
        f"/api/v2/stage-runs/{product_definition['id']}/instructions",
        json={"content": "Please tighten MVP scope.", "author": "boss"},
    )
    assert instruction.status_code == 200
    event_payload = instruction.json()["event"]
    assert event_payload["stage_run_id"] == product_definition["id"]
    assert event_payload["event_type"] == "stage_instruction"

    detail = client.get(f"/api/v2/stage-runs/{product_definition['id']}")
    assert detail.status_code == 200
    assert detail.json()["summary"]["event_count"] >= 2
