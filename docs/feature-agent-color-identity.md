# Feature Doc: Agent Color Identity Across Chat, Tasks, Artifacts, and Processes

**Status**: Ready for implementation
**Date**: 2026-05-21
**Owner**: Catown

## 1. Summary

Catown will give each agent a stable visual identity color and apply it consistently
across the chat workspace.

This feature makes it easier to scan:

- who produced a message
- which agent owns a task
- which agent generated an artifact
- which agent started a background shell/process subtree

## 2. Goals

The feature must colorize agent identity in these places:

1. agent names
2. chat bubble borders
3. task card borders
4. generated artifact rows in the right sidebar
5. spawned process rows in the right sidebar

The feature should be readable in the current dark UI and should not flood the page
with large solid blocks of color.

## 3. Non-Goals

- No user-configurable color picker in this iteration.
- No persistence/config schema changes for custom palettes in this iteration.
- No attempt to retroactively infer exact ownership for every historical workspace file.
  When ownership is unknown, Catown should fall back to a neutral style.

## 4. UX Direction

Each agent gets a stable accent color.

The accent should be applied as:

- colored name text
- subtle colored border or left accent line
- colored icon tint where helpful
- gentle hover/focus tint derived from the same accent

The default treatment should stay restrained:

- keep existing panel backgrounds
- avoid turning whole cards into solid saturated blocks
- preserve warning/error semantics such as approval waiting and failures

## 5. Agent Palette

Known agents get fixed palette slots:

- `analyst`
- `architect`
- `developer`
- `tester`
- `release`
- `valet`

Legacy/default aliases such as `assistant`, `bot`, `dev`, `qa`, and `rel` should
resolve to the canonical agent type first, then inherit that type's color.

Unknown future agents should receive a deterministic fallback color derived from
their normalized agent key so that the same unknown agent is still visually stable.

## 6. Ownership Resolution Rules

### 6.1 Chat Messages

- Source field: `message.agent_name`
- If present, use that agent's accent for:
  - sender name
  - assistant bubble border/edge
- User messages stay on the existing user style.

### 6.2 Task Cards

- Source field: `taskRun.target_agent_name`
- Apply the agent accent to:
  - task header actor name
  - task card border/edge
  - inline task status surface where appropriate

If the task has warning/error states, those states remain visible; the agent accent
acts as ownership identity, not as a replacement for status semantics.

### 6.3 Artifact Rows

Artifacts in the right sidebar can come from two sources:

1. runtime-derived references extracted from cards/task runs
2. workspace scan results

Ownership priority:

1. runtime card `card.agent`
2. task run `taskRun.target_agent_name`
3. merged runtime ownership for the same artifact path
4. neutral fallback when no owner is known

This means a scanned workspace file should inherit runtime ownership when Catown has
already seen that exact artifact path referenced by an agent.

### 6.4 Process Rows

Ownership priority:

1. explicit process node `agent_name`
2. subagent handle metadata agent name
3. parent task run owner
4. neutral fallback

This is especially important for shell command rows, because the user wants spawned
subprocesses in the right sidebar to show which agent owns them.

## 7. Data Contract Changes

### 7.1 Frontend

Add optional ownership fields to sidebar view models:

- `BrowserArtifactEntry.agentName?: string | null`
- `ChatProcessEntry.agent_name?: string | null`

### 7.2 Backend

Extend `ChatProcessNodeInfo` with:

- `agent_name: Optional[str]`

Backend process projection should populate:

- task nodes from `task_run.target_agent_name`
- subagent nodes from active handle agent identity
- command nodes from runtime card agent identity when available

## 8. Rendering Contract

Frontend should expose one shared agent-theme resolver that returns:

- canonical agent key
- accent color
- subtle background tint
- border tint
- stronger text color when needed

All target panels should consume the same resolver instead of maintaining local
hard-coded color decisions.

## 9. Acceptance Criteria

The feature is complete when:

1. different agents render different stable colors in the same chat session
2. assistant message sender names are colorized by agent
3. assistant chat bubble borders visibly differ by agent
4. inline task cards show the owning agent color on their border/edge
5. artifact rows in the right sidebar show the producing agent color when ownership
   can be resolved
6. process rows in the right sidebar show the owning agent color, including shell
   command rows started under a task
7. unknown ownership falls back to the current neutral treatment
8. warning/error states remain readable and are not overridden by ownership color

## 10. Validation Plan

Validate with at least these scenarios:

1. Two different agents reply in one chat and their names/bubbles are visibly different.
2. A running delegated task shows a different accent than the default assistant.
3. A generated doc such as `PRD.md` or `tech-spec.md` appears in the artifact list with
   the source agent accent.
4. A `run_shell` process under a task appears in the process tree with the task owner
   accent.
5. A consult/subagent process row shows the consulted agent accent.
