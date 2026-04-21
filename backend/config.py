# -*- coding: utf-8 -*-
"""
Unified infrastructure/runtime paths.

All mutable runtime data and editable JSON configs live under a single Catown
home directory outside the source tree by default.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_SOURCE_DIR = BACKEND_DIR / "configs"


def _default_catown_home() -> Path:
    configured = os.getenv("CATOWN_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".catown").resolve()


def _copy_file_if_missing(src: Path, dst: Path) -> None:
    if dst.exists() or not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _to_sqlalchemy_db_url(raw_value: str) -> str:
    return raw_value if "://" in raw_value else f"sqlite:///{raw_value}"


class Settings:
    """Global infrastructure config plus managed runtime paths."""

    def __init__(self):
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("PORT", "8000"))
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

        self.CATOWN_HOME: Path = _default_catown_home()
        self.CONFIG_DIR: Path = Path(
            os.getenv("CATOWN_CONFIG_DIR", str(self.CATOWN_HOME / "config"))
        ).expanduser().resolve()
        self.STATE_DIR: Path = Path(
            os.getenv("CATOWN_STATE_DIR", str(self.CATOWN_HOME / "state"))
        ).expanduser().resolve()
        self.PROJECTS_ROOT: Path = Path(
            os.getenv("CATOWN_PROJECTS_ROOT", str(self.CATOWN_HOME / "projects"))
        ).expanduser().resolve()
        self.WORKSPACES_DIR: Path = Path(
            os.getenv("CATOWN_WORKSPACES_DIR", str(self.CATOWN_HOME / "workspaces"))
        ).expanduser().resolve()

        self.AGENT_CONFIG_FILE: str = os.getenv(
            "AGENT_CONFIG_FILE", str(self.CONFIG_DIR / "agents.json")
        )
        self.PIPELINE_CONFIG_FILE: str = os.getenv(
            "PIPELINE_CONFIG_FILE", str(self.CONFIG_DIR / "pipelines.json")
        )
        self.SKILLS_CONFIG_FILE: str = os.getenv(
            "SKILLS_CONFIG_FILE", str(self.CONFIG_DIR / "skills.json")
        )
        self.DATABASE_URL: str = os.getenv(
            "DATABASE_URL", str(self.STATE_DIR / "catown.db")
        )
        self.SQLALCHEMY_DATABASE_URL: str = _to_sqlalchemy_db_url(self.DATABASE_URL)

        self._ensure_runtime_layout()

    def _ensure_runtime_layout(self) -> None:
        for directory in (self.CATOWN_HOME, self.CONFIG_DIR, self.STATE_DIR, self.PROJECTS_ROOT, self.WORKSPACES_DIR):
            directory.mkdir(parents=True, exist_ok=True)

        if "AGENT_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "agents.json", Path(self.AGENT_CONFIG_FILE))
        if "PIPELINE_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "pipelines.json", Path(self.PIPELINE_CONFIG_FILE))
        if "SKILLS_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "skills.json", Path(self.SKILLS_CONFIG_FILE))


class SettingsProxy:
    """Reload settings lazily when relevant environment variables change."""

    _ENV_KEYS = (
        "HOST",
        "PORT",
        "LOG_LEVEL",
        "CATOWN_HOME",
        "CATOWN_CONFIG_DIR",
        "CATOWN_STATE_DIR",
        "CATOWN_PROJECTS_ROOT",
        "CATOWN_WORKSPACES_DIR",
        "AGENT_CONFIG_FILE",
        "PIPELINE_CONFIG_FILE",
        "SKILLS_CONFIG_FILE",
        "DATABASE_URL",
    )

    def __init__(self):
        self._settings: Settings | None = None
        self._signature: tuple[str | None, ...] | None = None

    def _current_signature(self) -> tuple[str | None, ...]:
        return tuple(os.getenv(key) for key in self._ENV_KEYS)

    def _get(self) -> Settings:
        signature = self._current_signature()
        if self._settings is None or self._signature != signature:
            self._settings = Settings()
            self._signature = signature
        return self._settings

    def __getattr__(self, item):
        return getattr(self._get(), item)


settings = SettingsProxy()
