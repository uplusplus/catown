# Evaluation Result Schema v1

Updated: 2026-05-09

This document defines the first bounded `evaluation_result` contract for Catown.

Its purpose is to record the outcome of applying an evaluation rubric without directly mutating
runtime state.

## 1. Scope

Schema v1 covers:

- result identity
- rubric reference
- evaluated target reference
- reviewer ownership
- overall status
- criterion-level results
- evidence references
- optional recommended action request ids

It does **not** yet cover:

- persisted review workflows
- consensus rules
- direct gate enforcement
- rubric version pinning

## 2. Envelope

Every v1 evaluation result uses the same top-level envelope:

```json
{
  "kind": "evaluation_result",
  "version": 1,
  "result_id": "eval-prd-1",
  "rubric_id": "rubric-analysis-prd",
  "target": {},
  "reviewer": {},
  "overall_status": "passed",
  "criterion_results": [],
  "summary": "",
  "recommended_action_request_ids": [],
  "metadata": {}
}
```

Common rules:

- `kind` must be `evaluation_result`
- `version` must be `1`
- `criterion_results` must contain at least one item
- criterion ids must be unique within one result
- `overall_status=passed` cannot include failed or needs-review criteria

## 3. Target Reference

The target reference identifies what was evaluated:

```json
{
  "artifact_id": "artifact-prd-1",
  "artifact_type": "document.prd",
  "file_path": "PRD.md",
  "workflow_id": "default",
  "stage_id": "analysis",
  "task_run_id": 12,
  "pipeline_run_id": null,
  "pipeline_stage_id": null
}
```

## 4. Criterion Result

Each criterion result carries:

- `criterion_id`
- `status`
- `score`
- `rationale`
- `evidence_refs`
- `metadata`

Supported statuses:

- `passed`
- `failed`
- `not_applicable`
- `needs_review`

Example:

```json
{
  "criterion_id": "stories",
  "status": "passed",
  "score": 1,
  "rationale": "Stories and acceptance criteria are present.",
  "evidence_refs": ["artifact-prd-1"]
}
```

## 5. Runtime Interpretation Rules

This schema records evaluation output; it does not authorize runtime state changes.

Instead:

- rubric describes how to judge
- result records how an agent/human/software judged
- action request expresses a requested follow-up
- runtime policy decides whether the follow-up is legal

So a failed evaluation result may reference a recommended rollback request, but it does not itself
roll back a stage.

## 6. Current Implementation Status

Schema v1 is now documented and represented by a minimal Pydantic contract in:

- `backend/services/evaluation_result_contracts.py`

The first action-request bridge is now present in:

- `backend/services/evaluation_action_requests.py`

It can compile failed or needs-review evaluation results into bounded `report_blocker` requests,
and failed evaluation results into bounded `suggest_rollback` requests.

The first build-and-validate bridge is also present in:

- `backend/services/evaluation_action_requests.py`

It can build a rollback request from a failed evaluation result and immediately validate that request
against canonical workflow policy, without executing the rollback.

The rollback bridge now also has a projected result form that includes the generated
action request, the action-request policy decision, canonical `policy_decision`, and
a ledger-ready policy-decision event payload.

The first read-model summary helper is now present in:

- `backend/services/evaluation_result_contracts.py`

It creates a compact summary with status, target, reviewer, criterion counts, and recommended-action
counts for future API or Monitor projection.

The first runner-policy validator is now present in:

- `backend/services/evaluation_result_policy.py`

It validates that an evaluation result's rubric is allowed for the target workflow stage according
to compiled runner governance policy.

This schema is **not** yet wired into:

- artifact review persistence
- pipeline stage gates
- release approval
- monitor projection

It is currently a draft contract intended to guide the next refactor.

## 7. Expected Next Steps

The next likely follow-ups are:

1. connect evaluation results to artifact acceptance flows
2. map tester/release conclusions into evaluation result payloads
3. validate generated follow-up action requests against workflow policy
4. add durable review result persistence
