# Mission Board Information Architecture

_Last updated: 2026-04-15_

## Purpose

This document turns the earlier frontend audit into a concrete page structure.

It does not describe the legacy Pipeline Dashboard.
It describes the new primary project-first work surface that should replace it.

The design goal is simple:

**When a user opens Catown, they should see project state, stage progress, decisions, assets, and recent activity in one coherent board without thinking in terms of pipelines or chatrooms.**

Related:

- `docs/Catown-UX-Interaction-Principles.md`
- `docs/Frontend-Mission-Board-Migration-Audit.md`
- `docs/Mission-Board-Minimum-V2-Contract.md`
- `docs/ADR-017-main-api-design.md`

---

## Design principles

### 1. Project-first, not pipeline-first

The main surface should be organized around the selected `Project`.

Not around:

- pipeline instances
- chatrooms
- pipeline templates
- old stage-card shells

### 2. Overview first, drill-down second

The user should understand the state of the project within a few seconds.

Only then should they drill into:

- a stage run
- a decision
- an asset
- a specific event

### 3. Work cues over raw data dumps

The board should answer:

- what is happening
- what is blocked
- what needs my input
- what changed recently
- what should I do next

It should not feel like a raw debug console by default.

### 4. Events are context, not the whole product

Recent activity is valuable, but it should support the board.
It should not dominate the whole page the way chatroom logs did.

### 5. One main board, one detail rail

The layout should separate:

- persistent board-level context
- focused inspection of the currently selected thing

That gives the UI a stable mental model.

### 6. Stable board, guided interaction

The main surface should remain structurally stable.

When the user needs to create, clarify, confirm, or steer work, the system should open a progressive conversational flow inside that stable board context.

The board gives orientation.
The conversational flow reduces form burden.
The interaction should feel guided, not form-heavy and not like a generic chat transcript.

---

## Primary layout

## Desktop layout

```text
+-------------------+--------------------------------------+---------------------------+
| Project rail      | Main board                           | Detail rail               |
|                   |                                      |                           |
| - project list    | - project hero                       | - selected stage detail   |
| - filters         | - next action strip                  | - or selected decision    |
| - create project  | - stage lane                         | - or selected asset       |
|                   | - decisions + assets split section   | - or selected event       |
|                   | - recent activity feed               |                           |
+-------------------+--------------------------------------+---------------------------+
```

## Mobile / narrow layout

```text
Project switcher
Project hero
Next action strip
Stage lane
Decisions
Assets
Recent activity
Bottom sheet / modal for detail
```

The mobile version should stack vertically and move the detail rail into a modal or bottom sheet.

---

## Page regions

## A. Project rail

### Purpose

Persistent project navigation and lightweight filtering.

### Content

- project search
- status filter
- stage filter
- project list
- lightweight create project entry

### Card contents per project

- project name
- current stage
- project status
- health status
- pending/blocked indicator
- last activity time

### Primary interaction

Selecting a project refreshes the rest of the board.

### Data source

- `GET /api/v2/projects`

### Notes

This replaces the old rooms sidebar.
It should feel like a mission roster, not a chatroom list.

"Create project" should be easy to reach here, but it should not dominate the primary home-state attention model when active work already exists.

---

## B. Project hero

### Purpose

Give immediate project context and state.

### Content

- project name
- one-line vision
- status badge
- current stage badge
- execution mode badge
- health status badge
- current focus text
- blocking reason if present
- latest summary if present

### Actions

- `Continue` when recommended action is `continue_project`
- `View current stage`
- `View pending decision`

### Data source

- `GET /api/v2/projects/{id}/overview`

### Visual direction

This should be the strongest visual block on the page.
It is the board header, not just another card.

---

## C. Next action strip

### Purpose

Translate backend intent into one prominent user-facing cue.

### Content

- recommended next action label
- short explanation copy
- one primary CTA
- optional secondary CTA to inspect related object

### Example mappings

- `continue_project` -> button: `Continue project`
- `review_current_stage` -> button: `Inspect current stage`
- `resolve_scope_confirmation` -> button: `Review scope decision`
- `review_release_pack` -> button: `Open release pack`

### Data source

- `recommended_next_action` from project overview
- `pending_decisions`
- `current_stage_run`
- `key_assets`

### Notes

This strip replaces the old "what do I do with this paused pipeline" confusion.

---

## D. Stage lane

### Purpose

Show the project progression as a stage-run timeline.

### Content

- current stage run card
- recent completed runs
- active/waiting states
- summary counters

### Card contents

- stage name
- run index
- status
- lifecycle badge
- summary
- started/ended times
- attention indicator if `requires_attention`

### Interactions

- click a stage run to open detail rail
- default selection should be current stage run when available

### Data sources

- `GET /api/v2/projects/{id}/stage-runs`
- `GET /api/v2/stage-runs/{id}`
- `GET /api/v2/stage-runs/{id}/events`

### Notes

This replaces the old pipeline stages strip.
The user should think "project progression" not "engine stage internals".

---

## E. Decisions panel

### Purpose

Centralize human checkpoints.

### Content

- pending decisions first
- recent resolved decisions below or collapsed
- severity/attention styling for pending items

### Decision card contents

- decision title
- decision type
- context summary
- requested action
- recommended option
- impact summary
- created time

### Actions

- approve
- reject
- open detail in rail

### Data sources

- `GET /api/v2/projects/{id}/decisions`
- `GET /api/v2/decisions/{id}`
- `POST /api/v2/decisions/{id}/resolve`

### Notes

This should become the main place for approval work.
Not pipeline pause-state buttons.

---

## F. Assets panel

### Purpose

Expose current deliverables and their relationships.

### Content

- key current assets as cards or chips
- type grouping when useful
- approval state
- version and freshness

### Asset card contents

- asset title
- asset type
- summary
- status
- version
- updated time

### Interactions

- click asset to open detail rail
- open related stage or decision from detail rail

### Data sources

- overview `key_assets`
- `GET /api/v2/projects/{id}/assets`
- `GET /api/v2/assets/{id}`

### Notes

This replaces the old "artifacts" box.
The language should be product-facing, not pipeline-facing.

---

## G. Recent activity feed

### Purpose

Provide motion and observability without turning the page into a chat transcript.

### Content

- recent stage events for the selected current/recent stage
- compact event timeline
- badges for status/event type
- optional agent attribution

### Event item contents

- event summary
- event type badge
- stage name
- agent name if present
- created time

### Interactions

- click event to open full context in detail rail
- filter between `all / current stage / warnings`

### Data source

- `GET /api/v2/stage-runs/{id}/events`
- or event arrays already returned by stage detail

### Notes

This preserves the sense that "the system is alive" without making chat the primary skeleton.

---

## H. Detail rail

### Purpose

Show focused context for the currently selected object without leaving the board.

### Supported modes

- project detail summary
- stage run detail
- decision detail
- asset detail
- event detail

### Default behavior

- if a current stage run exists, open that by default
- otherwise show project summary / empty guidance

### Stage detail mode

Should show:

- stage header
- input assets
- output assets
- linked decisions
- event timeline
- stage summary counts

### Decision detail mode

Should show:

- context summary
- options
- impact summary
- approve/reject controls
- related assets and stage

### Asset detail mode

Should show:

- markdown/content viewer
- relationships
- stage links
- decision links
- metadata

### Event detail mode

Should show:

- event summary
- payload prettified
- related stage or asset link

---

## Default board behavior

When a project is selected, the board should initialize like this:

1. load project overview
2. load stage-run list
3. load project decisions
4. load project assets if not already sufficiently represented
5. auto-select `current_stage_run` into the detail rail when present
6. load events for the selected stage run

This gives a deterministic startup path and avoids the old scattered fetch flow.

---

## Empty states

## No projects

Show:

- a strong empty-state card
- create project CTA
- short text explaining the project-first flow

## Project with no stage runs

Show:

- project hero
- empty stage lane
- CTA to continue or initialize work

## Project with no pending decisions

Show:

- calm state card such as `No decisions waiting right now`

## Project with no assets yet

Show:

- a hint that assets will appear as stage work produces outputs

---

## What should not appear on the first Mission Board

The first Mission Board should not expose these legacy-first structures as primary UI:

- pipeline selector
- new pipeline modal
- start/pause/resume pipeline button cluster
- pipeline messages box
- pipeline artifacts box
- pipeline file browser
- chatroom-centric message panel
- pipeline websocket subscription panel logic

If any of these remain temporarily for compatibility, they should sit outside the new primary board.

---

## Suggested implementation slices

## Slice 1: Static board shell

Implement only the new layout and placeholder states.
No legacy deletion yet.

## Slice 2: Read-only data wiring

Wire:

- project rail
- overview hero
- stage lane
- decisions list
- assets list
- event feed

Use only the frozen minimum v2 contract.

## Slice 3: Detail rail

Add:

- stage detail
- decision detail
- asset detail
- event detail

## Slice 4: Primary actions

Add:

- continue project
- resolve decision

## Slice 5: Retire old primary shell

Once the Mission Board can support normal daily use:

- demote Pipeline tab
- stop using old chatroom/pipeline flows as the default entry path
- rewrite frontend tests around the new board

---

## One-line summary

The Mission Board should feel like a project command surface: overview at the center, decisions and deliverables at hand, execution activity visible, and detail always one click away.
