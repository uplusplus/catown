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

---

## 4. 非目标

- 不把 Monitor 的完整事件流搬进 Chat。
- 不在 Chat 里展示所有历史 task run。
- 不改变 Project Browser Processes 的边界：右栏只展示真正后台活动任务。
