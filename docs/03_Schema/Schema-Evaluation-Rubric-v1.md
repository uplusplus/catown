# Evaluation Rubric Schema v1

Updated: 2026-05-09

This document defines the first bounded `evaluation_rubric` contract for Catown.

Its purpose is to carry quality and semantic judgment criteria without making the local software
executor pretend to be the semantic judge.

## 1. Scope

Schema v1 covers:

- rubric identity
- workflow/stage/agent/artifact applicability
- criteria
- evaluator ownership
- scoring scale
- optional acceptance thresholds
- guidance text for ambiguous judgments

It does **not** yet cover:

- persisted rubric templates
- rubric result records
- rubric-driven gate enforcement
- pairwise or multi-reviewer consensus rules

## 2. Design Goals

The contract is intentionally:

- JSON-compatible
- versioned
- easy for LLMs and humans to emit
- easy for software to validate
- suitable for both structured and taste-heavy judgment

## 3. Envelope

Every v1 evaluation rubric uses the same top-level envelope:

```json
{
  "kind": "evaluation_rubric",
  "version": 1,
  "rubric_id": "rubric-analysis-prd",
  "name": "PRD completeness rubric",
  "domain": "software_delivery",
  "applies_to": {
    "workflow_id": "default",
    "stage_id": "analysis",
    "agent_type": "analyst",
    "artifact_type": "document.prd"
  },
  "criteria": [],
  "guidance": "",
  "metadata": {}
}
```

Common rules:

- `kind` must be `evaluation_rubric`
- `version` must be `1`
- `rubric_id` identifies this rubric
- `criteria` must contain at least one criterion
- criterion ids must be unique within one rubric

## 4. Criterion Shape

Each criterion carries:

- `criterion_id`
- `name`
- `description`
- `scale`
- `evaluator`
- `required`
- `weight`
- `acceptance_threshold`
- `guidance`
- `metadata`

Example:

```json
{
  "criterion_id": "ambiguity",
  "name": "Ambiguity reduction",
  "description": "Important assumptions should be explicit.",
  "scale": "score_1_5",
  "evaluator": "hybrid",
  "required": true,
  "weight": 2,
  "acceptance_threshold": 4,
  "guidance": "Prefer concrete acceptance criteria over broad goals."
}
```

Supported scales:

- `pass_fail`
- `score_1_5`
- `score_1_10`
- `qualitative`

Supported evaluator owners:

- `agent`
- `human`
- `software`
- `hybrid`

Threshold rules:

- `pass_fail` threshold must be between `0` and `1`
- `score_1_5` threshold must be between `1` and `5`
- `score_1_10` threshold must be between `1` and `10`
- `qualitative` criteria cannot define a numeric threshold

## 5. Runtime Interpretation Rules

This schema does not authorize a rubric to directly change runtime state.

Instead:

- the rubric describes quality criteria
- an agent, human, or software evaluator applies the rubric
- the result is recorded as `evaluation_result`
- the software records or routes the result
- runtime policy decides whether any gate, rollback, or approval action follows

So:

- `evaluation_rubric` describes how to judge quality
- `evaluation_result` records one actual judgment
- `action_request` expresses what an agent wants done after judgment
- runtime policy decides whether that request may affect state

## 6. Relationship to Workflow Specs and Artifacts

The intended layering is:

- `workflow_spec`
  defines where evaluation should happen
- `workflow_spec.stages[].evaluation.rubric_refs`
  references which rubrics should be used for a stage
- `artifact_contract`
  describes what was produced
- `evaluation_rubric`
  describes how that output should be judged
- `evaluation_result`
  records how one output was judged
- `action_request`
  asks the executor to act on the judgment

This keeps workflow structure, produced objects, semantic criteria, semantic results, and runtime
intent separate.

## 7. Current Implementation Status

Schema v1 is now documented and represented by a minimal Pydantic contract in:

- `backend/services/evaluation_rubric_contracts.py`

This schema is **not** yet wired into:

- pipeline stage gates
- artifact acceptance
- review result persistence
- workflow spec embedding

It is currently a draft contract intended to guide the next refactor.

## 8. Expected Next Steps

The next likely follow-ups are:

1. attach optional rubric refs to workflow stages or artifact contracts
2. map existing tester/release conclusions into rubric result records
3. decide which rubric outcomes can suggest action requests and which require final approval
4. connect evaluation results to durable review persistence
