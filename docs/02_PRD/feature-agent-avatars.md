# Feature Doc: Agent-Specific Business Avatars In Chat

**Status**: Ready for implementation
**Date**: 2026-05-22
**Owner**: Catown

## 1. Summary

Replace the left-side single-letter chat avatars such as `D` and `T` with stable,
agent-specific avatars that better reflect each agent's business responsibility.

This applies to chat-thread surfaces where the current UI shows the small avatar
block at the far left of a message, task card, or activity batch.

## 2. Goals

The feature should:

1. make agents visually distinguishable without relying only on initials
2. use icons that are strongly associated with each agent's responsibility
3. keep the avatar compact and readable in the current dark UI
4. preserve the existing per-agent color identity

## 3. Non-Goals

- No uploaded custom avatar images in this iteration
- No backend schema changes
- No change to user-message avatars beyond preserving the current user treatment

## 4. UX Direction

Each built-in agent gets a compact icon avatar:

- `analyst`: research / reading / evidence
- `architect`: structure / system design
- `developer`: implementation / code
- `tester`: validation / QA
- `release`: delivery / package / launch
- `valet`: orchestration / routing / coordination

Unknown agents should fall back to the current initials-based avatar.

The avatar should continue using the resolved agent accent color so that icon shape
and color identity reinforce each other.

## 5. Scope

Apply the new avatar treatment to the left-side chat avatars used by:

1. runtime cards
2. activity batches
3. agent messages
4. inline task-run cards

## 6. Rendering Rules

For assistant/agent-owned avatars:

1. resolve agent identity from the same agent name already used by the card/message
2. pick a fixed icon by canonical agent type
3. tint the avatar border/background/icon with the existing agent theme variables

For user-owned avatars:

1. keep the current user avatar treatment

For unknown agents:

1. keep the initials fallback
2. still apply resolved agent color when the system can resolve one

## 7. Acceptance Criteria

The feature is complete when:

1. `D` / `T` style initial-only avatars are replaced by role-relevant icons for built-in agents
2. those avatars still match each agent's existing accent color
3. unknown agents still render safely with initials
4. user avatars remain visually distinct from agent avatars
