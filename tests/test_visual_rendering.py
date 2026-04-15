"""
Mission Board static rendering tests.

These checks stay intentionally lightweight: they verify the frontend shell,
key component copy, and the design tokens/styles that define the Mission Board
surface without depending on a browser runtime.

Run:
    cd catown
    .venv/bin/python -m pytest tests/test_visual_rendering.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"
SRC_ROOT = FRONTEND_ROOT / "src"


@pytest.fixture(scope="module")
def client():
    backend_dir = REPO_ROOT / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    os.chdir(backend_dir)

    tmp = tempfile.mkdtemp(prefix="catown-vr-")
    os.environ["DATABASE_URL"] = os.path.join(tmp, "test.db")
    os.environ["LOG_LEVEL"] = "WARNING"

    from main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def shell_html() -> str:
    return (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source() -> str:
    return (SRC_ROOT / "App.tsx").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_source() -> str:
    return (SRC_ROOT / "styles.css").read_text(encoding="utf-8")


class TestMissionBoardShell:
    def test_vite_shell_has_root_mount(self, shell_html: str):
        assert "<div id=\"root\"></div>" in shell_html
        assert 'src="/src/main.tsx"' in shell_html

    def test_shell_title_and_description_are_mission_board_specific(self, shell_html: str):
        assert "<title>Catown Mission Board</title>" in shell_html
        assert "project-first mission board" in shell_html.lower()
        assert "pipeline dashboard" not in shell_html.lower()

    def test_shell_uses_mission_board_fonts(self, shell_html: str):
        assert "Space+Grotesk" in shell_html
        assert "Noto+Sans+SC" in shell_html


class TestMissionBoardComposition:
    def test_app_assembles_project_first_sections(self, app_source: str):
        for component in [
            "ProjectRail",
            "ProjectHero",
            "NextActionStrip",
            "StageLane",
            "DecisionPanel",
            "AssetPanel",
            "ActivityFeed",
            "DetailRail",
        ]:
            assert component in app_source

    def test_app_headline_is_mission_board_copy(self, app_source: str):
        assert "Catown Command Surface" in app_source
        assert "Project-first Mission Board" in app_source
        assert "React/Vite frontend reset" in app_source

    def test_app_does_not_reference_legacy_chatroom_pipeline_shell(self, app_source: str):
        forbidden = [
            "/api/pipelines",
            "/api/chatrooms",
            "message-input",
            "sidebar",
            "renderPipelineContent",
        ]
        for token in forbidden:
            assert token not in app_source


class TestDesignTokensAndStates:
    def test_styles_define_command_surface_tokens(self, styles_source: str):
        for token in [
            "--bg:",
            "--accent:",
            "--accent-2:",
            "--danger:",
            'font-family: "Space Grotesk", "Noto Sans SC", sans-serif;',
        ]:
            assert token in styles_source

    def test_styles_define_board_layout_and_panels(self, styles_source: str):
        assert ".board-layout" in styles_source
        assert "grid-template-columns: 290px minmax(0, 1fr) 360px;" in styles_source
        assert ".panel-shell" in styles_source
        assert "backdrop-filter: blur(18px);" in styles_source

    def test_styles_define_loading_error_and_notice_states(self, styles_source: str):
        for selector in [
            ".loading-state",
            ".error-banner",
            ".notice-banner",
            ".detail-loading-state",
            ".detail-error-state",
            ".main-board.is-busy",
        ]:
            assert selector in styles_source


class TestBackendFrontendDelivery:
    def test_root_serves_built_frontend_artifact(self, client):
        response = client.get("/")
        assert response.status_code == 200
        html = response.text
        assert '<script type="module" crossorigin' in html
        assert "assets/index-" in html

    def test_root_response_is_not_legacy_fallback_page(self, client):
        html = client.get("/").text
        assert "Backend service is running" not in html
        assert "Pipeline Dashboard" not in html
