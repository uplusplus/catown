"""Runtime path layout tests."""
import importlib
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _reload_config_module():
    if "config" in sys.modules:
        del sys.modules["config"]
    import config

    return importlib.reload(config)


def test_defaults_use_unified_catown_home(tmp_path, monkeypatch):
    catown_home = tmp_path / "catown-home"
    monkeypatch.setenv("CATOWN_HOME", str(catown_home))
    monkeypatch.delenv("AGENT_CONFIG_FILE", raising=False)
    monkeypatch.delenv("PIPELINE_CONFIG_FILE", raising=False)
    monkeypatch.delenv("SKILLS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CATOWN_PROJECTS_ROOT", raising=False)
    monkeypatch.delenv("CATOWN_WORKSPACES_DIR", raising=False)

    config = _reload_config_module()
    settings = config.settings

    assert Path(settings.AGENT_CONFIG_FILE).is_relative_to(catown_home)
    assert Path(settings.PIPELINE_CONFIG_FILE).is_relative_to(catown_home)
    assert Path(settings.SKILLS_CONFIG_FILE).is_relative_to(catown_home)
    assert Path(settings.DATABASE_URL).is_relative_to(catown_home)
    assert settings.PROJECTS_ROOT.is_relative_to(catown_home)
    assert settings.WORKSPACES_DIR.is_relative_to(catown_home)

    assert Path(settings.AGENT_CONFIG_FILE).exists()
    assert Path(settings.PIPELINE_CONFIG_FILE).exists()
    assert Path(settings.SKILLS_CONFIG_FILE).exists()
    assert not Path(settings.DATABASE_URL).exists()
    assert settings.CONFIG_DIR.exists()
    assert settings.STATE_DIR.exists()
    assert settings.PROJECTS_ROOT.exists()
    assert settings.WORKSPACES_DIR.exists()
    assert list(settings.PROJECTS_ROOT.iterdir()) == []
    assert list(settings.WORKSPACES_DIR.iterdir()) == []


def test_explicit_config_override_is_not_seeded(tmp_path, monkeypatch):
    catown_home = tmp_path / "catown-home"
    custom_agents = tmp_path / "custom" / "agents.json"
    monkeypatch.setenv("CATOWN_HOME", str(catown_home))
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(custom_agents))

    config = _reload_config_module()
    settings = config.settings

    assert Path(settings.AGENT_CONFIG_FILE) == custom_agents.resolve()
    assert not custom_agents.exists()
    assert settings.CONFIG_DIR.exists()
