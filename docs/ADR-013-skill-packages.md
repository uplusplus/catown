# ADR-013: Canonical Skill Packages

**Date**: 2026-04-23
**Status**: Accepted

## Context

Catown's first skill implementation used `skills.json`:

- `levels.hint` is always injected.
- `levels.guide` is injected for active pipeline skills.
- `levels.full` is written into the workspace for on-demand reading.

That model is token-efficient, but the source format is mostly machine-oriented JSON. It is harder for people to edit, awkward to version as a reusable unit, and not a good substrate for importing open-source skill content.

Codex-style skills use a directory with a required `SKILL.md`, optional `scripts/`, `references/`, and `assets/`. Claude Code works naturally with project-readable Markdown knowledge. Catown should choose one best internal shape that is easy to manage, extend, review, import, and export.

## Decision

Catown skills are loaded from one canonical source:

```text
${CATOWN_HOME}/skills/<skill-id>/
```

`skills.json` is not a runtime source. Existing JSON content may be converted by an importer, but after import the package directory is the only managed representation.

## Package Layout

```text
<skill-id>/
  SKILL.md
  skill.json
  scripts/
  references/
  assets/
```

`SKILL.md` is the source of truth for humans and AI agents. It should contain YAML frontmatter with at least:

```yaml
---
name: code-generation
description: Generate production-quality code from technical specs.
---
```

`skill.json` is required and stores Catown-specific metadata:

```json
{
  "category": "development",
  "required_tools": ["read_file", "write_file"],
  "version": "0.1.0",
  "status": "active",
  "levels": {
    "hint": "Code generation: follow project style and validate imports.",
    "guide": "Short stage-time instructions."
  }
}
```

If `levels.full` is omitted, Catown uses the `SKILL.md` body as the full document. If `levels.guide` is omitted, Catown uses the `## Guide`, `## Usage`, or `## Workflow` section when present.

## Workspace Export

When a pipeline stage starts, Catown writes selected skills to:

```text
<workspace>/.catown/skills/
  index.md
  registry.json
  <skill-id>/
    SKILL.md
    skill.json
```

The workspace export is still a package tree. Agents read `index.md` to discover available skills and `SKILL.md` for deeper instructions.

## Import Interface

Public skills are imported into the canonical package directory through:

```text
POST /api/skills/import
```

Request:

```json
{
  "source": "https://github.com/org/skills/tree/main/example-skill",
  "skill_id": "example-skill",
  "ref": "main",
  "subdir": "example-skill",
  "force": false
}
```

`source` may be a GitHub repository, a GitHub tree URL, a zip archive, a raw `SKILL.md`, or a local package path. If the source has no `skill.json`, Catown synthesizes one from `SKILL.md` frontmatter and records provenance in `source`.

Current packages can be inspected through:

```text
GET /api/skills
```

Marketplaces are configured in:

```text
${CATOWN_HOME}/config/skill_marketplaces.json
```

The default config includes:

- `builtin`: Catown's built-in URL/local importer.
- `skills-cli`: optional `npx skills` adapter for public skill ecosystems.
- `github-skill`: optional `gh skill` adapter for GitHub CLI based skill flows.
- `skillhub-cn`: optional `skillhub` adapter for SkillHub.cn. The CLI can be installed from `https://skillhub.cn/install/skillhub.md`; Catown runs `skillhub install <source>` in a temporary workspace and imports the resulting package.

Configured marketplaces can be inspected through:

```text
GET /api/skills/marketplaces
```

`POST /api/skills/import` may include `"marketplace": "<id>"`; omitting it uses the configured default.

## Compatibility Rules

- `agents.json` continues to reference skills by id.
- `pipelines.json` continues to use `active_skills` and `hint_only_skills`.
- Tool permissions still come from agent tool whitelists, not from skills.
- Skill `required_tools` remains advisory and can be validated separately.
- Package resources are passive files unless a tool explicitly reads or executes them.
- JSON registries are accepted only by import/migration tooling, not by runtime loading.

## Rationale

This keeps Catown's low-token progressive disclosure while making skills durable, reviewable, portable, and suitable for absorbing open-source skills. A single package model avoids split-brain management and makes validation, versioning, provenance, and future marketplace flows much simpler.
