# -*- coding: utf-8 -*-
"""Load Catown skills from canonical SKILL.md packages.

Catown's skill source of truth is a package directory:

    <skills-dir>/<skill-id>/
      SKILL.md
      skill.json          # required machine metadata
      scripts/            # optional bundled resources
      references/
      assets/

`SKILL.md` is the human and AI-readable source of truth. `skill.json` is the
machine metadata used for management, routing, provenance, and future imports
from open-source skill repositories.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


SkillRegistry = Dict[str, Dict[str, Any]]


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def load_skill_registry(
    skills_dir: str | Path | None = None,
) -> SkillRegistry:
    """Load skills from canonical package directories.

    The optional argument remains keyword-friendly so callers are explicit about
    the only supported runtime source.
    """

    return _load_package_registry(Path(skills_dir)) if skills_dir else {}


def write_workspace_skill_packages(
    skills: Mapping[str, Mapping[str, Any]],
    skill_names: Iterable[str],
    workspace: str | Path,
) -> None:
    """Export selected skills to a workspace in compatible package form.

    The generated layout is intentionally readable by humans, Catown agents, and
    coding agents that understand `SKILL.md` folders.
    """

    workspace_path = Path(workspace)
    root = workspace_path / ".catown" / "skills"
    root.mkdir(parents=True, exist_ok=True)

    selected: SkillRegistry = {}
    for raw_name in skill_names:
        skill_id = _normalize_skill_id(raw_name)
        skill = dict(skills.get(skill_id, {}))
        if not skill:
            continue
        selected[skill_id] = skill
        skill_root = root / skill_id
        skill_root.mkdir(parents=True, exist_ok=True)

        skill_md = _render_skill_markdown(skill_id, skill)
        (skill_root / "SKILL.md").write_text(skill_md, encoding="utf-8")
        metadata = _skill_metadata(skill_id, skill)
        (skill_root / "skill.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if selected:
        (root / "registry.json").write_text(
            json.dumps(
                {skill_id: _skill_metadata(skill_id, skill) for skill_id, skill in selected.items()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "index.md").write_text(_render_index(selected), encoding="utf-8")


def _load_package_registry(path: Path) -> SkillRegistry:
    if not path.exists() or not path.is_dir():
        return {}

    registry: SkillRegistry = {}
    for skill_dir in sorted(p for p in path.iterdir() if p.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        manifest_file = skill_dir / "skill.json"
        if not skill_file.exists() or not manifest_file.exists():
            continue
        skill = _load_skill_package(skill_dir, skill_file)
        registry[skill["id"]] = skill
    return registry


def _load_skill_package(skill_dir: Path, skill_file: Path) -> Dict[str, Any]:
    raw = skill_file.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    manifest = _load_manifest(skill_dir)

    skill_id = _normalize_skill_id(
        manifest.get("id") or frontmatter.get("id") or skill_dir.name or frontmatter.get("name")
    )
    name = manifest.get("name") or frontmatter.get("name") or skill_id
    description = manifest.get("description") or frontmatter.get("description") or ""
    short_description = (
        manifest.get("short_description")
        or frontmatter.get("short-description")
        or frontmatter.get("short_description")
        or description
    )

    levels = dict(manifest.get("levels") or {})
    levels.setdefault("hint", f"{name}: {short_description}".strip(": "))
    levels.setdefault("guide", _extract_named_section(body, ("Guide", "Usage", "Workflow")) or body.strip())
    levels.setdefault("full", body.strip())

    return _normalize_skill_definition(
        skill_id,
        {
            **manifest,
            "id": skill_id,
            "name": name,
            "description": description,
            "required_tools": manifest.get("required_tools") or [],
            "category": manifest.get("category") or "general",
            "levels": levels,
            "compatibility": {
                **dict(manifest.get("compatibility") or {}),
                "codex": True,
                "claude": True,
                "catown": True,
            },
            "source": {
                **dict(manifest.get("source") or {}),
                "type": manifest.get("source_type") or manifest.get("source", {}).get("type", "package"),
                "path": str(skill_dir),
            },
        },
    )


def _load_manifest(skill_dir: Path) -> Dict[str, Any]:
    path = skill_dir / "skill.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _split_frontmatter(raw: str) -> tuple[Dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    return _parse_simple_yaml(match.group(1)), raw[match.end() :]


def _parse_simple_yaml(raw: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _extract_named_section(markdown: str, names: tuple[str, ...]) -> str:
    headings = list(_HEADING_RE.finditer(markdown))
    lowered = {name.lower() for name in names}
    for index, heading in enumerate(headings):
        title = heading.group(2).strip().lower()
        if title not in lowered:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        return markdown[start:end].strip()
    return ""


def _normalize_skill_definition(skill_id: str, raw: Mapping[str, Any]) -> Dict[str, Any]:
    normalized_id = _normalize_skill_id(skill_id)
    levels = dict(raw.get("levels") or {})
    name = str(raw.get("name") or normalized_id)
    description = str(raw.get("description") or "")
    levels.setdefault("hint", f"{name}: {description}".strip(": "))
    levels.setdefault("guide", levels.get("full") or description)
    levels.setdefault("full", levels.get("guide") or description)

    return {
        **dict(raw),
        "id": normalized_id,
        "name": name,
        "description": description,
        "required_tools": list(raw.get("required_tools") or []),
        "category": raw.get("category") or "general",
        "levels": levels,
    }


def _normalize_skill_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    return raw.strip("-")


def _render_skill_markdown(skill_id: str, skill: Mapping[str, Any]) -> str:
    levels = skill.get("levels", {})
    body = str(levels.get("full") or levels.get("guide") or skill.get("description") or "").strip()
    frontmatter = {
        "name": skill.get("name") or skill_id,
        "description": skill.get("description") or levels.get("hint") or "",
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    lines.extend(["---", "", body, ""])
    return "\n".join(lines)


def _skill_metadata(skill_id: str, skill: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": skill_id,
        "name": skill.get("name") or skill_id,
        "description": skill.get("description") or "",
        "category": skill.get("category") or "general",
        "required_tools": list(skill.get("required_tools") or []),
        "status": skill.get("status") or "active",
        "version": skill.get("version") or "0.1.0",
        "compatibility": {
            "catown": True,
            "codex": True,
            "claude": True,
            **dict(skill.get("compatibility") or {}),
        },
        "source": skill.get("source") or skill.get("origin") or {},
    }


def _render_index(skills: Mapping[str, Mapping[str, Any]]) -> str:
    lines = [
        "# Catown Skills",
        "",
        "This directory is generated from canonical Catown skill packages. Each skill is a directory with `SKILL.md` for humans and AI agents plus `skill.json` for management metadata.",
        "",
    ]
    for skill_id, skill in sorted(skills.items()):
        name = skill.get("name") or skill_id
        description = skill.get("description") or skill.get("levels", {}).get("hint", "")
        lines.append(f"- [{name}](./{skill_id}/SKILL.md): {description}")
    lines.append("")
    return "\n".join(lines)
