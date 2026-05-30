# Feature Doc: Right Sidebar Runtime Monitor

**Status**: Implemented (v1)  
**Date**: 2026-05-23  
**Owners**: BOSS + Catown Runtime/UI  
**Related**: ADR-016 (Chat Project Browser), ADR-018 (Task Activity Projection), ADR-019 (Canonical Chat Timeline), ADR-021 (User-Visible Runtime Step Projection), ADR-023 (Execution Authorization Timing)

---

## 1. Background

当前 Chat 右侧栏已经有 `Files / Artifacts / Processes` 三类能力：

- `Files` 解决“项目里有什么文件”
- `Artifacts` 解决“项目产出了什么交付物”
- `Processes` 解决“当前有哪些后台任务/命令仍在运行”

但运行时协作关系仍然分散在多处：

- chat timeline
- task activity
- runtime cards
- approval queue
- process tree

用户如果想快速回答“现在系统内部发生了什么协作动作”，需要来回切多个区域。因此新增右侧栏 `Runtime`，用于把运行时动作压缩成可快速扫读的视图。

---

## 2. Final Product Split

这一轮实现最终采用了双视图，而不是单一视图：

1. Chat 右侧栏 `Runtime`
   主线是**时间线**，每个动作统一渲染为四元组：
   - `from`
   - `action`
   - `to`
   - `result`

2. Monitor 页 `Runtime Map`
   保留此前更偏 lane / relation / action 的宽布局视图，用于更宽屏、更偏对照式的观察。

也就是说：

- 右侧栏优先回答“最近发生了什么”
- Monitor 优先回答“这些动作分布在谁身上、彼此如何排布”

---

## 3. Sidebar Runtime Design

### 3.1 Primary Principle

右侧栏 `Runtime` 采用**时间优先**模型：

- 最新事件在最上面
- 每一行是一个动作卡片
- 每张卡片表达一个统一四元组：`from / action / to / result`

这比 lane-first 方案更符合右侧栏的狭窄空间，也更容易连续阅读。

### 3.2 Card Schema

每条 runtime 事件渲染为：

- `from`
  动作发起方，通常是 Agent，也可能是 `System`
- `action`
  动作类型，例如 `msg`、`tool`、`approval`、`delegate`、`background`、`consult`
- `to`
  动作目标，例如另一个 Agent、`User`、某个 Tool、某个后台任务
- `result`
  对本次动作的结果或摘要描述

卡片同时带有：

- 时间
- task run 快捷跳转
- 状态 pill：`running / waiting / failed / done`
- 类型标签：如 `message / tool / approval / background`

### 3.3 Ordering

排序规则：

- 主排序：按事件时间倒序
- 最新事件固定在最上面
- 当前仅保留最近一段窗口，避免右栏过长

### 3.4 Density Rules

右栏布局以紧凑为优先：

- 卡片纵向间距压缩
- 四元格子内容允许换行
- `result` 独占一整行，减少长文本挤压
- 只显示高密度摘要，不在右栏展开完整日志

---

## 4. Monitor Runtime Map Design

原本更偏“关系图”的方案没有删除，而是迁移到了 Monitor。

### 4.1 Purpose

`Monitor / Runtime Map` 用于保留以下观察方式：

- Agent 间消息、委托、审批关系的纵向排列
- Agent 调用 Tool / Approval / Background Task / Consult 的横向排列
- 宽屏下更适合并排观察多个 actor

### 4.2 Layout Rules

- agent 间关系放在 lane 内左侧区域
- agent 触发的 runtime actions 放在 lane 内右侧区域
- relation 采用“新的在上”
- action 采用“新的在最右”

### 4.3 Styling Direction

由于 Monitor 页面横向空间更大，这里做了单独优化：

- lane card 更宽
- action card 最小宽度上调
- 长标题和正文支持截断与换行
- 与 chat 页分离维护样式，避免样式表不一致导致“没有格式”

---

## 5. Supported Runtime Semantics in v1

当前 v1 重点覆盖这些动作语义：

1. `A --msg-> B`
   Agent 向 Agent 发送消息

2. `A -delegate-> B`
   Agent 将任务委托给另一个 Agent

3. `B -> ToolCall`
   Agent 执行工具调用

4. `B -approval-> User`
   Agent 向用户发起权限/审批请求

5. `B -> Background Task`
   Agent 启动后台运行任务，例如 `run_shell`

6. `B -> Consult/Subagent`
   Agent 发起 consult / subagent / handoff 一类运行动作

此外还支持部分等待态、失败态和 runtime process 补充信息。

---

## 6. Data Sources

本 feature 没有新增独立后端接口，前端直接从现有投影和卡片组装：

1. `taskTimelinesById`
   用于时间线事件和排序基线

2. `taskActivitiesById`
   用于补充 active work、delegated wait、background rows 等信息

3. runtime cards
   用于补充 `agent_message`、`tool_call`、`consult_call`、`agent_error`

4. process tree
   用于补充后台进程、task、subagent 运行节点

5. approval queue state
   用于补充 pending approvals

---

## 7. Event Mapping

### 7.1 Sidebar Timeline Mapping

右栏时间线统一落到四元组事件：

- `agent_message` -> `from=AgentA, action=msg, to=AgentB`
- `handoff_created` / `delegated_task_dispatched` -> `action=delegate`
- `tool` kind -> `action=tool` 或 `background`
- `approval` kind -> `action=approval, to=User`
- `task_run_waiting_for_delegated_work` -> `action=wait`
- `consult_call` -> `action=consult`
- process node `command` -> `action=background`
- process node `subagent` -> `action=subagent`

### 7.2 Monitor Runtime Map Mapping

Monitor 侧保留双结构：

- relation
  - `message`
  - `delegate`
  - `approval`

- action
  - `tool`
  - `background`
  - `approval`
  - `handoff`
  - `consult`
  - `task`

---

## 8. UX Notes

### 8.1 Sidebar Runtime

- 右栏以“快速扫读最近动作”为目标
- 不追求完整 DAG
- 不追求一次展示所有历史细节
- 优先保证紧凑、可读、能快速定位当前 run

### 8.2 Monitor Runtime Map

- 更适合宽屏观察
- 保留旧方案的空间表达优势
- 适合作为右栏时间线的补充，而不是替代

---

## 9. Non-Goals

- 不把右栏做成完整调试器
- 不替代 Monitor 的事件流与诊断页
- 不在 v1 提供无限历史缩放或拖拽大图
- 不要求覆盖所有 runtime event，只优先覆盖最常见、最可见的动作类型

---

## 10. Implementation Notes

本轮落地包含：

- Chat 右侧栏新增 `Runtime` tab
- 右侧栏 `Runtime` 改为 timeline-first quadruple cards
- Monitor 新增 `Runtime Map` 页面，保留旧 lane 方案
- `styles.css` 与 `monitor.css` 分别补齐对应样式
- 压缩右栏布局
- 放宽 Monitor card 宽度并处理文本溢出

---

## 11. Next Iteration Candidates

后续可继续增强：

1. 统一 action label 命名
   例如 `msg / tool / approval / delegate / background / consult`

2. hover / click 展开更多 payload

3. 过滤器
   例如只看 approvals、只看 tools、只看 active

4. 更强的关联关系
   例如 approval 对应恢复了哪个 tool call，delegation 最后由哪个结果闭环

5. 更细的压缩策略
   例如对重复 tool call 做分组，或对相邻 message 做合并
