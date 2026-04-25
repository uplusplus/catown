# -*- coding: utf-8 -*-
"""Import public skills into Catown's canonical package layout."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict

from skills.loader import load_skill_registry


_SAFE_ID_RE = re.compile(r"[^a-z0-9]+")


def import_skill_from_source(
    source: str,
    skills_dir: str | Path,
    *,
    skill_id: str | None = None,
    ref: str | None = None,
    subdir: str | None = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Import a public skill source into `<skills_dir>/<skill-id>`.

    Supported sources:
    - Local package directory containing `SKILL.md`
    - Local or remote `.zip` archive containing one or more skill packages
    - Raw `SKILL.md` URL
    - GitHub repository URL, optionally with `/tree/<ref>/<path>`
    """

    source = (source or "").strip()
    if not source:
        raise ValueError("source is required")

    target_root = Path(skills_dir)
    target_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="catown-skill-import-") as tmp:
        tmp_dir = Path(tmp)
        candidate = _materialize_source(source, tmp_dir, ref=ref)
        package_dir = _resolve_package_dir(candidate, subdir=subdir or _github_tree_subdir(source))
        package_id = _choose_skill_id(package_dir, skill_id)
        target_dir = target_root / package_id

        if target_dir.exists():
            if not force:
                raise FileExistsError(f"Skill '{package_id}' already exists")
            shutil.rmtree(target_dir)

        shutil.copytree(
            package_dir,
            target_dir,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        _ensure_manifest(target_dir, package_id, source)

    imported = load_skill_registry(target_root).get(package_id)
    if not imported:
        raise ValueError(f"Imported skill '{package_id}' is not a valid package")
    return imported


def _materialize_source(source: str, tmp_dir: Path, *, ref: str | None) -> Path:
    local_path = Path(source).expanduser()
    if local_path.exists():
        return local_path.resolve()

    parsed = urllib.parse.urlparse(source)
    if parsed.scheme == "file":
        file_path = Path(urllib.request.url2pathname(parsed.path)).expanduser()
        if file_path.exists():
            return file_path.resolve()

    if _looks_like_github_repo(parsed):
        archive = tmp_dir / "github.zip"
        _download(_github_zip_url(source, ref=ref), archive)
        return _extract_zip(archive, tmp_dir / "github")

    if parsed.path.lower().endswith(".zip"):
        archive = tmp_dir / "skill.zip"
        _download(source, archive)
        return _extract_zip(archive, tmp_dir / "zip")

    if parsed.path.endswith("SKILL.md") or parsed.path.lower().endswith(".md"):
        package = tmp_dir / "raw-skill"
        package.mkdir()
        _download(source, package / "SKILL.md")
        return package

    raise ValueError("Unsupported skill source. Use a GitHub repo, zip, raw SKILL.md, or local package path.")


def _resolve_package_dir(candidate: Path, *, subdir: str | None) -> Path:
    if subdir:
        package = (candidate / subdir).resolve()
        if not package.is_relative_to(candidate.resolve()):
            raise ValueError("subdir escapes source root")
        if not (package / "SKILL.md").exists():
            raise ValueError(f"No SKILL.md found in subdir '{subdir}'")
        return package

    if (candidate / "SKILL.md").exists():
        return candidate

    matches = sorted(candidate.rglob("SKILL.md"))
    if not matches:
        raise ValueError("No SKILL.md found in source")
    if len(matches) > 1:
        raise ValueError("Multiple skills found; provide subdir")
    return matches[0].parent


def _choose_skill_id(package_dir: Path, requested: str | None) -> str:
    if requested:
        return _normalize_id(requested)
    frontmatter = _read_frontmatter(package_dir / "SKILL.md")
    manifest = _read_json(package_dir / "skill.json")
    return _normalize_id(manifest.get("id") or frontmatter.get("id") or package_dir.name)


def _ensure_manifest(package_dir: Path, skill_id: str, source: str) -> None:
    skill_file = package_dir / "SKILL.md"
    manifest_file = package_dir / "skill.json"
    frontmatter = _read_frontmatter(skill_file)
    manifest = _read_json(manifest_file)

    name = manifest.get("name") or frontmatter.get("name") or skill_id
    description = manifest.get("description") or frontmatter.get("description") or ""
    levels = dict(manifest.get("levels") or {})
    levels.setdefault("hint", f"{name}: {description}".strip(": "))

    merged = {
        **manifest,
        "id": skill_id,
        "name": name,
        "description": description,
        "category": manifest.get("category") or "imported",
        "required_tools": manifest.get("required_tools") or [],
        "status": manifest.get("status") or "active",
        "version": manifest.get("version") or "0.1.0",
        "levels": levels,
        "source": {
            **dict(manifest.get("source") or {}),
            "type": manifest.get("source", {}).get("type", "import"),
            "url": manifest.get("source", {}).get("url", source),
        },
    }
    manifest_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def _looks_like_github_repo(parsed: urllib.parse.ParseResult) -> bool:
    return parsed.netloc.lower() == "github.com" and len([p for p in parsed.path.split("/") if p]) >= 2


def _github_zip_url(source: str, *, ref: str | None) -> str:
    parsed = urllib.parse.urlparse(source)
    parts = [p for p in parsed.path.split("/") if p]
    owner, repo = parts[0], parts[1].removesuffix(".git")

    tree_ref = None
    if len(parts) >= 4 and parts[2] == "tree":
        tree_ref = parts[3]
    resolved_ref = ref or tree_ref or _github_default_branch(owner, repo)
    return f"https://api.github.com/repos/{owner}/{repo}/zipball/{resolved_ref}"


def _github_tree_subdir(source: str) -> str | None:
    parsed = urllib.parse.urlparse(source)
    if not _looks_like_github_repo(parsed):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) <= 4 or parts[2] != "tree":
        return None
    return "/".join(parts[4:])


def _github_default_branch(owner: str, repo: str) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    with urllib.request.urlopen(url, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("default_branch") or "main"


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "catown-skill-importer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        target.write_bytes(response.read())


def _extract_zip(archive: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        target_root = target.resolve()
        for member in zf.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target_root):
                raise ValueError("Zip archive contains unsafe paths")
            zf.extract(member, target)
    children = [child for child in target.iterdir()]
    return children[0] if len(children) == 1 and children[0].is_dir() else target


def _read_frontmatter(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    values: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _SAFE_ID_RE.sub("-", raw).strip("-")
