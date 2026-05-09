# Artifact Contract Schema v1

Updated: 2026-05-06

This document defines the first bounded `artifact_contract` schema for Catown.

Its purpose is to unify the repo's current artifact shapes:

- pipeline-facing `StageArtifact` records
- project-facing `Asset` records
- future agent-emitted `publish_artifact` requests

## 1. Scope

Schema v1 covers four bounded artifact modes:

- `workspace_file`
- `workspace_directory`
- `document`
- `structured_asset`

It does **not** yet define:

- full versioning semantics across asset supersession
- approval-state transitions for artifacts
- artifact-link dependency graph semantics
- media-specific contracts for image/video/audio outputs

## 2. Design Goals

The contract is intentionally:

- JSON-compatible
- versioned
- easy to emit from LLM agents
- easy to validate in software
- broad enough to bridge today's file-centric pipeline outputs and richer project assets

## 3. Envelope

Every v1 artifact contract uses the same top-level envelope:

```json
{
  "kind": "artifact_contract",
  "version": 1,
  "artifact_id": "artifact-prd-1",
  "artifact_type": "document.prd",
  "title": "PRD draft",
  "summary": "Structured product requirements.",
  "producer": {
    "agent_name": "Analyst",
    "agent_type": "analyst",
    "stage_name": "analysis",
    "task_run_id": 12,
    "pipeline_run_id": 5,
    "pipeline_stage_id": 9,
    "stage_run_id": null
  },
  "tags": [],
  "source_input_refs": [],
  "metadata": {},
  "mode": "document"
}
```

Common rules:

- `kind` must be `artifact_contract`
- `version` must be `1`
- `artifact_id` identifies the artifact instance
- `artifact_type` is the domain-level artifact class
- `producer` identifies the runtime/stage/agent that produced it
- `mode` selects the concrete payload shape

## 4. Artifact Modes

### 4.1 `workspace_file`

Use when the artifact is primarily a single file in the workspace.

Example:

```json
{
  "mode": "workspace_file",
  "file_path": "PRD.md",
  "media_type": "text/markdown"
}
```

This best matches today's simple `StageArtifact(file)` records.

### 4.2 `workspace_directory`

Use when the artifact is primarily a directory tree in the workspace.

Example:

```json
{
  "mode": "workspace_directory",
  "directory_path": "src/"
}
```

This best matches today's simple `StageArtifact(directory)` records.

### 4.3 `document`

Use when the artifact is a document and the runtime may need one or more of:

- workspace path
- markdown content
- structured JSON projection

Example:

```json
{
  "mode": "document",
  "format": "mixed",
  "file_path": "PRD.md",
  "content_markdown": "# PRD\n\n## Stories",
  "content_json": {
    "stories": 5,
    "acceptance_criteria": 12
  }
}
```

Rules:

- a document must provide at least one of:
  - `file_path`
  - `content_markdown`
  - `content_json`

### 4.4 `structured_asset`

Use when the artifact is best treated as a typed project asset with a known schema.

Example:

```json
{
  "mode": "structured_asset",
  "schema_name": "design.tokens.v1",
  "storage_path": "assets/design-tokens.json",
  "content_json": {
    "color.primary": "#223344"
  },
  "content_markdown": null
}
```

This best matches richer `Asset` records that want schema identity and structured content.

## 5. Relationship to Current Models

Current repo state is split across several shapes:

- `pipeline_stages.expected_artifacts`
  - weak expected-output contract
- `StageArtifact`
  - stores `artifact_type`, `file_path`, `summary`
- `Asset`
  - stores `asset_type`, `content_json`, `content_markdown`, `storage_path`

Schema v1 is intended to become the common typed layer above these.

That means:

- `workspace_file` and `workspace_directory`
  approximate the current `StageArtifact` model
- `document` and `structured_asset`
  approximate the richer `Asset` model

## 6. Relationship to Action Requests

`publish_artifact` from [Schema-Action-Request-v1.md](Schema-Action-Request-v1.md) currently carries a simplified artifact payload.

The intended long-term direction is:

- action requests ask the runtime to publish an artifact
- artifact publication normalizes into `artifact_contract`
- runtime then persists the artifact via one of the storage models

So:

- `action_request` expresses intent
- `artifact_contract` expresses the produced object

## 7. Current Implementation Status

Schema v1 is now documented and represented by a minimal Pydantic contract in:

- `backend/services/artifact_contracts.py`

The first publication bridge is now present in:

- `backend/services/artifact_publication.py`

It compiles `publish_artifact` action requests into canonical `artifact_contract` payloads for
document, workspace-file, workspace-directory, and structured-asset artifacts.

This schema is **not** yet wired into:

- pipeline stage artifact recording
- project asset persistence
- publish_artifact runtime persistence flow
- artifact approval and supersession logic

It is currently a draft contract intended to guide the next refactor.

## 8. Expected Next Steps

The next likely follow-ups are:

1. map current `StageArtifact` writes into `artifact_contract` normalization
2. map current `Asset` writes into `artifact_contract` normalization
3. connect `publish_artifact` contract compilation to selected runtime persistence paths
4. define artifact approval/supersession policy on top of this schema
