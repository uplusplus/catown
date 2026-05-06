# Action Request Schema v1

Updated: 2026-05-06

This document defines the first bounded `action_request` contract for Catown.

Its purpose is to prevent worker/orchestration agents from directly mutating runtime state while still letting them ask the local software executor to perform meaningful work.

## 1. Scope

Schema v1 covers agent-emitted requests that fit one of these categories:

- `use_tool`
- `ask_agent`
- `request_approval`
- `report_blocker`
- `suggest_rollback`
- `publish_artifact`

It does **not** yet cover:

- child-handle control requests as first-class agent outputs
- workflow-spec generation
- evaluation-rubric generation
- final human approval payloads

## 2. Design Goals

The contract is intentionally:

- bounded
- JSON-compatible
- versioned
- easy for LLMs to emit
- easy for software to validate

The system should treat these requests as:

- semantic outputs from LLM agents
- that must be validated by software
- before they can affect runtime state

## 3. Envelope

Every v1 action request uses the same top-level envelope:

```json
{
  "kind": "action_request",
  "version": 1,
  "request_id": "req-123",
  "type": "use_tool",
  "source": {
    "agent_name": "Developer",
    "agent_type": "developer",
    "stage_name": "development",
    "task_run_id": 12,
    "pipeline_run_id": 5,
    "pipeline_stage_id": 9,
    "turn_index": 3
  },
  "summary": "Read the API route file.",
  "metadata": {},
  "payload": {}
}
```

Common rules:

- `kind` must be `action_request`
- `version` must be `1`
- `request_id` must be unique at least within one run
- `type` selects the concrete payload schema
- `source` identifies where the request came from
- `payload` must match the concrete request kind

## 4. Request Kinds

### 4.1 `use_tool`

Purpose:

- ask the software executor to run one tool call

Payload:

```json
{
  "tool_name": "read_file",
  "arguments": {
    "file_path": "backend/routes/api.py"
  },
  "reason": "Need to inspect the current endpoint wiring."
}
```

### 4.2 `ask_agent`

Purpose:

- ask another role for clarification, handoff, or delegated input

Payload:

```json
{
  "target_agent_name": "Architect",
  "message": "Please confirm the auth boundary for this endpoint.",
  "attached_artifact_refs": [
    "artifact:tech-spec.md"
  ]
}
```

### 4.3 `request_approval`

Purpose:

- ask the runtime to create an approval or escalation item

Payload:

```json
{
  "queue_kind": "approval",
  "target_kind": "tool",
  "target_name": "delete_file",
  "reason": "Deleting generated fixtures needs explicit confirmation.",
  "resume_supported": true,
  "request_payload": {
    "tool_name": "delete_file",
    "arguments": {
      "file_path": "tmp.txt"
    }
  }
}
```

### 4.4 `report_blocker`

Purpose:

- report a runtime-relevant blocker without directly forcing rollback

Payload:

```json
{
  "severity": "blocker",
  "reason": "The test report found unauthorized access to protected endpoints.",
  "blocker_code": "auth-missing-001",
  "suggested_resolution": "Return to development and add auth checks."
}
```

### 4.5 `suggest_rollback`

Purpose:

- suggest a rollback target to the runtime

Payload:

```json
{
  "target_stage_name": "development",
  "reason": "Testing found blocker-class defects.",
  "blocker_code": "auth-missing-001"
}
```

### 4.6 `publish_artifact`

Purpose:

- ask the runtime to register and persist a produced artifact

Payload:

```json
{
  "artifact_type": "document.prd",
  "title": "PRD draft",
  "summary": "Structured product requirements.",
  "file_path": "PRD.md",
  "content_markdown": null,
  "content_json": {
    "stories": 5,
    "acceptance_criteria": 12
  }
}
```

## 5. Runtime Interpretation Rules

This schema does not authorize an agent to directly change runtime state.

Instead:

- the agent emits an `action_request`
- the local software executor validates it
- the executor decides whether the request is legal in the current runtime state
- only then may runtime state transition

Examples:

- `suggest_rollback` does not itself rollback a stage
- `request_approval` does not itself approve anything
- `publish_artifact` does not itself make an artifact canonical unless the executor accepts it

## 6. Relationship to OpenAI Protocol

This schema sits **above** the OpenAI protocol.

OpenAI-compatible request/response formats still handle:

- messages
- assistant output
- tool call envelopes
- streaming

Catown's `action_request` schema handles:

- runtime-facing intent
- bounded orchestration signals
- artifact publication
- approval and blocker routing

So:

- OpenAI protocol defines how Catown talks to the model
- Catown action-request schema defines how agent intent becomes executor input

## 7. Current Implementation Status

Schema v1 is now documented and represented by a minimal Pydantic contract in:

- `backend/services/action_request_contracts.py`

The first compatibility bridge has also been added for blocked-tool approval payloads:

- `backend/services/approval_replay.py`
  now includes a helper that compiles blocked-tool approval intent into `request_approval`
  action-request form without yet changing queue persistence semantics.

The second compatibility bridge has also been added for pipeline-gate approval payloads:

- `backend/services/approval_replay.py`
  now includes a helper that compiles pipeline-gate approval intent into the same
  `request_approval` action-request form, again without yet changing queue persistence semantics.

This schema is **not** yet wired into:

- pipeline engine execution
- orchestration runtime execution
- approval queue creation
- artifact publication flow

It is currently a draft contract intended to guide the next refactor.

## 8. Expected Next Steps

The next likely follow-ups are:

1. map existing implicit request payloads into schema v1
2. define `artifact contract schema v1`
3. compile workflow specs so action requests can be validated against role/stage policy
4. add runtime adapters that convert current event payloads into typed action requests
