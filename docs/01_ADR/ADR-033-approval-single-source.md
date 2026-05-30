# ADR-033: Approval Notification Single Source of Truth

## Status

Accepted

## Context

The chat runtime originally had two independent ways to surface pending tool approvals to the frontend:

1. `approval_pending` / `approval_queue_updated` events on the request-scoped SSE stream.
2. Persistent `ApprovalQueueItem` rows in the database.

Those two paths evolved for different purposes:

- The SSE event is a streaming control signal from the executor layer: "this turn is blocked, stop waiting."
- The `ApprovalQueueItem` is the business-state entity that owns the approval lifecycle: pending, approved, rejected, expired, leases, TTL, and auditability.

When the frontend treated both paths as approval UI sources, it produced duplicate dialogs, stale cards, and delayed refresh behavior when background or delegated approvals appeared outside the currently active SSE turn.

## Decision

`ApprovalQueueItem` is the single source of truth for approval state. Streaming transports may notify the frontend that approval state changed, but they must not become an independent approval-state model.

## Changes

### 1. Keep SSE lightweight and request-scoped

`stream_turn_executor.py` emits:

```python
yield {
    "type": "approval_queue_updated",
    "queue_item_id": queue_item.id,
    "queue_item_public_id": queue_item.public_id,
}
```

This SSE event remains a lightweight signal for the in-flight turn. It is not the canonical approval UI payload, and Chat should not depend on it to build approval cards.

### 2. Queue item must exist before notification

The queue item must be written before any transport notifies the frontend. This preserves the invariant that every notification can be resolved back to a durable `ApprovalQueueItem`.

### 3. WebSocket is the short-term notification rail for backend-initiated approval changes

The existing `/ws` channel already supports chatroom room joins and generic topic subscriptions. Short-term, approval changes should fan out there as `approval_queue_item_changed` notifications:

- Broadcast after queue item create / resolve / expire.
- Fan out to the owning chatroom room when `chatroom_id` exists so Chat can react immediately.
- Fan out to the `monitor` topic so Monitor can refresh from the same signal.
- Keep the payload signal-oriented: `queue_item_id`, `chatroom_id`, `task_run_id`, `status`, `reason`, `captured_at`, plus lightweight identity fields.

The notification payload is not the approval-state authority; it only tells the frontend what durable row to refresh.

### 4. Chat consumes approval changes from durable queue data

`ChatTab.tsx` must:

- React to `/ws` `approval_queue_item_changed` notifications by refreshing the targeted queue item when possible.
- Render approval UI only from `ApprovalQueueItem` data, including the `approval_queue_items` already embedded in `task_run_update.detail`.
- Avoid using the SSE `approval_queue_updated` event as the primary source for approval-card state.

### 5. Monitor refreshes from the same notification family

`MonitorTab.tsx` should react to the same `approval_queue_item_changed` notification and refresh the approval queue snapshot when the approvals page is active. `monitor_task_run.detail.approval_queue_items` remains a valid enriched projection, but it is no longer the only fast path for surfacing approval changes.

### 6. Internal executor behavior is unchanged

The executor still needs to know a tool was blocked so it can stop the current turn and preserve continuation state. This ADR changes the outbound approval-notification model, not the executor's internal control flow.

## Consequences

### Positive

- No duplicate approval dialogs.
- Background and delegated approvals no longer depend on an active SSE turn to become visible.
- Approval lifecycle stays anchored to one durable entity.
- Reconnect and recovery behavior improves because the frontend can always refresh from the queue item or queue snapshot.
- Chat and Monitor can converge on the same notification family while still reading truth from the database-backed APIs.

### Negative

- The frontend still performs a follow-up fetch after notification in some cases.
- Two transports remain in the short term: SSE for request-scoped live turn streaming, WebSocket for cross-cutting approval change notifications.
- Ordering still matters: queue row first, notification second.

### Neutral

- This ADR does not require a full transport unification yet. It only defines which transport is allowed to carry approval-state truth, and that remains the persistent queue model.
