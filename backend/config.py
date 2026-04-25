# -*- coding: utf-8 -*-
"""
Unified infrastructure/runtime paths.

All mutable runtime data and editable JSON configs live under a single Catown
home directory outside the source tree by default.
"""
from __future__ import annotations

import os
import shutil
import json
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


def _merge_json_file(src: Path, dst: Path, merge_key: str) -> None:
    """Seed missing entries from a bundled JSON config into a runtime config."""
    if not src.exists() or not dst.exists():
        return
    try:
        with src.open("r", encoding="utf-8") as f:
            src_data = json.load(f)
        with dst.open("r", encoding="utf-8") as f:
            dst_data = json.load(f)
    except Exception:
        return
    if not isinstance(src_data, dict) or not isinstance(dst_data, dict):
        return
    src_items = src_data.get(merge_key)
    dst_items = dst_data.setdefault(merge_key, {})
    if not isinstance(src_items, dict) or not isinstance(dst_items, dict):
        return

    changed = False
    for key, value in src_items.items():
        if key not in dst_items:
            dst_items[key] = value
            changed = True
        elif isinstance(value, dict) and isinstance(dst_items.get(key), dict):
            if _merge_missing_dict_values(value, dst_items[key]):
                changed = True
    if "default" in src_data and "default" not in dst_data:
        dst_data["default"] = src_data["default"]
        changed = True
    if changed:
        dst.write_text(json.dumps(dst_data, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_missing_dict_values(src: dict, dst: dict) -> bool:
    changed = False
    for key, value in src.items():
        if key not in dst:
            dst[key] = value
            changed = True
        elif key == "enabled":
            continue
        elif isinstance(value, dict) and isinstance(dst.get(key), dict):
            if _merge_missing_dict_values(value, dst[key]):
                changed = True
    return changed


def _ensure_agent_tool(config_file: Path, agent_name: str, tool_name: str) -> None:
    """Add a newly introduced tool to an existing runtime agent config."""
    if not config_file.exists():
        return
    try:
        with config_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    agents = data.get("agents")
    if not isinstance(agents, dict) or agent_name not in agents:
        return
    tools = agents[agent_name].setdefault("tools", [])
    if not isinstance(tools, list) or tool_name in tools:
        return
    tools.append(tool_name)
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_agent_rule(config_file: Path, agent_name: str, rule: str) -> None:
    """Add a newly introduced role rule to an existing runtime agent config."""
    if not config_file.exists():
        return
    try:
        with config_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    agents = data.get("agents")
    if not isinstance(agents, dict) or agent_name not in agents:
        return
    role = agents[agent_name].setdefault("role", {})
    if not isinstance(role, dict):
        return
    rules = role.setdefault("rules", [])
    if not isinstance(rules, list) or rule in rules:
        return
    rules.append(rule)
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_skill_packages_if_missing(src: Path, dst_dir: Path) -> None:
    """Convert bundled legacy JSON definitions into canonical skill packages."""
    if not src.exists() or any(dst_dir.glob("*/SKILL.md")):
        return

    with src.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return

    for skill_id, skill in data.items():
        if not isinstance(skill, dict):
            continue
        skill_root = dst_dir / skill_id
        skill_root.mkdir(parents=True, exist_ok=True)
        levels = skill.get("levels") or {}
        body = str(levels.get("full") or levels.get("guide") or skill.get("description") or "").strip()
        name = str(skill.get("name") or skill_id)
        description = str(skill.get("description") or "")
        skill_md = "\n".join(
            [
                "---",
                f"name: {json.dumps(name, ensure_ascii=False)}",
                f"description: {json.dumps(description, ensure_ascii=False)}",
                "---",
                "",
                body,
                "",
            ]
        )
        (skill_root / "SKILL.md").write_text(skill_md, encoding="utf-8")
        metadata = {
            "id": skill_id,
            "name": name,
            "description": description,
            "category": skill.get("category") or "general",
            "required_tools": skill.get("required_tools") or [],
            "status": skill.get("status") or "active",
            "version": skill.get("version") or "0.1.0",
            "levels": {
                "hint": levels.get("hint") or f"{name}: {description}".strip(": "),
                "guide": levels.get("guide") or body,
            },
            "source": {"type": "bundled-template", "path": str(src)},
        }
        (skill_root / "skill.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


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
        self.SKILL_MARKETPLACES_CONFIG_FILE: str = os.getenv(
            "SKILL_MARKETPLACES_CONFIG_FILE", str(self.CONFIG_DIR / "skill_marketplaces.json")
        )
        self.SKILLS_DIR: Path = Path(
            os.getenv("CATOWN_SKILLS_DIR", str(self.CATOWN_HOME / "skills"))
        ).expanduser().resolve()
        self.DATABASE_URL: str = os.getenv(
            "DATABASE_URL", str(self.STATE_DIR / "catown.db")
        )
        self.SQLALCHEMY_DATABASE_URL: str = _to_sqlalchemy_db_url(self.DATABASE_URL)
        self.MONITOR_NETWORK_MEMORY_MAX_ENTRIES: int = max(
            100, int(os.getenv("MONITOR_NETWORK_MEMORY_MAX_ENTRIES", "4000"))
        )
        self.MONITOR_NETWORK_RETENTION_HOURS: int = max(
            1, int(os.getenv("MONITOR_NETWORK_RETENTION_HOURS", "168"))
        )
        self.MONITOR_NETWORK_MAX_PERSISTED: int = max(
            1, int(os.getenv("MONITOR_NETWORK_MAX_PERSISTED", "50000"))
        )
        self.MONITOR_NETWORK_CLEANUP_INTERVAL_SECONDS: int = max(
            10, int(os.getenv("MONITOR_NETWORK_CLEANUP_INTERVAL_SECONDS", "300"))
        )

        self._ensure_runtime_layout()

    def _ensure_runtime_layout(self) -> None:
        for directory in (
            self.CATOWN_HOME,
            self.CONFIG_DIR,
            self.STATE_DIR,
            self.PROJECTS_ROOT,
            self.WORKSPACES_DIR,
            self.SKILLS_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if "AGENT_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "agents.json", Path(self.AGENT_CONFIG_FILE))
            _ensure_agent_tool(Path(self.AGENT_CONFIG_FILE), "valet", "skill_manager")
            _ensure_agent_rule(
                Path(self.AGENT_CONFIG_FILE),
                "valet",
                "用户要求安装、下载、导入、启用或排查 skill/技能/marketplace 时，优先调用 skill_manager；不要用 web_search 或 execute_code 猜安装命令。未指定来源时先 action=marketplaces，再选择合适 marketplace。",
            )
        if "PIPELINE_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "pipelines.json", Path(self.PIPELINE_CONFIG_FILE))
        if "SKILLS_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "skills.json", Path(self.SKILLS_CONFIG_FILE))
        if "SKILL_MARKETPLACES_CONFIG_FILE" not in os.environ:
            _copy_file_if_missing(
                DEFAULT_CONFIG_SOURCE_DIR / "skill_marketplaces.json",
                Path(self.SKILL_MARKETPLACES_CONFIG_FILE),
            )
            _merge_json_file(
                DEFAULT_CONFIG_SOURCE_DIR / "skill_marketplaces.json",
                Path(self.SKILL_MARKETPLACES_CONFIG_FILE),
                "marketplaces",
            )
        _seed_skill_packages_if_missing(DEFAULT_CONFIG_SOURCE_DIR / "skills.json", self.SKILLS_DIR)


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
        "CATOWN_SKILLS_DIR",
        "AGENT_CONFIG_FILE",
        "PIPELINE_CONFIG_FILE",
        "SKILLS_CONFIG_FILE",
        "SKILL_MARKETPLACES_CONFIG_FILE",
        "DATABASE_URL",
        "MONITOR_NETWORK_MEMORY_MAX_ENTRIES",
        "MONITOR_NETWORK_RETENTION_HOURS",
        "MONITOR_NETWORK_MAX_PERSISTED",
        "MONITOR_NETWORK_CLEANUP_INTERVAL_SECONDS",
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
