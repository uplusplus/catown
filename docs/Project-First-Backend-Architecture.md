# Project-First Backend Architecture

_Last updated: 2026-04-15_

## Why we are still splitting downward

The current refactor is not about aesthetic cleanup.

It serves three concrete goals:

1. Separate business progression from response shaping
2. Prevent `ProjectService` from becoming the next god class
3. Stabilize `/api/v2/*` so frontend work has a fixed target

In plain terms: the backend is now runnable, but it is still being organized into something the frontend can safely depend on.

---

## Current architectural target

```text
routes/*_v2.py
  -> services/*
    -> orchestration/*
    -> read_models/*
    -> domain helpers (for example asset_service)
      -> models/* + alembic
```

### Layer intent

#### Routes

Routes should be thin.

They should mostly do:

- request parsing
- calling a service method
- returning HTTP responses

They should not own:

- dashboard aggregation
- overview/detail response assembly
- business state transitions

#### Services

Services are the application-facing business facade.

They should mostly do:

- expose stable entry points to routes
- hold transaction boundaries
- coordinate orchestration and read-model builders
- keep business actions discoverable

They should not become the final home of every query/detail serializer.

#### Read models

Read models own response shape for UI-facing queries.

Examples:

- dashboard
- project overview
- asset detail
- stage-run detail
- stage-run event list

This is where we want most contract-level API shape decisions to live.

#### Orchestration

Orchestration modules own multi-object progression logic.

Examples:

- project progression
- decision side effects
- bootstrap flow
- stage execution dispatch

They are responsible for moving the system forward, not for formatting API payloads.

#### Models + Alembic

This layer owns schema.

Key rule:

- schema drift should be resolved by migrations, not by startup-time patching

---

## What has already been moved

The current refactor already shifted several responsibilities:

- legacy pipeline startup coupling was removed from `backend/main.py`
- `projects` schema drift was formalized via Alembic
- `events` schema drift was formalized via Alembic
- `ProjectViewBuilder` now owns more of dashboard/overview aggregation
- `StageRunViewBuilder` now owns stage-run detail and event/list views
- `AssetViewBuilder` now owns asset detail and dependency views
- `projects_v2.py`, `assets_v2.py`, and `stage_runs_v2.py` are thinner than before
- v2 contract regression tests now cover the key response shapes

---

## Current module map

### Routes

- `backend/routes/projects_v2.py`
- `backend/routes/assets_v2.py`
- `backend/routes/decisions_v2.py`
- `backend/routes/stage_runs_v2.py`
- `backend/routes/dashboard_v2.py`

### Services

- `backend/services/project_service.py`
- `backend/services/asset_service.py`

### Read models

- `backend/read_models/project_views.py`
- `backend/read_models/stage_run_views.py`
- `backend/read_models/asset_views.py`
- `backend/read_models/serializers.py`

### Orchestration

- `backend/orchestration/project_flow_coordinator.py`
- `backend/orchestration/project_bootstrap_coordinator.py`
- `backend/orchestration/decision_effects.py`

### Execution kernel / runtime primitives

- `backend/execution/bootstrap_stage_executor.py`
- `backend/execution/event_log.py`
- `backend/execution/tool_audit.py`
- `backend/execution/llm_audit.py`
- `backend/execution/tool_dispatch.py`

---

## Why this matters before frontend work

The frontend still contains legacy `/api/pipelines/*` assumptions.

If frontend work starts before the v2 backend contract is stabilized, the UI will bind itself to moving targets and create churn on both sides.

So the current sequence is intentional:

1. stabilize schema ownership
2. thin route layer
3. move response shape into read models
4. keep v2 contract covered by tests
5. then expand frontend migration

---

## Near-term next steps

1. Freeze the minimum `/api/v2/*` contract needed by the future Mission Board
2. Audit the current frontend for which interaction blocks should be kept, dropped, or rebuilt
3. Build a new project-first Mission Board instead of extending the legacy Pipeline Dashboard
4. Rewrite frontend tests around project/stage-run/decision/asset flows once the new board exists

---

## One-line summary

The point of the current backend refactor is to turn the project-first backend from "runnable" into a stable, layered foundation that future frontend and runtime work can build on safely.
