# -*- coding: utf-8 -*-
"""Configurable skill marketplace adapters."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from config import settings
from skills.importer import import_skill_from_source
from skills.loader import load_skill_registry


def load_marketplaces(config_file: str | Path | None = None) -> Dict[str, Any]:
    path = Path(config_file or settings.SKILL_MARKETPLACES_CONFIG_FILE)
    if not path.exists():
        return {"default": "builtin", "marketplaces": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {"default": "builtin", "marketplaces": {}}


def list_marketplaces(config_file: str | Path | None = None) -> List[Dict[str, Any]]:
    config = load_marketplaces(config_file)
    marketplaces = config.get("marketplaces") or {}
    return [
        {
            "id": marketplace_id,
            "name": item.get("name") or marketplace_id,
            "adapter": item.get("adapter") or "builtin",
            "enabled": item.get("enabled", True),
            "command": item.get("command"),
            "command_available": _command_available(item),
            "install_url": item.get("install_url"),
            "bootstrap_available": bool(item.get("bootstrap")),
            "description": item.get("description") or "",
        }
        for marketplace_id, item in sorted(marketplaces.items())
        if isinstance(item, dict)
    ]


def import_skill_from_marketplace(
    *,
    source: str,
    skills_dir: str | Path,
    marketplace: str | None = None,
    skill_id: str | None = None,
    ref: str | None = None,
    subdir: str | None = None,
    force: bool = False,
    config_file: str | Path | None = None,
) -> Dict[str, Any]:
    config = load_marketplaces(config_file)
    marketplace_id = marketplace or config.get("default") or "builtin"
    marketplace_config = _get_marketplace(config, marketplace_id)
    adapter = marketplace_config.get("adapter") or "builtin"

    if adapter == "builtin":
        return import_skill_from_source(
            source,
            skills_dir,
            skill_id=skill_id,
            ref=ref,
            subdir=subdir,
            force=force,
        )
    if adapter == "skills-cli":
        return _import_with_cli(
            marketplace_config,
            source=source,
            skills_dir=skills_dir,
            skill_id=skill_id,
            force=force,
            adapter="skills-cli",
        )
    if adapter == "gh-skill":
        return _import_with_cli(
            marketplace_config,
            source=source,
            skills_dir=skills_dir,
            skill_id=skill_id,
            force=force,
            adapter="gh-skill",
        )
    if adapter == "skillhub":
        return _import_with_workspace_cli(
            marketplace_config,
            source=source,
            skills_dir=skills_dir,
            skill_id=skill_id,
            force=force,
            adapter="skillhub",
        )

    raise ValueError(f"Unsupported marketplace adapter: {adapter}")


def set_marketplace_enabled(
    marketplace_id: str,
    enabled: bool,
    *,
    bootstrap: bool = True,
    config_file: str | Path | None = None,
) -> Dict[str, Any]:
    path = Path(config_file or settings.SKILL_MARKETPLACES_CONFIG_FILE)
    config = load_marketplaces(path)
    marketplace = _get_marketplace_for_update(config, marketplace_id)
    marketplace["enabled"] = enabled

    bootstrap_result: Dict[str, Any] | None = None
    if enabled and bootstrap and _command_available(marketplace) is False and marketplace.get("bootstrap"):
        bootstrap_result = _run_bootstrap(marketplace)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    updated = next(item for item in list_marketplaces(path) if item["id"] == marketplace_id)
    return {"marketplace": updated, "bootstrap": bootstrap_result}


def _get_marketplace(config: Dict[str, Any], marketplace_id: str) -> Dict[str, Any]:
    marketplaces = config.get("marketplaces") or {}
    marketplace = marketplaces.get(marketplace_id)
    if not isinstance(marketplace, dict):
        raise ValueError(f"Skill marketplace '{marketplace_id}' is not configured")
    if not marketplace.get("enabled", True):
        raise ValueError(f"Skill marketplace '{marketplace_id}' is disabled")
    return marketplace


def _get_marketplace_for_update(config: Dict[str, Any], marketplace_id: str) -> Dict[str, Any]:
    marketplaces = config.setdefault("marketplaces", {})
    marketplace = marketplaces.get(marketplace_id)
    if not isinstance(marketplace, dict):
        raise ValueError(f"Skill marketplace '{marketplace_id}' is not configured")
    return marketplace


def _import_with_cli(
    marketplace_config: Dict[str, Any],
    *,
    source: str,
    skills_dir: str | Path,
    skill_id: str | None,
    force: bool,
    adapter: str,
) -> Dict[str, Any]:
    command = marketplace_config.get("command")
    args = list(marketplace_config.get("args") or [])
    if not command:
        raise ValueError(f"Marketplace adapter '{adapter}' has no command configured")
    if shutil.which(command) is None:
        raise ValueError(f"Marketplace command not found: {command}")

    with tempfile.TemporaryDirectory(prefix="catown-skill-marketplace-") as tmp:
        download_dir = Path(tmp) / "download"
        download_dir.mkdir()
        _run_download_command(command, args, source, download_dir)
        package_dir = _find_downloaded_package(download_dir)
        imported = import_skill_from_source(
            str(package_dir),
            skills_dir,
            skill_id=skill_id,
            force=force,
        )
        _annotate_marketplace_source(imported["id"], skills_dir, adapter, source)
        return load_skill_registry(skills_dir)[imported["id"]]


def _import_with_workspace_cli(
    marketplace_config: Dict[str, Any],
    *,
    source: str,
    skills_dir: str | Path,
    skill_id: str | None,
    force: bool,
    adapter: str,
) -> Dict[str, Any]:
    command = marketplace_config.get("command")
    args = list(marketplace_config.get("args") or [])
    if not command:
        raise ValueError(f"Marketplace adapter '{adapter}' has no command configured")
    if shutil.which(command) is None:
        install_hint = marketplace_config.get("install_url") or marketplace_config.get("description") or ""
        raise FileNotFoundError(f"Marketplace command not found: {command}. {install_hint}".strip())

    with tempfile.TemporaryDirectory(prefix="catown-skill-marketplace-") as tmp:
        download_dir = Path(tmp) / "workspace"
        download_dir.mkdir()
        cmd = [command, *args, "install", source]
        result = subprocess.run(
            cmd,
            cwd=download_dir,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Marketplace CLI install failed: {detail}")

        package_dir = _find_downloaded_package(download_dir)
        imported = import_skill_from_source(
            str(package_dir),
            skills_dir,
            skill_id=skill_id,
            force=force,
        )
        _annotate_marketplace_source(imported["id"], skills_dir, adapter, source)
        return load_skill_registry(skills_dir)[imported["id"]]


def _run_download_command(command: str, args: List[str], source: str, destination: Path) -> None:
    attempts = [
        [command, *args, "install", source, "--path", str(destination), "-y"],
        [command, *args, "install", source, "--dir", str(destination), "-y"],
        [command, *args, "install", source, "--target", str(destination), "-y"],
    ]
    errors: List[str] = []
    for cmd in attempts:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            return
        errors.append((result.stderr or result.stdout or "").strip())
    raise RuntimeError("Marketplace CLI install failed: " + " | ".join(error for error in errors if error))


def _command_available(marketplace: Dict[str, Any]) -> bool | None:
    command = marketplace.get("command")
    if not command:
        return None
    return shutil.which(command) is not None


def _run_bootstrap(marketplace: Dict[str, Any]) -> Dict[str, Any]:
    bootstrap = marketplace.get("bootstrap")
    if not isinstance(bootstrap, dict):
        return {"ok": True, "skipped": True}

    command = bootstrap.get("command")
    args = list(bootstrap.get("args") or [])
    if not command:
        raise ValueError("Marketplace bootstrap has no command configured")
    if shutil.which(command) is None:
        raise FileNotFoundError(f"Marketplace bootstrap command not found: {command}")

    result = subprocess.run(
        [command, *args],
        capture_output=True,
        text=True,
        timeout=int(bootstrap.get("timeout_seconds") or 240),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Marketplace bootstrap failed: {detail}")

    return {
        "ok": True,
        "command": command,
        "stdout": (result.stdout or "").strip()[-2000:],
        "stderr": (result.stderr or "").strip()[-2000:],
    }


def _find_downloaded_package(root: Path) -> Path:
    if (root / "SKILL.md").exists():
        return root
    matches = sorted(root.rglob("SKILL.md"))
    if not matches:
        raise ValueError("Marketplace CLI did not produce a SKILL.md package")
    if len(matches) > 1:
        raise ValueError("Marketplace CLI produced multiple skills; install one at a time")
    return matches[0].parent


def _annotate_marketplace_source(skill_id: str, skills_dir: str | Path, adapter: str, source: str) -> None:
    manifest_path = Path(skills_dir) / skill_id / "skill.json"
    if not manifest_path.exists():
        return
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    current_source = dict(manifest.get("source") or {})
    current_source.update({"marketplace": adapter, "url": source})
    manifest["source"] = current_source
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
