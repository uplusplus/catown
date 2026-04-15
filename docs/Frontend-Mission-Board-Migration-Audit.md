# Frontend Mission Board Migration Audit

_Last updated: 2026-04-15_

> **Status note (current state)**
>
> This document started as a migration audit while `frontend/index.html` was still the dominant legacy shell.
>
> That is no longer the current frontend state.
>
> **Today, the default Catown frontend is the React/Vite/TypeScript Mission Board implemented in `frontend/src/`.**
> `frontend/index.html` now serves as the Vite shell, and the primary board already runs on core `/api/v2/*` project-first flows.
>
> Read the sections below as:
>
> - **historical evidence** explaining why the old shell had to be retired,
> - plus **migration guidance** for demoting remaining legacy pipeline/chatroom surfaces to compatibility or debug roles.
>
> This document should no longer be read as saying the legacy Pipeline Dashboard is still the active main frontend.

## Purpose

This document answers a narrower question than "how do we wire the old frontend to the new backend?"

That is the wrong optimization target.

The real question is:

**Which parts of the current frontend still have product value, which parts are legacy pipeline shell, and what should the new project-first Mission Board look like?**

The backend refactor already moved the domain center of gravity to:

- `Project`
- `StageRun`
- `Asset`
- `Decision`
- `Event`

So the frontend should now be reorganized around those objects instead of preserving the old `pipeline + chatroom` worldview.

---

## Audit conclusion (historical finding, now largely executed)

At audit time, `frontend/index.html` was still deeply coupled to the legacy pipeline model.

This was not a case of a few stale endpoints.

It still contained:

- a dedicated Pipeline tab in the status modal
- a standalone Pipeline Dashboard shell
- chatroom-based message loading and streaming
- pipeline-specific approval, rejection, start, pause, and resume controls
- pipeline artifacts and pipeline file-browser panels
- pipeline websocket subscription logic

That meant the correct next move was **not** to make the new backend imitate the old page.

That next move has now largely been carried out:

1. current UI blocks were audited for product value
2. a Mission Board information architecture was defined
3. the new primary screen was bound to `/api/v2/*`
4. the React Mission Board replaced the old dashboard as the default main surface

What remains is the cleanup tail: demoting or retiring remaining legacy pipeline/chatroom surfaces in slices.

---

## Evidence from current frontend

### Legacy endpoint families still consumed

#### Pipeline APIs

`frontend/index.html` still directly calls:

- `GET /api/pipelines`
- `POST /api/pipelines`
- `GET /api/pipelines/{id}`
- `POST /api/pipelines/{id}/start`
- `POST /api/pipelines/{id}/pause`
- `POST /api/pipelines/{id}/resume`
- `POST /api/pipelines/{id}/approve`
- `POST /api/pipelines/{id}/reject`
- `GET /api/pipelines/{id}/messages`
- `GET /api/pipelines/{id}/artifacts`
- `POST /api/pipelines/{id}/instruct`
- `GET /api/pipelines/{id}/files`
- `POST /api/pipelines/{id}/files`
- `GET /api/pipelines/config/templates`
- `WS /api/pipelines/ws`

#### Chatroom APIs

`frontend/index.html` still directly calls:

- `GET /api/chatrooms/{id}/messages`
- `POST /api/chatrooms/{id}/messages`
- `POST /api/chatrooms/{id}/messages/stream`

### High-coupling frontend regions

The highest-coupling regions in `frontend/index.html` are:

- chatroom message loading and streaming around `frontend/index.html:523`
- pipeline/gate event-card compatibility logic around `frontend/index.html:821`
- gate actions that auto-discover the current pipeline around `frontend/index.html:1317`
- project selection that joins websocket rooms via `chatroom_id` around `frontend/index.html:1835`
- status modal Pipeline tab around `frontend/index.html:2035`
- full Pipeline Dashboard shell starting around `frontend/index.html:3098`
- pipeline-specific websocket subscription around `frontend/index.html:3583`
- pipeline file browser around `frontend/index.html:3609`

### Test suite still encodes legacy worldview

`tests/test_frontend.py` is still framed as "Pipeline Dashboard" coverage.

It asserts or depends on:

- pipeline presence in the static page
- `chatroom_id` on projects
- `/api/chatrooms/*` message behavior
- `/api/pipelines/*` lifecycle behavior
- pipeline roles such as `analyst / architect / developer / tester / release`
- end-to-end flow as `project -> chatroom -> pipeline`

So the migration target is not only frontend code.
The frontend test contract also needs to be rewritten around the project-first product model.

---

## Keep / Drop / Rebuild

## Keep

These interaction goals still seem valuable and should survive, even if the implementation and layout change:

- project list and project switching
- one-screen visibility into current project state
- current stage progress and recent stage history
- recent events / execution log / timeline feel
- pending decisions with clear approve or reject actions
- asset visibility, especially key outputs and dependency links
- lightweight operational controls such as continue, retry, or inspect

## Drop

These are legacy shell concepts and should not be preserved as first-class product structure:

- Pipeline tab as a primary information architecture pillar
- pipeline create/start/pause/resume/approve/reject as the top-level control model
- chatroom as the main product entity for project work
- pipeline messages and pipeline artifacts as the dominant content partitions
- pipeline template chooser as the entry point for doing work
- pipeline-specific websocket and file-browser assumptions as page skeleton

## Rebuild

These are worth redesigning from scratch in project-first form:

- the main work surface should become a Mission Board, not a pipeline detail panel
- the right-side detail region should pivot between project overview, stage run detail, asset detail, and decision detail
- the activity feed should be event-first, not chatroom-first
- human input should attach to project/stage/decision context, not to a standalone chatroom shell
- approval controls should follow `Decision` and `StageRun` semantics, not pipeline pause-state semantics

---

## Proposed Mission Board information architecture

## A. Left rail: Project navigation

Purpose:

- switch project
- create project
- filter by status or stage

Suggested data:

- `GET /api/v2/projects`

Suggested item fields:

- project name
- current stage
- project status
- pending decision count
- last activity timestamp

## B. Main hero: Project overview

Purpose:

- show what this project is
- show where it is in the flow
- tell the user what needs attention now

Suggested data:

- `GET /api/v2/projects/{id}/overview`

Suggested blocks:

- project header
- one-line vision
- current stage badge
- execution mode
- release readiness summary
- recommended next action

## C. Stage lane

Purpose:

- replace pipeline-stage mental model with stage-run history and current execution

Suggested data:

- `GET /api/v2/projects/{id}/stage-runs`
- `GET /api/v2/stage-runs/{id}`
- `GET /api/v2/stage-runs/{id}/events`

Suggested blocks:

- current stage run card
- recent stage runs timeline
- stage status badges
- stage instructions / retry / continue entry points

## D. Decisions panel

Purpose:

- centralize human checkpoints

Suggested data:

- `GET /api/v2/projects/{id}/decisions`
- `POST /api/v2/decisions/{id}/resolve`

Suggested blocks:

- pending decisions list
- decision summary and context
- approve / reject controls
- post-decision expected next state

## E. Assets panel

Purpose:

- make generated outputs inspectable without pipeline artifact language

Suggested data:

- project overview asset summaries
- `GET /api/v2/assets/{id}`

Suggested blocks:

- key deliverables grid
- asset detail viewer
- dependency / provenance links
- latest generated vs approved state

## F. Event feed

Purpose:

- preserve the sense of system activity without anchoring on chatroom logs

Suggested data:

- project-level events if exposed directly
- otherwise aggregate from stage-run events in current scope

Suggested blocks:

- recent execution events
- warnings / failures
- tool or agent highlights when relevant
- filters for stage / severity / type

---

## Mapping from legacy UI blocks to project-first replacements

| Legacy block | Keep idea? | New home |
|---|---|---|
| Rooms sidebar | Yes | Project navigation |
| Chat message feed | Partly | Event feed + contextual notes/input |
| Pipeline stage cards | Yes | Stage lane / stage-run detail |
| Pipeline artifacts list | Yes | Assets panel |
| Pause / approve / reject pipeline buttons | Partly | Decision actions + stage-run actions |
| Pipeline templates modal | No | Remove from main UX |
| Pipeline file browser | Maybe later | Secondary workspace inspector, not core board |
| Pipeline websocket subscription | Yes, concept only | Unified v2 event source |
| Chatroom join/send logic | No as primary shell | Context-bound project/stage interaction |

---

## Suggested migration order

## Step 1: Freeze a thin v2 frontend contract

Before broad frontend coding, explicitly decide the minimum stable data the Mission Board needs from:

- `/api/v2/projects`
- `/api/v2/projects/{id}/overview`
- `/api/v2/projects/{id}/stage-runs`
- `/api/v2/stage-runs/{id}`
- `/api/v2/stage-runs/{id}/events`
- `/api/v2/projects/{id}/decisions`
- `/api/v2/decisions/{id}/resolve`
- `/api/v2/assets/{id}`

## Step 2: Build Mission Board alongside legacy dashboard

Do not start by mutating the old Pipeline Dashboard in place.

Add a new project-first board first, then migrate users and tests to it.

## Step 3: Switch the main entry view

Once the Mission Board can cover:

- project overview
- current stage
- pending decisions
- key assets
- recent activity

it should replace the Pipeline tab / dashboard as the primary surface.

## Step 4: Retire legacy dependencies in slices

Recommended retirement order:

1. remove Pipeline tab as a first-class status view
2. stop using `/api/pipelines/*` for main interaction
3. stop using `/api/chatrooms/*` as primary work log transport
4. rewrite `tests/test_frontend.py` around Mission Board behavior
5. demote remaining legacy pipeline/chatroom features to compatibility or debug surfaces

---

## Testing implications

`tests/test_frontend.py` should eventually stop asserting:

- pipeline lifecycle as core UX
- chatroom_id as mandatory project field for main flows
- pipeline dashboard presence on the root page

It should instead assert:

- Mission Board renders
- project overview loads
- stage-run detail loads
- decision resolution updates visible state
- asset detail renders
- event feed updates correctly

---

## Decision statement

We should not make the new project-first backend conform to the old Pipeline Dashboard.

We should reuse the valuable interaction goals from the current frontend, but rebuild the primary UI around:

- `Project`
- `StageRun`
- `Decision`
- `Asset`
- `Event`

That gives the frontend a product model aligned with the backend we are actually trying to build, instead of reintroducing legacy pipeline assumptions through the UI.
