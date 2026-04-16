# Frontend Component Migration Checklist: React Shell -> Cockpit Homepage

**Date**: 2026-04-16
**Status**: Working migration checklist
**Related**: `docs/Frontend-UX-Freeze-Cockpit-Homepage.md`, `docs/Catown-UX-Interaction-Principles.md`, `docs/ADR-023-frontend-react-mission-board-architecture.md`

---

## Purpose

This document translates the frozen cockpit-homepage UX target into concrete migration work against the current React frontend in `frontend/src/`.

It is not a rewrite spec for every pixel.
It is the component-level bridge from the current React Mission Board shell to the cockpit-first homepage.

---

## Current baseline

The current React shell is organized around:

- `ProjectRail`
- `ProjectHero`
- `NextActionStrip`
- `StageLane`
- `DecisionPanel`
- `AssetPanel`
- `ActivityFeed`
- `DetailRail`

This shell already proves the key data flow, but its page structure still reflects the previous Mission Board phase.

The cockpit-first homepage instead wants:

- a quiet left rail
- a clearly dominant center stage
- a weaker right band
- `Navigation Core` as the center anchor
- route visualization integrated into the center anchor
- `Current Segment` integrated under the selected route point
- `Captain Intervention` as the human-authority surface
- `System Pulse` as a filtered signal layer

---

## Target homepage map

Use this as the destination structure.

```text
Left rail
- ProjectRail

Center stage
- Navigation Core
  - mission layer
  - flight-state layer
  - route visualization
  - Current Segment
- Agent Systems / Autonomy Status
- System Pulse

Right band
- Captain Intervention
- Key changes / compact change list
```

---

## Component mapping

### `ProjectRail.tsx`

Current role:

- project switcher
- inline project creation
- project list with compact health metadata

Target role:

- remains the left rail
- should become quieter and more navigational

Keep:

- project roster
- selection behavior
- low-friction create-project flow

Change later:

- reduce semantic overlap with center-stage labels
- make create-project affordance visibly lower emphasis once projects exist
- consider adding lightweight search/filter only if it does not compete with the center stage

Migration priority:

- low

Reason:

- this component is already structurally close to the target model

---

### `ProjectHero.tsx`

Current role:

- top summary card for project state
- status, focus, stage, movement, summary, block reason, readiness
- continue/review CTA zone

Target role:

- should be split and absorbed into `Navigation Core`
- mission-level summary becomes the `mission layer`
- status/readiness become the `flight-state layer`

Keep:

- project name
- one-line vision / mission statement
- status, health, blocking state
- current focus and latest summary where still useful

Change later:

- stop behaving like a standalone hero card
- merge with route framing rather than sitting above it as a separate banner
- separate informational mission state from action controls
- move release-readiness signals into a smaller health/gate treatment unless release is the active gate

Migration priority:

- high

Reason:

- the cockpit-homepage definition explicitly replaces the old static hero-card pattern

---

### `NextActionStrip.tsx`

Current role:

- renders recommended next action
- surfaces blocking reason
- provides action framing copy

Target role:

- should not remain a separate co-equal strip for long
- its content should be redistributed into either:
  - route/gate framing inside `Navigation Core`, or
  - `Captain Intervention` when the next move requires human authority

Keep:

- action-copy mapping by recommendation type
- the logic that turns backend next-action codes into human-readable guidance

Change later:

- stop treating action focus as an isolated banner
- attach gate-related actions directly to the route point or current segment
- route decision-type actions toward intervention cards instead of a generic strip

Migration priority:

- high

Reason:

- this component carries valuable semantics, but the target homepage wants those semantics embedded in the cockpit flow

---

### `StageLane.tsx`

Current role:

- renders stage runs as selectable cards
- acts as the project progression surface

Target role:

- becomes the route-visualization layer inside `Navigation Core`
- should evolve from a stage-card list into a route-aware navigation surface

Keep:

- stage-run selection behavior
- stage status and lifecycle semantics
- active-selection feedback

Change later:

- replace list/grid feeling with a horizontal route model
- keep current point, next gate, and route silhouette visible at once
- allow the selected route point to control the lower `Current Segment`
- reduce the feel of "cards in a panel" and increase the feel of "navigation path"

Migration priority:

- highest

Reason:

- this is the biggest structural mismatch between the current shell and the cockpit-homepage target

---

### `DetailRail.tsx`

Current role:

- right-side inspection surface for stage, decision, asset, and event detail
- handles loading/error/empty states well
- supports linked navigation between objects

Target role:

- should be split conceptually
- stage detail should move inward and become `Current Segment` beneath the selected route point
- remaining cross-object deep inspection may stay as a lighter side rail or a secondary inspection layer

Keep:

- linked navigation patterns
- decision/asset/event detail rendering
- loading and stale-error handling
- context trail behavior

Change later:

- stop making stage detail primarily a right-band concept
- move stage inputs, outputs, events, and decisions into the local route context first
- reserve the side rail for secondary inspection, not the main explanation of the current stage
- decide whether non-stage detail remains permanently visible or appears on demand

Migration priority:

- highest

Reason:

- `Current Segment` is one of the core structural ideas of the target homepage, and it is currently living in the wrong place

---

### `DecisionPanel.tsx`

Current role:

- lists decisions
- supports approve/reject directly in-card
- shows pending counts

Target role:

- should feed `Captain Intervention`
- only the top 1 to 3 meaningful human-authority items should dominate the homepage

Keep:

- pending-decision visibility
- quick decision selection
- clear resolve actions with busy states

Change later:

- reduce the feel of a broad decision inventory on the homepage
- rank and compress intervention items by urgency and authority requirement
- avoid exposing raw approve/reject controls without enough context
- push non-critical or already-resolved decisions into deeper inspection rather than top-level emphasis

Migration priority:

- high

Reason:

- the target homepage wants a captain-authority surface, not a general-purpose decision board

---

### `AssetPanel.tsx`

Current role:

- lists generated assets
- supports asset selection
- gives compact deliverable summaries

Target role:

- should become subordinate to the current route context
- assets matter most as inputs, outputs, and proof of progress inside `Current Segment`

Keep:

- asset selection
- compact summaries
- type/version/status display

Change later:

- reduce the feel of a co-equal homepage panel
- surface only the assets that matter to the active segment or current gate by default
- move broader asset browsing into deeper inspection or a dedicated asset mode later

Migration priority:

- medium

Reason:

- asset inspection matters, but the cockpit-homepage model treats it as route-contextual rather than homepage-dominant

---

### `ActivityFeed.tsx`

Current role:

- project activity list
- stage-aware event context
- selectable event items

Target role:

- should shrink into `System Pulse` and a compact `Key changes` layer
- must not compete with `Navigation Core` for center-stage attention

Keep:

- filtered event summaries
- event selection
- stage and agent attribution

Change later:

- reduce volume and visual weight
- emphasize meaningful changes rather than generic recency
- separate anomaly signals from routine progress signals
- keep the feed compact enough that it supports, rather than dominates, understanding

Migration priority:

- high

Reason:

- the current event list is useful, but still reads too much like a co-equal board section

---

### `App.tsx`

Current role:

- composes the whole page
- coordinates project/stage/detail state
- places all primary modules in the current Mission Board layout

Target role:

- should become the orchestration layer for the cockpit homepage composition
- should stop laying out the page as several co-equal board panels in sequence

Keep:

- data-loading flow
- selection hooks
- transition hooks
- notice and detail-feedback behavior
- continue / resolve / create orchestration

Change later:

- restructure the page into left rail / center stage / right band explicitly
- replace the current `ProjectHero -> NextActionStrip -> StageLane -> panels -> feed` stack with the target module hierarchy
- let stage selection drive `Current Segment` directly in the center stage
- make the right band visually subordinate and intervention-focused

Migration priority:

- highest

Reason:

- even if child components improve, the page will still feel like the old shell until the parent composition changes

---

## Migration phases

### Phase 1: Language and framing cleanup

Goal:

- align user-visible language with the cockpit-homepage target

Includes:

- rename visible top-level copy
- reduce references to `Mission Board` in UI text
- start framing the center stage as navigation rather than a generic board

Status:

- partially done

---

### Phase 2: Center-stage restructure

Goal:

- establish `Navigation Core` as the dominant homepage anchor

Includes:

- absorb `ProjectHero` into `Navigation Core`
- fold `NextActionStrip` into route/gate framing
- redesign `StageLane` into route visualization
- move stage detail inward as `Current Segment`

Status:

- not yet done

This is the most important phase.

---

### Phase 3: Right-band compression

Goal:

- turn the right side into `Captain Intervention` plus compact changes

Includes:

- reduce `DecisionPanel` to ranked authority items
- keep only the most meaningful change summaries visible
- move lower-priority detail out of the homepage spotlight

Status:

- not yet done

---

### Phase 4: Supporting operational surfaces

Goal:

- add the missing cockpit support layers

Includes:

- introduce `Agent Systems / Autonomy Status`
- refine `System Pulse`
- decide what remains in a persistent inspection rail versus on-demand detail

Status:

- not yet done

---

## Priority order

If work must happen in sequence, do it in this order:

1. `App.tsx` page composition
2. `StageLane.tsx` -> route visualization
3. `DetailRail.tsx` -> `Current Segment` migration
4. `ProjectHero.tsx` absorption into `Navigation Core`
5. `NextActionStrip.tsx` redistribution
6. `DecisionPanel.tsx` -> `Captain Intervention`
7. `ActivityFeed.tsx` -> `System Pulse / Key changes`
8. `AssetPanel.tsx` contextual reduction
9. `ProjectRail.tsx` quiet refinement

---

## Definition of done

The migration should be considered structurally successful when:

- the homepage is immediately read as a cockpit, not a card board
- the center stage clearly dominates the page
- route position is easier to read than event chronology
- stage detail is explained inside the center navigation context
- intervention items feel ranked and authority-based
- activity and asset views support the route instead of competing with it
- the UI no longer depends on the phrase `Mission Board` to explain itself
