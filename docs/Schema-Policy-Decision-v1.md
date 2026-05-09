# Policy Decision Schema v1

Updated: 2026-05-09

This document defines the first bounded `policy_decision` schema for Catown.

Unlike `action_request`, `artifact_contract`, `evaluation_rubric`, and
`evaluation_result`, this schema is produced by local software, not directly by an
LLM agent.

Its purpose is to give Catown a stable, auditable shape for runtime policy
decisions such as:

- whether an action request is allowed by workflow policy
- whether an artifact contract satisfies stage delivery policy
- whether an evaluation result is allowed for the target stage

## 1. Scope

Schema v1 covers policy decisions for:

- `action_request_policy`
- `artifact_contract_policy`
- `evaluation_result_policy`
- `workflow_spec_policy`
- `runner_policy`

It does **not** yet define:

- database persistence tables for decisions
- retention or compaction policy
- human approval resolution state
- executor-side mutation semantics after a decision is accepted

## 2. Design Goals

The contract is intentionally:

- JSON-compatible
- versioned
- software-produced
- suitable for run ledger and Monitor read models
- separate from the original request/result/artifact payload

This keeps the boundary clear:

- agents and humans produce semantic inputs
- local software validates those inputs against workflow/runner policy
- `policy_decision` records the software verdict

## 3. Envelope

Every v1 policy decision uses the same top-level envelope:

```json
{
  "kind": "policy_decision",
  "version": 1,
  "decision_id": "policy-decision-action-request-policy-req-gate-1",
  "decision_type": "action_request_policy",
  "subject": {
    "kind": "action_request",
    "id": "req-gate-1",
    "type": "request_approval"
  },
  "accepted": true,
  "stage_name": "analysis",
  "policy_source": "workflow_spec",
  "pipeline_name": "default",
  "stage_count": 4,
  "violations": [],
  "metadata": {}
}
```

Common rules:

- `kind` must be `policy_decision`
- `version` must be `1`
- `decision_id` identifies the policy decision record
- `decision_type` identifies the policy checker family
- `subject` identifies the object being judged
- `accepted` is the software verdict
- `violations` explains rejected or warned conditions

## 4. Subjects

Supported subject kinds:

- `action_request`
- `artifact_contract`
- `evaluation_result`
- `workflow_spec`
- `runner_policy`

The `subject.type` field is optional and carries the more specific type when
available, for example:

- `request_approval`
- `publish_artifact`
- `workspace.file`
- `document.test_report`

## 5. Violations

Each violation uses:

```json
{
  "code": "artifact_not_expected",
  "message": "Artifact path does not match expected artifacts.",
  "field": "file_path",
  "severity": "error"
}
```

Supported severity values:

- `info`
- `warning`
- `error`

Only `error` violations should block executor progress by default.

## 6. Relationship to Other Schemas

`policy_decision` sits above the current protocol objects:

- [Action Request Schema v1](Schema-Action-Request-v1.md)
- [Artifact Contract Schema v1](Schema-Artifact-Contract-v1.md)
- [Evaluation Result Schema v1](Schema-Evaluation-Result-v1.md)
- [Workflow Spec Schema v1](Schema-Workflow-Spec-v1.md)

It does not replace them. It records the software verdict after those objects are
validated against workflow/runner policy.

## 7. Current Implementation Status

Schema v1 is now documented and represented by a minimal Pydantic contract in:

- `backend/services/policy_decision_contracts.py`

The first projection helper is also present:

- `project_policy_decision(...)`

It can project existing service-level decisions from:

- `ActionRequestPolicyDecision`
- `ArtifactContractPolicyDecision`
- `EvaluationResultPolicyDecision`

into canonical `policy_decision` payloads.

A stable read-model summary helper is also present:

- `summarize_policy_decision(...)`

It returns decision id, decision type, subject identity, accepted status, stage/policy
context, and violation counts without exposing the full payload.

A run-ledger payload helper is also present:

- `build_policy_decision_event_payload(...)`

It returns a `policy_decision_recorded` event payload containing the summary and,
optionally, the full canonical policy decision contract.

`ArtifactPublicationPolicyResult.to_payload()` already includes both the projected
`policy_decision` and this ledger-ready event payload after a `publish_artifact`
request is compiled and validated.

`EvaluationRollbackPolicyResult.to_payload()` also includes the projected
`policy_decision` and ledger-ready event payload after a failed evaluation result
is converted into a rollback action request and validated.

`EvaluationResultPolicyResult.to_payload()` includes the projected `policy_decision`
and ledger-ready event payload after an evaluation result itself is validated
against runner policy.

This schema is **not** yet wired into:

- run ledger persistence
- pipeline stage completion
- artifact acceptance
- approval queue resolution
- Monitor API read models

## 8. Expected Next Steps

The next likely follow-ups are:

1. emit policy_decision payloads into run ledger events
2. persist policy decisions when artifact/action/evaluation checks occur
3. expose policy decisions in Monitor read models
4. let executor gates consume accepted/rejected decisions explicitly
