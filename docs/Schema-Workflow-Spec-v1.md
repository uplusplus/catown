# Workflow Spec Schema v1

Updated: 2026-05-07

This document defines the first bounded `workflow_spec` schema for Catown.

Its purpose is to make the current pipeline template shape explicit and versioned, instead of leaving it implicit inside `PipelineConfig`, `StageConfig`, and direct executor consumption.

## 1. Scope

Schema v1 covers:

- workflow identity
- stage ordering
- agent ownership per stage
- gate type
- timeout budget
- delivery expectations
- rollback configuration
- skill injection configuration
- evaluation rubric references

It does **not** yet cover:

- arbitrary DAG branching beyond ordered stage lists
- conditional-expression syntax for `gate=condition`
- sidecar topology
- approval policy beyond simple gate type
- full domain-specific rubric embedding

## 2. Design Goals

The contract is intentionally:

- JSON-compatible
- versioned
- easy for LLMs and humans to emit
- easy for software to validate and compile
- close enough to today's `pipelines.json` to support incremental migration

## 3. Envelope

Every v1 workflow spec uses the same top-level envelope:

```json
{
  "kind": "workflow_spec",
  "version": 1,
  "workflow_id": "default",
  "name": "标准软件开发流水线",
  "description": "需求分析 -> 架构设计 -> 开发 -> 测试 -> 发布",
  "domain": "software_delivery",
  "stages": [],
  "metadata": {}
}
```

Common rules:

- `kind` must be `workflow_spec`
- `version` must be `1`
- `workflow_id` identifies the template
- `stages` is an ordered stage list in v1

## 4. Stage Shape

Each stage carries:

- `stage_id`
- `display_name`
- `agent_type`
- `gate`
- `timeout_minutes`
- `context_prompt`
- `delivery`
- `rollback`
- `skills`
- `evaluation`
- `metadata`

Example:

```json
{
  "stage_id": "testing",
  "display_name": "测试",
  "agent_type": "tester",
  "gate": "auto",
  "timeout_minutes": 30,
  "context_prompt": "请基于 PRD 的验收标准测试代码。",
  "delivery": {
    "expected_artifacts": ["test_report.md"],
    "required": true
  },
  "rollback": {
    "enabled": true,
    "max_attempts": 3,
    "target_stage_name": "development"
  },
  "skills": {
    "active": ["test-generation", "bug-reporting"],
    "hint_only": ["security-testing"]
  },
  "evaluation": {
    "rubric_refs": ["rubric-testing"],
    "required": true
  },
  "metadata": {}
}
```

## 5. Current Mapping to Existing Pipeline JSON

Schema v1 is intentionally designed to compile from today's `backend/configs/pipelines.json`.

Current field mapping:

- `name` -> `workflow_id`
- `display_name` -> `display_name`
- `agent` -> `agent_type`
- `gate` -> `gate`
- `timeout_minutes` -> `timeout_minutes`
- `expected_artifacts` -> `delivery.expected_artifacts`
- `rollback_on_blocker` -> `rollback.enabled`
- `max_rollback_count` -> `rollback.max_attempts`
- `rollback_target` -> `rollback.target_stage_name`
- `active_skills` -> `skills.active`
- `hint_only_skills` -> `skills.hint_only`
- `evaluation_rubrics` -> `evaluation.rubric_refs`

This means schema v1 is not yet a brand-new workflow language; it is the first explicit contract layer over the current pipeline template shape.

## 6. Relationship to Runtime Policy

`workflow_spec` does not itself perform state transitions.

It only describes:

- what stages exist
- which role owns them
- what should be delivered
- what gate/rollback rules apply

The software executor still owns:

- whether a stage may start now
- whether a gate actually blocks
- whether rollback is legal in the current runtime state
- checkpoint / replay / lease / recovery semantics

So:

- `workflow_spec` describes the plan
- runtime policy decides whether the plan may advance

## 7. Relationship to Action Requests and Artifact Contracts

The intended long-term layering is:

- `workflow_spec`
  defines stage structure and expectations
- `action_request`
  expresses what agents want the executor to do inside that workflow
- `artifact_contract`
  describes what was produced and how it should be carried forward

This keeps:

- workflow structure
- runtime intent
- produced objects

as separate protocol objects.

## 8. Current Implementation Status

Schema v1 is now documented and represented by a minimal Pydantic contract in:

- `backend/services/workflow_spec_contracts.py`

It also includes a compatibility compiler from today's pipeline-template payload shape.

The first runtime-facing export bridge is now present in:

- `backend/pipeline/config.py`

`PipelineConfigManager` can now load today's `pipelines.json` and export a canonical
`workflow_spec` view alongside the legacy `PipelineConfig` / `StageConfig` view.

The first governance-policy bridge is now present in:

- `backend/services/runner_policy.py`

`RunnerGovernancePolicy` can now be compiled directly from canonical `workflow_spec`,
without first requiring legacy `StageConfig` objects.
Stage-level evaluation rubric refs are included in the compiled stage metadata as
`evaluation_policy`.
Evaluation results can then be validated against that projected policy by
`backend/services/evaluation_result_policy.py`.

The first API exposure bridge is now present in:

- `GET /api/pipelines/templates/{pipeline_name}/workflow-spec`

This makes canonical workflow specs visible from a stable read-side API without yet forcing the
pipeline executor to consume them as its primary execution input.

The first action-request policy bridge is now present in:

- `backend/services/action_request_policy.py`

It validates `action_request` payloads against compiled workflow policy before those requests are
allowed to become runtime effects. This is still an isolated validator, not a pipeline-engine
behavior change.

The first execution-readiness validator is now present in:

- `backend/services/workflow_spec_policy.py`

It checks canonical workflow specs for deterministic execution hazards such as empty stage lists,
duplicate stage ids, missing stage owners, invalid timeouts, invalid rollback targets, and delivery
contracts that require artifacts without naming them.

The first compile-with-diagnostics helper is also present in:

- `backend/services/workflow_spec_policy.py`

It compiles today's pipeline template payload into canonical `workflow_spec` and returns the
execution-readiness report in the same result object.

The first config-load read-side bridge is now present in:

- `backend/pipeline/config.py`

`PipelineConfigManager` now caches both canonical workflow specs and their execution-readiness
reports while preserving the legacy `PipelineConfig` view.

The first diagnostics API bridge is now present in:

- `GET /api/pipelines/templates/{pipeline_name}/workflow-spec/report`

It exposes the cached execution-readiness report through a stable read-side endpoint.

The first submitted-spec validation API is now present in:

- `POST /api/pipelines/workflow-spec/validate`

It accepts an arbitrary canonical workflow spec and returns the same execution-readiness report
without creating a pipeline or starting execution.

This schema is **not** yet wired into:

- pipeline executor start path
- orchestration runtime path
- runtime-enforced action-request validation
- richer DAG or sidecar topology

It is currently a draft contract intended to guide the next refactor.

## 9. Expected Next Steps

The next likely follow-ups are:

1. connect submitted workflow specs to a durable draft/template persistence path
2. connect action-request policy validation to selected runtime request paths
3. define v2 for richer branching / sidecar topology
4. connect workflow versioning and compatibility rules into the executor
