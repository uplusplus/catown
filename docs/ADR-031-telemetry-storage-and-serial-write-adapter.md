# ADR-031: Telemetry Split DB and Serial Write Adapter
**Date**: 2026-05-26
**Status**: In Progress
**Decision Owner**: Catown Runtime

---

## Context

Catown had been writing business data, runtime audit events, network monitor data, and LLM audit rows into the same SQLite database: `catown.db`.

In single-agent streaming paths, several high-frequency write classes were competing for the same SQLite writer lock:

- business writes: `messages`, `task_runs`, `task_run_events`
- runtime audit writes: `llm_calls`, `tool_calls`, `events`
- monitor writes: `monitor_network_records`

The main amplification issues were:

1. many tiny transactions committed independently
2. all writes contending on the same primary SQLite file

Observed runtime failures showed repeated `sqlite3.OperationalError: database is locked` while monitor and audit writes continued to hit the database under streaming load.

---

## Decision

### 1. Split telemetry from the main business DB

Keep core business state in `catown.db`:

- `messages`
- `task_runs`
- `task_run_events`
- other primary business tables

Move telemetry-style data to `telemetry.db`:

- `monitor_network_records`
- `llm_calls`
- `tool_calls`
- `events`

### 2. Add a serial write adapter for telemetry

Telemetry writes should stop calling `Session.commit()` directly from request threads. Instead:

1. the caller builds a write request
2. the request enters an in-memory queue
3. a single background writer serializes writes
4. commits happen in batches

This explicitly converts concurrent callers into serialized SQLite writes.

### 3. Fix telemetry first, not all business writes at once

Phase 1 focuses only on the high-frequency telemetry paths:

- `monitor_network_records`
- `llm_calls`
- `tool_calls`
- `events`

`task_run_events` stays on the primary DB for now to avoid changing business consistency semantics in the same step.

### 4. Align SQLite settings for local desktop runtime

Both databases should use:

- `PRAGMA journal_mode=WAL`
- a shared configurable `busy_timeout`
- batched commits on the telemetry writer

---

## Rationale

### Why split telemetry first?

The highest write density and lock amplification came from observability paths, not from the core business tables. Splitting telemetry reduces primary-path interference with the least semantic risk.

### Why add a serial write adapter?

SQLite can handle concurrent readers, but true concurrent writers still serialize internally. The correct fix is not "let more threads write directly", but "absorb concurrency at an adapter and commit in order".

### Why not move `task_run_events` yet?

`task_run_events` still carries business-facing event semantics and immediate visibility assumptions. Moving it async in the same change would expand risk beyond the telemetry bottleneck.

---

## Design

### 1. Telemetry DB configuration

Add:

- `TELEMETRY_DATABASE_URL`
- `TELEMETRY_SQLALCHEMY_DATABASE_URL`
- dedicated telemetry engine and session factory

Default paths:

- main DB: `~/.catown/state/catown.db`
- telemetry DB: `~/.catown/state/telemetry.db`

### 2. Serial write adapter

Primary component:

- `backend/services/telemetry_writer.py`

Responsibilities:

- queue write requests
- run a single writer worker
- batch DB work
- support `flush()` and shutdown drain
- return IDs for synchronous audit helper call sites that still need immediate linkage

### 3. Monitor write integration

`MonitorNetworkBuffer.append()` now:

- appends to memory buffer first
- enqueues telemetry persistence
- no longer commits from the request thread

### 4. Audit write integration

`audit_recorder.py` now records through the telemetry writer for:

- `LLMCall`
- `ToolCall`
- `Event`

The public helper API shape remains stable, but the internals no longer perform direct DB commits.

### 5. Lifecycle

On startup:

- initialize telemetry DB
- start the telemetry writer on demand

On shutdown:

- drain queued writes
- flush remaining batches
- stop the writer

---

## Implementation Plan

### Phase 1: Telemetry split infrastructure

- [x] **1-1**: add telemetry DB config
- [x] **1-2**: add telemetry engine and session factory
- [x] **1-3**: bind audit models to telemetry metadata
- [x] **1-4**: initialize telemetry tables on startup

### Phase 2: Serial write adapter

- [x] **2-1**: add telemetry writer
- [x] **2-2**: support single-writer queue plus batched commit
- [x] **2-3**: support flush, shutdown, and error logging

### Phase 3: Move monitor hot writes

- [x] **3-1**: switch `monitor_network_records` to queued writes
- [x] **3-2**: remove direct commit hotspots from `network_buffer.py`

### Phase 4: Move audit hot writes

- [x] **4-1**: switch `llm_calls` to queued writes
- [x] **4-2**: switch `tool_calls` to queued writes
- [x] **4-3**: switch `events` to queued writes
- [x] **4-4**: remove scattered direct commits from `audit_recorder.py`

### Phase 5: SQLite settings and verification

- [x] **5-1**: enable WAL and busy timeout for the primary DB
- [x] **5-2**: enable WAL and busy timeout for the telemetry DB
- [x] **5-3**: add dedicated writer tests
- [ ] **5-4**: validate that single-agent streaming no longer blocks on telemetry writes under real incident load

---

## Progress Notes

### 2026-05-26

- Added this ADR and locked the direction to `main business DB + dedicated telemetry DB + single serialized writer`.
- Completed Phase 1: telemetry config, dedicated engine/session, dedicated metadata, and startup table creation.
- Completed the first part of Phase 2/3: introduced `TelemetryWriter` and moved `monitor_network_records` to background serialized writes.
- Telemetry tables now write to `~/.catown/state/telemetry.db` instead of the main metadata bundle.

### 2026-05-27

- Extended `TelemetryWriter` to support queued and batched writes for `LLMCall`, `ToolCall`, and `Event`, including synchronous ID return for call sites that need linkage.
- Migrated `audit_recorder.py` off direct audit commits so runtime audit helpers now persist through the telemetry writer.
- Removed invalid ORM relationships between `LLMCall` and `ToolCall` after the telemetry split, because cross-table foreign keys were intentionally dropped for the separate SQLite DB design.
- Verified the new audit path with `pytest backend/tests/test_audit_recorder.py -q` and a local smoke run covering create/finalize/event persistence through `telemetry_writer`.
- Added dedicated `telemetry_writer` tests covering record persistence, `flush()` semantics, and 20-thread concurrent create calls serialized into the telemetry DB without lock failures.

---

## Consequences

### Positive

- high-frequency telemetry writes no longer contend directly with primary business writes on the same SQLite file
- SQLite's single-writer constraint is absorbed explicitly by the adapter
- additional telemetry producers can move to the same writer without changing the business DB path

### Negative

- telemetry persistence becomes briefly asynchronous from the request thread perspective
- shutdown/flush and queue backpressure handling become important runtime responsibilities
- the storage path evolves from direct ORM writes to an adapter layer, increasing implementation complexity

---

## Related

- [ADR-010: Monitoring Audit Visualization](./ADR-010-monitoring-audit-visualization.md)
- [ADR-014: Network Monitor Semantics](./ADR-014-network-monitor-semantics.md)
- [ADR-029: TaskRun Hanging Analysis and Fix](./ADR-029-taskrun-hanging-analysis-and-fix.md)
- [ADR-030: Event-Sourced TaskRun State](./ADR-030-event-sourced-taskrun-state.md)
