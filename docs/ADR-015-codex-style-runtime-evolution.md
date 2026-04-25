# ADR-015: Codex 风格运行时内核演进

**状态**: 进行中
**日期**: 2026-04-24
**决策人**: BOSS
**相关**: ADR-012 (会话上下文管理), PRD §21 (上下文压缩), PRD §1645 (`/api/pipelines/{id}/messages`)

---

## 1. 背景

Catown 当前有两套明显不同的执行形态：

- **工作流层**：`pipeline/engine.py` 提供固定 stage、gate、artifact、rollback、workspace、git 节点。
- **任务运行时层**：`routes/api.py` 负责聊天室 turn loop、工具调用、SSE 流式输出、多 Agent 接力。

在项目式交付上，Catown 的 workflow/gate 能力并不弱；问题主要出在 **runtime kernel 不统一**：

- 聊天室和 Pipeline 过去都存在 append-only `messages` 增长问题。
- Pipeline 的 Agent 消息传递更像“留言箱”，而不是调度语义。
- 运行时状态缺少统一的 task/run ledger。

相较之下，Codex 风格系统的核心不是静态 pipeline，而是 **动态任务运行时**：

- 以 `task/run` 为主，而不是以固定 stage 为主
- 以 `turn` 为基本调度单位
- 每轮重组上下文，而不是无限追加 transcript
- 工具调用、消息、审批、恢复都作为运行时状态的一部分
- 是否并行、是否委派、是否等待，是调度器的决定，不是模板硬编码

---

## 2. 对标 Codex 时值得吸收的设计

### 2.1 Turn-first runtime

Codex 更像：

```
Task/Run
  -> Turn Loop
    -> LLM
    -> Tools / Environment
    -> State update
    -> Re-assemble next turn
```

而不是：

```
Pipeline template
  -> fixed stage A
  -> fixed stage B
  -> fixed stage C
```

### 2.2 每轮重组上下文

Codex 风格运行时不会把完整历史无上限 append 到 prompt。较好的模式是：

- 最近 1 轮工具协议保留原始 assistant/tool transcript
- 更早轮次折叠成摘要
- BOSS 指令、工具策略、共享事实、前序 handoff 分层注入

### 2.3 控制面 / 数据面分离

需要把不同类型的上下文拆开：

- `system`: 稳定身份层
- `developer`: 操作规则、工具政策、阶段约束、BOSS 指令
- `user`: 任务事实、共享消息、handoff、项目状态
- `assistant/tool protocol`: 最近一轮结构化协议

### 2.4 Durable inbox/outbox

Agent 间消息如果只存在进程内内存队列，无法支撑：

- backend 重启恢复
- run resume
- 崩溃后的 message replay
- 对消息是否已消费的审计

Codex 风格 runtime 需要 durable message state，而不是仅有 transcript。

### 2.5 Scheduler 而非“只有 stage 当前执行者”

真正的多 Agent runtime 不是“允许 A 给 B 发消息”就够了，还需要定义：

- message 是留言还是阻塞请求
- 是否收到消息就调度目标 agent 跑一轮
- 哪些任务是 blocker，哪些可以 sidecar 并行
- 谁拥有当前 write scope / workspace slice

---

## 3. Catown 当前差距

### 3.1 已有优势

Catown 在 workflow/gate 层已经具备：

- manual gate / approve / reject / rollback
- artifact 记录
- workspace 隔离
- Git commit / tag

这些是项目治理层面的优势，不应丢掉。

### 3.2 主要短板

#### A. Stage-first，run-time kernel 不统一

Pipeline 主要仍由“当前 stage 的 agent”驱动，而不是统一调度器决定下一步。

#### B. Agent 消息传递语义偏弱

早期实现中，pipeline inter-agent message 主要依赖进程内 `_interagent_message_queue`，更像临时留言，而不是 durable inbox。

#### C. 上下文直到最近才开始 turn-state 化

聊天室同步、SSE、以及 Pipeline stage loop 过去都倾向于直接 append `messages`。

#### D. 缺少更高层的 RunLedger / TaskState

当前有：

- `TurnContextState`：适合一轮或几轮 prompt 重组

但仍缺：

- run 级 ledger
- scheduler state
- message delivery state
- durable handoff / work ownership

---

## 4. 决策

Catown **不改成纯 Codex 形态**，而是采用双层架构：

### 4.1 底层：Codex 风格 Runtime Kernel

目标能力：

- `TurnContextState`
- durable inbox/outbox
- tool ledger
- BOSS / inter-agent state 注入
- scheduler hooks
- 可恢复 run state

### 4.2 上层：保留 Catown Workflow Layer

保留并继续利用：

- pipeline template
- stage / gate / artifact / rollback
- project governance

但这层不再默认主导所有运行时路径：

- 聊天室内的多 Agent 协作优先走 **orchestration-first runtime**
- 固定 pipeline 只在确实需要 stage/gate 治理时启用
- 如果后续验证表明 pipeline 对默认交互价值不高，可以继续降级为可选治理模块

换言之：

> 底层学 Codex，做动态任务运行时；上层保留 Catown，做项目治理和交付流程。

---

## 5. 演进阶段

### Phase 1: Turn-state prompt rebuild

目标：所有主要 LLM/tool loop 都改成“状态驱动重组消息”，不再 append-only。

当前状态：

- `routes/api.py` 聊天室同步单 Agent：已完成
- `routes/api.py` 单 Agent SSE：已完成
- `routes/api.py` standalone/project 多 Agent SSE：已完成
- `pipeline/engine.py` `_run_agent_stage()`：已完成

### Phase 2: Durable pipeline inbox/outbox

目标：移除 pipeline 进程内 message queue，改用 durable inbox。

当前状态：

- 新增 `pipeline_message_deliveries` 作为 direct message inbox
- `send_message` / BOSS `instruct` / rollback handoff 已写入 durable delivery
- `_pop_messages_for_agent()` 已改为从数据库 claim + consume

未完成：

- 仍是 `pending -> consumed` 的轻量消费语义
- 尚无 lease / retry / dead-letter 语义

### Phase 3: Scheduler semantics

目标：定义消息如何驱动调度，而不只是“留一条消息”。

待实现：

- direct message 是否触发目标 agent 子轮次
- blocker / sidecar 任务建模
- ownership / write scope 约束

### Phase 4: RunLedger / TaskState

目标：引入统一 run 级 ledger，连接：

- tool work
- inter-agent handoff
- decisions / assumptions
- stage handover
- resume/recovery

当前状态：

- 已新增 `task_runs` / `task_run_events` 两张持久化表
- 聊天室同步单 Agent、SSE 单 Agent、SSE 多 Agent 编排开始写入 run 级事件
- 事件类型已覆盖 `user_message_saved`、`runtime_mode_selected`、`agent_turn_started`、`tool_round_recorded`、`handoff_created`、`agent_turn_completed`、`task_run_failed`
- 已暴露查询接口：`GET /api/chatrooms/{chatroom_id}/task-runs` 与 `GET /api/task-runs/{task_run_id}`

未完成：

- pipeline 路径尚未统一接入同一套 run ledger
- scheduler blocker/sidecar/ownership 语义仍未进入 ledger
- resume/recovery 仍主要依赖现有消息与运行时状态，尚未形成完整 checkpoint 模型

---

## 6. 本次实现落点

本次演进已完成两类关键改动：

### 6.1 Pipeline 也接入 TurnContextState

`backend/pipeline/engine.py` 的 `_run_agent_stage()` 现在：

- 用 `TurnContextState` 收集 BOSS 指令
- 用 `TurnContextState` 收集 inter-agent message
- 记录 tool round
- 每轮重建 prompt，而不是直接 append raw fragments

这让 Pipeline 路径与聊天室运行时开始共享同一种 prompt 组装模型。

### 6.2 引入 durable pipeline inbox

新增表：

- `pipeline_message_deliveries`

作用：

- direct pipeline message 的 durable recipient inbox
- 支持 `pending` / `consumed` 状态
- 支持 run 恢复后重新读取数据库中的待处理消息

这一步的重点不是“做完完整 scheduler”，而是先把消息状态从内存队列迁到 durable storage。

### 6.3 聊天室多 Agent 路径改成 orchestration-first

`routes/api.py` 中原来的 “multi-agent pipeline” 已开始改写为 Codex 风格编排：

- 以 `@mention` 形成 turn agenda，而不是进入固定 stage
- 每个 agent turn 都重建 prompt
- 前序 agent 输出通过 handoff / inbox 注入到下一位 agent，而不是直接把上一个回答拼进用户原始消息
- 工具调用开始透传运行时上下文（`agent_id` / `agent_name` / `chatroom_id` / `project_id`）

### 6.4 RunLedger 首轮落地

`backend/services/run_ledger.py` 与 `backend/models/database.py` 新增了 run 级持久化骨架：

- `TaskRun`：一条聊天室执行 run 的摘要记录
- `TaskRunEvent`：该 run 下按顺序追加的事件明细

当前聊天室路径的落地方式：

- `POST /api/chatrooms/{id}/messages`：用户消息保存后创建 `TaskRun`
- `POST /api/chatrooms/{id}/messages/stream`：SSE 路径也创建独立 `TaskRun`
- 单 Agent / 多 Agent 编排均在执行过程中写入事件
- run 完成或失败时写回 `status` / `summary` / `completed_at`

这一步的价值不是取代 pipeline，而是先把聊天 runtime 从“只有 transcript 和 runtime_card”推进到“run + ordered events”的可审计形态。

这意味着 Catown 的默认多 Agent 协作路径，已经不再依赖 pipeline 才能工作。

---

## 7. 后续明确不在本阶段完成的内容

以下内容仍然保留到后续阶段：

- `reply_to_message_id` / thread/correlation 语义
- inflight lease / ack timeout / retry / dead-letter
- message-driven subturn scheduler
- run 级 TaskState / RunLedger
- stage 内并行 sidecar worker

---

## 8. 结论

Catown 的正确方向不是“取消 pipeline 改成纯对话式 runtime”，而是：

- 用 Codex 风格 runtime kernel 强化底层执行与恢复能力
- 继续保留 Catown 的 pipeline/gate/artifact 治理层

这是“项目治理”与“任务运行时”两层分离的架构路线。

---

## 9. 2026-04-25 回看：当前与 Codex 的差距

截至 2026-04-25，Catown 相比本文前述阶段已经继续向 Codex 风格运行时推进，尤其是在聊天室 orchestration 路径上已有明显收敛，但整体仍是：

> 已有 Codex 风格 orchestration 外形，尚未形成 Codex 风格统一运行内核。

### 9.1 已经比较接近 Codex 的部分

- **orchestration-first 聊天室运行时已成形**
  - 多 Agent 聊天默认走 `task/run -> scheduler -> turn -> handoff` 路径，而不是固定 stage pipeline。
  - 已具备 blocker / sidecar 调度建模、handoff 事件、运行态可视化。

- **run ledger + Monitor 基础链路已落地**
  - `task_runs` / `task_run_events` 已能记录 mode selection、tool round、handoff、scheduler dispatch/completion、failure、resume/recovery。
  - Monitor 已可查看 run ledger、scheduler plan、handoff、runtime state、recovery lease。

- **主 turn loop 已开始共享执行器**
  - 非流式路径已通过 `backend/services/nonstream_turn_executor.py` 收敛，覆盖 sync 单 Agent、sync orchestration、pipeline stage agent loop。
  - 流式路径已通过 `backend/services/stream_turn_executor.py` 收敛，覆盖 standalone SSE、project 单 Agent SSE、multi-agent orchestration SSE。

- **resume / recovery 能力已有实用可靠性**
  - 支持启动时扫描中断 orchestration run 自动恢复。
  - 支持手动 `resume`。
  - 已补上 recovery lease，避免多实例对同一个 run 重复恢复。

- **prompt rebuild / turn-state 方向基本正确**
  - 聊天路径和 pipeline stage loop 都已经转向每轮重组上下文，不再纯 append transcript。
  - `TurnContextState`、history summary、tool round summary、task-state fragments 已经构成第一层 runtime context kernel。

### 9.2 仍明显落后于 Codex 的部分

#### A. 仍缺少统一 runner 外壳

当前仍是多条执行路径并存：

- 单 Agent sync
- 单 Agent SSE
- multi-agent orchestration sync
- multi-agent orchestration stream
- pipeline stage engine

这些路径虽然已经开始共享 `nonstream_turn_executor` / `stream_turn_executor`、上下文重组和 run ledger 语义，但还没有收敛成一套完整的 runner / execution envelope。  
这意味着：

- 行为语义仍可能分叉
- 恢复逻辑仍需按路径分别处理
- approval / tool / subagent / resume 难以统一接入

这是当前与 Codex 的**最大结构性差距**。

#### B. 还没有真正的 subagent lifecycle

Codex 风格更强调：

- spawn
- wait
- close
- tree / ownership / parent-child relation

Catown 当前更接近“协作工具”层：

- `delegate_task`
- `send_direct_message`
- `query_agent`

这些能力可用，但还不是统一的 runtime-managed subagent lifecycle。  
Monitor 里的 `Sub-Agent Tree` 目前也更像项目 agent 目录，而不是实际运行时子树。

#### C. 消息传递和调度语义仍偏轻

虽然 pipeline durable inbox 已落地，但目前仍主要是：

- `pending -> consumed`

尚缺：

- inflight lease
- ack timeout
- retry
- dead-letter
- ownership / write-scope enforcement

换言之，Catown 已有 durable message storage，但还没有形成 Codex 风格的**消息驱动执行语义**。

#### D. recovery 仍是“重建型”，不是“checkpoint 型”

当前的恢复逻辑已经可用，但本质仍然主要依赖：

- 已保存消息
- task_run ledger 事件
- scheduler state replay

来重建剩余执行状态。  
这与更理想的 Codex 风格 checkpoint continuation 仍有差距：

- 缺少完整 executor snapshot
- 缺少标准化 task state checkpoint
- 缺少更细粒度的恢复断点模型

#### E. TaskState 仍偏薄

当前已有 `TaskState`，但内容主要仍集中在：

- current request
- goal
- blockers
- working summary
- validation checklist

距离更成熟的 Codex 风格 task/run state 还缺：

- assumptions / open questions
- todo / done criteria
- ownership / write scope
- delegated work state
- step-level progress / pending actions

#### F. approval / sandbox 还未进入统一 runtime 主链

Catown 现有：

- pipeline gate approval
- 若干工具级 sandbox / security 限制

但还没有形成 Codex 风格那种统一的：

- action approval
- escalation
- sandbox policy projection
- pending approval queue
- execution policy as runtime state

目前 Monitor 里这部分也仍以占位 UI 为主。

#### G. context compaction 还是早期形态

当前已经具备：

- history summary
- tool round summary
- task-state fragments

但还没有形成真正的：

- compaction event
- checkpoint-friendly context snapshot
- context budget pressure telemetry
- compaction-aware resume model

Monitor 里这块仍是“尚未发出 compaction 事件”的状态。

#### H. 缺少 workspace-native shell kernel

Codex 的一个关键优势是“面向工作区执行”的原生能力，而不是纯 RPC 工具箱。

Catown 当前仍以工具为主：

- `read_file`
- `write_file`
- `list_files`
- `execute_code`

这能做很多事，但与 Codex 风格的：

- 持续 shell session
- PTY / stdin / session state
- patch-aware editing loop
- command-level approval / escalation

相比还有明显差距。

### 9.3 当前阶段的判断

截至目前，Catown 更准确的定位应是：

> `Codex-style orchestration runtime + Catown workflow/governance layer`

而不是：

> `Codex-style unified agent kernel`

这说明路线没有走偏，但“底层内核收敛”还没有完成。

### 9.4 建议的后续优先级

如果目标是继续向 Codex 风格演进，优先级建议如下：

1. **先收敛统一 runner**
   - 把 sync / SSE / orchestration / pipeline stage 的核心执行语义进一步收敛。

2. **再补 subagent lifecycle**
   - 从协作工具升级为 runtime-managed spawn / wait / close / tree。

3. **然后补 approval / sandbox 主链**
   - 让危险动作、权限升级、执行策略进入统一 runtime state。

4. **最后推进 checkpoint 型恢复**
   - 把当前 replay/rebuild 恢复，逐步演进为更标准化的 task/run checkpoint continuation。

---

## 10. Pipeline 是否保留：与 Codex 编排的差别、优劣势、目标定位

### 10.1 判断结论

当前判断是：

> Pipeline 有必要存在，但不应继续作为默认执行主干。

更准确地说：

- **Codex 风格编排** 应负责默认任务运行时
- **pipeline** 应负责治理、审批、交付约束

因此，pipeline 的长期定位不应是“另一套和 runtime 平级的执行内核”，而应逐步演进为：

> 基于统一 runner 的 governance layer / blueprint layer

### 10.2 什么场景下 pipeline 仍有必要

pipeline 在以下场景仍明显有价值：

- **阶段化交付**
  - 如 PRD -> 架构 -> 开发 -> 测试 -> 发布 的固定链路
- **人工审批 / gate**
  - 需要明确的 `approve / reject / rollback`
- **产物导向**
  - 需要文档、测试报告、CHANGELOG、release tag 等明确 artifact
- **项目治理**
  - 需要阶段状态、负责人、回滚目标、对外可解释流程
- **发布控制**
  - release 阶段与 Git tag / 版本动作天然适合 workflow 语义

换言之，pipeline 更适合：

- 治理型项目
- 合规型流程
- milestone 明确的交付链
- 需要向管理者或协作团队清晰展示阶段状态的场景

### 10.3 什么场景下 pipeline 不该做默认主干

对于以下典型 Codex 场景，pipeline 不适合作默认路径：

- 交互式调研
- bug 修复
- 代码 review
- 探索式实现
- 短循环调试
- 多 agent 临时接力协作

这些任务的特点是：

- 回路短
- 路径不稳定
- 经常中途改变计划
- 强依赖工具调用和即时反馈

这类任务更适合：

- `task/run`
- `turn`
- `scheduler`
- `handoff`
- `resume/recovery`

而不是先进入固定 stage 再执行。

### 10.4 pipeline 与 Codex 编排的本质差别

#### A. pipeline：stage-first

pipeline 更接近：

```text
Pipeline Template
  -> Stage A
  -> Stage B
  -> Stage C
  -> Gate / Rollback / Release
```

特征是：

- 固定模板驱动
- 阶段先于任务细节
- 人工审批清晰
- artifact / rollback 语义强
- 适合项目治理

#### B. Codex 风格编排：task/run/turn-first

Codex 风格更接近：

```text
Task / Run
  -> Scheduler
  -> Turn
  -> Tools / Messages / Handoff
  -> State Update
  -> Next Turn
```

特征是：

- 任务先于阶段
- 调度器决定谁先做、谁 sidecar、谁接力
- 每轮基于状态重建上下文
- 更像真实 coding loop
- 适合交互式开发

### 10.5 pipeline 的优势

- **治理能力强**
  - 固定阶段、审批、打回、回滚、release 动作都天然契合
- **项目可解释性强**
  - 更容易回答“当前卡在哪一阶段”
- **产物意识强**
  - 对文档、报告、版本产物管理更自然
- **流程稳定**
  - 适合交付链清晰、参与方较多的项目
- **对非技术管理更友好**
  - 比动态 runtime 更容易被组织理解与采纳

### 10.6 pipeline 的劣势

- **刚性强**
  - 不适合真实开发中的回环、跳转、临时插入 side task
- **不适合默认聊天协作**
  - 会把短任务、探索式任务、调试任务过度流程化
- **重复实现运行时能力**
  - 容易形成第二套 executor / messaging / recovery 逻辑
- **消息与调度语义偏轻**
  - 目前 durable inbox 还主要是 `pending -> consumed`
- **还未完全并入统一 run ledger / task state**
  - 导致恢复、观测、调度语义仍分叉

### 10.7 Codex 风格编排的优势

- **更贴近实际 coding 过程**
  - 调研、修改、测试、再调研的短循环非常自然
- **调度灵活**
  - 哪个 agent 先上、谁 sidecar、何时 handoff 都可动态决定
- **默认多 agent 协作体验更好**
  - 不必先把所有行为塞进 pipeline template
- **更适合工具循环**
  - tool call / result / state update / next turn 语义更统一
- **更适合 run-level 恢复**
  - 与 scheduler state、handoff、task run ledger 更容易统一

### 10.8 Codex 风格编排的劣势

- **治理弱**
  - 如果没有额外约束，缺少明确 stage / gate / artifact 结构
- **对管理视角不够友好**
  - 动态运行时强，但“阶段看板”表达力偏弱
- **更依赖底层内核成熟度**
  - 需要统一 runner、subagent lifecycle、approval/sandbox、checkpoint state 才能真正稳定
- **如果没有治理层，容易漂移**
  - 可能一直局部推进，但难以形成可审计的交付链

### 10.9 当前 Catown 的最合理定位

结合现状，更合理的结构不是：

- 删掉 pipeline，全部改成纯动态 runtime

也不是：

- 保持 pipeline 与 runtime 两套平行内核长期共存

而是：

> 用 Codex 风格编排做默认 runtime，用 pipeline 做可选治理壳。

这意味着：

- 默认聊天室、多 agent 协作、短任务执行走 orchestration-first
- pipeline 在确有治理需求时启用
- pipeline 长期要“降级为治理层”，而不是维持完整独立 engine

### 10.10 代码层面的保留 / 并入 / 弱化建议

#### A. 应保留的 pipeline 能力

- pipeline template / config
- stage / gate 定义
- rollback 规则
- release / tagging / artifact 语义
- 面向治理的 API 与 UI

这些能力是 Catown 相比纯 Codex 风格运行时的差异化优势。

#### B. 应逐步并入统一 runner 的能力

- stage 执行时的 agent turn loop
- tool 调用主循环
- inter-agent 消息消费语义
- prompt rebuild / turn-state 组装
- resume / recovery 主链
- run ledger / task state 写入

也就是说，真正执行 agent work 的“发动机”不应长期分裂在：

- `backend/routes/api.py`
- `backend/pipeline/engine.py`

两套实现里。

#### C. 应逐步弱化的 pipeline 特征

- pipeline 作为默认多 agent 聊天协作主路径
- pipeline 自己维护一整套独立 async execution kernel
- pipeline 特有但无法复用到统一 runtime 的消息语义

### 10.11 推荐的目标架构

长期更推荐的目标是：

```text
Unified Run Controller
  -> Orchestration Runtime Kernel
     -> turn loop
     -> tool loop
     -> handoff / inbox
     -> scheduler
     -> run ledger
     -> recovery / checkpoint

Pipeline Governance Layer
  -> pipeline template
  -> stage / gate policy
  -> artifact expectations
  -> rollback / release rules
  -> governance UI / API
```

也就是说：

- **运行时只有一套**
- **治理策略可以有多种**

pipeline 在这个架构中更像：

- blueprint compiler
- governance wrapper
- delivery policy layer

而不是另一台执行引擎。

### 10.12 演进建议

#### Phase A：短期

- 保留现有 pipeline
- orchestration 继续作为默认聊天室运行时
- 尽快把 pipeline run 也接入统一 run ledger / monitor 语义

#### Phase B：中期

- 让 pipeline template 能编译成统一 runner 可理解的约束
- gate / artifact / rollback 成为 run policy，而不是独立 engine 私有逻辑

#### Phase C：长期

- 把 `pipeline/engine.py` 收缩成治理适配层
- 让真正执行逻辑完全收敛到统一 runner
- 如果后续真实使用表明某些 pipeline 能力价值低，可再继续裁剪

### 10.13 最终判断

因此，关于“pipeline 有必要存在么”的最终结论是：

- **有必要存在**
- **没必要继续做默认执行主干**
- **最有价值的未来角色是治理层，而不是第二套运行时内核**

---

## 11. 拆解后的开发计划（按 Codex 风格逐步实施）

### 11.1 总体原则

实施上不做“大爆炸式重写”，而采用：

- **先打通观测与状态骨架**
  - 先让 pipeline 与 orchestration 写入同一套 run ledger，避免继续分叉。
- **再收敛执行内核**
  - 先共享 runner 语义，再谈删减 `pipeline/engine.py`。
- **最后把 pipeline 压缩成治理层**
  - 保留 gate / artifact / rollback / release 的项目治理价值。

换言之，顺序应是：

```text
Unified visibility
  -> Unified execution semantics
  -> Unified runner
  -> Pipeline as governance layer
```

### 11.2 Phase 1：pipeline 先接入统一 run ledger

目标：让 pipeline run 不再是 Monitor 与恢复体系外的一等公民缺口。

本阶段具体工作：

1. **建立 `PipelineRun -> TaskRun` 链接**
   - 在 `pipeline_runs` 上增加 `task_run_id`
   - 启动 pipeline 时创建对应 `TaskRun`
   - 后续 API / Monitor 都能回到同一条 run 轨迹

2. **把 pipeline 生命周期事件写入 `task_run_events`**
   - 覆盖 `start / pause / resume / approve / reject / instruct`
   - 覆盖 `stage_started / stage_completed / stage_failed / gate_blocked`
   - 覆盖 `pipeline_completed / pipeline_failed`

3. **先统一观测，不急于统一执行**
   - 这一阶段的目标不是删除 pipeline engine
   - 而是先消除“聊天 run 可观测、pipeline run 不可观测”的断层

当前落地状态：

- 已为 `PipelineRun` 增加 `task_run_id`
- 已在 `start_pipeline()` 中自动创建并链接 `TaskRun`
- 已在 pipeline 主要生命周期节点追加 `task_run_events`
- 已在 `PipelineRunOut` 中暴露 `task_run_id`
- 已补测试，验证：
  - `start_pipeline()` 会创建 run-ledger bridge
  - `instruct()` 会向关联 `TaskRun` 追加事件

这一步完成后，pipeline 虽仍是独立执行路径，但已经开始进入统一 runtime ledger。

### 11.3 Phase 2：抽出共享 runner 语义

目标：减少 `routes/api.py` 与 `pipeline/engine.py` 两套执行内核的重复。

建议拆分的共享能力：

- `turn execution`
  - LLM 调用
  - tool loop
  - tool round 记录
  - turn-state rebuild
- `message / handoff consumption`
  - durable inbox 读取
  - handoff 注入
  - blocker / sidecar 基础语义
- `run state mutation`
  - target agent 更新
  - summary 写回
  - run ledger 事件落盘

阶段性目标不是立刻把 pipeline 全量迁移，而是让：

- orchestration turn
- pipeline stage turn

逐步调用同一批 runner helper。

### 11.4 Phase 3：把 pipeline 从执行引擎改造成治理编排器

目标：让 pipeline 负责“约束与治理”，而不是继续维护自己的完整 runtime。

这一阶段的方向应是：

- pipeline template 编译成 run policy / stage policy
- gate 变成统一 runner 可识别的 approval node
- artifact expectation 变成可审计的 delivery contract
- rollback 变成治理动作，而不是 pipeline 私有恢复机制

理想状态下，pipeline 负责回答：

- 这次交付有哪几个治理节点
- 哪些节点需要人工批准
- 需要哪些产物
- 失败时回到哪里

而统一 runner 负责回答：

- 当前轮该谁执行
- 当前轮上下文如何组装
- 工具怎么跑
- 消息怎么消费
- run 如何恢复

### 11.5 Phase 4：补齐 Codex 风格缺口

在 pipeline 已退居治理层后，再补当前与 Codex 的核心差距：

1. **subagent lifecycle**
   - `spawn / wait / close`
   - parent-child run tree
   - delegated work state

2. **approval / sandbox 主链**
   - action approval
   - escalation reason
   - pending approval queue
   - policy 进入 runtime state

3. **checkpoint 型恢复**
   - 比当前 replay/rebuild 更强的 executor snapshot
   - 更标准的 task state checkpoint
   - 更细粒度的 resume 断点

### 11.6 推荐实施顺序

按当前代码基座，建议严格按下面顺序推进：

1. **先完成 Phase 1**
   - 让 pipeline run 在 ledger / Monitor / API 层完全可追踪
2. **再做 Phase 2**
   - 抽共享 runner helper，减少双引擎分叉
3. **然后做 Phase 3**
   - 把 pipeline 收缩为治理层
4. **最后做 Phase 4**
   - 补 subagent / approval / checkpoint

### 11.7 当前实施切片

当前已开始执行的切片是：

> `pipeline -> task_run ledger bridge`

它是最小风险、最高杠杆的第一步，原因是：

- 不破坏现有 pipeline 行为
- 能立刻提升可观测性
- 为后续统一 recovery / monitor / scheduler state 打基础
- 为将来把 pipeline 从“执行引擎”收缩为“治理层”提供迁移锚点

### 11.8 2026-04-25 新进展：pipeline policy 已开始显式编译

本轮又向前推进了一小步：

- 新增 `backend/services/runner_policy.py`
  - 把 pipeline template 中的治理约束显式编译为统一 runner 可理解的 policy 结构
  - 当前已覆盖：
    - `approval`
    - `delivery contract`
    - `rollback`
    - `timeout`
    - `stage ordering`

- `pipeline_run_started` 事件现在会带上 `runner_policy`
  - 这意味着 pipeline 不只是“开始跑了”，还会把本次 run 的治理约束一起投影到 run ledger。

- `pipeline_stage_started / completed / gate_blocked`
  - 现在都会带上 `stage_policy`
  - manual gate、expected artifacts、rollback target 不再只是 engine 内部 if/else，而是开始以显式 policy 形式暴露

- pipeline engine 已开始用编译后的 policy 驱动部分行为
  - 如 `timeout`
  - `expected_artifacts`
  - `manual gate`
  - `rollback_on_blocker`

这还不是“统一 runner 已经完成”，但意义在于：

> pipeline 的治理语义已经不再完全藏在 `pipeline/engine.py` 私有分支里，而是开始投影成统一 runtime 可以识别的 policy object。

下一步更自然的方向就是：

- 让 orchestration / chat runtime 也逐步消费同一类 policy object
- 再进一步把 approval / sandbox / escalation 纳入同一条 runtime policy 主链

### 11.9 2026-04-25 新进展：chat / orchestration runtime 也开始消费同类 policy

在上一小步完成后，聊天室运行时也开始接入同一条 `runner_policy` 表达：

- 单 Agent chat / stream
  - `runtime_mode_selected` 已开始写入 `runner_policy`
  - `agent_turn_started` 已开始带上单步 `stage_policy`

- multi-agent orchestration / stream
  - `orchestration_started`
  - `scheduler_plan_created`
  - `scheduler_step_dispatched / completed / resumed`
  - 这些事件现在都会带上 `runner_policy` 或对应的 `stage_policy`

- recovery 路径
  - orchestration recovery 也开始带上同类 policy snapshot
  - 这样恢复链路不再只重建 scheduler state，也开始重建治理/调度语义视图

这一步的重点仍不是让 chat runtime 立刻受复杂治理约束驱动，而是先把：

- pipeline governance policy
- chat runtime policy
- orchestration schedule policy

收敛到同一类可序列化对象上。

这样后续把 approval / sandbox / escalation 接进统一 runtime state 时，就不必再分别改：

- `pipeline/engine.py`
- `routes/api.py`
- recovery path

而是可以围绕一条共享 policy 主链继续演进。

### 11.10 2026-04-25 新进展：tool-level approval / sandbox / escalation 已投影进 runner policy

本轮继续把之前还停留在“隐式约束”的工具语义显式化：

- `backend/tools/base.py`
  - 新增统一 tool policy snapshot / pack 构建能力
  - 每个工具现在都可以被投影成统一结构，至少包含：
    - `approval`
    - `sandbox`
    - `escalation`
    - `risk_level`
    - `side_effect_scope`

- chat / orchestration runtime
  - `runtime_mode_selected`
  - `orchestration_started`
  - `scheduler_plan_created`
  - 对应 `runner_policy.metadata` 现在会带上：
    - `tool_names`
    - `tool_policies`
    - `tool_policy_summary`

- stage 级 policy
  - 单 Agent turn / orchestration step / pipeline stage 的 `stage_policy.metadata`
  - 现在会带上：
    - `tool_names`
    - `tool_policy_summary`
  - 这样 UI / recovery / audit 不必反查工具注册表，也能知道该阶段允许哪些工具、是否涉及 network、是否可能触发 escalation。

- pipeline
  - `pipeline_run_started.runner_policy.metadata.stage_tool_packs`
  - 现在会按 stage 保存各自的 tool policy pack
  - 这意味着 pipeline 不只是表达 gate / rollback / timeout，也开始表达“每个 stage 允许什么工具、这些工具的治理面是什么”。

这一轮仍然是 **projection first**，不是 **enforcement first**：

- 还没有真正做成统一 pending approval queue
- 还没有把 escalation 做成可恢复的 action 节点
- 还没有把 sandbox 拒绝 / 升级执行接成完整状态机

但现在最关键的一步已经完成：

> approval / sandbox / escalation 不再只是散落在 tool 实现里的注释和 if/else，而是已经进入统一 `runner_policy` 主链，能被 runtime ledger、monitor、recovery 共同消费。

接下来再往 Codex 风格靠拢时，更自然的增量路径就是：

1. 把 tool call 失败区分为普通失败 vs `approval_blocked` / `sandbox_blocked`
2. 引入统一 pending approval / escalation queue
3. 把 recovery 扩展到可恢复被阻塞的 action，而不只是恢复 orchestration 调度

### 11.11 2026-04-25 新进展：tool blocked 已显式分成 approval_blocked / sandbox_blocked

在 11.10 把 tool policy 投影进 `runner_policy` 之后，这一轮继续往前走了一步：

- 共享 tool result classification 已落在统一服务层
  - 现在 tool 执行结果不再只有粗糙的 `success=true/false`
  - 会进一步归类为：
    - `succeeded`
    - `failed`
    - `approval_blocked`
    - `sandbox_blocked`

- chat / stream runtime
  - `tool_result` SSE 事件
  - `tool_call` runtime card
  - 现在都显式带上：
    - `status`
    - `blocked`
    - `blocked_kind`
    - `blocked_reason`

- run ledger
  - `tool_round_recorded` 现在会带上：
    - `tool_status_counts`
    - `blocked_tool_count`
    - `blocked_tools`
  - 同时会额外写入 `tool_call_blocked` 事件
  - 这样 task run detail 可以直接看到阻塞点，而不必从原始 tool output 里猜测。

- pipeline runtime
  - pipeline 工具白名单拒绝现在被显式投影成 `approval_blocked`
  - workspace / sandbox 型拒绝会被显式投影成 `sandbox_blocked`
  - 其 task ledger 事件也与 chat runtime 对齐

这一轮依然没有实现真正的：

- pending approval queue
- approval token / resume token
- blocked action resume

但它已经完成了一个很关键的中间层收敛：

> 现在系统已经能稳定地区分“普通工具失败”与“治理/沙箱阻塞”，后续再补 pending approval queue 时，不需要重新发明 blocked action 的表达。

### 11.12 2026-04-25 新进展：统一 pending approval / escalation queue 已落地

在 11.11 解决 blocked action 表达之后，这一轮把“阻塞后放到哪里等待人工处理”也补上了。

- 新增持久化队列模型
  - `approval_queue_items`
  - 统一承载两类等待处理项：
    - `approval`
    - `escalation`

- shared service
  - 新增 `backend/services/approval_queue.py`
  - 提供：
    - create
    - list
    - get
    - resolve
    - serialize
  - 并通过 `request_key` 做最小去重，避免同一阻塞点无限重复入队。

- runtime / ledger 接入
  - blocked tool call 现在会：
    - 先写 `approval_queue_item_created`
    - 再写 `tool_call_blocked`
  - `tool_call_blocked` payload 里会带 `queue_item_id`
  - `task_run detail` 现在也会直接附带 `approval_queue_items`

- pipeline gate 接入
  - manual gate 被阻塞时，也会进入同一个 approval queue
  - queue item target 为 `pipeline_gate`
  - 后续 gate approve / reject 时，会同步 resolve 对应 queue item

- 新 API
  - `GET /api/approval-queue`
  - `GET /api/approval-queue/{id}`
  - `POST /api/approval-queue/{id}/approve`
  - `POST /api/approval-queue/{id}/reject`

当前这套 queue 已经能承担：

- 统一查看当前有哪些 pending approval / escalation
- 把 tool blocked 与 pipeline gate 放进同一条人工处理通道
- 为后续 UI / monitor / recovery 接入提供稳定对象模型

但它还没有完全达到 Codex 那种闭环：

- tool approval 通过后，还不会自动恢复原 blocked action
- escalation 目前只是“持久化决策 + ledger 可见”，还不是“执行恢复”
- queue 还没进入 monitor 首页的一等公民视图

所以后续最自然的下一步就是：

1. 把 queue resolution 接成 blocked action resume / retry
2. 再把 queue 投影到 monitor / frontend runtime controls

### 11.13 2026-04-25 新进展：approval queue 已开始闭环到 blocked tool replay

在 11.12 把 pending approval queue 落地之后，这一轮继续把它往 Codex 风格闭环推进了一步：

- tool approval 不再只是“人工点同意然后改状态”
  - 对 `target_kind=tool`
  - 且 `request_payload.resume_supported=true`
  - 的 queue item，`POST /api/approval-queue/{id}/approve` 现在会直接尝试 replay blocked tool

- runtime tool replay
  - 普通 chat / orchestration 路径下，会按 queue item 里保存的：
    - `tool_name`
    - `arguments`
    - `chatroom_id`
    - `project_id`
    - `agent_name`
  - 重建 runtime kwargs，并以 `__catown_approval_granted=true` 再执行一次 tool
  - replay 结果会重新进入统一 `tool_round_recorded` ledger，而不是走一条旁路

- pipeline tool replay 准备工作也补上了
  - pipeline blocked tool queue item 现在会额外落：
    - `pipeline_run_id`
    - `pipeline_stage_id`
    - `stage_name`
    - `display_name`
  - 并且 `pipeline.engine` 新增了 blocked pipeline tool replay helper，为后续 API / UI 统一恢复打底

- queue resolution payload 更有“动作语义”
  - approve 之后的 `resolution_payload` 现在会显式记录：
    - `action_taken`
    - `replay_status`
    - `replay_success`
    - `replay_blocked`
    - `replay_blocked_kind`
    - `replay_result_preview`
  - 因此前端/monitor 不必再自己推断“批准之后到底发生了什么”

- 删除清理链也同步补齐
  - project/chat 删除时，现在会连带清掉 `approval_queue_items`
  - 避免 bulk delete `task_runs` 绕过 ORM cascade 后留下孤儿 queue row

这意味着系统已经从：

- blocked tool 只能“记录下来，等人看”

推进到：

- 一部分 blocked tool 已经能“批准后立即 replay，并把 replay 结果重新写回统一 ledger”

离完全体 Codex 风格闭环还差两步：

1. replay 之后继续回到 agent turn，而不是只停在 tool replay 本身
2. monitor / frontend 把 pending queue 和 replay 结果做成一等公民控制面

### 11.14 2026-04-25 新进展：approval queue 已投影到 monitor 控制面

这一轮把 queue 从“后端可查”继续推进到“monitor 可操作”：

- 新增 monitor 视图
  - `GET /api/monitor/approval-queue`
  - 返回 pending / approved / rejected 的统一视图
  - 每个条目会补齐：
    - chat / project / task run 语义
    - request / resolution preview
    - replay 状态

- monitor overview 也补了队列统计
  - `system.stats.approval_queue_total`
  - `system.stats.approval_queue_pending`

- 前端 monitor 的 Approvals 页改成读真实 queue
  - pending approvals 直接来自 `approval-queue`
  - approve / reject 能在 monitor 里直接执行
  - queue 状态变更后会回流到 task-run / ledger / monitor snapshot

这一步的意义是：

- approval queue 不再只是运行时内部日志
- 它已经成为 monitor 上的操作对象
- 更接近 Codex 那种“任务态 / 审批态 / 运行态统一可见”的控制面

### 11.15 2026-04-25 新进展：approval replay 已能回到原 agent turn 继续执行

这一轮把 approval queue 从“批准后 replay tool”继续推进到“批准后恢复 agent turn”：

- approve replayable runtime tool 时，不再停在 tool replay
  - 对 runtime chat/orchestration 里的 replayable blocked tool
  - approve 后会先 replay tool
  - 然后把 replay 结果写成一条 `tool_result` chat message
  - 再回到原 `task_run` 上继续触发 `trigger_agent_response(...)`

- 续跑保持在同一个 task run 内
  - 不新建第二个 run
  - 会把原 run 重新置回 `running`
  - 然后继续追加：
    - `approval_queue_item_followup_triggered`
    - 后续 agent turn / tool round / agent completion 事件
  - 这样 ledger 视角更接近 Codex 的“单次执行连续恢复”

- prompt/history 也补了 tool-role 语义
  - `build_recent_history(...)` 现在识别 `message_type in {"tool_result", "tool"}`
  - 会把它们转换成 `role="tool"` 的 history message
  - 并带上 `tool_call_id`
  - 这使 replay 结果能以更像原生工具回传的方式重新进入模型上下文

- follow-up 只对 runtime replay 生效
  - pipeline gate / pipeline blocked tool 目前仍保持保守
  - 先不跨到 pipeline 自动续跑，避免把治理性阻塞和可恢复执行混在一起

这一步完成后，runtime approval 闭环已经从：

- blocked
- 人工 approve
- replay tool

推进到：

- blocked
- 人工 approve
- replay tool
- 把结果回灌给模型
- 在原 turn 上继续完成执行

这已经明显更接近 Codex 风格的“工具中断后恢复同一轮执行”。

### 11.16 2026-04-25 实施状态核对

为避免这份 ADR 同时承载“目标态”和“已实现增量”后产生误读，这里补一份当前核对结论。

#### A. 已基本实现

- `11.8 pipeline policy 已开始显式编译`
- `11.9 chat / orchestration runtime 也开始消费同类 policy`
- `11.10 tool-level approval / sandbox / escalation 已投影进 runner policy`
- `11.11 tool blocked 已显式分成 approval_blocked / sandbox_blocked`
- `11.12 统一 pending approval / escalation queue 已落地`
- `11.13 approval queue 已开始闭环到 blocked tool replay`
- `11.14 approval queue 已投影到 monitor 控制面`
- `11.15 approval replay 已能回到原 agent turn 继续执行`

这些条目当前都已有对应代码落点，不再只是计划。

#### B. 部分实现，尚未收口

- `11.2 Phase 1：pipeline 先接入统一 run ledger`
  - bridge 与主要 lifecycle 事件已落地
  - 但 pipeline 仍未完全成为统一 runtime 的一等公民

- `11.3 Phase 2：抽出共享 runner 语义`
  - 已抽出 shared turn executor / turn-state / policy / ledger helper
  - 但 sync / SSE / orchestration / pipeline stage 仍未完全收敛成单一 runner envelope

- `11.4 Phase 3：把 pipeline 从执行引擎改造成治理编排器`
  - 已开始出现 policy compiler / governance projection
  - 但 pipeline 仍保留独立 engine 语义，还不是纯治理层

#### C. 尚未完成

- `11.5 Phase 4：补齐 Codex 风格缺口`
  - 真正的 subagent lifecycle 仍未完成
  - checkpoint 型恢复仍未完成
  - ownership / write-scope / retry / dead-letter 仍未完成
  - pipeline blocked tool 的 approve 后自动续跑仍未完成

#### D. 当前准确判断

截至当前，更准确的状态不是：

- “ADR 里的目标架构已经全部做完”

而是：

- “ADR 中最近几轮增量进展大多已落地”
- “ADR 中定义的长期目标架构仍在收敛中”
