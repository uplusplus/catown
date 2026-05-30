# ADR-033: Approval Notification Single Source of Truth

## Status

Proposed

## Context

The chat runtime has two independent paths that notify the frontend about pending tool approvals:

1. **SSE `approval_pending` event** — emitted directly by `stream_turn_executor.py` when a tool call is blocked during an LLM turn. Carries a full payload (`tool`, `blocked_kind`, `blocked_reason`, etc.) and the frontend renders an approval dialog directly from this event.

2. **`ApprovalQueueItem` (DB)** — created by `approval_queue.py` as a persistent record of the approval request. Supports full lifecycle: `pending → approved/rejected/expired`, TTL auto-expiry, resolution lease for concurrency control, and audit trail.

These two paths evolved independently:
- The SSE event is a **streaming control signal** from the executor layer: "this turn is blocked, stop waiting."
- The ApprovalQueueItem is a **business state entity** from the service layer: "there is a request that needs human approval."

Because the frontend listens to both independently, it renders two overlapping approval dialogs when a tool is blocked.

### Affected files

- `backend/services/stream_turn_executor.py` — yields `approval_pending` SSE event
- `backend/services/approval_queue.py` — creates/persists `ApprovalQueueItem`
- `backend/services/tool_governance.py` — classifies tool results as blocked
- `backend/services/stream_transport.py` — renders `approval_pending` as SSE chunk
- `backend/services/orchestration_stream_runner.py` — forwards `approval_pending` to client
- `frontend/src/components/ChatTab.tsx` — consumes both signals

## Decision

**`ApprovalQueueItem` is the single source of truth for approval state. The SSE stream must not carry independent approval payloads.**

### Changes

#### 1. SSE stream: replace `approval_pending` with lightweight notification

`stream_turn_executor.py` currently yields:

```python
yield {
    "type": "approval_pending",
    "agent_name": agent_name,
    "tool": blocked_tool_result.tool_name,
    "blocked_kind": blocked_tool_result.blocked_kind,
    "blocked_reason": blocked_tool_result.blocked_reason,
    ...
}
```

Replace with:

```python
yield {
    "type": "approval_queue_updated",
    "queue_item_id": queue_item.id,
    "queue_item_public_id": queue_item.public_id,
}
```

The SSE event no longer carries the full approval payload. It is purely a **signal** that the queue has changed.

#### 2. `execute_tool` callback: create `ApprovalQueueItem` before yielding

Ensure the `execute_tool` callback in `routes/api.py` creates the `ApprovalQueueItem` **before** the stream executor yields the notification. The queue item ID must be available so the SSE notification can reference it.

#### 3. Frontend: single approval source

`ChatTab.tsx` must:
- On receiving `approval_queue_updated` SSE event → call `api.getApprovalQueue()` (or use the pushed item ID) to fetch the queue item.
- Render the approval dialog **only** from `ApprovalQueueItem` data.
- Remove any direct rendering of `approval_pending` SSE payloads as approval dialogs.

#### 4. Retain `approval_pending` as internal-only (optional)

If the executor layer still needs to signal "turn blocked" for internal bookkeeping (e.g., `orchestration_stream_runner.py` uses it to `return` early), keep the event type but mark it as `internal: true` and strip it before sending to the client.

## Consequences

### Positive

- **No duplicate dialogs.** Single signal path = single UI.
- **Resilient to reconnects.** If the SSE connection drops and reconnects, the frontend can query the approval queue to recover pending approvals. The current `approval_pending` event is lost on disconnect.
- **Consistent lifecycle.** Approve/reject/expire all operate on one entity. No synchronization between SSE state and DB state.
- **Auditability.** All approval state transitions are in the DB, not scattered across ephemeral SSE events.
- **Simpler frontend.** One code path to render approval UI instead of two.

### Negative

- **Slight latency increase.** The frontend needs to fetch the queue item after receiving the SSE notification, adding one round-trip. Mitigated by including the `queue_item_id` in the SSE event so the fetch is a targeted lookup, not a full list query.
- **`execute_tool` must ensure DB write before SSE yield.** The ordering constraint (create queue item → then yield notification) must be enforced. If the stream executor yields before the queue item is created, the frontend will query and find nothing.

### Neutral

- The `stream_turn_executor.py` still needs to know that a tool was blocked (to break the turn loop). This internal control flow is unchanged; only the **outbound SSE payload** changes.
