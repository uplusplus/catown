# ADR-020: Prompt Guidance vs Runtime Contracts

**Status**: Accepted  
**Date**: 2026-05-19  
**Decision makers**: BOSS + Catown Runtime

## Context

Catown agents receive rich prompt context: role descriptions, team members, skills,
tool hints, chat history, and current project state. This helps an agent decide how to
behave, but it does not by itself create durable runtime facts.

The failure case that triggered this ADR was a project chat request to test the current
project. Valet could see that Tester exists and owns testing work, but the request still
ran as a Valet single-agent turn. Valet used `consult_agent`, which is a synchronous
expert-question tool with target-agent tools disabled. Tester therefore could not run
`pytest`, and Valet produced a final response saying the test could not be executed.
The run was marked completed even though the user's testing objective was not completed.

This is not a Tester-specific routing problem. It is a boundary problem between prompt
guidance and software-owned runtime contracts.

## Decision

Catown will treat prompts as guidance for judgment and expression, not as the source of
truth for permissions, ownership, task state, dispatch, waiting, completion, recovery, or
audit facts.

Runtime-critical behavior must be represented as software-owned contracts and events.
Prompts may explain those contracts to agents, but the backend must enforce and record
the facts that make the contracts true.

Parent/child relationships are not an agent hierarchy. They represent ownership transfer
inside one user objective: a coordinator or current owner may transfer a bounded piece of
work to another agent, and the child run becomes the accountable owner for that delegated
work. The parent remains accountable for the original user objective and cannot complete
until required child-owner results exist, or until the objective is explicitly blocked,
failed, cancelled, or returned to the user.

## Boundary

Prompt guidance is appropriate for soft constraints:

- Role style and tone.
- Professional judgment and domain heuristics.
- Output structure and report format.
- How to communicate assumptions and uncertainty.
- Lightweight suggestions such as "handoff specialized work when appropriate."

Software contracts are required for hard constraints:

- Tool permissions and sandbox boundaries.
- Agent ownership and work-scope rules.
- Task/run lifecycle transitions.
- Parent/child run relationships.
- Dispatch, waiting, resume, retry, cancellation, and failure semantics.
- Completion criteria for user-visible work.
- Observable facts: who did what, when, with which tool, and what result.

Operational rule:

If a failure only makes the answer lower quality, prompt guidance may be enough. If a
failure can create incorrect state, hide unfinished work behind `completed`, bypass
permissions, make clients disagree, break recovery, or prevent audit, it must be
software-owned.

## Consequences

1. Agent role ownership is not merely prompt text.
   Catown needs a runtime-readable ownership/capability model. For example, testing work
   may be owned by Tester, implementation by Developer, release gating by Release, and
   coordination by Valet. The exact ownership model can evolve, but it must be visible to
   the backend.

2. Coordinator agents require coordinator runtime semantics.
   A coordinator such as Valet may clarify, decompose, dispatch, wait, and summarize.
   It must not be able to satisfy specialized work by producing a final answer when the
   specialized owner has not produced the required result.

3. Parent/child runs encode responsibility transfer, not social rank.
   Agents can still collaborate as peers in chat. The parent/child structure exists at
   the run/task layer so the backend can answer: who owns the original objective, who now
   owns each delegated work item, what result is required, and whether the parent is still
   waiting on a child owner.

4. Consultation is not task dispatch.
   `consult_agent` asks for synchronous advice and may intentionally disable target-agent
   tools. It must not be used as a substitute for durable work assignment when the target
   needs tools, progress tracking, approvals, or a result that gates completion.

5. Chat mentions are not enough as durable dispatch.
   A message such as `@Tester please test this` is a conversation event. It can be used
   as an input to routing, but the backend must still create or link a durable target run
   if the work is meant to be executed and waited on.

6. Completion must be objective-scoped.
   A parent or coordinator run must not be marked completed merely because the
   coordinator generated a final message. It completes only when the user objective's
   required owner results exist, or it must enter a blocked/failed/needs-user-input state.

## Implementation Direction

Future work should move toward a common runtime protocol:

- Represent agent capabilities and ownership in backend-readable configuration.
- Give coordinator runs explicit child-run handles and waiting state.
- Treat delegation as a factual owner-transfer event with a parent owner, child owner,
  requested work, required result, and child run identifier.
- Record durable dispatch facts such as target agent, dispatch kind, parent run,
  requested work, required result, and completion criteria.
- Treat target-agent execution as a real run when the task requires tools or progress.
- Surface parent/child state through the canonical timeline and task activity projection.
- Keep prompt instructions aligned with these contracts, but never rely on prompt
  compliance as the only enforcement mechanism.

## Non-Goals

- Do not add hidden keyword routing rules as the primary solution.
- Do not hard-code one-off mappings such as "pytest means Tester" without a general
  ownership/dispatch contract.
- Do not make the frontend infer missing dispatch or completion facts.
- Do not use final assistant text as proof that the requested work was completed.
