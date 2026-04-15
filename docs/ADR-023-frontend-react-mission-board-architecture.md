# ADR-023: Frontend React Mission Board Architecture

**Date**: 2026-04-15
**Status**: Accepted
**Related**: `docs/Frontend-Mission-Board-Migration-Audit.md`, `docs/Mission-Board-Minimum-V2-Contract.md`, `docs/Mission-Board-Information-Architecture.md`

---

## Decision

Catown frontend now moves directly to a new `React + Vite + TypeScript` Mission Board.

We are not doing a compatibility-first migration of the legacy Pipeline Dashboard.

We are also not continuing the giant single-file `frontend/index.html` architecture as the primary frontend implementation model.

Instead, the new frontend should:

- use `React` for view composition
- use `TypeScript` for contract-facing state safety
- use `Vite` as the build and dev entry
- organize UI around the project-first domain model
- treat legacy `pipeline/chatroom` flows as deprecated, not as primary skeleton

---

## Why

### 1. The backend worldview already changed

The backend center of gravity is now:

- `Project`
- `StageRun`
- `Decision`
- `Asset`
- `Event`

A frontend that stays organized around `Pipeline + Chatroom` would keep fighting the new backend shape.

### 2. The old single-file frontend is no longer a healthy base

The previous `frontend/index.html` accumulated:

- page shell
- rendering logic
- state management
- websocket logic
- pipeline actions
- chatroom flows
- file browser logic

This made it too easy for old product assumptions to remain embedded in the implementation.

### 3. The repo already has the right technical base for a modern frontend

`frontend/package.json` and `frontend/vite.config.ts` already provide:

- React
- TypeScript
- Vite

So the most reasonable "one-step" architecture is to use the stack that is already present, instead of inventing a second transitional architecture.

---

## What we rejected

### Rejected: keep extending the giant single HTML file

Reason:

- keeps product and implementation coupled to legacy pipeline worldview
- difficult to maintain
- difficult to test and evolve cleanly

### Rejected: compatibility-first UI migration

Reason:

- would force the new frontend to inherit old Pipeline Dashboard information architecture
- would encourage backend contract drift back toward legacy endpoints

### Rejected: framework migration later, plain JS modules now

Reason:

- good as an incremental path, but no longer the best choice once the user explicitly wants the final architecture now
- the repo already has React/Vite tooling ready

---

## Adopted frontend structure

```text
frontend/
  index.html
  src/
    main.tsx
    App.tsx
    api/
      client.ts
    components/
      ProjectRail.tsx
      ProjectHero.tsx
      NextActionStrip.tsx
      StageLane.tsx
      DecisionPanel.tsx
      AssetPanel.tsx
      ActivityFeed.tsx
      DetailRail.tsx
    lib/
      format.ts
    types.ts
    styles.css
```

---

## Architectural rules

### 1. Project-first state model

Frontend state should be centered on:

- selected project
- overview
- stage runs
- decisions
- assets
- events
- selected detail target

Not on:

- chatrooms
- pipeline runs
- pipeline messages
- pipeline artifact boxes

### 2. V2 API only for primary board flows

The primary Mission Board should rely on `/api/v2/*` endpoints.

Legacy `/api/pipelines/*` and `/api/chatrooms/*` are not required dependencies for the new main surface.

### 3. Detail inspection stays inside the board

The selected stage, decision, asset, or event should render inside the detail rail instead of redirecting the user into disconnected old views.

### 4. Read-first before control-heavy

The new board should first be excellent at:

- understanding project state
- surfacing decisions
- showing outputs
- showing recent activity

Then it can grow more controls.

---

## Immediate implications

1. `frontend/index.html` becomes the Vite shell, not the old application implementation.
2. The legacy Pipeline Dashboard is no longer the main frontend architecture.
3. Frontend work should continue by improving the new React Mission Board rather than reviving old pipeline/chatroom panels.
4. Frontend tests will need to be rewritten around Mission Board behavior.

---

## Implementation plan

### Phase 1: Foundation reset

Already started:

- convert `frontend/index.html` into the Vite shell
- establish `frontend/src/` React/TypeScript entry structure
- connect the first Mission Board read flow to `/api/v2/*`

### Phase 2: Main board stabilization

Next focus:

- stabilize `ProjectRail`, `ProjectHero`, and `NextActionStrip` as the persistent board context
- sharpen `StageLane` so project progression is visually obvious
- decide whether the first `ActivityFeed` is stage-scoped or project-scoped

### Phase 3: Detail workbench deepening

Then:

- deepen `DetailRail` so stage, decision, asset, and event modes are all genuinely useful
- ensure each mode exposes enough related context to keep the user inside the board

### Phase 4: State and interaction consolidation

Then:

- reduce `App.tsx` orchestration weight by moving state/query concerns into clearer frontend modules
- separate board state, selected-entity state, and async loading/error state
- tighten continue / resolve refresh behavior
- improve empty states, loading states, and action-result feedback

### Phase 5: Test and delivery switch

Finally:

- rewrite `tests/test_frontend.py` around React Mission Board behavior instead of Pipeline Dashboard behavior
- keep `npm run build` as the frontend release check
- treat `frontend/dist/index.html` as the production root artifact served by the backend
- delete legacy dashboard assumptions only after the new board is stable enough to be the default surface

---

## One-line summary

The frontend now officially moves to a React/Vite/TypeScript Mission Board organized around `Project / StageRun / Decision / Asset / Event`, with no compatibility-first commitment to the legacy Pipeline Dashboard.
