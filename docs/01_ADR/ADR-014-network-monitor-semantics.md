# ADR-014: Network Monitor Semantics

**Status**: Accepted  
**Date**: 2026-04-24  
**Decision Owner**: BOSS

---

## Context

The standalone Monitor `Network` page is used for debugging real transport behavior across several communication boundaries:

- `frontend <-> backend`
- `backend <-> LLM`
- `backend -> other`
- `frontend -> other`

As the feature expanded, two semantic problems appeared:

1. Aggregated records could blur request-side and response-side data.
2. Internal synthetic events such as `llm_wait`, `tool_wait`, and `: ping` could be mistaken for provider-originated raw response data.

We need a stable rule that keeps the default view readable while preserving a path toward deeper stream-level debugging.

---

## Decision

### 1. Default mode uses aggregated request records

The default `Network` page should show one record per request / session-level exchange.

This keeps the page readable and avoids exploding the event count for streaming protocols such as SSE and LLM delta streams.

### 2. Aggregated records must keep directions explicit

Every aggregated record must clearly expose:

- `request_direction`
- `response_direction`

These are not inferred only by visual placement. They are part of the event semantics.

Examples:

- `Frontend (home) -> Backend API`
- `Backend API -> Frontend (home)`
- `developer -> LLM (api.openai.com)`
- `LLM (api.openai.com) -> developer`

### 3. Request-side data and response-side data must not be mixed semantically

For aggregated records:

- `raw_request` contains only outbound payload for that direction pair
- `raw_response` contains only inbound payload for that direction pair

If a field was sent from backend to LLM, it belongs to `raw_request` of the `backend <-> LLM` record, not to `raw_response`.

### 4. Internal control events are not provider raw response

Synthetic control events such as:

- `llm_wait`
- `tool_wait`
- SSE `: ping`

are internal transport / UI support signals. They are not OpenAI or provider-originated response payloads.

They should be:

- hidden by default in the Monitor page, or
- explicitly labeled as internal control events

They must not be presented as if they were provider-native raw response data.

### 5. Non-aggregated mode is a future deep-debugging mode

When the Monitor later exposes a stream-level view, it should:

- stop aggregating chunk events
- assign the same `stream_id` / `flow_id` to all events from one stream
- visually group the same stream with a stable color

This mode is for deep investigation, not the default everyday debugging view.

---

## Rationale

### Why not make the default view fully unaggregated?

Because streaming protocols produce too many low-level events:

- a single SSE response may generate many frames
- a single LLM call may emit many delta chunks

This makes the default page noisy, harder to scan, and more expensive to retain.

### Why require explicit directions?

Because debugging transport requires a precise answer to:

- who sent this?
- who received this?
- was this request-side input or response-side output?

Without explicit directions, nested content such as runtime cards or prompt material becomes easy to misread.

---

## Implementation Guidance

### Required fields for aggregated monitor records

- `from_entity`
- `to_entity`
- `request_direction`
- `response_direction`
- `raw_request`
- `raw_response`

### Default hidden noise

The Monitor page should hide by default:

- Monitor page self-requests
- monitor SSE maintenance traffic
- frontend/backend heartbeat-only traffic
- `/api/frontend-meta` version-guard traffic

### Future work

- add `flow_id` / `stream_id`
- add a dedicated unaggregated stream view
- optionally distinguish `provider_raw_response` from `synthetic_transport_events`

---

## Consequences

### Positive

- The default monitor view remains readable.
- Request and response semantics become much harder to misinterpret.
- The project now has a clear migration path toward chunk-level stream debugging.

### Negative

- Aggregated records still hide intra-stream timing details unless a future stream view is added.
- Some payloads, especially SSE responses carrying runtime cards, may still contain nested higher-level data even when direction semantics are correct.
