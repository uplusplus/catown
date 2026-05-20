# ADR-021: User-Visible Runtime Step Projection

**Status**: Proposed  
**Date**: 2026-05-20  
**Decision makers**: BOSS + Catown Runtime

## Context

Catown currently exposes chat/test card steps by projecting runtime events into
`live`, `done`, or `error` rows. This projection is too close to the raw event log.
Events such as `agent_turn_started`, `llm_request_created`, `tool_call_started`, and
`scheduler_step_dispatched` are factual, but they are not always user-visible work
steps.

The result is confusing: several historical "start" events can remain `live` at the
same time, even though the user needs to understand what the agent is actually doing
now. A frontend rule such as "only keep the last live step" would make the UI look
tidier, but it would hide the real runtime semantics instead of fixing them.

This ADR refines ADR-018 and ADR-019: the canonical timeline remains backend-owned,
but chat-facing steps must be projected as user-visible runtime work units, not raw
event rows.

## Decision

Catown will distinguish between:

- **Runtime events**: immutable facts recorded for audit, ordering, recovery, and
  monitor diagnostics.
- **User-visible steps**: a backend projection of meaningful work units that explain
  the agent's current and completed work in chat/test cards.

Chat-facing steps must tell the user what work happened or is happening. They must not
blindly mark every start/request/dispatched event as `live`.

Top-level chat/test card steps should usually have at most one `live` row. When real
parallel work exists, that row must be a truthful aggregate work unit whose detail
contains the active child/scheduler/subagent statuses. The aggregate is not a UI trick:
it represents the actual runtime state "parallel work is running/waiting".

Raw events and full per-handle diagnostics remain available in detail payloads,
monitor views, process trees, and facts/refs.

## Step Model

User-visible steps are work units such as:

- user request received
- agent turn started
- model request/response phase
- tool call running/completed
- approval/timeout wait
- scheduler planning
- parallel agent work running
- delegated/subagent result received
- final response completed

Each step has:

- stable identity
- label and summary for chat
- state: `live`, `done`, or `error`
- optional actor/tool/run refs
- detail content with raw events or runtime facts
- optional children or aggregate facts for parallel work

The projection owns step lifecycle. A step becomes `done` when a later factual phase
shows that work completed or the run advanced to a successor phase. A start event alone
does not make a historical row stay `live`.

## Example 1: Single Agent LLM and Tool Call

Runtime facts:

1. user message saved
2. Valet agent turn started
3. Valet LLM request created
4. LLM response started
5. LLM response completed with tool calls
6. Valet tool call started: `run_shell`
7. tool round recorded
8. Valet second LLM request created
9. LLM final response completed
10. agent turn completed

User-visible projection while the tool is running:

```text
done  User request received
done  Valet started
done  Valet asked the model
done  Model requested tool use
live  Valet running run_shell
```

After the tool returns:

```text
done  User request received
done  Valet started
done  Valet asked the model
done  Model requested tool use
done  Valet ran run_shell
live  Valet composing final response
```

After completion:

```text
done  User request received
done  Valet started
done  Valet asked the model
done  Model requested tool use
done  Valet ran run_shell
done  Valet responded
```

The raw `llm_request_created` and `tool_call_started` events remain facts, but their
chat-facing step state is derived from the work lifecycle, not from event name alone.

## Example 2: Coordinator Dispatches Parallel Work

Runtime facts:

1. Valet starts a coordinated task
2. scheduler plan created: Backend blocking, Frontend sidecar, Tests sidecar
3. Backend step dispatched
4. Frontend step dispatched
5. Tests step dispatched or waiting on Backend
6. Backend completes
7. Frontend is still running
8. Tests is waiting/running
9. all child work completes
10. Valet resumes and summarizes

User-visible projection during parallel work:

```text
done  Valet planned 3 work items
live  Parallel agent work running
      Backend: done
      Frontend: running
      Tests: waiting on Backend
```

After all parallel work completes:

```text
done  Valet planned 3 work items
done  Parallel agent work completed
live  Valet summarizing results
```

This is a case where the top-level projection has one `live` step while truthfully
representing multiple active lower-level runtime handles. The monitor and detail views
may still show each scheduler/subagent lifecycle entry independently.

## Special Cases

### True Parallelism

Parallel scheduler steps and subagents are real. Chat/test cards should represent them
with one aggregate top-level `live` work unit plus child status details. Monitor views
may show each child step as independently running.

### Approval, Timeout, and Blocking

The current public state shape has only `live`, `done`, and `error`. Until a distinct
`blocked` state exists, approval/timeout waits should be represented as one live
waiting step, with detail explaining the blocked reason and available actions.

### Terminal Runs

When a task run is `completed`, `failed`, or `cancelled`, no chat-facing step may remain
`live`. The final visible state must be `done` or `error`.

### Incomplete or Interrupted Runs

If a run is still marked `running` but events are incomplete, the projection may create
a current synthetic work unit such as `Runtime interrupted` or `Waiting for recovery`.
This is preferable to leaving old start events as live.

### Event Timeline vs Step Projection

The event timeline is allowed to contain many start/completion facts. The step
projection is not a one-row-per-event rendering. It groups and settles events into the
work units users can reason about.

## Implementation Plan

1. Introduce a shared backend step projection helper.
   Create a service that consumes ordered `TaskRunEvent`s, task status, checkpoint
   scheduler runtime, subagent lifecycle, approval queue state, and runtime cards where
   applicable. It should emit user-visible work units and keep raw event refs in detail.

2. Replace event-name-based live state rules.
   Remove the rule that `LIVE_EVENT_TYPES` automatically stay live. Start-like events
   should open or update a work unit; later phases should settle it.

3. Model serial work lifecycles.
   Add pair/group logic for agent turns, LLM exchanges, tool calls/tool rounds, and
   final responses. For serial work, only the active phase should be live.

4. Model parallel aggregate lifecycles.
   Use scheduler runtime and subagent lifecycle to derive aggregate steps such as
   `Parallel agent work running`, `Waiting for delegated work`, or `Parallel work
   completed`. Include child statuses in facts/detail rather than flattening them as
   multiple top-level live rows.

5. Align `task_activity_projection` and `chat_timeline_projection`.
   Both projections should consume the same work-unit builder so chat inline cards,
   message traces, and task run traces agree about states and current step.

6. Update frontend assumptions after backend projection is fixed.
   Keep the frontend as a renderer of backend steps. The frontend may fold long history
   for readability, but it must not infer factual running state or repair projection
   mistakes.

7. Add regression tests.
   Cover:
   - single agent LLM/tool lifecycle
   - terminal run has no live steps
   - scheduler parallel work has one aggregate live top-level step with child statuses
   - approval/timeout wait projects one live waiting step
   - interrupted running run does not leave multiple stale start events live

## Consequences

- Chat steps become more truthful and less noisy.
- Monitor can remain event-rich while chat/test cards stay work-unit oriented.
- Backend projections become more complex, but frontend state repair becomes simpler.
- Historical events remain auditable without leaking raw event semantics into the chat
  UX.

## Non-Goals

- Do not hide true parallel work.
- Do not move raw event diagnostics out of monitor.
- Do not make the frontend responsible for settling backend step states.
- Do not treat "one live row" as a cosmetic invariant detached from runtime truth.
