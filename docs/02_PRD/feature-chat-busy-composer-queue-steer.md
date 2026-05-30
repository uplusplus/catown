# Feature Doc: Busy Chat Composer Queue / Steer / Abort

**Status**: Implemented (v1)  
**Date**: 2026-05-30  
**Owners**: BOSS + Catown Chat/UI + Runtime  
**Related**: ADR-001 (Queue Modes), ADR-005 (Chat Input), ADR-009 (Chat Runtime State)

---

## 1. Background

Catown already has queue-mode language in docs (`steer / followup / collect / steer-backlog`),
but the chat composer did not expose a reliable keyboard interaction for "the previous turn is
still running, and the user wants to prepare or inject the next instruction now".

The old composer behavior was too coarse:

- while the current turn was sending/streaming, the textarea was disabled;
- the user could not keep typing inside the composer;
- there was no local queue for follow-up drafts;
- there was no focused-composer `Esc` path to either steer a queued draft or abort the current run.

This created a gap between:

- the runtime concept of "user intervention while busy"; and
- the actual keyboard UX available inside the chat input.

---

## 2. Goal

Add a **composer-scoped busy interaction** for the case where:

- chat is still processing the previous input;
- focus is already inside the composer textarea.

The target UX is:

1. if the textarea has content, `Enter` queues that content locally instead of sending it;
2. if the local queue has data, one `Esc` steers the next queued draft into the chat flow;
3. double `Esc` aborts the current processing flow.

---

## 3. Scope

This feature is intentionally narrow.

It only applies when all of the following are true:

- the current chat turn is still busy;
- keyboard focus is inside the main chat composer textarea;
- the event is handled by the composer itself.

It does **not** redefine:

- global `Esc`;
- Files / Artifacts / Runtime / Monitor panels;
- project browser navigation;
- non-composer controls.

This keeps the logic focus-driven and avoids repeating the earlier cross-panel `Esc` regressions.

---

## 4. Final UX

### 4.1 Busy + `Enter`

When chat is busy and the focused composer has non-empty text:

- `Enter` queues the current draft into a local busy-input queue;
- the current draft is cleared from the textarea;
- the queued draft is **not** sent immediately;
- `Shift+Enter` still inserts a newline.

### 4.2 Busy + Single `Esc`

When chat is busy, the composer is focused, and the local queue has at least one queued draft:

- one `Esc` steers the oldest queued draft into the chat flow;
- steer is implemented as an **interrupt-and-replace** send path;
- the queued draft leaves the queue once steer is committed.

To preserve double-`Esc` abort, the first `Esc` uses a short disambiguation window:

- if no second `Esc` arrives inside the window, the steer send is executed;
- if a second `Esc` arrives inside the window, the pending steer is cancelled and abort wins.

### 4.3 Busy + Double `Esc`

When chat is busy and the composer is focused:

- two `Esc` presses inside the double-press window abort the current processing flow;
- abort does not auto-send queued drafts;
- queued drafts remain local.

### 4.4 After Busy Ends

Queued drafts are local staging data, not auto-followups.

When the current busy state ends:

- if the composer is empty and the local queue still has data, the next queued draft is restored
  into the textarea;
- this lets the user review, edit, or send it normally;
- queued drafts are not silently auto-sent.

---

## 5. Edge Rules

### 5.1 Suggestion / Mention Panels

If command suggestions, history suggestions, or the mention picker are open:

- `Esc` closes that local panel first;
- steer / abort logic does not run on the same keypress.

### 5.2 Empty Draft + Busy + `Enter`

If the composer draft is empty:

- `Enter` does nothing special while busy.

### 5.3 Busy Without Queue

If chat is busy but the queue is empty:

- a single `Esc` only arms the double-`Esc` abort window;
- no steer action is scheduled.

### 5.4 Button Scope

For v1:

- the textarea stays editable while busy;
- attachment / mention helper controls remain conservative and do not become busy-edit workflows;
- the feature is keyboard-first inside the textarea.

---

## 6. Runtime Semantics

### 6.1 Local Queue

The queue in this feature is frontend-local composer state.

It is used only for:

- staging follow-up drafts while the current turn is busy;
- selecting which staged draft can be sent with `steer`.

### 6.2 `steer`

For this feature, `steer` means:

- cancel the currently running interruptible chat task run(s) for the same chatroom; then
- submit the queued draft as the next user message with explicit `queue_mode=steer`.

This is a practical runtime approximation of "interrupt current work and inject the new user
instruction now".

### 6.3 `abort`

For this feature, `abort` means:

- cancel the current active interruptible chat task run for the same chatroom; and
- stop the current frontend stream.

It does not submit a new user message.

---

## 7. Non-Goals

- Do not add a new global `Esc` priority ladder.
- Do not change Files / project browser / monitor navigation semantics.
- Do not build a general-purpose queued-turn manager UI in this iteration.
- Do not silently auto-send queued drafts after completion.
- Do not broaden this to touch gestures or mobile system back in the same change.

---

## 8. Implementation Split

### Frontend

- keep the composer textarea editable while `sending`;
- add local queued-draft state in `ChatTab`;
- add busy-composer `Enter` and `Esc` handling only in the textarea keydown path;
- add a narrow status hint so queued-draft state is visible.

### Backend

- extend chat message requests with `queue_mode`;
- support `queue_mode=steer` in sync + streaming send paths;
- cancel currently running interruptible task runs in the same chatroom before processing the
  new steer message.

---

## 9. Why This Shape

This v1 deliberately chooses a reliable local interaction instead of a broad global shortcut
system:

- focus-driven behavior is easier to reason about;
- the composer is the only place where busy-turn text intervention is well-defined;
- local queue + steer + abort covers the real interruption workflow without reintroducing
  panel-level `Esc` bugs.
