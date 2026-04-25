import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills.loader import load_skill_registry, write_workspace_skill_packages
from skills.importer import import_skill_from_source
from skills.marketplace import import_skill_from_marketplace, list_marketplaces


def test_loads_skill_package(tmp_path):
    skill_dir = tmp_path / "skills" / "package-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: package-skill
description: Use this package skill.
---

# Package Skill

## Guide

Do the short workflow.

## Full Reference

Read the longer material.
""",
        encoding="utf-8",
    )
    (skill_dir / "skill.json").write_text(
        json.dumps({"category": "testing", "required_tools": ["execute_code"]}),
        encoding="utf-8",
    )

    registry = load_skill_registry(tmp_path / "skills")

    assert registry["package-skill"]["category"] == "testing"
    assert registry["package-skill"]["levels"]["hint"] == "package-skill: Use this package skill."
    assert registry["package-skill"]["levels"]["guide"] == "Do the short workflow."
    assert "Full Reference" in registry["package-skill"]["levels"]["full"]


def test_write_workspace_skill_packages_exports_compatible_layout(tmp_path):
    registry = {
        "code-generation": {
            "name": "Code Generation",
            "description": "Generate code.",
            "required_tools": ["read_file", "write_file"],
            "category": "development",
            "levels": {
                "hint": "Generate code.",
                "guide": "Short guide",
                "full": "# Code Generation\n\nFull body",
            },
        }
    }

    write_workspace_skill_packages(registry, ["code-generation"], tmp_path)

    root = tmp_path / ".catown" / "skills"
    assert (root / "code-generation" / "SKILL.md").exists()
    assert (root / "code-generation" / "skill.json").exists()
    assert "Code Generation" in (root / "index.md").read_text(encoding="utf-8")
    exported = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    assert exported["code-generation"]["name"] == "Code Generation"


def test_import_skill_from_local_package_synthesizes_manifest(tmp_path):
    source = tmp_path / "hub" / "useful-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        """---
name: useful-skill
description: Imported from a public hub.
---

# Useful Skill

## Guide

Use it carefully.
""",
        encoding="utf-8",
    )

    imported = import_skill_from_source(str(source), tmp_path / "skills")

    assert imported["id"] == "useful-skill"
    assert imported["category"] == "imported"
    assert (tmp_path / "skills" / "useful-skill" / "skill.json").exists()


def test_import_skill_from_configured_builtin_marketplace(tmp_path):
    source = tmp_path / "hub" / "market-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        """---
name: market-skill
description: Imported through marketplace config.
---

# Market Skill
""",
        encoding="utf-8",
    )
    config = tmp_path / "marketplaces.json"
    config.write_text(
        json.dumps(
            {
                "default": "local",
                "marketplaces": {
                    "local": {
                        "adapter": "builtin",
                        "enabled": True,
                        "description": "Local test marketplace",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    imported = import_skill_from_marketplace(
        source=str(source),
        skills_dir=tmp_path / "skills",
        config_file=config,
    )

    assert imported["id"] == "market-skill"
    assert list_marketplaces(config)[0]["id"] == "local"


def test_lists_skillhub_marketplace_from_default_config():
    config = Path(__file__).resolve().parents[1] / "configs" / "skill_marketplaces.json"

    marketplaces = list_marketplaces(config)

    skillhub = next(item for item in marketplaces if item["id"] == "skillhub-cn")
    assert skillhub["adapter"] == "skillhub"
    assert skillhub["enabled"] is False
    assert skillhub["bootstrap_available"] is True
