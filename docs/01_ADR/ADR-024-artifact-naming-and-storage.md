# ADR-024: Artifact Naming and Storage Conventions

**Status**: Proposed
**Date**: 2026-05-22
**Decision makers**: BOSS + Catown Runtime

**Related**:
- [ADR-015: Codex-Style Runtime Evolution](./ADR-015-codex-style-runtime-evolution.md)
- [ADR-016: Chat Project Browser](./ADR-016-chat-project-browser.md)
- [ADR-020: Prompt Guidance vs Runtime Contracts](./ADR-020-prompt-vs-runtime-contracts.md)
- [ADR-022: Tester Runner Contract](./ADR-022-tester-runner-contract.md)
- [ADR-023: Execution and Authorization Timing](./ADR-023-execution-authorization-timing.md)

## Context

Catown currently mixes several artifact patterns:

- some workflow stages still expect fixed filenames such as `PRD.md`, `tech-spec.md`,
  `test_report.md`, and `CHANGELOG.md`;
- some newer paths already use semantic directories, such as
  `reports/test-report-{task_ref}.md`;
- the project browser and artifact views reason about artifacts partly from workspace
  files and partly from runtime contracts;
- artifact-history archiving now preserves overwritten files, but that is only a safety
  net after the overwrite risk already exists.

This creates four classes of problems:

1. fixed names can silently overwrite earlier outputs;
2. concurrent or repeated work such as testing can produce multiple legitimate outputs
   that cannot share one canonical filename;
3. artifact paths do not always communicate what the document means;
4. workflow policy currently overuses exact file names where directory-scoped delivery
   would better match the product intent.

The product rule should not be "write a fixed artifact name and hope history catches
mistakes." The primary rule should be that artifact paths are semantically meaningful,
organized by type, and unique when concurrency or repetition is expected.

## Decision

Catown will treat artifact storage as a first-class naming contract, not as an incidental
file-write detail.

The priority order is:

1. semantic naming for meaningful singleton documents;
2. unique timestamped naming for true multi-version outputs;
3. directory-based storage by artifact class;
4. history archiving only as a last-resort safety net.

Artifact history remains enabled, but it is not the primary versioning model.

## Naming Rules

### 1. Meaningful singleton documents must encode the business decision or subject in the filename

Documents whose value depends on what they are about must not use bare generic names.

These documents are normally maintained in-place at one semantic path. Creating a new
document is done by choosing a new subject-specific path, not by timestamping revisions
of the same document purpose.

Examples:

- `docs/adr/ADR-024-artifact-naming-and-storage.md`
- `docs/features/project-browser-artifact-handling.md`
- `docs/prd/project-browser-artifact-lifecycle.md`
- `docs/specs/project-browser-artifact-storage.md`

Disallowed examples:

- `PRD.md`
- `tech-spec.md`
- `feature.md`
- `notes.md`

The filename must answer "about what?" rather than only "what kind of file is this?"

Examples of singleton semantic documents:

- ADRs
- PRDs
- feature documents
- technical specifications
- release notes when the product expects one current changelog document

For these artifact classes:

- refresh/update writes should overwrite the current semantic path;
- new business topics should create new semantic filenames;
- artifact-history may preserve overwritten content as a fallback, but history is not the
  primary versioning model.

### 2. True multi-version outputs must include a timestamp

Outputs that represent repeated runs of the same artifact type, or that may be produced
concurrently by multiple active processes, must include a UTC timestamp in the physical
storage path.

Required timestamp format:

- `YYYYMMDDTHHMMSSffffffZ`

Examples:

- `reports/tests/20260522T143015231004Z--run-17--project-browser-open.md`
- `reports/tests/20260522T143122004981Z--task-45--backend-pytest.md`
- `reports/reviews/20260522T143244889110Z--auth-session-audit.md`

The timestamp is part of the canonical storage path, not only metadata.

When helpful, the timestamped name should also include:

- a run reference such as `run-17`;
- a task reference such as `task-45`;
- a short subject slug such as `backend-pytest`.

Test reports are the primary required example of this policy because the same logical
artifact kind may legitimately exist in multiple versions at the same time.

### 3. Use stable slugs

Except for controlled prefixes like `ADR-024`, filenames should use lowercase kebab-case
slugs and should avoid spaces.

Examples:

- `project-browser-artifact-storage`
- `execution-authorization-timing`
- `backend-pytest`

## Directory Rules

Artifact classes must be stored in explicit directories instead of one shared root.

Recommended canonical layout:

- `docs/adr/`
- `docs/features/`
- `docs/prd/`
- `docs/specs/`
- `reports/tests/`
- `reports/reviews/`
- `reports/releases/`

Additional directories may be introduced later, but new artifact classes should follow
the same rule: one class, one directory family.

Root-level generic artifact files should be treated as legacy compatibility paths rather
than the preferred destination for new outputs.

## Delivery Contract Rules

Workflow delivery policy should prefer directory-scoped expectations over fixed filenames
whenever a stage may produce more than one valid artifact instance over time.

Examples:

- use `docs/prd/` for semantic PRD documents, with one subject-specific current file per topic
- use `docs/specs/` for semantic spec documents, with one subject-specific current file per topic
- use `reports/tests/` instead of `test_report.md` for repeated test-report outputs
- use `reports/releases/` when release reporting is intentionally versioned as multiple report instances

Exact filename expectations are still allowed only when the product truly requires a
singleton path with external meaning.

Examples of valid singleton expectations:

- `README.md`
- `package.json`
- a framework-mandated config path

Workflow policy should not model versioned artifacts as singleton filenames.

It should also not model singleton semantic documents as timestamped run outputs unless
the product explicitly wants a versioned report series instead of one maintained document.

## Current Alias Rule

Some surfaces may still want a stable "current" pointer for convenience. That is allowed,
but the current alias is not the canonical artifact instance.

Examples:

- a database row with `is_current=true`
- a UI projection that labels the latest test report as current
- an optional derived summary file maintained from a versioned source

If a current alias exists, the versioned artifact instance remains the source of truth.

## Archive Rule

Artifact history archiving remains enabled as a fallback protection layer.

Its role is:

- preserve older content when a path is overwritten;
- protect legacy or manually edited flows;
- reduce accidental data loss during transition.

Its role is not:

- to replace semantic naming;
- to replace timestamped paths for repeated outputs;
- to justify fixed artifact names for workflow-produced deliverables.

## Consequences

Positive consequences:

- fewer accidental overwrites;
- clearer project-browser artifact lists;
- cleaner matching between artifact meaning and artifact path;
- better support for concurrent testing and repeated reporting without forcing every
  document class into a versioned model;
- better alignment between runtime contracts and visible workspace state.

Costs:

- workflow configs must be updated from singleton files to directory expectations;
- some prompts, examples, and tests still reference old fixed names and must migrate;
- users may temporarily see both legacy and new artifact locations during transition.

## Migration Plan

Migration should happen in this order:

1. Add the naming helper and canonical directory rules in runtime code.
2. Convert new artifact writes to canonical paths first.
3. Update workflow `expected_artifacts` to directory-scoped expectations.
4. Keep legacy root files readable during transition where necessary.
5. Preserve artifact-history as the final safety net.

Priority targets for the first implementation wave:

1. test reports
2. PRD and spec documents
3. release notes and changelog artifacts
4. older fixed-name examples in configs and tests

## Non-Goals

This ADR does not require:

- renaming every existing legacy document immediately;
- removing artifact-history;
- forcing every ordinary note or source file into the artifact taxonomy;
- inventing a document database separate from workspace storage.

## Decision Summary

Catown should not treat artifact naming as an afterthought.

Meaningful artifacts must be named by subject, concurrency-prone outputs must be
timestamped, artifact classes must live in explicit directories, and archive history must
remain only the last line of defense.
