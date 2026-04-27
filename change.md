# Change: Context Orchestration Refactor

## Version

0.2.0-task-state-chat-orchestration

## Goal

Refactor Catown prompt generation from ad-hoc monolithic system prompt concatenation into layered context orchestration, while keeping the first phase intentionally narrow:

- Keep the existing LLM provider interface unchanged.
- Keep Chat Completions compatible `messages` arrays.
- Keep tool schemas, tool loop behavior, database schema, and frontend interaction unchanged.
- Share one context assembly model across pipeline and major chat execution paths.

The new prompt layout is:

1. `system`: stable agent identity, role, long-term rules.
2. `developer`: stage rules, skill hints/guides, tool guidance, BOSS instructions.
3. `user`: project state, chat/runtime context, memories, team context, inter-agent messages.
4. recent history: windowed chat history.
5. current input/tool-loop continuation.

## Modified Files

- `backend/services/context_builder.py`
- `backend/services/turn_state.py`
- `backend/services/chat_prompt_builder.py`
- `backend/services/task_state.py`
- `backend/pipeline/engine.py`
- `backend/routes/api.py`
- `backend/chatrooms/manager.py`
- `backend/tools/query_agent.py`
- `backend/tests/test_prompt_context_builder.py`
- `backend/tests/test_context_integration.py`

## Implementation

### New Context Builder

Added `backend/services/context_builder.py` with:

- `ContextScope`: `session`, `run`, `stage`, `turn`, `agent_private`, `shared_fact`.
- `ContextVisibility`: `global`, `agent`, `role`, `stage`, `private`.
- `ContextFragment`: in-memory context metadata object with `role`, `content`, `scope`, `visibility`, `source`, and `priority`.
- `ContextSelector`: selects context fragments by scope/visibility/priority and enforces fragment/token budgets.
- `PromptAssembly`: structured prompt output with `system_message`, `developer_messages`, `user_context_messages`, `history_messages`, and `current_input_messages`.
- `assemble_messages()`: emits messages in the fixed order `system -> developer -> user -> history -> current input`.
- `developer_role_supported=False` fallback: merges developer context into system under `## Developer Context`.

Added builder helpers:

- `build_base_system_prompt(agent_config)`: builds stable SOUL/ROLE/RULES identity from agent config or DB agent object.
- `build_operating_developer_context(...)`: adds a Codex-style operating contract for context priority, visibility boundaries, attention management, and tool-use policy.
- `build_stage_developer_context(...)`: builds stage instructions, active skill guides, skill hints, and tool guidance.
- `build_boss_instruction_context(...)`: injects BOSS instructions as developer context.
- `build_runtime_user_fragments(...)`: splits project/chat/runtime/team/memory/inter-agent context into separate prioritized user fragments.
- `build_runtime_user_context(...)`: compatibility wrapper that combines runtime user fragments when a single user context message is needed.
- `build_turn_state_developer_fragments(...)`: converts per-turn BOSS instructions into developer fragments.
- `build_turn_state_user_fragments(...)`: converts inter-agent messages, previous work, and summarized older tool rounds into user fragments.
- `build_recent_history(...)`: preserves the existing windowing semantics without summary compression.

### Turn State

Added `backend/services/turn_state.py` with:

- `TurnContextState`: per-turn runtime state used to rebuild messages each LLM/tool loop iteration.
- `ToolRoundRecord`: keeps recent assistant tool calls plus tool results as protocol-compatible continuation messages.
- `ToolResultRecord`: normalized tool result object.
- `normalize_tool_call(...)` and `build_tool_result_record(...)`: helper functions for consistent tool loop recording.

Older tool rounds are summarized into user context while the most recent tool round remains as protocol messages. This keeps tool-call continuation compatible while preventing the current turn from growing unbounded.

### Task State

Added `backend/services/task_state.py` with:

- `TaskState`: a lightweight working-memory object for non-pipeline chat/query turns.
- `build_task_state(...)`: derives structured task memory from the current project state plus the current request.
- `build_task_state_fragments(...)`: emits high-priority user fragments for:
  - `## Active Task State`
  - `## Validation Checklist`

This moves `current_focus`, `blocking_reason`, and `latest_summary` out of the generic project status block and into a dedicated task-memory layer that is easier for the selector to prioritize.

### Pipeline Path

Updated `backend/pipeline/engine.py`:

- `_run_agent_stage()` now uses `assemble_messages()` instead of manually building a single system prompt plus user context.
- Stage config, skill hint/guide context, and tool names are emitted as developer messages.
- The operating contract is emitted as the first developer message.
- BOSS instructions are emitted as developer messages.
- Pipeline runtime context and inter-agent messages are emitted as user messages.
- Tool loop state is rebuilt through `TurnContextState` each iteration: recent tool messages stay in protocol order, older rounds become summaries.
- Pipeline context selection uses `ContextSelector.for_context_window(...)` when model context metadata is available.
- Existing `chat_with_tools(messages, tools=...)` call and tool loop append behavior remain unchanged.

### Chat Path

Updated `backend/routes/api.py`:

- Added `_assemble_chat_messages()` as the route-level adapter around `context_builder`.
- Chat no longer preserves the legacy DB `agent.system_prompt` as the primary prompt source.
- DB agents now build stable system identity from structured `soul` plus `config.role`.
- Remaining chat/SSE prompt preview and staging variables no longer read the legacy `agent.system_prompt` property.
- Agent skill hints are injected into developer context for chat paths.
- Standalone assistant response path now uses the shared assembly helper.
- Standalone SSE assistant path now uses the shared assembly helper.
- Project agent response path now replaces final model-bound messages with the shared assembly helper.
- Multi-agent single-turn helper now replaces final model-bound messages with the shared assembly helper.
- Main project SSE/tool path now replaces final model-bound messages with the shared assembly helper.
- Mentioned-agent SSE collaboration branches now replace final model-bound messages with the shared assembly helper.
- Chat runtime context is now assembled from structured user fragments instead of moving the old `_build_runtime_context_block()` wholesale.
- Runtime fragments include project identity, project status, chat context, chat lineage, team members, relevant memories, inter-agent messages, previous agent work, and turn/tool summaries.
- Chat context selection uses model context-window metadata where available to derive a token budget for runtime fragments.

### Shared Chat Builder

Added `backend/services/chat_prompt_builder.py`:

- Extracts the shared chat prompt assembly logic out of `api.py`.
- Centralizes agent base system prompt generation, team/memory context helpers, model context-window lookup, and context selector construction.
- Lets primary route handling and fallback chat manager use the same prompt assembly entry point.

Updated `backend/chatrooms/manager.py`:

- Fallback `ChatroomManager._call_agent_llm()` now uses the shared chat prompt builder instead of its own partial prompt assembly path.
- Shared chat assembly now injects task-state fragments ahead of generic runtime fragments, so chat/fallback paths keep the current request, active goal, blocker, and validation checklist in a higher-priority context layer.

### Query Agent Tool

Updated `backend/tools/query_agent.py`:

- `query_agent` now uses `target_agent` as the tool parameter instead of the ambiguous `agent_name`.
- Runtime caller metadata is now carried separately via `caller_agent_name`.
- The queried agent now receives the same layered prompt model as other chat entry points.
- The queried agent now also receives task-state fragments derived from the shared project state and the current query payload.
- A light alias remains internally so older direct callers using `agent_name` as the target can still be resolved during transition.

### Skills Behavior

Preserved ADR-008 behavior:

- `hint` is injected into developer context for all skills assigned to the agent.
- `guide` is injected only when the skill is active for the current stage.
- `full` content remains outside the prompt and continues to live under `.catown/skills/<skill-id>/SKILL.md`.

## Tests

Added `backend/tests/test_prompt_context_builder.py` with coverage for:

- System prompt only contains stable identity/role/rules and excludes stage/BOSS/inter-agent context.
- Message order is `system -> developer -> user -> history -> current input`.
- Skill `hint` is always injected and `guide` only appears for active skills.
- BOSS instructions go to developer context.
- Inter-agent messages go to user context.
- Empty stage/skills/runtime context still produces usable fallback messages.
- Developer role fallback merges developer context into system.
- Recent history preserves previous windowing and visibility behavior.
- DB-like agent objects use structured config fields and ignore legacy monolithic `system_prompt`.
- Runtime user context is split into structured prioritized fragments.
- Task-state fragments are emitted separately from generic project status so selectors can keep active work memory under tighter budgets.
- Context selector can limit fragments, filter by scope/visibility, and enforce token budgets across developer and user roles.
- Context selector can derive runtime budget from model context window.
- Turn state keeps recent tool protocol continuation while summarizing older tool rounds.

Added `backend/tests/test_context_integration.py` with coverage for:

- `query_agent` uses layered prompt assembly and the new `target_agent` schema.
- fallback `ChatroomManager._call_agent_llm()` uses the shared chat prompt builder.
- task-state fragments appear in both `query_agent` and fallback chat assembly.

## Test Results

Passed:

```bash
python -m py_compile backend/services/context_builder.py backend/services/turn_state.py backend/services/chat_prompt_builder.py backend/pipeline/engine.py backend/routes/api.py backend/chatrooms/manager.py backend/tools/query_agent.py backend/tests/test_prompt_context_builder.py backend/tests/test_context_integration.py
python -m py_compile backend/services/task_state.py backend/services/chat_prompt_builder.py backend/services/context_builder.py backend/tools/query_agent.py backend/tests/test_prompt_context_builder.py backend/tests/test_context_integration.py
pytest backend/tests/test_prompt_context_builder.py backend/tests/test_context_integration.py
```

Result:

```text
20 passed
```

Plan regression command also run:

```bash
pytest backend/tests/test_config_models.py backend/tests/test_llm_extended.py backend/tests/test_api_routes.py
```

Result:

```text
25 failed, 63 passed
```

Observed failures are concentrated in pre-existing/non-context-builder areas:

- `AgentConfigV2` tests expect legacy minimal config shape, but current model requires structured `soul` and `role`.
- `create_agent_config_from_provider()` tests expect a `system_prompt` keyword that the current implementation no longer accepts.
- `LLMClient.chat()` tests fail on existing `started_at` being undefined in `backend/llm/client.py`.
- Several API route tests expect legacy default agent names such as `assistant`/lowercase mentioned names, while current config/runtime returns names such as `Valet` and `Analyst`.

No failures were observed in the new context builder unit tests or Python syntax checks.

---

## Merged From `changed.md`

# Changed

## 2026-04-25

### b1ef180 `Add approval replay follow-up runtime flow`

范围：

- `backend/routes/api.py`
- `backend/services/context_builder.py`
- `backend/tests/test_api_routes.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 打通 `approve -> replay blocked tool -> 写入 tool_result -> 回到原 agent turn` 的 runtime 主链
- 让 replay 后续跑保持在同一个 `task_run` 内，而不是新开第二个 run
- 补齐 `tool_result` 历史消息到 `role="tool"` 的上下文投影
- 在 ADR 中补充实现状态核对，明确哪些已完成、哪些仍是目标态

约定：

- 每完成一个完整特性，就单独提交一次
- 每次特性提交后，同步追加一条 `changed.md` 记录

### e279453 `Expose approval follow-up state in monitor`

范围：

- `backend/services/monitor_projection.py`
- `backend/tests/test_monitor.py`
- `frontend/src/components/MonitorTab.tsx`
- `frontend/src/types.ts`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 把 approval replay 后的 `followup_attempted / followup_status / followup_reason / followup_error / followup_message_id` 补成 monitor 一等字段
- 让 Approvals 页面能直接区分 replay 成功、follow-up 已继续、follow-up 被跳过、follow-up 失败
- 补 monitor 后端测试并验证前端构建通过

### 3038bd9 `Expose context compaction telemetry`

范围：

- `backend/services/context_builder.py`
- `backend/services/chat_prompt_builder.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/services/monitor_projection.py`
- `backend/routes/monitor.py`
- `backend/tests/test_prompt_context_builder.py`
- `backend/tests/test_monitor.py`
- `frontend/src/components/MonitorTab.tsx`
- `frontend/src/types.ts`

内容：

- 让 `ContextSelector` 显式产出 selector diagnostics，而不只是静默截断上下文
- 在 chat runtime 和 pipeline stage runtime 发生 compaction 时写入 `context_compaction` task-run event
- 在 monitor overview / Context 页展示 recent compactions 和 compaction 数量
- 补 selector 诊断测试、monitor overview 测试，并验证前端构建通过

### c91cd2a `Expose checkpoint-friendly task snapshots`

范围：

- `backend/services/run_ledger.py`
- `backend/routes/api.py`
- `backend/tests/test_run_recovery.py`
- `backend/tests/test_monitor.py`
- `frontend/src/components/MonitorTab.tsx`
- `frontend/src/types.ts`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 从 `task_run` ledger 与 approval queue 派生 `checkpoint_snapshot`，不引入新的持久化表结构
- 让 recovery 事件显式带上启动恢复前的 checkpoint snapshot，便于回看恢复起点
- 让 monitor task-run 详情直接展示 latest agent turn、latest compaction、latest scheduler runtime
- 补 recovery / monitor 断言，并验证前端构建通过

### `Resume pipeline stages after blocked tool approval`

范围：

- `backend/pipeline/engine.py`
- `backend/routes/api.py`
- `backend/tests/test_pipeline_engine.py`
- `backend/tests/test_api_routes.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 pipeline stage 在 tool approval block 时显式停在 `blocked`，而不是把被阻塞的 tool turn 误记成正常完成
- 让 approval replay 成功后，给 pipeline 注入 follow-up context，并恢复暂停的 pipeline 继续跑当前 stage
- 补 pipeline blocked-tool runtime 测试，以及 approval queue API 对 pipeline replay/resume 的断言

### `Expose checkpoint continuation cursor`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_run_recovery.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 从现有 ledger / approval queue 派生 `checkpoint_snapshot.continuation_cursor`，明确恢复时下一步该做什么
- 让 recovery 事件显式记录恢复前 cursor，例如 `resume_scheduler`
- 让 monitor task-run 列表和详情都能直接看到 continuation cursor，而不必手工阅读整条 event stream

### `Expose turn-local continuation state`

范围：

- `backend/services/runner_lifecycle.py`
- `backend/services/run_ledger.py`
- `backend/tests/test_api_routes.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `tool_round_recorded` payload 开始携带最近一轮可恢复的 turn-local state，包括 protocol messages 与 tool results
- 让 `checkpoint_snapshot` 派生 `turn_local_state`，把最近一轮 continuation payload 放进 monitor / recovery 视图
- 补 blocked tool 与 monitor task-run 的断言，保证 turn-local continuation state 可读可用

### `Expose multi-round continuation tail`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 从多个 `tool_round_recorded` 事件派生 `protocol_tail_messages` 与 `prior_round_summaries`
- 让 `turn_local_state` 不再只看最近一轮，而是显式保留多轮 continuation tail
- 补 monitor 断言，覆盖 recent tail 与 older summaries 的组合场景

### `Rehydrate continuation state into follow-up turns`

范围：

- `backend/services/turn_state.py`
- `backend/routes/api.py`
- `backend/tests/test_prompt_context_builder.py`
- `backend/tests/test_api_routes.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 增加从 `checkpoint_snapshot` 回填 `TurnContextState` 的 helper，把 protocol tail 与 prior summaries 真接回运行态
- 让 runtime approved replay follow-up 在重新触发 agent 时优先消费 continuation state，而不是只靠纯文本 `extra_context`
- 补 unit test 与 blocked-tool replay integration test，确认 follow-up prompt 中带回了 prior tool-call protocol

### `Record continuation-state consumption in recovery`

范围：

- `backend/routes/api.py`
- `backend/tests/test_run_recovery.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 startup/manual orchestration recovery 事件显式记录本次恢复消费了哪类 continuation state
- 在 `task_run_recovery_started`、`scheduler_recovery_state_rebuilt`、`task_run_recovery_completed` 中补 `recovery_continuation_state`
- 补 recovery 测试，确保恢复路径明确声明使用了 `runtime_snapshot` 等层

### `Consume protocol tail during orchestration recovery`

范围：

- `backend/routes/api.py`
- `backend/tests/test_run_recovery.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 interrupted orchestration recovery 在重跑 agent turn 时把 `checkpoint_snapshot` 直接传入 turn builder
- 恢复后的 `TurnContextState` 现在会真实消费 checkpoint 里的 protocol tail 与 prior summaries，不再只停留在 recovery 元数据层
- 补 recovery 测试，确认恢复后的 LLM 输入里重新带回了 checkpoint 中的 tool-call assistant/tool message

### `Scope checkpoint turn state to the latest turn`

范围：

- `backend/services/run_ledger.py`
- `backend/routes/api.py`
- `backend/tests/test_run_recovery.py`
- `backend/tests/test_monitor.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `checkpoint_snapshot.turn_local_state` 只基于最新一个 agent turn 的事件窗口派生，避免旧 turn 的 protocol tail 泄漏到后续 turn
- 让 orchestration recovery 在每个恢复 step 前重建一次 step-local `checkpoint_snapshot`，保证 continuation state 会随着恢复推进而前滚
- 补 monitor / recovery 测试，确认旧的 tool protocol 只会出现在第一个恢复 turn，不会污染后续 turn

### `Expose step-local continuation state for recovered dispatches`

范围：

- `backend/routes/api.py`
- `backend/tests/test_run_recovery.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 recovery 期间每个 `scheduler_step_dispatched` 事件都携带该 step 实际消费的 `checkpoint_snapshot`
- 同时写入 step-local `recovery_continuation_state`，明确本步恢复到底带回了多少 protocol tail / summary
- 补 recovery 测试，确认第一个恢复 step 仍带旧 checkpoint tail，而后续 step 已看到前滚后的空 tail

### `Rehydrate pipeline stages from checkpoint continuation state`

范围：

- `backend/pipeline/engine.py`
- `backend/tests/test_pipeline_engine.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 pipeline stage 在启动 turn loop 前先从关联 `task_run` 构建 `checkpoint_snapshot`
- 用 `build_turn_state_from_checkpoint_snapshot(...)` 初始化 stage 的 `TurnContextState`，把最近一次 tool protocol tail 重新送回 resumed stage
- 补 pipeline 测试，确认 approval 后恢复的 stage 首次 LLM 输入包含 checkpoint 中的 assistant/tool protocol

### `Rehydrate streaming orchestration steps from checkpoint continuation state`

范围：

- `backend/routes/api.py`
- `backend/tests/test_api_routes.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 streaming orchestration 在每个 step dispatch 前也从 `task_run` 重建一次 `checkpoint_snapshot`
- 让 `_iter_agent_turn_events(...)` 用 checkpoint-seeded `TurnContextState` 起步，而不是只靠 `previous_agent_work` 纯文本摘要
- 补 stream 测试，确认后续 agent step 的首次 LLM 输入重新带回上一位 agent 最近的 tool protocol

### `Seed streaming single-agent turns from checkpoint continuation state`

范围：

- `backend/routes/api.py`
- `backend/tests/test_api_routes.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 standalone/project single-agent streaming 入口在启动 stream turn loop 前也先构建 `checkpoint_snapshot`
- 用 `build_turn_state_from_checkpoint_snapshot(...)` 初始化 stream turn state，避免这些入口继续从空白 `TurnContextState()` 起步
- 补 project stream 测试，确认首个 streaming LLM 调用会重新带回 checkpoint 中的 assistant/tool protocol

### `Project pipeline stage-start checkpoint consumption into task events`

范围：

- `backend/services/run_ledger.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_pipeline_engine.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽出通用 `describe_checkpoint_continuation_state(...)` helper，避免 recovery/pipeline 各自复制 continuation-state 统计逻辑
- 让 `pipeline_stage_started` 事件显式携带当次 stage 实际看到的 `checkpoint_snapshot` 与 `continuation_state`
- 补 pipeline engine 测试，确认 resumed stage 的 stage-start 事件会投影 protocol tail 消费情况，而新开 stage 则明确显示为空

### `Project pipeline resume continuation state into resume events`

范围：

- `backend/pipeline/engine.py`
- `backend/tests/test_pipeline_engine.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `pipeline_resumed` 事件也显式携带 resume 当下的 `checkpoint_snapshot` 与 `continuation_state`
- 这样 approval replay 后的 pipeline resume 不再只是“恢复了”，而是能说明恢复时实际看到了哪些 protocol tail / summaries
- 补 pipeline resume 测试，确认 resume 事件会投影 checkpoint 中的 assistant/tool continuation state

### `Expose derived continuation state on task-run snapshots`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `checkpoint_snapshot` 直接带上统一派生的 `continuation_state`，避免 monitor / frontend 再各自从 cursor + turn-local state 二次推断
- 补 monitor 测试，确认 blocked-tool snapshot 会直接暴露 continuation-state 计数，而无 continuation 的 latest-turn snapshot 会明确显示 `consumed = false`
- Monitor 的 Checkpoint Snapshot 卡片开始直接展示 continuation-state 摘要与原始 payload

### `Surface continuation-state summaries across monitor run views`

范围：

- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 Monitor task-run 列表直接显示 snapshot 级 continuation-state 摘要，不再必须点进详情才能看恢复上下文
- 让 task-run event 列表对带有 `continuation_state` / `recovery_continuation_state` / `checkpoint_snapshot.continuation_state` 的事件直接显示摘要行
- 这样 approval replay、pipeline resume、stage start、recovery dispatch 等事件在 UI 上不再只剩原始 payload 折叠块

### `Serialize derived continuation state onto task-run events`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_run_recovery.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `serialize_task_run_detail(...)` 在每条 event 上直接附带 `continuation_state` 与 `continuation_state_summary`
- 统一从 `recovery_continuation_state` / `continuation_state` / `checkpoint_snapshot.continuation_state` 中提取，避免前端继续逐类解析 event payload
- 补 recovery 测试与 Monitor UI 适配，确认恢复事件现在能直接返回可读 continuation 摘要

### `Serialize checkpoint continuation summaries onto task-run snapshots`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `checkpoint_snapshot` 直接附带 `continuation_state_summary`，与 event 级 summary 一样由后端统一生成
- 补 monitor 测试，确认 blocked-tool snapshot 会返回稳定 summary，而无 continuation 的 snapshot 返回 `null`
- Monitor run 列表与 detail 卡片优先使用后端给出的 snapshot summary，继续减少前端本地字符串拼装

### `Promote continuation summaries to top-level task-run summaries`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `serialize_task_run_summary(...)` 直接输出顶层 `continuation_state` 与 `continuation_state_summary`，不再只嵌在 `checkpoint_snapshot` 下
- 补 monitor 测试，确认 task-run 列表接口直接返回这些顶层字段
- Monitor run 列表与 detail 卡片优先消费顶层 continuation 摘要，继续减少对嵌套 checkpoint 字段的耦合

### `Expose latest continuation event summaries on task-run summaries`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_run_recovery.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `serialize_task_run_summary(...)` 直接输出 `latest_continuation_event_type` / `latest_continuation_event_summary` / `latest_continuation_event_at`
- 补 recovery 测试，确认恢复完成后的 task-run detail 会直接指出最近一次 continuation 事件摘要
- Monitor run 列表与 detail 区开始直接展示最近一次 continuation 事件，而不必从完整事件流中回看

### `Promote scheduler runtime summaries to task run summaries`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `serialize_task_run_summary(...)` 直接输出顶层 `latest_scheduler_runtime` 与 `scheduler_runtime_summary`
- 补 monitor 测试，确认带 runtime payload 的 task-run summary 会直接暴露最新 scheduler snapshot 与格式化摘要
- Monitor run 列表与 detail 卡片直接消费顶层 scheduler runtime 摘要，继续减少对嵌套 checkpoint 字段的依赖

### `Promote continuation cursor summaries to task run summaries`

范围：

- `backend/services/run_ledger.py`
- `backend/tests/test_monitor.py`
- `frontend/src/types.ts`
- `frontend/src/components/MonitorTab.tsx`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 让 `checkpoint_snapshot` 与 `serialize_task_run_summary(...)` 直接输出 `continuation_cursor_summary`
- 补 monitor 测试，确认 blocked-tool run 会直接返回 cursor 摘要，而无 cursor 的 run 返回 `null`
- Monitor run 列表与 detail 卡片优先消费后端给出的 cursor 摘要，继续减少前端本地字符串拼装

### `Share orchestration startup policy across runtime entry points`

范围：

- `backend/routes/api.py`
- `backend/tests/test_api_routes.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 把 orchestration target resolution / schedule build / runner policy 编译提取成共享准备逻辑，供 sync、stream、recovery 共用
- 让 multi-agent `runtime_mode_selected` 事件也直接携带 orchestration `runner_policy`，把 policy projection 提前到 run startup 时刻
- 补 API 测试，确认 sync / stream orchestration 的 mode-selected 事件与后续 scheduler plan 在 mode / stage_count / sidecar metadata 上保持一致

### `Share runtime mode selection envelope across sync and stream entry points`

范围：

- `backend/routes/api.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取统一的 `runtime_mode_selected` 写入辅助逻辑，收口 `run_kind` / `target_agent_name` 更新与 mode-selected event payload 组装
- 让 standalone / project single-agent、standalone / project multi-agent 在 sync 与 stream 入口都走同一套 mode-selection envelope
- 继续把 runner startup 语义从分支逻辑中抽出来，为后续统一 runner 外壳做准备

### `Share project target-agent resolution across sync and stream runtime entry points`

范围：

- `backend/routes/api.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取统一的 project target-agent 解析辅助逻辑，收口 `@mention -> project agent / global auto-assign / default fallback`
- 让 project single-agent sync 与 stream 两条入口共用同一套 target resolution 语义
- 继续削减 runner startup 阶段的分支内重复逻辑，避免后续 policy / approval / ownership 接线再次分叉

### `Share chat turn runtime preparation across execution paths`

范围：

- `backend/routes/api.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取统一的 chat turn runtime 准备逻辑，收口 `llm_client` / recent messages / `turn_state` / tool schemas / tool runtime kwargs
- 让 project single-agent sync、project single-agent stream、orchestrated sync turn、orchestrated stream turn 共用同一套 turn-runtime preparation
- 继续把 turn execution envelope 从分支实现里抽离，为后续向统一 runner 外壳收敛打基础

### `Share standalone turn runtime preparation across sync and stream`

范围：

- `backend/routes/api.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取统一的 standalone turn runtime 准备逻辑，收口 standalone target resolution、default client fallback、recent messages、checkpoint turn state
- 让 standalone sync 与 standalone stream 两条路径共用同一套 turn-runtime preparation
- 继续把 turn execution envelope 的“无工具单 Agent”分支也拉回共享 runtime 语义

### `Share context compaction callback semantics across chat and pipeline`

范围：

- `backend/services/runtime_event_helpers.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_runtime_event_helpers.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取统一的 context compaction callback helper，收口去重、payload 组装、summary 文案生成
- 让 chat runtime 与 pipeline stage runtime 共用同一套 compaction event 语义，只保留各自的事件落点适配
- 补 helper 单测，确保 compaction event 会去重且无 compacted 标记时不会误发事件

### `Share runtime lifecycle event payload building`

范围：

- `backend/services/runtime_event_helpers.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_runtime_event_helpers.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取统一的 runtime lifecycle payload helper，收口 `client_turn_id`、`stage_policy`、target/pipeline metadata 的组装规则
- 让 chat standalone、project single-agent、orchestration turn 与 pipeline stage turn 共用同一套基础 payload 语义
- 补 helper 单测，确认可序列化 policy payload 且会过滤缺失字段

### `Share approval replay follow-up resolution payloads`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 提取 approval replay follow-up payload helper，统一 skipped / failed / continued 三类 resolution shape
- 提取 replay actionable 判断，统一要求 approved replay 成功且没有再次 blocked 才继续 runtime 或 pipeline
- 让 chat runtime 与 pipeline approved-tool replay 分支共用同一套 resolution payload 语义
- 补 helper 单测，确认 payload 会过滤缺失字段且 replay actionability 判断一致

### `Share approval replay resolution and round payloads`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 把 approval replay resolution payload 从 API route 抽到共享 service，统一 `action_taken`、`resume_supported`、replay outcome 与 result preview
- 把 approval replay tool-round payload 抽到共享 service，统一 queue item 与 pipeline cursor 的 ledger 记录 shape
- 让 approve blocked-tool 路径只保留 replay / follow-up / resolve 编排职责，不再定义 replay payload 协议
- 补 helper 单测，并跑 approval replay 与 pipeline gate 的聚焦回归

### `Share blocked-tool approval queue request semantics`

范围：

- `backend/services/approval_replay.py`
- `backend/services/runner_lifecycle.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 把 blocked tool queue kind、title、resume_supported 判定、request key、request payload shape 抽到共享 approval replay service
- 让 `record_tool_round(...)` 只负责发现 blocked tool 与创建 queue item，不再内联定义 approval request 协议
- 保持 runtime 与 pipeline blocked-tool request payload 的 pipeline cursor 字段一致
- 补 helper 单测，并跑 approval replay、sandbox blocked、pipeline blocked-tool ledger 的聚焦回归

### `Share pipeline gate approval payload helpers`

范围：

- `backend/services/approval_replay.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 把 pipeline gate approval request key、request payload、resolution payload 抽到共享 approval replay service
- 让 pipeline engine gate queue/resolve 路径复用统一 helper，不再内联定义 approval queue payload shape
- 补 helper 单测，确认 pipeline/run/stage cursor 与 stage policy payload 被稳定保留
- 跑 approval replay helper 与 pipeline gate approval 聚焦回归

### `Share approval queue event payload helpers`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/services/runner_lifecycle.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 approval queue item created/resolved event payload helper，统一 queue item ledger 投影 shape
- 抽取 reject queue item 的 resolution payload helper，统一 `queue_resolved_only` 与 `resume_supported` 语义
- 让 runtime approve/reject、runner blocked-tool queue created、pipeline gate queue created/resolved 都复用共享 helper
- 补 helper 单测，并跑 approval replay、sandbox blocked、pipeline gate、pipeline blocked-tool ledger 聚焦回归

### `Share approved tool replay follow-up context helper`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 把 approved tool replay follow-up context 从 API route 抽到共享 approval replay service
- 让 chat runtime 与 pipeline approved replay continuation 共用同一套 tool/status/result preview 文案
- 明确保留“继续执行但不要无条件重跑同一 tool call”的恢复语义
- 补 helper 单测，并跑 approval replay runtime/pipeline 聚焦回归

### `Share approval replay request parsing helpers`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 approval queue request payload 解析 helper，统一处理空值、非 dict JSON 与已解析 dict
- 抽取 replay tool name、arguments text、tool call id、pipeline run/stage cursor resolver
- 让 API approve/reject、runtime blocked-tool replay、pipeline blocked-tool replay 共用同一套 request/cursor 解析语义
- 补 helper 单测，并跑 runtime/pipeline approval replay 与 pipeline gate 聚焦回归

### `Share replay argument parsing helper`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 replay arguments JSON object 解析 helper，统一非法 JSON 与非 object 参数处理
- 让 runtime blocked-tool replay 与 pipeline blocked-tool replay 共用同一套参数校验语义
- 补 helper 单测，确认合法 object、非 object JSON、非法 JSON 的结果
- 跑 approval replay runtime/pipeline 聚焦回归

### `Share replay tool result record helper`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 replay tool result record 构造 helper，统一 `queue-replay-<queue_item_id>` tool call id 规则
- 让 runtime blocked-tool replay 与 pipeline blocked-tool replay 的成功/失败返回共用同一套 result record 构造
- 保持原有 `build_tool_result_record(...)` 的 success/status/block 分类逻辑不变
- 补 helper 单测，并跑 approval replay runtime/pipeline 聚焦回归

### `Share approval queue resume strategy helpers`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/services/run_ledger.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 approval queue pipeline cursor 判断与 resume strategy helper
- 让 API replay 分流、runtime follow-up guard、approve follow-up 分流共用同一套 pipeline cursor 判断
- 让 run ledger continuation cursor 使用共享 request payload 解析和 pipeline run/stage resolver
- 补 helper 单测，并跑 approval replay 与 run recovery 聚焦回归

### `Share pending approval continuation cursor helper`

范围：

- `backend/services/approval_replay.py`
- `backend/services/run_ledger.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 pending approval continuation cursor helper，统一 `await_approval` cursor shape
- 让 run ledger 构造 checkpoint continuation cursor 时复用 approval replay service
- 保持 source event、turn/tool、blocked kind、queue item、pipeline run/stage cursor 字段一致
- 补 helper 单测，并跑 approval replay helper 与 run recovery 聚焦回归

### `Complete P0 runtime envelope baseline`

范围：

- `backend/services/approval_replay.py`
- `backend/routes/api.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽取 approved replay follow-up triggered/failed event payload helper，统一 runtime 与 pipeline follow-up event shape
- 让 chat runtime 与 pipeline approved replay follow-up 分支共用同一套 follow-up event payload 构造
- 在 ADR 中明确 P0 baseline 完成边界：startup envelope、turn envelope、approval/replay envelope、checkpoint/recovery cursor 已完成基础收口
- 把剩余“单一 executor loop、subagent lifecycle、sandbox escalation token、durable inbox/outbox replay”明确划入 P1

### `Extract durable pipeline inbox service`

范围：

- `backend/services/pipeline_inbox.py`
- `backend/pipeline/engine.py`
- `backend/tests/test_pipeline_inbox.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 抽出 durable pipeline inbox/outbox service，统一 delivery 创建、pending claim/consume、message serialization
- 让 pipeline tool `send_message`、BOSS instruction、rollback message 都复用统一 delivery helper
- 让 stage runtime 读取普通 inter-agent message 与 `HUMAN_INSTRUCT` 时复用统一 consume helper
- 保留 legacy `HUMAN_INSTRUCT` backfill 行为，并移入 inbox service
- 补 service 单测，并跑原 pipeline durable delivery 回归

### `Add pipeline inbox lease retry semantics`

范围：

- `backend/models/database.py`
- `backend/services/pipeline_inbox.py`
- `backend/tests/test_pipeline_inbox.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 为 `pipeline_message_deliveries` 增加 lease owner、lease expiry、attempt count、last error、dead-letter 时间等 durable replay 字段
- 在 `pipeline_inbox` 中新增 claim / ack / fail API，支持 `pending -> inflight -> consumed` 与失败重试 / dead-letter
- 让过期 inflight delivery 可被重新 claim，避免跨进程 worker 崩溃后消息永久卡住
- 保持原有 pop-and-consume API 兼容 pipeline stage loop
- 补 lease、reclaim、dead-letter 单测，并跑 pipeline durable delivery 回归

### `Project pipeline inbox state into checkpoints`

范围：

- `backend/models/database.py`
- `backend/services/pipeline_inbox.py`
- `backend/services/run_ledger.py`
- `backend/tests/test_pipeline_inbox.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 为 `TaskRun` 与 `PipelineRun` 建立双向 relationship，让 runtime checkpoint 能直接看到关联 pipeline runs
- 增加 `summarize_pipeline_run_inbox(...)`，把 delivery status、agent 分布、pending/inflight/dead-letter 计数投影成 monitor/recovery 友好结构
- 让 `build_task_run_checkpoint_snapshot(...)` 输出 `pipeline_inbox` 与 `pipeline_inbox_summary`
- 补 checkpoint projection 单测，并跑 monitor task-run 回归

### `Project scheduler events into subagent lifecycle`

范围：

- `backend/services/subagent_lifecycle.py`
- `backend/services/run_ledger.py`
- `backend/tests/test_subagent_lifecycle.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 subagent lifecycle projection service，把 scheduler plan/dispatch/resume/complete events 归一成 subagent state
- 在 checkpoint snapshot 中输出 `subagent_lifecycle` 与 `subagent_lifecycle_summary`
- 让 monitor/task-run summary 能看到 spawned/running/completed 的 subagent 状态聚合
- 补 service 与 checkpoint 单测，并跑 orchestration recovery / monitor 聚焦回归

### `Record failed subagent lifecycle terminal state`

范围：

- `backend/services/subagent_lifecycle.py`
- `backend/routes/api.py`
- `backend/tests/test_subagent_lifecycle.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 扩展 subagent lifecycle projection，支持 `scheduler_step_failed` / `scheduler_step_cancelled` 终态
- 非流式 orchestration step 执行异常时记录 failed lifecycle event，并把 task run 标记为 failed
- 流式 orchestration step 执行异常时记录 failed lifecycle event，返回 SSE error/done，并把 task run 标记为 failed
- 补 failed/cancelled projection 单测，并跑 sync/stream orchestration ledger 回归

### `Add approval queue resume token leases`

范围：

- `backend/models/database.py`
- `backend/services/approval_queue.py`
- `backend/services/approval_replay.py`
- `backend/tests/test_approval_queue.py`
- `backend/tests/test_approval_replay.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 为 approval/escalation queue item 增加 `resume_token`、`resolution_owner`、`resolution_lease_expires_at`
- 创建 queue item 时生成 durable resume token，并在数据库初始化时为历史 item 回填 token
- 新增 approval queue resolution lease claim helper，避免多个恢复/处理者同时解决同一个 pending item
- continuation cursor 与 queue serialization 暴露 resume token / lease 状态
- 补 approval queue lease 单测，并跑 approval replay / monitor queue 回归

### `Enforce approval queue resolution leases in API`

范围：

- `backend/routes/api.py`
- `backend/tests/test_approval_queue.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- approve / reject API 在处理 pending queue item 前先 claim resolution lease
- 如果 queue item 已被其他 resolver lease，API 返回 409，避免重复处理同一 approval / escalation item
- 保持 resolve 后清理 lease，后续 serialization 可看到处理期间的 owner / expiry
- 修正 approval queue 单测导入方式，避免测试数据库 reload 时持有旧 mapper
- 跑 approval/escalation API 回归与 approval queue lease 单测

### `Add task-run cancellation lifecycle primitive`

范围：

- `backend/routes/api.py`
- `backend/services/subagent_lifecycle.py`
- `backend/tests/test_task_run_cancel.py`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 `POST /api/task-runs/{task_run_id}/cancel`，允许把 running task run 显式取消
- 取消时从 checkpoint 的 `subagent_lifecycle` 找出未终止 subagents，并为每个 step 写入 `scheduler_step_cancelled`
- 写入 `task_run_cancelled` ledger event 后，将 TaskRun 状态改为 `cancelled`
- 新增 `cancellable_subagents_from_lifecycle(...)`，让 cancel primitive 复用统一 lifecycle projection
- 补 API 单测，覆盖 active subagent terminalize 与 non-running run 409

### `Extract orchestration scheduler event helpers`

范围：

- `backend/services/orchestration_events.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_events.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 orchestration scheduler event helper service，统一 scheduler plan / step payload shape
- 抽取 dispatch / complete / resume / fail / cancel 的 ledger event record helper
- 让 sync orchestration、stream orchestration、recovery orchestration 与 cancel API 复用统一 step lifecycle event helper
- 保持原有 event type 与 payload 字段兼容，减少 API route 内重复拼装 runtime / step_state / stage_policy 的代码
- 补 helper 单测，并跑 sync/stream/recovery/cancel 聚焦回归

### `Extract orchestration handoff helpers`

范围：

- `backend/services/orchestration_handoffs.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_handoffs.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 orchestration handoff service，统一 previous-work 文本、handoff payload 与 `handoff_created` ledger event
- 让 sync orchestration、stream orchestration 与 recovery orchestration 复用统一 handoff enqueue / record helper
- 保持原有 handoff payload 字段兼容，包括 recovered handoff 的 `recovered: true`
- 保留 route 内 wrapper，降低对既有调用点的改动范围
- 补 handoff helper 单测，并跑 sync/stream/recovery orchestration 回归

### `Extract orchestration finalizer helper`

范围：

- `backend/services/orchestration_finalizer.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_finalizer.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 orchestration finalizer service，统一 last blocking result / latest result / latest completed turn / fallback 的 summary 选择规则
- 让 sync orchestration、stream orchestration 与 recovery orchestration 复用 `finalize_orchestration_task_run(...)`
- recovery completion event 与 TaskRun final summary 复用同一套 `summarize_orchestration_result(...)`
- 补 finalizer helper 单测，并跑 sync/stream/recovery orchestration 回归

### `Extract orchestration step output state helper`

范围：

- `backend/services/orchestration_step_state.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_step_state.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 orchestration step output state service，统一 completed turns、results、last blocking result 的更新规则
- 让 sync orchestration、stream orchestration 与 recovery orchestration 复用 `record_orchestration_step_output(...)`
- 区分普通 sync results 与 stream/recovery 的 completed-turn-only 更新，保持 finalizer 行为兼容
- 补 step output state 单测，并跑 sync/stream/recovery orchestration 回归

### `Extract orchestration scheduler step completion helper`

范围：

- `backend/services/orchestration_step_completion.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_step_completion.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 scheduler step completion service，统一 `queue.mark_completed`、completed event、resumed event 与 handoff enqueue/record
- 让 sync orchestration、stream orchestration 与 recovery orchestration 复用 `complete_orchestration_scheduler_step(...)`
- 保持 recovered path 的 `recovered: true` metadata 与 Recovery summary prefix 兼容
- 补 completion helper 单测，并跑 sync/stream/recovery orchestration 回归

### `Extract nonstream orchestration step runner`

范围：

- `backend/services/orchestration_step_runner.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_step_runner.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 nonstream orchestration step runner，统一 dispatch、agent turn execution、message publish、output state、step completion、failure event
- 让非流式 orchestration 与 interrupted recovery 复用 `run_nonstream_orchestration_step(...)`
- 保留 sync path 的 results 更新与 recovery path 的 completed-turn-only 更新差异
- 支持 recovery dispatch 额外携带 checkpoint snapshot 与 recovery continuation state
- 补 step runner 成功/失败单测，并跑 sync orchestration 与 recovery 回归

### `Extract stream orchestration step runner helpers`

范围：

- `backend/services/orchestration_stream_runner.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_stream_runner.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 streaming orchestration step runner helper，收口 streaming dispatch、agent event iterator 参数、turn_complete 保存、failure、step completion payload
- 让 stream orchestration route 复用 `start_stream_orchestration_step(...)`、`iter_stream_orchestration_agent_events(...)`、`handle_stream_orchestration_turn_complete(...)`、`complete_stream_orchestration_step(...)`
- 保持 SSE payload shape 兼容，包括 `collab_step` 与 `collab_step_done`
- 补 stream runner helper 单测，并跑 streaming orchestration ledger / sidecar 回归

### `Centralize orchestration failure finalization`

范围：

- `backend/services/orchestration_finalizer.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_finalizer.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 `fail_orchestration_task_run(...)`，统一记录 terminal failure event 并关闭 TaskRun
- 让 sync orchestration、stream orchestration 与 interrupted recovery 的主要失败终结路径复用 failure finalizer
- 保留 recovery 的 `task_run_recovery_failed` 事件类型，同时统一 failed summary 写入规则
- 补 failure finalizer 单测，继续削薄 `backend/routes/api.py` 的 orchestration 运行时职责

### `Extract nonstream orchestration agent turn runner`

范围：

- `backend/services/orchestration_agent_turn.py`
- `backend/routes/api.py`
- `backend/tests/test_orchestration_agent_turn.py`
- `change.md`
- `docs/ADR-015-codex-style-runtime-evolution.md`

内容：

- 新增 nonstream orchestration agent turn runner，承接原 route-local `_run_single_agent_turn(...)` 主逻辑
- route 改为通过 `OrchestrationAgentTurnDeps` 注入 runtime preparation、prompt assembly、message save 与 memory extraction adapter
- sync orchestration 与 interrupted recovery 改用 service-level agent turn executor
- 补 agent turn runner 单测，验证 lifecycle event、message save 与 memory scheduling 行为
