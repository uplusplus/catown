"""
Mission Board frontend contract tests.

These tests validate the new project-first frontend entry and the v2 API
surface it depends on. They intentionally avoid legacy pipeline/chatroom shell
assumptions.

Run:
    cd catown
    python3 -m pytest tests/test_frontend.py -v
"""

import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    backend_dir = os.path.join(os.path.dirname(__file__), "..", "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.chdir(backend_dir)

    tmp = tempfile.mkdtemp(prefix="catown-ft-v2-")
    os.environ["DATABASE_URL"] = os.path.join(tmp, "test.db")
    os.environ["LOG_LEVEL"] = "WARNING"

    from main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def mission_project(client):
    created = client.post(
        "/api/v2/projects",
        json={
            "name": f"mission-{int(time.time() * 1000)}",
            "one_line_vision": "Drive the Mission Board from v2 contracts",
        },
    )
    assert created.status_code == 200, created.text
    yield created.json()["project"]


def advance_past_scope_confirmation(client, project_id: int):
    decisions = client.get(f"/api/v2/projects/{project_id}/decisions")
    assert decisions.status_code == 200, decisions.text
    pending = decisions.json()
    assert pending, "expected bootstrap decision"

    resolved = client.post(
        f"/api/v2/decisions/{pending[0]['id']}/resolve",
        json={"resolution": "approved"},
    )
    assert resolved.status_code == 200, resolved.text

    continued = client.post(f"/api/v2/projects/{project_id}/continue")
    assert continued.status_code == 200, continued.text


class TestFrontendEntry:
    def test_root_serves_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_root_prefers_built_frontend_artifact(self, client):
        html = client.get("/").text
        assert '<script type="module" crossorigin' in html

    def test_root_serves_mission_board_shell(self, client):
        html = client.get("/").text
        assert '<div id="root"></div>' in html
        assert "Project-first Mission Board" in html or "Catown" in html
        assert "pipeline dashboard" not in html.lower()


class TestMissionBoardBootstrap:
    def test_dashboard_contract_is_available(self, client, mission_project):
        response = client.get("/api/v2/dashboard")
        assert response.status_code == 200
        data = response.json()
        assert set(
            [
                "projects",
                "project_cards",
                "pending_decisions",
                "recent_assets",
                "active_stage_runs",
                "summary",
                "alerts",
            ]
        ).issubset(data.keys())
        assert any(item["id"] == mission_project["id"] for item in data["projects"])

    def test_projects_list_returns_project_first_rows(self, client, mission_project):
        response = client.get("/api/v2/projects")
        assert response.status_code == 200
        row = next(item for item in response.json() if item["id"] == mission_project["id"])
        assert set(
            [
                "id",
                "name",
                "status",
                "current_stage",
                "execution_mode",
                "health_status",
                "current_focus",
                "latest_summary",
            ]
        ).issubset(row.keys())

    def test_project_overview_matches_mission_board_sections(self, client, mission_project):
        response = client.get(f"/api/v2/projects/{mission_project['id']}/overview")
        assert response.status_code == 200
        data = response.json()
        assert set(
            [
                "project",
                "current_stage_run",
                "key_assets",
                "pending_decisions",
                "stage_summary",
                "recent_activity",
                "release_readiness",
                "recommended_next_action",
            ]
        ).issubset(data.keys())
        assert data["project"]["id"] == mission_project["id"]
        assert data["recommended_next_action"] == "resolve_scope_confirmation"
        assert data["release_readiness"]["status"] == "not_ready"

    def test_initial_decision_list_drives_decision_panel(self, client, mission_project):
        response = client.get(f"/api/v2/projects/{mission_project['id']}/decisions")
        assert response.status_code == 200
        decisions = response.json()
        assert len(decisions) >= 1
        first = decisions[0]
        assert set(["id", "decision_type", "title", "status", "created_at"]).issubset(first.keys())
        assert first["status"] == "pending"


class TestMissionBoardStageWorkbench:
    def test_continue_flow_produces_stage_run_assets_and_detail(self, client, mission_project):
        project_id = mission_project["id"]
        advance_past_scope_confirmation(client, project_id)

        stage_runs_response = client.get(f"/api/v2/projects/{project_id}/stage-runs")
        assert stage_runs_response.status_code == 200
        stage_runs = stage_runs_response.json()
        assert len(stage_runs) >= 1

        current_stage = next(item for item in stage_runs if item["stage_type"] == "product_definition")
        assert set(["id", "stage_type", "status", "lifecycle", "run_index"]).issubset(current_stage.keys())
        assert set(["phase", "is_active", "is_terminal", "requires_attention"]).issubset(
            current_stage["lifecycle"].keys()
        )

        detail_response = client.get(f"/api/v2/stage-runs/{current_stage['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert set(["stage_run", "project", "input_assets", "output_assets", "decisions", "events", "summary"]).issubset(
            detail.keys()
        )
        assert set(["input_count", "output_count", "decision_count", "event_count"]).issubset(
            detail["summary"].keys()
        )
        assert detail["project"]["id"] == project_id

    def test_stage_events_endpoint_feeds_activity_panel(self, client, mission_project):
        project_id = mission_project["id"]
        advance_past_scope_confirmation(client, project_id)

        stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs").json()
        current_stage = next(item for item in stage_runs if item["stage_type"] == "product_definition")

        response = client.get(f"/api/v2/stage-runs/{current_stage['id']}/events")
        assert response.status_code == 200
        events = response.json()
        assert isinstance(events, list)
        if events:
            assert set(["id", "event_type", "payload", "created_at"]).issubset(events[0].keys())

    def test_assets_endpoint_supports_asset_panel_and_detail_rail(self, client, mission_project):
        project_id = mission_project["id"]
        response = client.get(f"/api/v2/projects/{project_id}/assets")
        assert response.status_code == 200
        assets = response.json()
        assert len(assets) >= 1

        asset = assets[0]
        assert set(["id", "asset_type", "status", "version", "is_current"]).issubset(asset.keys())

        detail = client.get(f"/api/v2/assets/{asset['id']}")
        assert detail.status_code == 200
        detail_data = detail.json()
        assert detail_data["id"] == asset["id"]
        assert set(["relationships", "stage_links", "decision_links"]).issubset(detail_data.keys())


class TestMissionBoardDecisionWorkbench:
    def test_decision_detail_and_resolution_refresh_flow(self, client):
        created = client.post(
            "/api/v2/projects",
            json={
                "name": f"decision-{int(time.time() * 1000)}",
                "one_line_vision": "Exercise decision workbench",
            },
        )
        assert created.status_code == 200
        project_id = created.json()["project"]["id"]

        decisions = client.get(f"/api/v2/projects/{project_id}/decisions").json()
        decision = decisions[0]

        detail = client.get(f"/api/v2/decisions/{decision['id']}")
        assert detail.status_code == 200
        detail_data = detail.json()
        assert set(
            [
                "id",
                "decision_type",
                "title",
                "context_summary",
                "recommended_option",
                "alternative_options",
                "status",
            ]
        ).issubset(detail_data.keys())

        resolved = client.post(
            f"/api/v2/decisions/{decision['id']}/resolve",
            json={"resolution": "approved", "selected_option": "approve"},
        )
        assert resolved.status_code == 200

        refreshed = client.get(f"/api/v2/decisions/{decision['id']}")
        assert refreshed.status_code == 200
        assert refreshed.json()["status"] in ("approved", "resolved", "completed")


class TestMissionBoardInstructions:
    def test_stage_instruction_endpoint_exists_for_future_workbench_controls(self, client):
        created = client.post(
            "/api/v2/projects",
            json={
                "name": f"instruction-{int(time.time() * 1000)}",
                "one_line_vision": "Exercise stage instruction endpoint",
            },
        )
        project_id = created.json()["project"]["id"]
        advance_past_scope_confirmation(client, project_id)

        stage_runs = client.get(f"/api/v2/projects/{project_id}/stage-runs").json()
        current_stage = next(item for item in stage_runs if item["stage_type"] == "product_definition")

        response = client.post(
            f"/api/v2/stage-runs/{current_stage['id']}/instructions",
            json={"content": "Tighten the frontend information architecture.", "author": "boss"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "event" in data
        assert data["event"]["stage_run_id"] == current_stage["id"]
        assert data["event"]["event_type"] == "stage_instruction"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
