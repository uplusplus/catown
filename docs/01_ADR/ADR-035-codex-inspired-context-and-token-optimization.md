# ADR-035: Codex-Inspired Context and Token Optimization

**Status**: Implemented for core rollout; Responses provider features are optional
**Date**: 2026-06-01
**Decision maker**: BOSS
**Related**: ADR-009 Context Compression, ADR-012 LLM Session Context Management, ADR-015 Codex-style Runtime Evolution, ADR-026 Dynamic Context Control, ADR-028 Context Compression Early Trigger Fix, ADR-034 Monitor Performance

---

## 1. Context

Catown currently has two active runtime-cost problems:

1. Context compaction is triggered too quickly.
2. Input token usage is too high.

The current codebase already has important building blocks:

- `backend/services/context_builder.py`: `ContextSelector`, fragment ranking, truncation, prompt diagnostics.
- `backend/services/chat_prompt_builder.py`: selector profiles and model-window-aware materialization.
- `backend/services/runtime_event_helpers.py`: context budget and semantic compaction event emission.
- `backend/routes/monitor.py` and Monitor projections: selector-budget and token visibility.
- `backend/llm/client.py`: OpenAI-compatible Chat Completions calls, streaming usage collection, and network capture.

Live WSL runtime evidence on 2026-06-01:

- Runtime config uses `gpt-5.5` with `contextWindow=1000000`.
- `task_run_events` contains 70 legacy `context_compaction` events, most of which are selector-budget changes rather than real semantic compression.
- Recent legacy compaction events showed a model input window of 875000 tokens, but old selector overrides limited interactive fragments to 3200 tokens and `turn` scope to 500 tokens.
- `runtime_card_projections` showed 18 `llm_call` cards totaling about 125797 input tokens and 4074 output tokens.
- Recent Valet calls were input-heavy: roughly 7k-9k input tokens for about 200 output tokens.

This means early compaction is primarily a selector-budget and context-shape issue, not actual exhaustion of the model context window. High token cost is primarily an input-side issue.

---

## 2. Codex Lessons To Adopt

Codex-style optimization is not one compression algorithm. It is a layered architecture:

1. **State reuse before compression**
   - Use stateful Responses flows, `previous_response_id`, and WebSocket mode where available.
   - As of 2026-06-03, OpenAI-compatible `/responses` support is still not widely available across Catown's tested providers, so Responses/WebSocket is an optional provider capability rather than the ADR's primary rollout dependency.
   - This reduces repeated request assembly, repeated transport cost, repeated tool-schema processing, and latency.
   - It must not be documented as a guaranteed billing-token reduction. OpenAI documents that previous input tokens referenced by `previous_response_id` are still billed as input tokens.

2. **Explicit compaction when needed**
   - Treat compaction as a semantic lifecycle event, not a silent side effect.
   - Prefer provider-native `/responses/compact` when supported.
   - Keep a local summary fallback for OpenAI-compatible providers that only support Chat Completions.

3. **Context as a budgeted product layer**
   - The selector profile is a product behavior budget, not the model limit.
   - Large-window models still need caps because cost, latency, and attention quality degrade when prompts are too large.

4. **Artifacts over raw transcript**
   - Long tool output, file contents, browser dumps, test logs, and research results should be stored as artifacts with a compact prompt-visible summary.
   - The prompt should carry IDs, paths, hashes, and short summaries, not whole logs.

5. **Tool/schema governance**
   - Tool definitions and tool outputs are input-token cost centers.
   - Expose only the tools needed for the active agent, mode, and turn.

Primary references:

- OpenAI WebSocket mode: `https://developers.openai.com/api/docs/guides/websocket-mode`
- OpenAI conversation state: `https://platform.openai.com/docs/guides/conversation-state`
- OpenAI Responses compaction: `https://platform.openai.com/docs/api-reference/responses/input-tokens`
- OpenAI WebSocket performance article: `https://openai.com/index/speeding-up-agentic-workflows-with-websockets/`

---

## 3. Decision

Catown will optimize context and token usage through five layers:

1. **Token observability layer**
   - Record per-turn token categories before and after request assembly.
   - Persist enough diagnostics to answer which component caused cost or compaction.

2. **Selector profile layer**
   - Keep `ContextSelector` as the first-line budget control.
   - Make profiles ratio-aware and model-window-aware.
   - Stop treating any small fragment truncation as equivalent to dangerous context exhaustion.

3. **Artifactized context layer**
   - Store large tool outputs and external content outside the prompt.
   - Inject compact summaries and stable artifact references.

4. **Stateful provider layer**
   - Add a provider abstraction for Responses state: last response id, provider conversation id, and compaction checkpoint.
   - Use Chat Completions as the default runtime shape and Responses as an opportunistic provider-specific enhancement.

5. **Provider compaction layer**
   - Use `/responses/compact` or equivalent provider-native compaction when the session approaches real input-window pressure.
   - Preserve local structured summary fallback.

---

## 4. Architecture

```text
User turn
  -> assemble static/runtime context
  -> estimate token categories
  -> select fragments by profile budget
  -> summarize or artifactize large tool/history payloads
  -> choose provider mode
       -> Responses stateful session when supported
       -> Chat Completions fallback otherwise
  -> stream result and tool calls
  -> persist usage, compaction, artifacts, and provider state
```

### 4.1 Token Categories

Each LLM turn should report:

| Category | Examples |
| --- | --- |
| `system_static` | agent identity, global runtime rules |
| `developer_context` | tool policy, skills, stage contract |
| `history` | retained recent chat messages |
| `current_input` | current user message and protocol tail |
| `runtime_fragments` | project, task, run, stage, memory, handoff facts |
| `tool_schema` | OpenAI function/tool definitions |
| `tool_output_inline` | prompt-visible shell/browser/test/tool result text |
| `artifact_refs` | compact references to persisted artifacts |
| `provider_usage` | returned prompt/completion/total usage |

### 4.2 Compaction Vocabulary

Catown should distinguish:

- `selection_truncation`: selector shortened or dropped a low-priority fragment.
- `prompt_budget_pressure`: prompt is nearing the configured product budget.
- `model_window_pressure`: prompt is nearing the provider input window.
- `semantic_compaction`: a summary or provider compact checkpoint replaced prior context.

Only `semantic_compaction` should be treated as real context compression. Selector truncation is a budgeting event.

---

## 5. Development Plan

### Phase 0: Runtime Baseline And Budget Sanity

**Goal**: Stop false early compaction and make current token usage explainable.

**Implementation status on 2026-06-03**: complete for core rollout. Catown now separates selector budget changes from semantic compaction in new runtime events, Monitor reads the new context-budget API shape, and the WSL live config reload exposes `chat_interactive`, `consult_agent`, and `fallback_chat` selector profiles.

Tasks:

1. Verify live `context.selector_profiles` after config reload.
2. Keep `chat_interactive` ratio-derived caps for large windows, e.g. 30000+ fragment tokens for 1M context models.
3. Add a Monitor-visible distinction between selection truncation and semantic compaction.
4. Add token category estimates to `context_budget_event` diagnostics and expose them in Monitor.
5. Add a regression test using `gpt-5.5`/1M context config that proves first-turn context does not hard-compact.

Acceptance:

- A simple first turn on a 1M context model does not emit semantic compaction.
- Monitor can show why any selector truncation happened.
- Context budget events show token breakdown by major category.

Implemented pieces:

- `selection_changed`, `event_kind`, `context_pressure_kind`, and `semantic_compaction` are recorded in selector diagnostics.
- Selector budget changes emit `context_budget_event`; only real semantic compression continues to use `context_compaction`.
- Monitor exposes `/api/monitor/context-budget-events`, `overview.context_budget`, `recent_context_budget_events`, `system.stats.context_budget_events`, and `system.stats.semantic_compactions`.
- Task-run detail snapshots expose `latest_context_budget_event`.
- Prompt diagnostics include token categories such as `system_static`, `developer_context`, `runtime_fragments`, `history`, `current_input`, and `tool_output_inline`.
- `gpt-5.5`/1M-window selector regression coverage proves a simple first turn remains `selection_pass` and does not semantically compact.
- WSL live runtime config verification on 2026-06-02 confirmed `context.selector_profiles` contains `chat_interactive`, `consult_agent`, and `fallback_chat`; legacy selector-era `context_compaction` rows were cleared from the live WSL DB after backup so new Monitor semantic-compaction counts start from the ADR-035 event contract.

### Phase 1: Tool Output And Artifact Budget

**Goal**: Reduce input tokens without losing inspectability.

**Implementation status on 2026-06-03**: complete for core rollout. The tool-output path now bounds model-visible tool protocol results with head/tail summaries and full-output references. Raw `run_shell` output remains recoverable through the existing tee/log metadata path.
`consult_agent`/`query_agent` task-run calls now carry consult step references through structured tool metadata so long consultation answers can be summarized without losing their runtime lineage.
Long non-shell tool outputs now persist recoverable artifacts under `STATE_DIR/tool_outputs` and inject only artifact path/hash references plus bounded excerpts into prompt-visible tool messages.
Prompt-visible summaries also preserve tool-family key signals for shell/test, browser, and search/fetch outputs so the model can see commands, test counts, failure hints, URLs, titles, HTTP status, queries, and source hints without reading the full output.

Tasks:

1. Extend existing output filtering to run before prompt injection for `run_shell`, browser, test, build, and search-like outputs.
2. Store raw long outputs as artifacts or tee files.
3. Inject a compact summary with artifact path/id, command, exit code, failing tests/errors, and tail pointer.
4. Apply the same pattern to `consult_agent` and `query_agent`: return structured summaries by default, with expandable raw references.
5. Add tests for large pytest/build/browser outputs.

Acceptance:

- Tool-heavy turns reduce inline tool-output tokens by at least 60%.
- Raw output remains recoverable from artifact/tee references.
- Prompt-visible summaries preserve failure diagnostics.

Implemented pieces:

- `ToolResultRecord.to_message()` now feeds the model a bounded prompt-visible result instead of the raw stored result.
- Long current-turn tool results are summarized with a compact head/tail excerpt from the original output and `[truncated for context budget]`.
- `build_tool_result_record()` records `context_budget` metadata with original, stored, and prompt-visible character counts.
- Existing `run_shell` tee/log metadata is surfaced as prompt-visible references: full output tee, tracked process log, redirected output, and tracked process token.
- `consult_agent`/`query_agent` results executed through the tool registry surface consult step, task-run, and client-turn references when available.
- Long non-shell tool results executed through the tool registry are written to `STATE_DIR/tool_outputs` with `tool_output_artifact` metadata containing path, SHA-256, character count, tool name, task run id, and tool call id when available.
- Generic artifact references are surfaced in model-visible summaries as `Tool output artifact` and `Tool output sha256`, so browser/search/build-like outputs can be bounded without losing raw-output recovery.
- Prompt-visible long-output summaries include extracted key signals for `run_shell`/pytest, `browser`, `web_search`, `web_fetch`, and search-like tools.
- Older tool-round summaries use the same bounded prompt-visible result, preventing compacted tool history from reintroducing long raw output.
- Prompt diagnostics and Monitor context-budget projections expose `tool_output_budget` estimates, including summarized message count, original/stored/prompt-visible size, estimated saved tokens, and savings percentage.
- Tool-output budget diagnostics are grouped by tool name in both event detail and Monitor overview rollups, making high-cost tool families visible.
- Monitor overview now exposes context-budget trend buckets so tool-output and tool-schema token savings can be inspected over time.
- Prompt-visible long-output summaries now preserve richer browser/search/build signals: nested browser page/response URL/title/status/output path, structured web-search top results, shell exit codes, and pytest failed targets.
- Phase 5 evaluation can now derive tool-heavy before/after observations directly from `context_budget_event` payloads, including inline tool-output token reduction and estimated average input-token reduction.

### Phase 2: Tool Schema And Capability Profiles

**Goal**: Reduce repeated tool-schema and irrelevant capability cost.

**Implementation status on 2026-06-03**: complete for core rollout; profile tuning remains an ongoing operations task. Catown now estimates tool schema byte/token cost for active LLM tool schemas, records it in context-budget diagnostics, emits budget events for non-zero schema cost, and surfaces top schema-cost tools in Monitor.
The second slice adds config-driven tool capability profiles that filter the LLM-visible schema surface before each request while retaining the original agent tool whitelist for backend authorization and diagnostics.

Tasks:

1. Measure tool schema token cost per agent and per mode.
2. Introduce capability profiles: interactive chat, code/debug, browser, document, orchestration, consult.
3. Expose only active tools for the current agent and mode.
4. Shorten tool descriptions and schemas where behavior is already governed by backend policy.
5. Add Monitor diagnostics for active tool count and schema token estimate.

Acceptance:

- Tool schema token cost is visible.
- Default Valet/Developer turns do not inject unrelated tool schemas.
- Tool availability remains governed by backend policy, not prompt-only text.

Implemented pieces:

- Prompt diagnostics include `tool_schema_budget` and a `tool_schema` token category.
- Context-budget events are emitted for non-zero tool schema cost without marking it as semantic compaction.
- Monitor context-budget detail shows per-tool schema cost; overview rolls up top schema-cost tools.
- Runtime `context.tool_capability_profiles` define active schema surfaces for `interactive_chat`, `code_debug`, `browser`, `document`, `orchestration`, and `consult`.
- `prepare_chat_turn_runtime()` filters `available_tools` and `tool_schemas` before LLM calls, while preserving `raw_available_tools`, `tool_schemas_before_filter`, and `tool_schema_filter` diagnostics.
- Pipeline stage execution uses the same profile filter for LLM-visible tool schemas while retaining the original stage/agent tool whitelist for execution authorization.
- Prompt diagnostics now show original schema tokens, active schema tokens, estimated saved schema tokens, profile name, active tools, excluded tools, and top excluded schema-cost tools.
- Monitor overview now rolls up active schema cost and schema tokens saved by capability filtering.
- Monitor overview exposes token-savings trend buckets and per-agent/profile/mode schema recommendations for frequently filtered tools and keyword-activated conditional groups.
- Default `interactive_chat` capability profiles keep collaboration dispatch/status tools available, but move collaborator-listing tools (`list_agents`, `list_collaborators`) behind an explicit collaboration keyword group so ordinary turns do not pay for those schemas.

Optional ongoing tuning:

- Continue tuning default profiles against live usage traces, using Monitor schema recommendations to identify additional over-broad always-on tools.

### Phase 3: Stateful Responses Provider

**Goal**: Adopt Codex-style state reuse where provider support exists.

**Implementation status on 2026-06-03**: complete as an optional provider capability. Catown now has a provider-session state layer, Monitor-visible provider mode diagnostics, config-controlled Responses HTTP streaming, and config-controlled Responses WebSocket streaming. Chat Completions remains the default execution path; Responses modes can be enabled per global or agent runtime config with `runtime.provider_mode=responses_http` or `runtime.provider_mode=responses_websocket` when a provider actually supports those endpoints.

Tasks:

1. Add a provider session model for `last_response_id`, provider conversation id, mode, and compact checkpoint id.
2. Add a Responses provider path beside existing Chat Completions.
3. Use `previous_response_id` or conversation state for follow-up turns.
4. Support streaming text and function/tool calls through the existing turn loop.
5. Fall back to Chat Completions for OpenAI-compatible providers without Responses support.
6. Make billing expectations explicit in docs and Monitor: state reuse reduces repeated transport/assembly and latency, not necessarily billed input tokens.

Acceptance:

- Follow-up OpenAI Responses turns reuse provider state.
- Chat Completions fallback remains behavior-compatible.
- Monitor shows provider mode: `chat_completions`, `responses_http`, or `responses_websocket`.

Implemented pieces:

- `llm_provider_sessions` records active provider state per chatroom, agent, model, endpoint host, and provider mode.
- Provider sessions track `last_response_id`, provider conversation id, compact checkpoint id, turn count, status, and last-used timestamps without storing API keys.
- Streaming LLM request/response task events now include `provider_mode` and `provider_session` diagnostics.
- Streaming LLM runtime cards include provider mode/session payloads, allowing Monitor to show provider mode per card.
- Monitor overview rolls up `usage_window.provider_modes` with calls, state-reuse count, and response-id coverage.
- Frontend Monitor overview and runtime feed display provider mode and state reuse.
- `backend/llm/client.py` supports `responses_http` in `chat_stream()`, converts Chat Completions-style messages/tools into Responses input/tools, streams text deltas, maps Responses function-call events back to the existing tool loop shape, and emits real `response_id` values.
- `iter_stream_turn_events()` passes provider-session `previous_response_id` to clients that support it, so follow-up Responses HTTP turns can reuse provider state.
- When `responses_http` has a `previous_response_id`, the client now sends conservative delta input instead of the full assembled transcript: trailing tool outputs become `function_call_output` items, otherwise only the latest user message is sent while system/developer instructions remain explicit.
- Global and per-agent Settings now persist `runtime.provider_mode`, show effective provider mode, clear cached clients on update, and test the matching Chat Completions or Responses endpoint.
- Responses request diagnostics now surface `stateful_delta`, full/sent/omitted input item counts, estimated full/sent/omitted input tokens, and estimated instruction tokens. Monitor aggregates omitted item/token estimates, provider-reported input/output usage tokens, and average first-token/completion timings by provider mode, then shows per-call delta input savings in the runtime feed.
- Task-run timeline/activity projections surface provider delta summaries and provider request diagnostics, so a single run can be audited without relying only on Monitor overview.
- Responses HTTP request/stream behavior is covered by mocked SDK tests for text, function calls, tool schema conversion, and previous-response state propagation.
- `responses_websocket` uses OpenAI WebSocket mode as the transport for the same Responses request body, sends a top-level `response.create` payload without HTTP-only `stream` flags, preserves stateful delta input and compact-window reuse, maps Responses text/tool-call events into Catown's existing turn loop, and records WebSocket transport diagnostics in Monitor network events.
- Settings now enables `responses_websocket`, and provider connection tests use the selected provider mode instead of falling back to Chat Completions for WebSocket.
- If a Responses HTTP-compatible provider rejects `previous_response_id`, Catown retries once with the full context before any visible output and records `response_state_fallback=true` in request diagnostics, keeping `responses_http` usable on providers that reserve state reuse for WebSocket.

Live validation on 2026-06-02:

- WSL runtime provider `https://testvideo.site/v1` with model `gpt-5.5` successfully handled a minimal `responses_http` stream, returned content, `response_id`, usage, and timing diagnostics.
- The same provider rejected HTTP `previous_response_id` with `previous_response_id is only supported on Responses WebSocket v2`; after the fallback change, Catown retried with full context and completed the turn while exposing `response_state_fallback=true`.
- The same provider accepted the corrected WebSocket payload shape but closed the connection with `1013 no available account`, so state reuse over WebSocket remains blocked by provider capacity rather than Catown request shape.
- `https://token-plan-cn.xiaomimimo.com/v1` listed models and successfully handled ordinary Chat Completions with `mimo-v2.5-pro`, but both HTTP `/responses` and WebSocket `/responses` returned 404. It is usable as a Chat Completions provider, not as a Responses/WebSocket validation target.
- `https://api.chatanywhere.tech/v1` listed models and successfully handled ordinary Chat Completions with `gpt-4o-mini-ca` and `gpt-5.5-ca`; HTTP `/responses` returned provider 403 for the tested free key/model combinations, and WebSocket `/responses` returned 405. It is usable as a Chat Completions provider, but this key/provider combination is not a Responses WebSocket validation target.
- `https://api.laozhang.ai/v1` successfully handled HTTP `/responses` with `gpt-4.1`, returned a standard `resp_...` id and usage, and worked through Catown's `responses_http` path. HTTP `previous_response_id` returned `previous_response_not_found` for a freshly returned response id, so Catown fell back to full-context resend and completed the turn. WebSocket `/responses` returned 404, so this provider validates HTTP Responses but not Responses WebSocket state reuse.

Optional follow-up:

- Re-test Responses WebSocket only when a provider/account with real WebSocket `/responses` support is available.
- Validate provider-reported usage against billed provider usage before making any billing-token claims for Responses-specific modes.

### Phase 4: Provider-Native Compaction

**Goal**: Use real semantic compaction only when it is warranted.

**Implementation status on 2026-06-03**: complete for core rollout through local structured checkpoints; provider-native Responses compaction is optional. Catown now has provider-native Responses compaction for compatible `responses_http` and `responses_websocket` sessions plus a local structured summary fallback. Provider-native compaction calls `/responses/compact`, stores the returned canonical output window under `STATE_DIR/provider_compaction`, exposes it through provider-session metadata only for the next request after the source `last_response_id`, and then returns to normal Responses state reuse after the next `response_id` is recorded. Local fallback still persists the six-section structured checkpoint and is used for explicit API compaction, unsupported providers, or provider-native failures.

Tasks:

1. Trigger provider compaction only on model-window pressure or explicit user/admin request.
2. Use `/responses/compact` for supported OpenAI Responses sessions.
3. Persist compact checkpoints and replacement summaries.
4. Add local structured summary fallback with sections:
   - current objective
   - constraints and decisions
   - files/artifacts
   - tool outcomes
   - pending steps
   - risks and open questions
5. Add recovery tests for compaction followed by tool calls, retries, and resumed task runs.

Acceptance:

- Real semantic compaction is rare in normal 1M-window interactive turns.
- Compaction summaries preserve enough state to continue the task.
- Monitor can inspect compact checkpoint lineage.

Implemented pieces:

- `backend/services/provider_compaction.py` creates local `local_structured_summary` checkpoints with the six Phase 4 sections: current objective, constraints and decisions, files/artifacts, tool outcomes, pending steps, and risks/open questions.
- Local checkpoints persist as JSON files under `STATE_DIR/provider_compaction` and remain recoverable by checkpoint id.
- Provider-session rows are updated with the latest compact checkpoint id, while metadata keeps local and provider-native checkpoint histories for lineage.
- `backend/llm/client.py` supports provider-native Responses compaction through `compact_responses_context()`, converting Catown's chat/messages/tools into a full Responses context window for `/responses/compact`.
- Provider-native compaction can run for both Responses HTTP and Responses WebSocket sessions; `/responses/compact` remains the HTTP compaction endpoint even when normal answer generation uses WebSocket transport.
- Provider-native checkpoints persist the compact endpoint's `output` window, usage, request diagnostics, source context-budget event id, and source `last_response_id`.
- The next Responses request consumes the compacted output window as canonical input and intentionally omits `previous_response_id`; once that request records a new `response_id`, the compact window is no longer considered ready for reuse.
- `maybe_create_provider_or_local_compaction_checkpoint()` gates automatic semantic compaction to model-window pressure or explicit user/admin reasons, tries provider-native compaction for compatible `responses_http` sessions, falls back locally on unsupported providers or provider errors, and skips duplicate checkpoints for the same source budget event.
- Streaming LLM completion paths create a provider-native or local checkpoint after `llm_response_completed` when the latest context-budget event reports model-window pressure.
- `POST /api/task-runs/{task_run_id}/compact` creates an explicit local checkpoint from the latest task-run snapshot and active provider session.
- Monitor selected-run detail exposes a small `Create Checkpoint` action that calls the explicit task-run compaction API and refreshes the run ledger after creation.
- Task-run event serialization projects `context_compaction` events with the same semantic-compaction fields used for context-budget diagnostics.
- Monitor exposes `/api/monitor/compaction-checkpoints` so compact checkpoint lineage can be inspected by task run, provider session, checkpoint id, trigger reason, path, and section keys.
- Focused coverage verifies checkpoint persistence, provider-native output-window storage, compact-window next-request reuse, compact-window tool-output continuation, compact-window retry input stability, local fallback after provider-native failure, trigger gating, duplicate-source suppression, explicit API creation, section content, file recovery, provider-session metadata updates, provider-session reload readiness, Monitor lineage output, and frontend build compatibility.

Optional follow-up:

- Validate live resumed-task behavior after provider-native compaction in the configured runtime.
- Validate billed usage for `/responses/compact` only if provider-native Responses compaction becomes part of a specific deployment.
- Live validation on 2026-06-02 confirmed `https://testvideo.site/v1` accepted `/responses/compact` for model `gpt-5.5` and returned checkpoint ids plus usage. `https://token-plan-cn.xiaomimimo.com/v1` does not expose `/responses/compact` because `/responses` returns 404.
- `https://api.chatanywhere.tech/v1` was not tested for `/responses/compact` after HTTP `/responses` returned provider 403 and WebSocket `/responses` returned 405 for the tested key/model combinations.
- Live validation on 2026-06-03 confirmed `https://api.laozhang.ai/v1/responses/compact` accepted `gpt-4.1` requests and returned usage plus output items with both `message` and `compaction` types. The response body may be gzip-compressed even when the client does not auto-decode it, so direct diagnostics should handle gzip bytes.

### Phase 5: Evaluation And Rollout

**Goal**: Prove token savings without silently degrading answer quality.

**Implementation status on 2026-06-03**: complete for core rollout. Catown now evaluates ADR-035 Phase 5 target metrics from raw observations or persisted context-budget events; provider-specific billing validation remains optional and external.

Metrics:

| Metric | Target |
| --- | --- |
| False early semantic compactions | near zero for normal first turns |
| Inline tool-output tokens | 60%+ reduction on tool-heavy turns |
| Average input tokens per interactive answer | 30%-50% reduction after artifact/schema work |
| Time to first token | improves for stateful Responses/WebSocket providers |
| Task success/resume correctness | no regression in existing task-run and Monitor tests |

Rollout:

1. Enable observability first.
2. Enable higher selector budgets for large-window models.
3. Enable tool-output artifactization.
4. Gate Responses stateful mode behind provider capability/config.
5. Enable provider-native compaction after stateful mode is stable.

Implemented pieces:

- `backend/services/context_optimization_evaluation.py` evaluates Phase 5 observations against the ADR thresholds for false early semantic compactions, inline tool-output token reduction, average input-token reduction, time-to-first-token improvement, and task-success regression count.
- The evaluator can consume raw before/after observations or derive observations from `context_budget_event`/Monitor payloads, so tool-heavy runs can be scored without hand-copying token totals.
- Focused tests cover passing rollout observations, incomplete/failing observation sets, and context-budget-event-derived tool-heavy savings.
- Representative local Catown trace coverage scores combined tool-heavy and schema-only context-budget events with TTFT and task-success observations, proving the evaluator handles multi-event rollout evidence rather than a single isolated sample.
- Monitor exposes `/api/monitor/context-optimization-evaluation`, which evaluates recent persisted `context_budget_event` rows and reports the Phase 5 metric statuses. Legacy selector-era `context_compaction` rows are not treated as evaluator input; old local state may be cleared when validating this ADR.
- The Context Budget page displays the evaluation status and per-metric pass/fail/missing state beside token-savings trends.

Optional follow-up:

- Run live Catown traces in the configured runtime and review the Monitor evaluation output for task quality regressions.
- Compare provider-reported `/responses/compact` usage with billed provider usage before making any billing-token claims.
- Re-run Phase 5 TTFT and state-reuse measurements only if a Responses WebSocket-capable provider account is available.

---

## 6. Completion Assessment

ADR-035 core development is complete as of 2026-06-03.

The two original problems are addressed by the core rollout:

1. **Context quickly triggering compression**
   - Selector-budget changes are no longer treated as semantic compaction.
   - Large-window selector profiles are visible and budgeted separately from model-window pressure.
   - Legacy `context_compaction` rows are not used as rollout-evaluation input.

2. **Excessive token usage**
   - Long tool outputs are bounded and artifactized.
   - Prompt diagnostics and Monitor expose token categories, tool-output savings, and schema savings.
   - Capability profiles reduce irrelevant tool-schema exposure for ordinary turns.
   - Phase 5 evaluation can score persisted context-budget events for early compaction, token reduction, TTFT, and task-success regressions.

Responses HTTP/WebSocket and provider-native compact support are implemented and tested as optional provider capabilities, but they are not required for the ADR-035 core rollout because `/responses` support is not yet common across tested OpenAI-compatible providers.

The remaining items are operational follow-ups, not ADR blockers:

- tune capability profiles against more live usage traces;
- review Monitor evaluation output on representative live Catown sessions;
- validate provider billing only before making provider-specific billing-token claims;
- re-test Responses WebSocket only when a provider with real WebSocket `/responses` support is available.

---

## 7. Non-Goals

1. Do not claim `previous_response_id` makes previous input tokens free.
2. Do not depend on provider-specific hidden caches for correctness.
3. Do not replace Catown's `ContextSelector`; it remains the product budget layer.
4. Do not expose complex per-card context controls in the UI. Keep complexity in config and Monitor diagnostics.
5. Do not log API keys or raw provider config in token diagnostics.
