# Chat Handoff Tail-Mention Feature Doc

**Version**: v0.1  
**Date**: 2026-05-21  
**Status**: Implemented  
**Author**: Codex

**Related**:
- [ADR-020: Prompt Guidance vs Runtime Contracts](../01_ADR/ADR-020-prompt-vs-runtime-contracts.md)

---

## 1. Background

Catown currently supports lightweight agent-to-agent chat handoff by parsing `@agent`
mentions from agent-generated chat messages.

The original runtime rule was strict:

- a handoff was triggered only when the message started with `@agent`;
- the trigger check only looked at the beginning of the whole message;
- once triggered, the runtime scanned the whole message for all mentions.

This rule was safe, but it created a usability mismatch with how agents naturally write.
In practice, an agent often:

1. explains context first;
2. summarizes findings;
3. assigns work at the end.

That means a message can clearly intend to hand off work to `@developer`, while still
failing the runtime trigger because the mention is not at the very beginning of the full
message.

This exact failure happened in chat `#5`:

- Valet's final message contained `@developer`;
- the mention appeared deep in the body, not at the start of the full message;
- no new Developer turn was created;
- from the user's perspective, it looked like "Valet asked Developer, but Developer did
  not respond."

The root problem was not missing agent capability. It was a mismatch between:

- human/agent writing habits; and
- a rigid runtime handoff trigger.

---

## 2. Goal

Allow agents to provide background first and assign work at the end, while keeping
handoff routing predictable and low-risk.

Specifically, the system should support:

- narrative explanation in the main body;
- explicit handoff instructions in the final section;
- stable runtime behavior with low accidental routing risk.

---

## 3. Non-Goals

- Do not turn every `@agent` mention anywhere in the message into a handoff.
- Do not infer intent from vague prose.
- Do not use semantic LLM interpretation to decide whether a mention should trigger.
- Do not make the frontend guess whether a message was meant as a routing instruction.

---

## 4. Problem Analysis

### 4.1 Old Rule

Original rule in `assistant_handoff.py`:

- trigger only if the full message begins with `@agent`;
- extract mentions from the whole message body;
- synthesize follow-up user messages like `@developer ...`.

### 4.2 Why It Failed

The rule assumed that a handoff instruction should always appear at the very start of
the entire message.

This is not how people normally assign work in long-form chat. A more natural structure
is:

```text
Background and analysis...

Key findings...

@developer please investigate the SSE detail path next.
```

Under the original rule, this message would not trigger because the first character of
the whole message is not `@`.

### 4.3 Why "Any Line Start with @" Is Too Broad

A broader rule was considered:

- trigger if any line in the message starts with `@agent`.

This is more flexible, but carries meaningful false-positive risk:

- code blocks can contain lines like `@developer`;
- quoted transcripts can contain old handoff lines;
- examples, templates, and notes can contain mention lines not meant as routing;
- if trigger detection is broad but extraction still scans the whole message, a single
  line could accidentally activate multiple mentions elsewhere in the body.

### 4.4 Why "Last Paragraph Only" Fits Better

The safest human-shaped compromise is:

- let the agent explain first;
- require the explicit routing instruction to live in the last non-empty paragraph.

This maps well to real writing behavior:

- explanation first;
- action assignment last.

It also keeps runtime detection narrow and deterministic.

---

## 5. Options Considered

### Option A: Keep "message must start with @"

Pros:

- lowest routing ambiguity;
- very simple mental model;
- minimal implementation risk.

Cons:

- unnatural for long-form agent outputs;
- easy for agents to violate while still clearly intending a handoff;
- caused the actual chat `#5` failure.

Decision: not chosen.

### Option B: Trigger on any line starting with `@`

Pros:

- flexible;
- aligns with multi-paragraph writing.

Cons:

- larger false-positive surface;
- code blocks and examples become risky;
- body-wide extraction can over-dispatch if not redesigned carefully;
- harder to explain and reason about.

Decision: not chosen.

### Option C: Trigger only from the last non-empty paragraph

Pros:

- supports explanation-first, assignment-last writing;
- much narrower false-positive surface than Option B;
- easy to explain to agents;
- deterministic and cheap to implement in runtime.

Cons:

- still requires a formatting convention;
- middle-paragraph handoffs are intentionally ignored.

Decision: chosen.

---

## 6. Final Decision

Catown will treat lightweight chat handoff as a **tail-paragraph routing protocol**.

### 6.1 Trigger Rule

A lightweight chat handoff is triggered only when:

- the last non-empty paragraph of the final chat message exists; and
- the first line of that paragraph starts with one or more `@agent_name` mentions.

### 6.2 Extraction Rule

When triggered:

- only mentions on the first line of the last non-empty paragraph are extracted;
- the rest of the full message body does not contribute additional routing targets.

### 6.3 Ignored Contexts

Mentions must not trigger handoff when they appear only inside:

- fenced code blocks;
- non-final paragraphs;
- ordinary explanatory text outside the final paragraph.

### 6.4 Rewritten Per-Agent Trigger

When the runtime creates a concrete follow-up for each target agent:

- it rewrites the instruction into a single-agent handoff message;
- only the tail-paragraph instruction body is preserved.

Example:

Input:

```text
We confirmed the current blocker is in SSE detail delivery.

@tester @developer please inspect the backend
```

Generated follow-ups:

- `@tester please inspect the backend`
- `@developer please inspect the backend`

---

## 7. Implementation Summary

Implemented in:

- [backend/services/assistant_handoff.py](../../backend/services/assistant_handoff.py)
- [backend/services/context_builder.py](../../backend/services/context_builder.py)
- [backend/services/agent_action_runtime.py](../../backend/services/agent_action_runtime.py)

### Runtime changes

Added helper logic to:

- normalize line endings;
- find the last non-empty paragraph outside fenced code blocks;
- detect whether that tail paragraph begins with `@agent`;
- extract mentions only from that tail paragraph;
- rebuild target-specific handoff trigger content from the tail paragraph only.

### Prompt guidance changes

The shared operating contract and chat routing guidance now explain the protocol clearly:

- if an agent intends a lightweight handoff or notification by mention;
- it should place the mention at the start of the last non-empty paragraph of the final
  chat message.

This keeps prompt guidance aligned with runtime behavior.

---

## 8. Examples

### 8.1 Should trigger

```text
I checked the logs and narrowed it down to the SSE detail path.

@developer please inspect the backend event emission logic next.
```

Reason:

- last non-empty paragraph starts with `@developer`.

### 8.2 Should trigger multiple handoffs

```text
The failing surface is shared between runtime and tests.

@tester @developer please inspect the backend
```

Reason:

- last non-empty paragraph starts with multiple mentions on the first line.

### 8.3 Should not trigger

```text
@developer please inspect the backend

Closing note only.
```

Reason:

- mention is not in the last non-empty paragraph.

### 8.4 Should not trigger

```text
Background first.

```text
@developer please inspect the backend
```
```

Reason:

- mention is inside a fenced code block.

### 8.5 Should not trigger

```text
This likely needs follow-up from @developer, but I am not assigning it yet.
```

Reason:

- plain inline mention in explanatory text is not a routing instruction.

---

## 9. Risks and Mitigations

### Risk 1: Agents still format the handoff incorrectly

Impact:

- no follow-up agent turn is created.

Mitigation:

- prompt guidance now explicitly documents the protocol;
- tests protect the runtime rule.

### Risk 2: Final paragraph contains examples rather than real routing

Impact:

- accidental handoff.

Mitigation:

- trigger scope is limited to the last paragraph only;
- fenced code blocks are ignored;
- only first-line mentions of that paragraph count.

### Risk 3: Middle-paragraph routing intent is ignored

Impact:

- a human may think the handoff was clear, but the runtime does not trigger.

Mitigation:

- this is an intentional tradeoff for predictability;
- the protocol is simple and teachable.

---

## 10. Acceptance Criteria

- A message with background paragraphs and a final paragraph starting with `@developer`
  creates a Developer follow-up turn.
- A message where only a middle paragraph starts with `@developer` does not trigger.
- A fenced code block containing `@developer` does not trigger.
- A tail paragraph with `@tester @developer ...` produces two target-specific follow-up
  messages.
- Prompt guidance states the same protocol that runtime enforces.

---

## 11. Tests

The implementation is covered by targeted tests in:

- [backend/tests/test_assistant_handoff.py](../../backend/tests/test_assistant_handoff.py)
- [backend/tests/test_prompt_context_builder.py](../../backend/tests/test_prompt_context_builder.py)
- [backend/tests/test_agent_action_runtime.py](../../backend/tests/test_agent_action_runtime.py)

Verified command:

```bash
python -m pytest backend/tests/test_assistant_handoff.py backend/tests/test_prompt_context_builder.py backend/tests/test_agent_action_runtime.py -q --tb=short
```

Result at implementation time:

```text
49 passed
```

---

## 12. Follow-Up Ideas

- Add a runtime card or debug event when a message contains `@agent` outside the tail
  paragraph but does not trigger routing.
- Add a formatter/helper in agent prompt examples to make tail-paragraph handoff even
  more consistent.
- Consider a future structured handoff block if lightweight mentions become too limiting.
