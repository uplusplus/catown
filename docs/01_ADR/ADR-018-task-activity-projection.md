# ADR-018: Task Activity Projection for Chat Steps

**状态**: 进行中
**日期**: 2026-05-11
**决策人**: BOSS
**相关**: ADR-010, ADR-011, ADR-016

---

## 1. 背景

Chat 前台 Task Step 长期由多种来源拼接：stream step、runtime card、TaskRun summary/detail、recovered placeholder、shell tail、scheduler event。不同来源的刷新节奏不同，导致用户经常看不到当前进行到哪一步，或者展开 step 后看不到后台任务详情。

这个问题不能继续靠前端条件分支修补。Chat 需要一个唯一可信的任务活动投影。

---

## 2. 决策

新增 **Task Activity Projection**，由后端从 `TaskRun`、`TaskRunEvent`、checkpoint、approval queue、runtime payload 中生成统一结构。

Chat 前台 Task Step 优先消费该 projection：

- `current_step_id` 指示当前应该展开或高亮的步骤。
- `steps[]` 提供统一的 label、state、agent、tool、summary、detail。
- `version` / `latest_event_index` 作为刷新判断依据。
- 后台任务详情以 step detail 和 refs 暴露，完整历史仍在 Monitor。

---

## 3. 范围

Phase 1:

- 新增 `/api/task-runs/{id}/activity`。
- 从已有 TaskRun 事件账本生成活动步骤。
- Chat 内联 Task Card 优先使用 activity projection 渲染 trace。
- running task 自动拉取 activity，避免前台停在旧 summary。

Phase 2:

- 将 tracked `run_shell`、subagent handles、pipeline runtime 统一挂到 step refs。
- Monitor 复用同一 projection 显示完整诊断。
- 用 SSE/WebSocket 或 cursor polling 替代粗粒度刷新。

## 3.1 2026-05-30 Chat 切换性能事故补充

`Trace-20260530T094445.json` 显示，切换到 chatroom 5 时，Chat 在首轮 `/api/chatrooms/5/task-runs` 完成后同时触发 inline task-run prefetch 与 selected task-run detail fetch。`/api/task-runs/10` 是一个历史 `multi_agent_orchestration` failed run，当前账本约 4902 events；由于 Chat 调用 `getTaskRunDetail(runId)` 时未传 `event_limit`，后端返回完整 event ledger，单次 detail 响应约 17.35 MB。

事故链路不是单纯的 WebSocket `event_count` 增长，而是：

- inline prefetch effect 依赖 `liveTaskRunDetailsById`、`taskRunDetailsById`、`taskRuns` 这些 whole-object maps。
- 每个较小 task-run detail 返回后都会写入 `taskRunDetailsById`，使 effect 重新执行。
- 对于尚未完成的慢请求，fresh detail 仍然缺失；如果没有 per-run in-flight 去重，同一个 run 会再次进入 missing set。
- trace 中 `/api/task-runs/10` 被重复请求 4 次，每次约 17.35 MB；同一切换还拉取了一次 `/api/task-runs/10/activity`，约 52.24 MB。

因此 ADR-018 增加以下约束：

- Chat 内联 Task Card 的默认数据源应是 bounded projection（优先 `/api/task-runs/{id}/activity`），不能为了普通 chat 渲染默认拉取完整 `/api/task-runs/{id}` event ledger。
- `/api/task-runs/{id}/activity` 本身也必须 bounded：长历史 run 只返回固定 head/tail 可见步骤和 compact facts，不能把完整 timeline、`runtime.steps` 或 tool result 列表原样带回 Chat。
- 如果 Chat 仍需 `TaskRunDetail` 作为 approval/detail fallback，必须传入小的 `event_limit`，完整历史只属于 Monitor/detail debug 场景。
- Chat 侧任何 task-run detail/activity 预取必须按 `task_run_id` 去重 in-flight 请求；React effect 重新执行时不能重复启动同一 run 的未完成请求。
- selected-run detail loader 与 inline prefetch 必须共享或尊重同一 in-flight/freshness 边界，避免 selected run 与 candidate run 双重请求。

---

## 4. 非目标

- 不把 Monitor 的完整事件流搬进 Chat。
- 不在 Chat 里展示所有历史 task run。
- 不改变 Project Browser Processes 的边界：右栏只展示真正后台活动任务。
