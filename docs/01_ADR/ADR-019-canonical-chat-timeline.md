# ADR-019: Canonical Chat Timeline

**Status**: Accepted  
**Date**: 2026-05-15  
**Decision makers**: BOSS + Catown Runtime

## Context

Chat execution steps have been assembled from several client-side sources: SSE deltas,
runtime cards, websocket runtime-card broadcasts, task activity projections, and
optimistic placeholders. This made the frontend responsible for inferring execution
facts and reconciling partially ordered data.

That model is incorrect. A client may receive websocket and SSE messages out of order,
different clients can observe different intermediate states, and labels such as
`Valet -> LLM` / `LLM -> Valet` are not reliable identifiers. The result is confusing
timeline order and inconsistent step content.

## Decision

Catown will use a backend-owned canonical timeline for chat and task execution traces.
The frontend must render the timeline it receives and must not infer factual steps from
runtime cards or streaming content.

## Principles

1. Every event must represent a fact.
   Events such as LLM request creation, LLM response start/completion, tool call start,
   tool call completion, agent step start, and agent step completion are factual. The
   frontend must not invent these events from symptoms such as a content delta.

2. Every factual event must carry backend ordering.
   The minimum ordering fields are:
   - `sequence`: backend monotonic sequence for the owning scope
   - `occurred_at`: when the fact happened
   - `recorded_at`: when the backend recorded it

   Ordering uses `sequence` first. Timestamps are required as diagnostic and fallback
   data, but timestamps alone are not sufficient for ordering.

3. Runtime state is projected in the backend.
   The backend owns the current step list, current step state, parent/child
   relationships, LLM exchange phases, tool call/result ownership, and subagent/task
   relationships. Different frontend clients must receive the same state for the same
   timeline version.

4. Runtime cards are not a step source.
   Runtime cards may provide detail payloads for inspection, but they must not be
   interpreted by the frontend as chat steps. If old runtime-card data can be migrated
   into factual events, a migration script may do so. If the facts cannot be recovered
   reliably, old trace data is abandoned rather than guessed.

5. No compatibility fallback for guessed traces.
   Legacy frontend mapping from runtime cards/SSE deltas to `MessageStreamStep` will be
   removed. Pending UI may show a non-factual loading indicator, but factual traces come
   only from backend timeline projection.

## Canonical Step Shape

Timeline APIs return steps with this shape:

```json
{
  "id": "task-run:12:event:4",
  "scope": "task_run",
  "task_run_id": 12,
  "chatroom_id": 3,
  "sequence": 4,
  "occurred_at": "2026-05-15T10:12:30.000000",
  "recorded_at": "2026-05-15T10:12:30.000000",
  "event_type": "llm_request_created",
  "step_id": "llm:Valet:1",
  "parent_step_id": null,
  "actor": "Valet",
  "kind": "llm",
  "phase": "request",
  "state": "done",
  "facts": {},
  "summary": "Valet sent a request to the LLM.",
  "detail_content": "..."
}
```

## Consequences

- Frontend ordering is simple: render backend timeline steps by `sequence`.
- Client-side label matching and actor guessing are removed from the factual trace path.
- New trace correctness is enforced in backend tests.
- Old data is migrated only when factual ordering and ownership can be recovered.
