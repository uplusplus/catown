# Frontend UX Freeze: Cockpit-First Homepage

**Date**: 2026-04-16
**Status**: Frozen working definition
**Related**: `docs/Catown-UX-Interaction-Principles.md`, `docs/ADR-023-frontend-react-mission-board-architecture.md`, `docs/Mission-Board-Minimum-V2-Contract.md`

---

## Purpose

This document freezes the current frontend UX definition for Catown so product, frontend, and backend work stop mixing:

- the old legacy dashboard
- the first React Mission Board implementation
- the newer cockpit-first homepage direction

This is not a visual-polish brief.
It is the current product-level interaction definition.

---

## Headline

Catown's target frontend is no longer defined as a Mission Board.

The current target is a **cockpit-first homepage** centered on **Navigation Core**.

The previous React Mission Board remains the current implementation base, but it is now an intermediate shell rather than the final UX model.

---

## Naming freeze

Use these terms consistently.

### Product target

Use:

- `cockpit-first homepage`
- `Navigation Core`
- `Current Segment`
- `Captain Intervention`
- `Agent Systems / Autonomy Status`
- `System Pulse`

### Implementation baseline

Use:

- `React Mission Board shell`

This refers to the currently implemented React/Vite frontend in `frontend/src/`.

### Avoid as the primary name for the new target

Avoid using these as the main name for the future homepage:

- `Mission Board`
- `Project-first Mission Board`
- `dashboard`
- `pipeline dashboard`

These terms may still be useful when describing historical phases, old docs, or the current codebase, but they should no longer define the new UX target.

---

## Core definition

Catown should open as a calm operational surface for supervising project execution.

The homepage is not primarily:

- a feature directory
- a KPI dashboard
- a chat landing page
- a generic card board
- a classic project-management workflow tree

The homepage is primarily:

- a mission cockpit
- a route-aware execution surface
- a multi-agent operations view
- a focused intervention surface when human authority is needed

In practical terms, the homepage should help the user answer, in order:

1. which project is active
2. where the project is on its route
3. whether the system is progressing cleanly
4. whether captain intervention is required
5. what meaningful changes have happened recently

---

## Structural definition

The first-screen desktop homepage should use this three-band structure:

```text
+------------------+------------------------------------------------------+-----------------------+
| Left rail        | Center stage                                         | Right band            |
|                  |                                                      |                       |
| Projects         | [ Navigation Core ]                                  | [ Captain Intervention ]
|                  |                                                      |                       |
|                  | [ Agent Systems / Autonomy Status ]                  | [ Key changes ]       |
|                  |                                                      |                       |
|                  | [ System Pulse ]                                     |                       |
+------------------+------------------------------------------------------+-----------------------+
```

### Width balance

Desktop balance should feel roughly like:

- left rail: 14% to 18%
- center stage: 62% to 72%
- right band: 14% to 20%

The center stage must remain visually dominant.

---

## Module responsibilities

### Left rail

The left rail is for project orientation and lightweight switching.

It should contain:

- project search or light filtering
- project list
- low-emphasis create-project entry

It should not contain:

- heavy analytics
- large narrative blocks
- duplicated event feed content
- dominant hero content

### Navigation Core

`Navigation Core` is the central screen.
It replaces the old idea of a static hero card plus a separate stage-progress module.

Its job is to show:

- mission target
- current route position
- next gate
- route health
- route shape
- local current-segment detail

Its internal structure should be:

1. mission layer
2. flight-state layer
3. route-visualization layer
4. current-segment layer

### Route visualization

The route visualization should be part of `Navigation Core`, not a separate module.

Its preferred default form is:

- a horizontal main route across the upper portion of `Navigation Core`
- a lower `Current Segment` panel expanding beneath the selected current route node

It should not behave like:

- a percentage bar
- a generic timeline
- a workflow chart
- a decorative progress strip

It should make legible:

- the current route point
- the next critical gate
- the remaining route silhouette

### Current Segment

`Current Segment` is the local navigation panel inside `Navigation Core`.

Its role is to explain the currently active stage in local operational terms.

It should show:

- what is happening in the current segment
- what inputs and outputs matter
- what tension or unresolved gate exists
- what convergence is happening toward the next gate

It should not become:

- a generic checklist
- a full task database
- a strict parent-child tree
- a detached detail page

### Agent Systems / Autonomy Status

This module supports the navigation picture by exposing execution health.

It should help the user understand:

- whether auto-mode is healthy
- which agents are active
- whether handoffs are progressing
- whether the system is stalled or waiting

It should support `Navigation Core`, not visually replace it.

### Captain Intervention

This is the human-authority surface.

It should contain only the top 1 to 3 intervention items that truly need user action.

It should frame human action as captain authority, not as generic inbox triage.

It should not expose raw approve/reject controls without context.

### System Pulse

This is the compact operational signal layer.

It should show:

- latest meaningful progress
- anomalies when relevant
- filtered changes that affect understanding of the mission state

It should remain secondary to the route and intervention surfaces.

---

## Reading order freeze

The intended homepage reading order is:

1. identify active project context in the left rail
2. read route and current position in `Navigation Core`
3. inspect autonomy and agent-system health
4. check `Captain Intervention`
5. scan `System Pulse` and key changes

If activity feed or metadata becomes more visually dominant than `Navigation Core`, the homepage is off-model.

---

## Interaction definition

The homepage should remain stable and stateful.

It should not open by asking the user to type into a blank chat input.
It should open by showing current execution state.

Natural language remains first-class, but it complements the cockpit rather than replacing it.

That means:

- intent can still begin in language
- revisions can still happen in language
- interruptions can still happen in language
- important state should still be made legible in UI
- interventions should be compressed into clear context-rich cards

---

## Relationship to the current React frontend

The current React frontend in `frontend/src/` is still a valid implementation base.

However, it should now be understood as the **React Mission Board shell**, not as the final UX definition.

Its current structure still reflects the previous phase:

- `ProjectHero`
- `NextActionStrip`
- `StageLane`
- `DecisionPanel`
- `AssetPanel`
- `ActivityFeed`
- `DetailRail`

This shell is useful because it already proves:

- project-first data flow
- React/Vite composition
- `/api/v2/*` integration
- selection and detail loading patterns
- key actions such as continue and resolve

But it does not yet fully implement the cockpit-first homepage model.

---

## Migration mapping

Use this mapping when evolving the current frontend.

- `ProjectHero` -> absorbed into `Navigation Core` mission layer and flight-state layer
- `StageLane` -> replaced by the route-visualization layer inside `Navigation Core`
- `DetailRail` -> partially rethought as `Current Segment`; any remaining side detail should be subordinate to the cockpit model
- `NextActionStrip` -> folded into route/gate framing or intervention framing depending on content
- `DecisionPanel` -> feeds `Captain Intervention` or a deeper task layer, not a co-equal homepage block
- `ActivityFeed` -> reduced into `System Pulse` or `Key changes`, not a large competing center module
- `ProjectRail` -> remains the left rail with quieter create-project affordance

---

## Non-goals

The target homepage is not trying to be:

- a freeform canvas
- a fully conversational full-screen assistant
- a kanban board
- a dense admin console
- a BI dashboard

Those modes may exist elsewhere in the product later, but they do not define the homepage.

---

## Working implementation rule

Until a newer ADR replaces this freeze:

- docs should describe the target homepage as `cockpit-first`
- new homepage UX work should use `Navigation Core` as the central concept
- code references to `Mission Board` should be treated as implementation-era naming, not product-target naming
- frontend planning should optimize for migration from the current React shell into the cockpit model

---

## One-sentence product definition

**Catown should open as a cockpit-first project execution homepage centered on Navigation Core, where route position, autonomy health, and captain intervention are legible before any secondary activity or tooling surface.**
