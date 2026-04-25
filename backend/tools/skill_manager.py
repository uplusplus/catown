# -*- coding: utf-8 -*-
"""Skill management tool for agents."""
from __future__ import annotations

from typing import Any, Dict

from config import settings
from skills import import_skill_from_marketplace, list_marketplaces, load_skill_registry
from tools.base import BaseTool


class SkillManagerTool(BaseTool):
    """Install and inspect Catown skill packages."""

    name = "skill_manager"
    description = (
        "Manage Catown skills. When the user asks to install, add, download, enable, import, "
        "or troubleshoot a skill/技能/marketplace, call this tool instead of guessing shell commands. "
        "Use action='list' to inspect installed skills, action='marketplaces' to inspect configured "
        "skill marketplaces and CLI readiness, or action='install' to import a skill into Catown's "
        "canonical skills directory. Examples: install graphify from SkillHub.cn with "
        "{\"action\":\"install\",\"marketplace\":\"skillhub-cn\",\"source\":\"graphify\"}; "
        "inspect marketplaces first with {\"action\":\"marketplaces\"}. If install returns "
        "code='command_not_found', explain that the marketplace CLI is missing and ask the user to "
        "enable/install that marketplace CLI from the Skills configuration page."
    )

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "marketplaces", "install"],
                    "description": (
                        "Skill management action. Use 'marketplaces' before install when the user "
                        "names a marketplace or CLI readiness is unknown. Use 'install' for install/add/"
                        "download/import skill requests."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Skill source for action='install': a marketplace skill slug such as 'graphify' "
                        "for skillhub-cn, a GitHub repo/tree URL, zip URL/path, raw SKILL.md URL/path, "
                        "or local package path."
                    ),
                },
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Optional marketplace id from skill_marketplaces.json, for example 'skillhub-cn', "
                        "'builtin', or 'skills-cli'. Defaults to the configured default."
                    ),
                },
                "skill_id": {
                    "type": "string",
                    "description": "Optional target skill id. Defaults to package metadata or directory name.",
                },
                "ref": {
                    "type": "string",
                    "description": "Optional Git ref for GitHub imports.",
                },
                "subdir": {
                    "type": "string",
                    "description": "Optional package subdirectory when a source contains multiple skills.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Replace an existing skill with the same id.",
                    "default": False,
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        source: str | None = None,
        marketplace: str | None = None,
        skill_id: str | None = None,
        ref: str | None = None,
        subdir: str | None = None,
        force: bool = False,
        **_: Any,
    ) -> Dict[str, Any]:
        action = (action or "").strip().lower()

        if action == "list":
            skills = load_skill_registry(settings.SKILLS_DIR)
            return {
                "ok": True,
                "skills_dir": str(settings.SKILLS_DIR),
                "skills": [
                    {
                        "id": skill.get("id"),
                        "name": skill.get("name"),
                        "description": skill.get("description"),
                        "category": skill.get("category"),
                        "version": skill.get("version"),
                    }
                    for skill in skills.values()
                ],
            }

        if action == "marketplaces":
            return {
                "ok": True,
                "marketplaces": list_marketplaces(settings.SKILL_MARKETPLACES_CONFIG_FILE),
            }

        if action == "install":
            try:
                skill = import_skill_from_marketplace(
                    source=source or "",
                    skills_dir=settings.SKILLS_DIR,
                    marketplace=marketplace,
                    skill_id=skill_id,
                    ref=ref,
                    subdir=subdir,
                    force=force,
                )
                return {
                    "ok": True,
                    "message": "Skill installed",
                    "skill": {
                        "id": skill.get("id"),
                        "name": skill.get("name"),
                        "description": skill.get("description"),
                        "category": skill.get("category"),
                        "version": skill.get("version"),
                        "source": skill.get("source"),
                    },
                    "skills_dir": str(settings.SKILLS_DIR),
                }
            except FileExistsError as e:
                return {"ok": False, "error": str(e), "code": "already_exists"}
            except FileNotFoundError as e:
                return {"ok": False, "error": str(e), "code": "command_not_found"}
            except ValueError as e:
                return {"ok": False, "error": str(e), "code": "marketplace_error"}
            except Exception as e:
                return {"ok": False, "error": str(e), "code": "install_failed"}

        return {"ok": False, "error": f"Unsupported action: {action}", "code": "unsupported_action"}
