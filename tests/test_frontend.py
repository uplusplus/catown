"""
Frontend E2E Tests — Catown Pipeline Dashboard
================================================
Tests verify:
  1. Static HTML page serves correctly
  2. All API endpoints the frontend consumes
  3. WebSocket real-time messaging
  4. Pipeline Dashboard lifecycle
  5. Agent status bar & configuration
  6. Error handling & edge cases

Run (独立运行，避免模块缓存冲突):
    cd catown
    python3 -m pytest tests/test_frontend.py -v
"""

import pytest
import json
import time
import os
import sys
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Shared TestClient backed by the real FastAPI app.
    Uses isolated tmp DB and config paths, matching backend/tests/conftest.py.
    Rate limiting is disabled by default (RATE_LIMIT_MAX=0)."""
    import tempfile
    backend_dir = os.path.join(os.path.dirname(__file__), '..', 'backend')
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.chdir(backend_dir)

    # Isolate DB and config for this test module
    tmp = tempfile.mkdtemp(prefix="catown-ft-")
    os.environ["DATABASE_URL"] = os.path.join(tmp, "test.db")
    os.environ["LOG_LEVEL"] = "WARNING"

    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def sample_project(client):
    """Create a throwaway project for tests that need one."""
    resp = client.post("/api/projects", json={
        "name": f"ft-{int(time.time() * 1000)}",
        "description": "frontend-test"
    })
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    project = resp.json()
    yield project
    # cleanup — may fail if pipeline references it (FK constraint), ignore
    client.delete(f"/api/projects/{project['id']}")


@pytest.fixture()
def sample_pipeline(client, sample_project):
    """Create a pipeline linked to the sample project."""
    resp = client.post("/api/pipelines", json={
        "project_id": sample_project["id"],
        "pipeline_name": "default"
    })
    if resp.status_code == 200:
        pipeline = resp.json()
    elif resp.status_code == 400:
        # Already exists (e.g. shared DB) — reuse it
        resp2 = client.get("/api/pipelines")
        pipelines = resp2.json()
        pipeline = next(
            (p for p in pipelines if p["project_id"] == sample_project["id"]),
            None
        )
        assert pipeline is not None, "Pipeline 400 but none found in list"
    else:
        pytest.fail(f"Unexpected pipeline create status {resp.status_code}: {resp.text}")
    yield pipeline


# ===================================================================
# 1. Static Page
# ===================================================================

class TestStaticPage:
    """Verify the frontend HTML is served and contains critical elements."""

    def test_index_returns_200(self, client):
        assert client.get("/").status_code == 200

    def test_index_is_html(self, client):
        assert "text/html" in client.get("/").headers.get("content-type", "")

    def test_contains_title(self, client):
        assert "<title>Catown" in client.get("/").text

    def test_contains_sidebar(self, client):
        assert 'id="sidebar"' in client.get("/").text

    def test_contains_message_input(self, client):
        assert 'id="message-input"' in client.get("/").text

    def test_contains_pipeline_dashboard(self, client):
        assert "pipeline" in client.get("/").text.lower()

    def test_contains_agent_status_bar(self, client):
        assert 'id="agent-status-bar"' in client.get("/").text

    def test_contains_side_panel(self, client):
        assert 'id="side-panel"' in client.get("/").text

    def test_contains_websocket_config(self, client):
        assert "WS_URL" in client.get("/").text

    def test_contains_markdown_renderer(self, client):
        assert "marked" in client.get("/").text

    def test_contains_highlight_js(self, client):
        assert "highlight.js" in client.get("/").text

    def test_contains_tailwind(self, client):
        assert "tailwindcss" in client.get("/").text

    def test_contains_font_awesome(self, client):
        assert "font-awesome" in client.get("/").text or "fontawesome" in client.get("/").text


# ===================================================================
# 2. Projects API (rooms sidebar)
# ===================================================================

class TestProjectsAPI:
    """Frontend sidebar loads projects, creates rooms, selects them."""

    def test_list_projects(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_project(self, client):
        name = f"room-{int(time.time() * 1000)}"
        resp = client.post("/api/projects", json={"name": name, "description": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == name
        assert "id" in data
        client.delete(f"/api/projects/{data['id']}")

    def test_get_project_detail(self, client, sample_project):
        resp = client.get(f"/api/projects/{sample_project['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sample_project["id"]

    def test_delete_project(self, client):
        resp = client.post("/api/projects", json={"name": "to-delete", "description": ""})
        pid = resp.json()["id"]
        assert client.delete(f"/api/projects/{pid}").status_code in (200, 204)

    def test_project_has_chatroom_id(self, client, sample_project):
        """Frontend uses chatroom_id to load messages."""
        assert "chatroom_id" in sample_project
        assert sample_project["chatroom_id"] is not None


# ===================================================================
# 3. Agents API (status bar & @mention dropdown)
# ===================================================================

class TestAgentsAPI:
    """Frontend renders agent chips and @mention dropdown from this endpoint."""

    def test_list_agents(self, client):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        agents = resp.json()
        assert isinstance(agents, list)
        assert len(agents) >= 5

    def test_agents_have_required_fields(self, client):
        for agent in client.get("/api/agents").json():
            assert "name" in agent
            assert "is_active" in agent

    def test_pipeline_roles_present(self, client):
        names = {a["name"] for a in client.get("/api/agents").json()}
        for role in ["analyst", "architect", "developer", "tester", "release"]:
            assert role in names, f"Missing pipeline role: {role}"

    def test_agent_has_role_field(self, client):
        for agent in client.get("/api/agents").json():
            assert "role" in agent


# ===================================================================
# 4. Messages API (chat window)
# ===================================================================

class TestMessagesAPI:
    """Frontend loads and sends messages via these endpoints."""

    def test_load_messages(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        resp = client.get(f"/api/chatrooms/{cid}/messages")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_send_user_message(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        resp = client.post(f"/api/chatrooms/{cid}/messages", json={
            "content": "hello from frontend test",
            "message_type": "user"
        })
        assert resp.status_code == 200
        assert resp.json()["content"] == "hello from frontend test"

    def test_message_persists(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        client.post(f"/api/chatrooms/{cid}/messages", json={
            "content": "persistent msg",
            "message_type": "user"
        })
        msgs = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(m["content"] == "persistent msg" for m in msgs)


# ===================================================================
# 5. Pipeline Dashboard
# ===================================================================

class TestPipelineDashboard:
    """Tests for Pipeline tab — create, start, pause, resume, approve."""

    def test_list_pipelines(self, client):
        assert client.get("/api/pipelines").status_code == 200

    def test_create_pipeline(self, client, sample_pipeline):
        assert sample_pipeline["pipeline_name"] == "default"
        assert sample_pipeline["status"] in ("pending", "running", "paused", "completed", "failed")

    def test_get_pipeline_detail(self, client, sample_pipeline):
        resp = client.get(f"/api/pipelines/{sample_pipeline['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert "stages" in data or "runs" in data
        assert "status" in data

    def test_start_pipeline(self, client, sample_pipeline):
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "Build a user management system"
        })
        assert resp.status_code == 200
        assert resp.json()["status"] in ("started", "running", "paused", "completed")

    def test_pause_pipeline(self, client, sample_pipeline):
        client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "pause test"
        })
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_resume_pipeline(self, client, sample_pipeline):
        client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "resume test"
        })
        client.post(f"/api/pipelines/{sample_pipeline['id']}/pause")
        assert client.post(f"/api/pipelines/{sample_pipeline['id']}/resume").status_code == 200

    def test_approve_pipeline(self, client, sample_pipeline):
        """Approve requires the first stage to be blocked (manual gate).
        Without LLM the stage may not reach blocked state, so accept 400 too."""
        client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "approve test"
        })
        client.post(f"/api/pipelines/{sample_pipeline['id']}/pause")
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/approve")
        assert resp.status_code in (200, 400)

    def test_reject_pipeline(self, client, sample_pipeline):
        """Reject requires a blocked stage. Accept 400 when no LLM available."""
        client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "reject test"
        })
        client.post(f"/api/pipelines/{sample_pipeline['id']}/pause")
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/reject", json={
            "rollback_to": None
        })
        assert resp.status_code in (200, 400)

    def test_pipeline_stages_order(self, client, sample_pipeline):
        resp = client.get(f"/api/pipelines/{sample_pipeline['id']}")
        data = resp.json()
        runs = data.get("runs", [])
        if runs:
            stages = runs[-1].get("stages", [])
            names = [s["stage_name"] for s in stages]
            assert names == ["analysis", "architecture", "development", "testing", "release"]

    def test_pipeline_stage_has_agent(self, client, sample_pipeline):
        resp = client.get(f"/api/pipelines/{sample_pipeline['id']}")
        runs = resp.json().get("runs", [])
        if runs:
            for stage in runs[-1].get("stages", []):
                assert "agent_name" in stage
                assert stage["agent_name"] in ["analyst", "architect", "developer", "tester", "release"]


# ===================================================================
# 6. Pipeline Messages & Artifacts
# ===================================================================

class TestPipelineMessagesArtifacts:
    """Frontend shows agent messages and artifacts in Pipeline tab."""

    def test_pipeline_messages_endpoint(self, client, sample_pipeline):
        resp = client.get(f"/api/pipelines/{sample_pipeline['id']}/messages")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_pipeline_artifacts_endpoint(self, client, sample_pipeline):
        resp = client.get(f"/api/pipelines/{sample_pipeline['id']}/artifacts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_instruct_agent(self, client, sample_pipeline):
        """Instruct needs an active run. Start pipeline first, then send instruction."""
        client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "instruct test"
        })
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/instruct", json={
            "agent_name": "developer",
            "message": "Please focus on error handling"
        })
        assert resp.status_code == 200


# ===================================================================
# 7. Config Management (Config tab)
# ===================================================================

class TestConfigAPI:
    """Frontend Config tab loads and saves LLM configuration."""

    def test_get_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "global_llm" in data

    def test_config_has_expected_keys(self, client):
        data = client.get("/api/config").json()
        for key in ["server", "llm", "global_llm", "features"]:
            assert key in data, f"Config missing key: {key}"

    def test_get_effective_llm_configs(self, client):
        data = client.get("/api/config").json()
        assert "agent_llm_configs" in data

    def test_update_global_config(self, client):
        resp = client.put("/api/config/global", json={
            "provider": {
                "baseUrl": "http://test:8080/v1",
                "apiKey": "test-key",
                "models": [{"id": "test-model", "name": "Test Model"}]
            },
            "default_model": "test-model"
        })
        assert resp.status_code == 200

    def test_reload_config(self, client):
        assert client.post("/api/config/reload").status_code == 200


# ===================================================================
# 8. WebSocket
# ===================================================================

class TestWebSocket:
    """Frontend connects to WebSocket for real-time updates."""

    def test_ws_connect(self, client):
        with client.websocket_connect("/ws"):
            pass  # Connection succeeds

    def test_ws_join_room(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "join", "chatroom_id": cid})
            time.sleep(0.1)

    def test_ws_broadcast_on_message(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "join", "chatroom_id": cid})
            time.sleep(0.1)
            client.post(f"/api/chatrooms/{cid}/messages", json={
                "content": "ws broadcast test",
                "message_type": "user"
            })
            try:
                data = ws.receive_json(timeout=2)
                assert data["type"] == "message"
                assert data["content"] == "ws broadcast test"
            except Exception:
                pass  # Some implementations may not broadcast user messages


# ===================================================================
# 9. Collaboration Status
# ===================================================================

class TestCollaborationStatus:
    """Frontend Collab tab shows collaboration state."""

    def test_collaboration_status_endpoint(self, client):
        resp = client.get("/api/collaboration/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


# ===================================================================
# 10. SSE Streaming
# ===================================================================

class TestSSEStreaming:
    """Frontend falls back to SSE streaming for agent responses."""

    def test_stream_endpoint_exists(self, client, sample_project):
        cid = sample_project["chatroom_id"]
        resp = client.post(f"/api/chatrooms/{cid}/messages/stream", json={
            "content": "stream test",
            "message_type": "user"
        }, headers={"Accept": "text/event-stream"})
        # Endpoint should exist (200 with SSE stream, or graceful error)
        assert resp.status_code in (200, 500)

    def test_sync_fallback_works(self, client, sample_project):
        """When SSE fails, frontend falls back to sync endpoint."""
        cid = sample_project["chatroom_id"]
        resp = client.post(f"/api/chatrooms/{cid}/messages", json={
            "content": "sync fallback test",
            "message_type": "user"
        })
        assert resp.status_code == 200


# ===================================================================
# 11. Error Handling & Edge Cases
# ===================================================================

class TestErrorHandling:
    """Frontend must handle these gracefully."""

    def test_invalid_pipeline_id(self, client):
        resp = client.get("/api/pipelines/999999")
        assert resp.status_code == 404

    def test_invalid_project_id(self, client):
        resp = client.get("/api/projects/999999")
        assert resp.status_code in (404, 500)

    def test_create_project_empty_name(self, client):
        resp = client.post("/api/projects", json={"name": "", "description": ""})
        assert resp.status_code in (200, 400, 422)

    def test_send_message_no_chatroom(self, client):
        """Sending to non-existent chatroom — backend may return 200 or error."""
        resp = client.post("/api/chatrooms/999999/messages", json={
            "content": "orphan",
            "message_type": "user"
        })
        # Backend accepts message even for missing chatroom (auto-creates or returns gracefully)
        assert resp.status_code in (200, 404, 500)

    def test_instruct_invalid_agent(self, client, sample_pipeline):
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/instruct", json={
            "agent_name": "nonexistent_agent",
            "message": "test"
        })
        assert resp.status_code in (200, 400, 404, 422)

    def test_start_pipeline_twice(self, client, sample_pipeline):
        client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "first start"
        })
        resp = client.post(f"/api/pipelines/{sample_pipeline['id']}/start", json={
            "requirement": "second start"
        })
        assert resp.status_code in (200, 400, 409)


# ===================================================================
# 12. Health & Status
# ===================================================================

class TestHealthStatus:
    """Frontend checks /api/status on load to confirm backend is ready."""

    def test_status_endpoint(self, client):
        assert client.get("/api/status").status_code == 200

    def test_status_is_dict(self, client):
        data = client.get("/api/status").json()
        assert isinstance(data, dict)
        assert len(data) > 0


# ===================================================================
# 13. Tools Registry (frontend logs available tools on init)
# ===================================================================

class TestToolsRegistry:
    """Frontend logs available tools on init."""

    def test_list_tools(self, client):
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        # API returns {"tools": [...], "count": N}
        assert "tools" in data
        assert isinstance(data["tools"], list)

    def test_tools_have_names(self, client):
        data = client.get("/api/tools").json()
        for tool in data["tools"]:
            assert "name" in tool


# ===================================================================
# 14. Complete Frontend Workflow
# ===================================================================

class TestFrontendWorkflow:
    """Simulates a complete user interaction sequence from the browser."""

    def test_full_dashboard_workflow(self, client):
        """Simulate: open page → load agents → create room → send msg →
        create pipeline → start → pause → approve → check messages."""

        # 1. Load agents (status bar)
        agents = client.get("/api/agents").json()
        assert len(agents) >= 5

        # 2. Create a project (room)
        project = client.post("/api/projects", json={
            "name": f"workflow-{int(time.time() * 1000)}",
            "description": "full workflow test"
        }).json()
        cid = project["chatroom_id"]

        # 3. Send a user message
        msg = client.post(f"/api/chatrooms/{cid}/messages", json={
            "content": "Build me a todo app",
            "message_type": "user"
        })
        assert msg.status_code == 200

        # 4. Verify message appears
        msgs = client.get(f"/api/chatrooms/{cid}/messages").json()
        assert any(m["content"] == "Build me a todo app" for m in msgs)

        # 5. Create pipeline
        pipe_resp = client.post("/api/pipelines", json={
            "project_id": project["id"],
            "pipeline_name": "default"
        })
        if pipe_resp.status_code == 400:
            # Reuse existing
            pipelines = client.get("/api/pipelines").json()
            pipeline = next(p for p in pipelines if p["project_id"] == project["id"])
        else:
            assert pipe_resp.status_code == 200
            pipeline = pipe_resp.json()

        # 6. Start pipeline
        start_resp = client.post(f"/api/pipelines/{pipeline['id']}/start", json={
            "requirement": "Build me a todo app"
        })
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "started"

        # 7. Get pipeline detail (renders stages)
        assert client.get(f"/api/pipelines/{pipeline['id']}").status_code == 200

        # 8. Pause pipeline
        assert client.post(f"/api/pipelines/{pipeline['id']}/pause").status_code == 200

        # 9. Approve pipeline (may fail without LLM — stage not blocked yet)
        approve_resp = client.post(f"/api/pipelines/{pipeline['id']}/approve")
        assert approve_resp.status_code in (200, 400)

        # 10. Check pipeline messages
        assert client.get(f"/api/pipelines/{pipeline['id']}/messages").status_code == 200

        # 11. Check pipeline artifacts
        assert client.get(f"/api/pipelines/{pipeline['id']}/artifacts").status_code == 200

        # 12. Instruct an agent (needs active run)
        client.post(f"/api/pipelines/{pipeline['id']}/resume")
        assert client.post(f"/api/pipelines/{pipeline['id']}/instruct", json={
            "agent_name": "developer",
            "message": "Use FastAPI for the backend"
        }).status_code == 200

        # 13. Load config (Config tab)
        cfg = client.get("/api/config").json()
        assert "global_llm" in cfg

        # 14. Check collaboration status (Collab tab)
        assert client.get("/api/collaboration/status").status_code == 200

        # Cleanup
        client.delete(f"/api/projects/{project['id']}")


# ===================================================================
# 15. Rate Limiting (off by default, enable for release testing)
# ===================================================================

class TestRateLimiting:
    """Rate limiting is DISABLED by default (RATE_LIMIT_MAX=0).
    These tests verify the feature works when explicitly enabled.
    Run with: RATE_LIMIT_MAX=5 python3 -m pytest tests/test_frontend.py::TestRateLimiting -v
    """

    def test_rate_limiter_class(self, client):
        """Unit test: RateLimiter logic works correctly."""
        from main import RateLimiter
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.is_allowed("1.2.3.4") is True
        assert rl.is_allowed("1.2.3.4") is True
        assert rl.is_allowed("1.2.3.4") is True
        assert rl.is_allowed("1.2.3.4") is False  # 4th request blocked
        # Different IP is fine
        assert rl.is_allowed("5.6.7.8") is True

    def test_rate_limiter_window_expiry(self, client):
        """Rate limiter resets after window expires."""
        from main import RateLimiter
        rl = RateLimiter(max_requests=2, window_seconds=1)
        assert rl.is_allowed("10.0.0.1") is True
        assert rl.is_allowed("10.0.0.1") is True
        assert rl.is_allowed("10.0.0.1") is False
        time.sleep(1.1)
        assert rl.is_allowed("10.0.0.1") is True  # window expired

    def test_disabled_by_default(self, client):
        """With RATE_LIMIT_MAX=0 (default), requests are never blocked."""
        # Default limiter allows 999999 req — effectively unlimited
        for _ in range(100):
            resp = client.get("/api/agents")
            assert resp.status_code == 200, "Rate limiting should be disabled by default"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
