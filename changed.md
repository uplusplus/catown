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
