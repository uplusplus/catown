# ADR-025: LLM Upstream Error and Retry Policy

**Status**: Proposed  
**Date**: 2026-05-23  
**Decision makers**: BOSS + Catown Runtime

**Related**:
- [ADR-014: Network Monitor Semantics](./ADR-014-network-monitor-semantics.md)
- [ADR-020: Prompt Guidance vs Runtime Contracts](./ADR-020-prompt-vs-runtime-contracts.md)
- [ADR-023: Execution and Authorization Timing](./ADR-023-execution-authorization-timing.md)
- Archived legacy PRD, now superseded by numbered ADRs and retained in the
  [project wiki archive](https://github.com/uplusplus/catown/wiki/Archived-Project-Docs-2026-05-30)

## Context

Catown has two distinct kinds of abnormal LLM outcomes:

1. true upstream or transport failures, where the backend did not obtain a usable model
   result because the provider or network failed;
2. abnormal but successful-looking responses, where the provider call returned a response
   object but the content is unusable, such as an empty completion.

These categories must not be collapsed into one generic "empty response" bucket.

The incident that motivated this ADR was task run `#9` in WSL. The real backend logs and
database showed:

- `LLM API error with tools: Error code: 429 ... Concurrency limit exceeded for account, please retry later`
- `chat_with_tools()` raised, and the backend logged the failure with stacktrace
- the failure was a real upstream rate-limit condition, not a silent `None`/empty dict

This matters because recovery semantics differ:

- an upstream/transport failure may be retryable and must preserve the failure signal;
- an empty completion is a response-validation anomaly and must be logged as such;
- prompt logic and runtime state machines must not guess which one happened.

## Decision

Catown will separate LLM failures into three runtime classes and give each class a
different contract.

### 1. Upstream or connectivity failure

These are failures where the provider call did not produce a usable completion because
the request failed at the transport, gateway, provider, or timeout layer.

For synchronous tool chat:

- `LLMClient.chat_with_tools()` must raise an exception.
- It must not convert these failures into an empty dict or successful-looking response.
- It must log the failure and record a failed backend-LLM network event.

For streaming chat:

- `LLMClient.chat_stream()` may retry retryable upstream failures only before the
  first stream chunk or tool-call delta has been observed.
- If recovery succeeds before the first stream-visible event, the caller should see a
  normal stream and retry telemetry.
- If failure happens after any stream-visible output has started, or if the pre-output
  retry budget is exhausted, `LLMClient.chat_stream()` must surface the failure as a
  streamed `{"type": "error", ...}` event.
- It must log the failure and record a failed backend-LLM network event.

### 2. Abnormal response payload

These are cases where the provider call returned a response object, but the payload is
not a valid useful model answer for the current contract.

Example:

- `chat_with_tools()` receives a completion where `content is None` and there are no
  `tool_calls`.

This is not a transport failure. The call returned, so the client must preserve the
response shape, but it must also:

- log the anomaly;
- record a failed validation-style network event;
- keep the distinction visible in diagnostics.

### 3. Normal model refusal or non-retryable request error

These are cases where the upstream service is reachable and responds normally, but the
request is invalid, unauthorized, forbidden, or explicitly rejected.

These are not connectivity failures and should not enter automatic retry loops.

## Failure Taxonomy

### Retryable upstream/server/connectivity class

The following classes are considered retryable upstream failures when they match
provider semantics:

- `429` with retry-later meaning, including messages such as `please retry later`,
  `retry later`, or `concurrency limit exceeded`
- `408 Request Timeout`
- `500 Internal Server Error`
- `502 Bad Gateway`
- `503 Service Unavailable`
- `504 Gateway Timeout`
- provider-edge `52x` style failures when surfaced by the SDK/message text
- timeout exceptions
- connection establishment failures
- DNS resolution failures
- TLS / SSL handshake failures
- protocol-level disconnect or stream interruption failures before a valid terminal model
  result is available

### Non-retryable request or auth class

The following classes are not automatically retried:

- `400 Bad Request`
- `401 Unauthorized`
- `403 Forbidden`
- `404 Not Found`
- `422 Unprocessable Entity`
- explicit model refusal that is returned as a valid model response
- local validation/configuration bugs that make the request invalid

### Abnormal but non-transport response class

The following are logged separately and are not treated as transport exceptions by
themselves:

- empty completion with no content and no tool calls
- unexpected but still returned response object types that can be stringified

## Retry Contract

Automatic retry applies to retryable upstream failures for:

- synchronous `chat_with_tools()`
- `chat_stream()` only before any stream-visible output has been emitted

Current contract:

- total retry budget: `300` seconds
- backoff: exponential
- nominal sequence: `1s`, `2s`, `4s`, `8s`, ...
- per-sleep cap: `60s`
- retry only when the next delay still fits inside the total retry budget

This means the runtime retries to absorb transient provider pressure, but still returns a
real failure once the retry budget is exhausted.

The retry policy is intentionally narrower than "retry every 429." Only retry-later style
rate limits are retried automatically. This avoids blindly retrying permanent quota or
billing failures.

For streaming chat, the retry boundary is stricter:

- before first visible stream output: automatic retry is allowed
- after first visible stream output: automatic retry is forbidden

This preserves stream correctness by avoiding duplicated content chunks, repeated
tool-call deltas, or ambiguous partial-output recovery semantics.

## Interface Contract

### `LLMClient.chat_with_tools()`

Contract:

- returns a dict when a response object was obtained and parsed
- raises on true upstream/transport/provider-call failure
- does not hide those failures behind an empty dict
- may return a dict with `content=None` and `tool_calls=None` only when the upstream call
  actually returned such a response object

Interpretation:

- `raise Exception(...)` means the tool-chat request failed at the upstream/service or
  transport layer
- `{"content": None, "tool_calls": None, ...}` means the provider returned an abnormal
  payload, not that the connection silently vanished

### `LLMClient.chat_stream()`

Contract:

- yields stream progress events during normal execution
- may transparently retry retryable upstream failures before first stream-visible output
- must not automatically retry once any content chunk, tool-call delta, or equivalent
  stream-visible output has been emitted
- yields `{"type": "error", ...}` when stream creation or streaming fails
- does not require the caller to infer transport failure from a missing final chunk

### Logging and Monitor Requirements

True upstream or connectivity failures must produce:

- an error log with failure type and traceback/context
- a failed backend-LLM network event

Retry attempts must produce:

- a warning log before sleep
- a failed backend-LLM network event containing retry metadata

Retry success after earlier failures should produce:

- an info log indicating eventual recovery

Abnormal non-transport payloads such as empty completion must produce:

- an error log that names the validation anomaly
- a failed backend-LLM network event with response-validation metadata

## Rationale

### Why not map everything to "empty response"?

Because that destroys the operational meaning of the failure.

- A `429 please retry later` means the provider explicitly asked for backoff.
- A timeout means there may be no provider result at all.
- An empty completion means the provider returned a syntactically valid response object
  with unusable content.

The runtime, monitor, logs, and state machine need these distinctions to make correct
retry and failure decisions.

### Why allow stream retry only before first output?

Before the first visible chunk, the stream has not yet exposed user-visible output or
tool-progress state, so retrying is semantically close to retrying a normal request.

After visible stream output begins, automatic retry becomes much riskier:

- repeated content can be emitted to the UI
- tool-call deltas can be duplicated or interleaved
- it becomes unclear whether a partial result should be preserved or discarded

So the runtime should only auto-retry the pre-output segment of a stream.

### Why retry only a subset of `429`?

Not all `429` responses are transient.

Examples:

- transient concurrency pressure is often worth retrying
- hard quota exhaustion or billing problems are usually not fixed by waiting a few
  seconds

The runtime should only auto-retry when the response semantics indicate "retry later,"
not when the error is likely permanent until operator intervention.

## Consequences

1. State-machine code should treat `chat_with_tools()` exceptions as real upstream
   failures, not as empty-response branches.
2. Empty completion handling should remain a separate diagnostic path so it can be
   investigated without lying about the transport outcome.
3. The monitor view can distinguish retryable provider pressure from terminal failures by
   reading network-event metadata.
4. The older PRD wording of `retry 3 times (1s/2s/4s)` is superseded by the runtime
   contract in this ADR for retryable upstream failures, with the extra pre-output
   restriction on streaming.

## Non-goals

- This ADR does not require automatic retry for every LLM endpoint or every post-output
  streaming failure path.
- This ADR does not define user-facing copy for every provider error.
- This ADR does not treat model refusal as a transport failure.
- This ADR does not add silent fallback behavior that masks upstream failures from the
  runtime.

## Follow-up Work

- Align any remaining PRD/runtime references that still describe the older fixed
  `3`-retry policy.
- Expand retry classification helpers if the SDK starts surfacing richer typed transport
  exceptions.
- Add regression tests whenever a new provider or SDK variant introduces a different
  error shape for retryable upstream failures.
