# Mission Board Minimum V2 Contract

_Last updated: 2026-04-15_

## Purpose

This document freezes the minimum `/api/v2/*` response contract needed to build the first project-first Mission Board.

It is intentionally narrower than the full backend model.

The goal is not to expose every field the backend can return.
The goal is to define the smallest stable frontend target that can replace the legacy Pipeline Dashboard as the primary work surface.

Related:

- `docs/ADR-017-main-api-design.md`
- `docs/Frontend-Mission-Board-Migration-Audit.md`
- `docs/Project-First-Backend-Architecture.md`

---

## Scope

This minimum contract is for the first Mission Board only.

It should be enough to render:

- project navigation
- project overview hero
- current and recent stage runs
- pending decisions
- key assets
- recent activity / event feed
- basic continue / resolve interactions

It does **not** try to preserve:

- legacy pipeline lifecycle controls
- chatroom-first messaging shell
- pipeline templates / pipeline files as first-class UX

---

## Endpoints in scope

The first Mission Board should rely on these endpoints only:

- `GET /api/v2/projects`
- `GET /api/v2/projects/{id}/overview`
- `GET /api/v2/projects/{id}/stage-runs`
- `GET /api/v2/stage-runs/{id}`
- `GET /api/v2/stage-runs/{id}/events`
- `GET /api/v2/projects/{id}/decisions`
- `GET /api/v2/decisions/{id}`
- `POST /api/v2/decisions/{id}/resolve`
- `GET /api/v2/projects/{id}/assets`
- `GET /api/v2/assets/{id}`
- `POST /api/v2/projects/{id}/continue`

`GET /api/v2/dashboard` may still exist for admin or summary surfaces, but it is not required as the main data source for the first Mission Board.

---

## Field freeze by screen block

## A. Project navigation

### Endpoint

- `GET /api/v2/projects`

### Minimum fields per project item

```json
{
  "id": 1,
  "name": "FitPet",
  "status": "draft",
  "current_stage": "briefing",
  "execution_mode": "autopilot",
  "health_status": "healthy",
  "current_focus": "Confirm project brief",
  "blocking_reason": null,
  "last_activity_at": "2026-04-15T02:00:00",
  "created_at": "2026-04-15T01:00:00"
}
```

### Frontend usage

Required for the first board:

- `id`
- `name`
- `status`
- `current_stage`
- `execution_mode`
- `health_status`
- `blocking_reason`
- `last_activity_at`

Optional in first board:

- `current_focus`
- `created_at`
- `slug`
- `description`
- `target_platforms`
- `target_users`
- `references`
- `legacy_mode`

---

## B. Project overview hero

### Endpoint

- `GET /api/v2/projects/{id}/overview`

### Minimum top-level shape

```json
{
  "project": {},
  "current_stage_run": {},
  "key_assets": [],
  "pending_decisions": [],
  "stage_summary": {},
  "recent_activity": [],
  "release_readiness": {},
  "recommended_next_action": "continue_project"
}
```

### Minimum fields used from `project`

```json
{
  "id": 1,
  "name": "FitPet",
  "one_line_vision": "Help pet owners manage feeding and exercise",
  "status": "draft",
  "current_stage": "briefing",
  "execution_mode": "autopilot",
  "health_status": "healthy",
  "current_focus": "Confirm brief",
  "blocking_reason": null,
  "latest_summary": null,
  "last_activity_at": "2026-04-15T02:00:00"
}
```

### Minimum fields used from `current_stage_run`

```json
{
  "id": 10,
  "stage_type": "briefing",
  "run_index": 1,
  "status": "waiting_for_decision",
  "lifecycle": {
    "phase": "active",
    "is_active": false,
    "is_terminal": false,
    "requires_attention": true
  },
  "summary": "Draft brief created",
  "started_at": "2026-04-15T02:01:00",
  "ended_at": null,
  "created_at": "2026-04-15T02:01:00"
}
```

### Minimum fields used from `stage_summary`

```json
{
  "total": 1,
  "completed": 0,
  "active": 1,
  "latest_completed_stage": null
}
```

### Minimum fields used from `release_readiness`

```json
{
  "has_prd": false,
  "has_release_pack": false,
  "pending_release_decision": false,
  "status": "not_ready",
  "next_gate": null
}
```

### Minimum fields used from `recommended_next_action`

Allowed first-board values should be treated as stable UI intents:

- `continue_project`
- `review_current_stage`
- `review_prd`
- `review_definition_bundle`
- `review_task_plan`
- `review_test_report`
- `review_release_pack`
- `resolve_scope_confirmation`
- `resolve_direction_confirmation`
- `resolve_release_approval`
- `resolve_decision`
- `review_project`

The frontend should map these to UI buttons or focus cues, not to backend-specific implementation assumptions.

---

## C. Stage lane

### Endpoint

- `GET /api/v2/projects/{id}/stage-runs`

### Minimum fields per item

```json
{
  "id": 10,
  "project_id": 1,
  "stage_type": "briefing",
  "run_index": 1,
  "status": "waiting_for_decision",
  "lifecycle": {
    "phase": "active",
    "is_active": false,
    "is_terminal": false,
    "requires_attention": true
  },
  "triggered_by": "system",
  "trigger_reason": "project_created",
  "summary": "Draft brief created",
  "started_at": "2026-04-15T02:01:00",
  "ended_at": null,
  "created_at": "2026-04-15T02:01:00"
}
```

### Contract rule

The first Mission Board assumes all stage chips/cards can be rendered from the serialized stage-run object alone.
No pipeline-stage lookup should be required.

---

## D. Stage detail drawer / panel

### Endpoint

- `GET /api/v2/stage-runs/{id}`

### Minimum top-level shape

```json
{
  "stage_run": {},
  "project": {},
  "input_assets": [],
  "output_assets": [],
  "decisions": [],
  "events": [],
  "summary": {
    "input_count": 0,
    "output_count": 1,
    "decision_count": 1,
    "event_count": 6
  }
}
```

### Minimum fields from asset items in `input_assets` / `output_assets`

```json
{
  "id": 100,
  "project_id": 1,
  "asset_type": "project_brief",
  "title": "Project Brief",
  "summary": "MVP brief draft",
  "status": "in_review",
  "is_current": true,
  "produced_by_stage_run_id": 10,
  "direction": "output",
  "updated_at": "2026-04-15T02:02:00"
}
```

### Minimum fields from decision items in `decisions`

```json
{
  "id": 200,
  "project_id": 1,
  "stage_run_id": 10,
  "decision_type": "scope_confirmation",
  "title": "Confirm MVP scope",
  "context_summary": "Please approve the generated brief",
  "recommended_option": "approve",
  "alternative_options": ["reject"],
  "impact_summary": "Approval unlocks definition stage",
  "requested_action": "Choose whether to accept the brief",
  "status": "pending",
  "resolved_option": null,
  "resolution_note": null,
  "created_at": "2026-04-15T02:03:00",
  "resolved_at": null
}
```

---

## E. Event feed

### Endpoint

- `GET /api/v2/stage-runs/{id}/events`

### Minimum fields per event

```json
{
  "id": 300,
  "project_id": 1,
  "stage_run_id": 10,
  "asset_id": 100,
  "event_type": "stage_execution_completed",
  "agent_name": null,
  "stage_name": "briefing",
  "summary": "Briefing stage completed",
  "payload": {},
  "created_at": "2026-04-15T02:04:00"
}
```

### Contract rule

For the first Mission Board, the event feed only assumes:

- chronological list rendering
- event title/summary rendering
- optional badges from `event_type`
- optional stage/agent attribution

The frontend should not assume pipeline websocket event names or `pipeline_*` prefixes.

---

## F. Decisions panel

### Endpoints

- `GET /api/v2/projects/{id}/decisions`
- `GET /api/v2/decisions/{id}`
- `POST /api/v2/decisions/{id}/resolve`

### Minimum fields per decision item

Use the same decision shape as above.

The board requires these fields to be stable:

- `id`
- `project_id`
- `stage_run_id`
- `decision_type`
- `title`
- `context_summary`
- `recommended_option`
- `alternative_options`
- `impact_summary`
- `requested_action`
- `status`
- `resolved_option`
- `resolution_note`
- `created_at`
- `resolved_at`

### Resolve request shape

```json
{
  "resolution": "approved",
  "selected_option": "approve",
  "note": "Looks good"
}
```

### Resolve response requirement

The first-board flow assumes the response gives enough information to refresh visible state deterministically.
At minimum the response should continue including:

- updated `decision`
- any directly affected `project`
- any created or updated `stage_run`, if applicable

If the exact response shape evolves, it should still preserve those three semantic buckets.

---

## G. Assets panel

### Endpoints

- `GET /api/v2/projects/{id}/assets`
- `GET /api/v2/assets/{id}`

### Minimum asset list item fields

```json
{
  "id": 100,
  "project_id": 1,
  "asset_type": "project_brief",
  "title": "Project Brief",
  "summary": "MVP brief draft",
  "version": 1,
  "status": "in_review",
  "is_current": true,
  "approval_decision_id": 200,
  "produced_by_stage_run_id": 10,
  "updated_at": "2026-04-15T02:02:00",
  "created_at": "2026-04-15T02:02:00"
}
```

### Minimum asset detail fields

```json
{
  "id": 100,
  "project_id": 1,
  "asset_type": "project_brief",
  "title": "Project Brief",
  "summary": "MVP brief draft",
  "content_json": {},
  "content_markdown": "# Brief",
  "version": 1,
  "status": "in_review",
  "is_current": true,
  "approval_decision_id": 200,
  "produced_by_stage_run_id": 10,
  "relationships": {
    "upstream": [],
    "downstream": []
  },
  "stage_links": [
    {"stage_run_id": 10, "direction": "output"}
  ],
  "decision_links": [
    {"decision_id": 200, "relation_role": "subject"}
  ],
  "updated_at": "2026-04-15T02:02:00",
  "created_at": "2026-04-15T02:02:00"
}
```

---

## H. Primary action: continue project

### Endpoint

- `POST /api/v2/projects/{id}/continue`

### Contract requirement

The Mission Board only needs this endpoint to support one simple interaction:

- user presses continue
- backend advances or attempts to advance the project
- response returns enough state to refresh current project, stage, and decision/asset visibility

At minimum, the response should keep exposing these semantic buckets if they already exist:

- `project`
- `stage_run` or equivalent current-stage object
- any newly created `decision`
- any newly created `asset`

The exact response envelope can vary, but those state transitions must stay inspectable.

---

## Non-goals for this contract

The first Mission Board contract does not require:

- `/api/pipelines/*`
- `/api/chatrooms/*`
- `/api/pipelines/ws`
- pipeline template metadata
- pipeline file-browser APIs
- chatroom IDs on primary UI objects

If legacy compatibility endpoints remain in the backend, they should be treated as compatibility or debug surfaces, not as required frontend dependencies.

---

## Practical rule for future backend refactors

When changing serializers or read-model builders, preserve these first-board fields unless there is a deliberate frontend migration with matching test updates.

In other words:

- backend layering may continue to evolve
- Mission Board field names and meanings should now stabilize

That is the whole point of freezing this minimum contract.
