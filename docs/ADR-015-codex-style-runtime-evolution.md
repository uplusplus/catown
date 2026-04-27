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

### 11.17 2026-04-25 新进展：monitor 已显式展示 approval replay follow-up 状态

在 11.15 把 runtime approval replay 接回原 agent turn 之后，这一轮继续把这段状态补到 monitor 控制面：

- `monitor approval queue entry` 不再只暴露 replay 结果
  - 现在会额外投影：
    - `followup_attempted`
    - `followup_status`
    - `followup_reason`
    - `followup_error`
    - `followup_message_id`

- resolution preview 也更贴近实际恢复结果
  - 如果 replay 后续跑失败
  - monitor 不再只显示 replay preview
  - 会优先带出 `followup_error`

- 前端 Approvals 页面已能区分：
  - queue item 是否只是 replay 成功
  - 还是 replay 后已经继续完成 follow-up
  - 或 replay 后 follow-up 被跳过 / 失败

这一步的意义是：

- approval queue 不再只表达“批了没批”
- 也能表达“批完之后执行链有没有真正继续跑完”
- 更接近 Codex 风格 control plane 对恢复结果的可见性

### 11.18 2026-04-25 新进展：context compaction 已开始进入 runtime event 与 monitor

此前 Catown 虽然已经有：

- history summary
- task-state fragments
- `ContextSelector` token / fragment budget

但 compaction 仍然是“静默发生”，没有进入控制面。

这一轮把它往前推进了一步：

- `ContextSelector` 现在会显式产出 selector diagnostics
  - developer / user 各自的：
    - candidate_count
    - selected_count
    - dropped_count
    - truncated_count
    - candidate_tokens
    - selected_tokens
  - 以及 selector 侧的：
    - `max_fragments`
    - `max_tokens`

- chat runtime / pipeline runtime 现在会在发生 compaction 时写 `context_compaction` event
  - 不再只是“消息被截短了，但外部不知道”
  - 而是会把 compaction 作为 run 级事件留痕

- monitor overview / Context 页面也开始消费这类事件
  - overview 新增：
    - `system.stats.context_compactions`
    - `recent_compactions`
  - Context 页不再显示“尚未发出 compaction 事件”的占位文案
  - 已能看到最近 compaction 的：
    - dropped / truncated 数量
    - candidate / selected 数量
    - max fragment / token budget

这一步还不是完整的 checkpoint-friendly compaction 模型，但意义在于：

- compaction 首次从“隐式 selector 行为”升级成“显式 runtime telemetry”
- 后续再做：
  - compaction-aware resume
  - checkpoint snapshot
  - context pressure trend

就有了可复用的观测基础。

### 11.19 2026-04-25 新进展：checkpoint-friendly task snapshot 已开始进入 recovery 与 monitor

在 11.18 把 compaction 提升成显式 runtime telemetry 之后，这一轮继续往“checkpoint-friendly recovery”推进了一步，但仍然保持增量路线：

- 没有新增专门的 checkpoint 表
- 而是先从现有 `TaskRun.events` 与 `approval_queue_items` 派生统一 `checkpoint_snapshot`

这一轮补上的内容是：

- `serialize_task_run_summary()` 现在会输出 `checkpoint_snapshot`
  - 当前快照包含：
    - `event_count`
    - `latest_event_type`
    - `latest_event_at`
    - `latest_agent_turn`
    - `latest_compaction`
    - `latest_scheduler_runtime`
    - `pending_approval_count`
    - `approval_queue_count`
    - `status`
    - `summary`

- recovery 入口事件现在显式记录恢复起点快照
  - `task_run_recovery_started`
  - `scheduler_recovery_state_rebuilt`
  - 都会带上恢复开始时的 `checkpoint_snapshot`

- monitor 的 task-run detail 现在也直接显示 checkpoint snapshot
  - 不再需要手工翻整条 event stream 才知道：
    - 最近一次 agent turn 停在什么响应
    - 最近一次 compaction 丢了多少上下文
    - 最近一次 scheduler runtime 是否已经可重建

这一步仍然不是完整的 Codex 风格 checkpoint continuation：

- 现在的 snapshot 还是“从 ledger 派生”
- 不是 executor 在关键点主动持久化的标准 checkpoint object
- 也还不包含完整 subagent tree / inflight tool state / turn-local continuation cursor

但它已经把恢复语义从“只有 rebuild 逻辑”推进到：

- 恢复前有可读 checkpoint 视图
- 恢复时有显式 checkpoint 起点记录
- monitor 上能直接看到 checkpoint 级状态

后续如果继续往前走，下一层自然会是：

- 更标准的 scheduler continuation cursor
- approval / replay / follow-up 与 checkpoint snapshot 的统一表达
- 从“派生 snapshot”逐步演进到“显式 checkpoint object”

### 11.20 2026-04-25 新进展：pipeline blocked tool 已能停住 stage 并在 approval 后恢复

在 11.13 到 11.15 把 runtime blocked tool replay 链逐步闭环之后，pipeline 侧还留着一处明显差距：

- tool 调用虽然已经会产出 `approval_queue_item`
- 但 stage 本身并不会因为 blocked tool 停住
- approve 后也不会把 pipeline 接回当前 stage 继续执行

这一轮先把这条链补到了可用状态：

- `_run_agent_stage()` 现在会把 blocked tool round 显式挂到当前 stage 的临时状态
  - 记录：
    - `tool_name`
    - `status`
    - `blocked_kind`
    - `blocked_reason`
    - `turn`

- `_execute_stage()` 发现本轮 stage 因 blocked tool 停住后，不再把阶段记成 `completed`
  - 而是：
    - 置 `stage.status = "blocked"`
    - 保留 `output_summary`
    - 写入 `pipeline_stage_blocked`
    - 让外层执行循环按既有逻辑把 pipeline 置成 `paused`

- approval queue API 在 replay pipeline blocked tool 成功后，开始补 follow-up continuation
  - 先通过 `pipeline_engine.instruct(...)` 注入 replay result
  - 再对暂停中的 pipeline 调 `pipeline_engine.resume(...)`
  - 同时写：
    - `approval_queue_item_followup_triggered`
    - 若失败则 `approval_queue_item_followup_failed`

这一步的意义是：

- pipeline 不再把“被审批挡住的 tool turn”伪装成正常 stage 完成
- pipeline blocked tool 与 runtime blocked tool 终于开始共享同一套：
  - blocked queue
  - replay
  - follow-up continuation

它仍然不是完整的 executor checkpoint continuation：

- resume 现在还是“给当前 stage 注入 follow-up context，然后重跑当前 stage”
- 不是从精确的 turn-local cursor 继续

但相较之前，已经从“只有 replay，没有恢复执行”推进到了：

- stage 会停住
- queue 能 replay
- replay 后 pipeline 会继续

### 11.21 2026-04-25 新进展：checkpoint snapshot 已开始携带 continuation cursor

在 11.19 把 checkpoint snapshot 引入 recovery 与 monitor 之后，仍然留着一个明显问题：

- snapshot 能告诉你“最近发生了什么”
- 但还不能明确告诉你“如果现在要恢复，下一步应该怎么续”

这一轮先补了一个轻量级 `continuation_cursor`，仍然坚持不新增专门表结构：

- `checkpoint_snapshot.continuation_cursor` 完全从现有：
  - `TaskRun.events`
  - `approval_queue_items`
  - `runtime` payload
  派生

当前 cursor 会显式区分几类恢复语义：

- `await_approval`
  - 存在 pending tool approval / escalation queue item
  - 同时给出：
    - `resume_strategy`
    - `turn`
    - `tool_name`
    - `blocked_kind`
    - `queue_item_id`

- `followup_injected`
  - approval replay 已完成，follow-up continuation 已经被触发

- `resume_scheduler`
  - 当前 run 仍有 scheduler runtime snapshot，可按现有恢复逻辑继续 rebuild

- `continue_agent_turn`
  - 最近停在 tool round 之后，但还没有进入 blocked/pending queue 语义

- `none`
  - 当前没有需要恢复的 continuation cursor

这一轮同时把 cursor 投到了两个地方：

- recovery 入口事件
  - `task_run_recovery_started`
  - `scheduler_recovery_state_rebuilt`
  - 都会记录恢复开始前的 continuation cursor

- monitor task-run 控制面
  - 列表接口已经能返回 `checkpoint_snapshot.continuation_cursor`
  - 详情页也直接展示：
    - next action
    - resume strategy
    - tool / turn / queue 等 cursor payload

这一步仍然不是完整的 turn-local continuation cursor：

- 现在的 cursor 只是“恢复决策视图”
- 还不是 executor 可直接反序列化恢复的内部 program counter

但它已经把 Catown 从“只有快照，没有恢复建议”推进到了：

- snapshot 除了描述状态
- 还开始描述恢复入口与下一动作

### 11.22 2026-04-25 新进展：checkpoint snapshot 已开始携带 turn-local continuation state

在 11.21 加入 `continuation_cursor` 之后，还有一个残留问题：

- cursor 已经知道“下一步应该做什么”
- 但还没有携带“恢复时实际要喂给模型/执行器的最近一轮 turn-local payload”

这一轮继续保持无 schema 迁移路线，先把最近一轮 tool round 的 continuation state 显式写进 ledger：

- `tool_round_recorded` payload 现在开始携带 `turn_local_state`
  - 包括：
    - `assistant_content`
    - `tool_results`
    - `protocol_messages`

- `checkpoint_snapshot` 现在会继续派生 `turn_local_state`
  - 当前包含：
    - `turn`
    - `tool_names`
    - `blocked_tool_count`
    - `assistant_content`
    - `protocol_messages`
    - `tool_results`
    - `blocked_tool`

这一步的意义在于：

- `continuation_cursor` 负责回答：
  - “接下来该做什么”
- `turn_local_state` 负责回答：
  - “如果要从最近一轮 tool turn 继续，手头可恢复的最小 protocol payload 是什么”

monitor 上也同步做了投影：

- task-run 详情现在能直接查看：
  - continuation cursor
  - turn-local state payload

这仍然不是完整的 executor continuation object：

- 当前只覆盖“最近一轮 tool turn”的 protocol payload
- 还没有把多轮 turn state、older summary rounds、完整 in-flight cursor 结构化持久化

但相较之前，已经从“只有恢复建议”推进到了：

- 有恢复建议
- 也有最近一轮可直接复用的 turn-local continuation payload

### 11.23 2026-04-25 新进展：turn-local continuation state 已开始保留 multi-round protocol tail

在 11.22 把最近一轮 tool turn 的 continuation payload 放进 snapshot 之后，还有一个明显差距：

- 只能看到“最后一轮”
- 看不到多轮 tool turn 的最近 protocol tail
- 也看不到更早轮次已经被压缩成了什么摘要

这一轮继续保持纯派生路线，从已有 `tool_round_recorded` event 序列里补了两层结构：

- `protocol_tail_messages`
  - 当前取最近两轮 tool round 的 protocol messages
  - 作为“恢复时最值得保留的 recent protocol tail”

- `prior_round_summaries`
  - 更早的 tool round 不再混在 tail 里
  - 而是派生为更轻量的 per-round summary
    - `turn`
    - `tool_names`
    - `blocked_tool_count`
    - `assistant_content`

于是当前的 `turn_local_state` 分成了三层：

- 最新一轮：
  - `protocol_messages`
  - `tool_results`
- 最近多轮 tail：
  - `protocol_tail_messages`
- 更老轮次摘要：
  - `prior_round_summaries`

这一步的意义是：

- snapshot 不再只是“最近一轮的孤立 payload”
- 而开始接近真正 continuation object 会需要的：
  - recent protocol tail
  - older round summary
  - blocked tool marker

它仍然不是完整的 executor continuation object：

- tail 长度现在是派生规则，不是 executor 显式 checkpoint policy
- older summary 仍然是展示/恢复辅助结构，不是 turn engine 直接消费的原生 state

但已经比 11.22 更接近 Codex 风格：

- 不只知道最后一轮
- 也开始知道“最近几轮该带什么、再早的轮次该怎么折叠”

### 11.24 2026-04-25 新进展：continuation state 已开始回填到 follow-up runtime turn

在 11.23 之前，Catown 已经能：

- 派生 continuation cursor
- 派生 turn-local state
- 派生 multi-round protocol tail

但这些状态仍然主要停留在“可观测层”，没有真正接回运行态。

这一轮把第一条消费链补上了：

- `build_turn_state_from_checkpoint_snapshot(...)`
  - 现在能从 `checkpoint_snapshot` 回填：
    - `continuation_protocol_messages`
    - `continuation_summaries`
  - 并把它们装回 `TurnContextState`

- `TurnContextState.protocol_messages()`
  - 现在会先输出 continuation protocol tail
  - 再拼接本轮新增的 tool round protocol

- `TurnContextState.summarized_tool_lines()`
  - 现在也会先带上 prior round summaries
  - 再接本轮运行中继续累积的 summary lines

- runtime approval replay follow-up
  - 在重新触发 `trigger_agent_response(...)` 时
  - 不再只传一段 `extra_context`
  - 而是同时把 `checkpoint_snapshot` 回填到 `TurnContextState`

这意味着当前的 follow-up runtime turn 已经开始真正消费：

- prior tool-call protocol
- multi-round continuation tail
- older round summaries

而不再只是“给模型一句文字描述”。

这一步仍然不是完整的 executor continuation：

- 当前只接回了 runtime follow-up 这条链
- orchestration / pipeline / startup recovery 还没有统一消费这一套 state

但意义很明确：

- continuation state 第一次从“监控/调试数据”
- 进入“实际运行态输入”

### 11.25 2026-04-25 新进展：recovery 事件已开始显式声明 continuation-state consumption

在 11.24 之后，runtime follow-up 已经会真实消费 continuation state，但 startup/manual recovery 仍有一个可见性缺口：

- 恢复链本身虽然依赖 checkpoint snapshot
- 但事件层还没有明确声明：
  - “这次恢复到底消费了哪类 continuation state”

这一轮先把这层恢复元数据补齐：

- 新增 `recovery_continuation_state`
  - 当前由 recovery 起点 snapshot 派生
  - 会显式记录：
    - `consumed`
    - `next_action`
    - `resume_strategy`
    - `consumed_layers`
    - `protocol_tail_message_count`
    - `prior_round_summary_count`

- 这份元数据现在会进入：
  - `task_run_recovery_started`
  - `scheduler_recovery_state_rebuilt`
  - `task_run_recovery_completed`

对于当前 orchestration recovery，这层元数据最常见的表达是：

- `next_action = resume_scheduler`
- `resume_strategy = rebuild_from_runtime_snapshot`
- `consumed_layers` 至少包含 `runtime_snapshot`

这一步的意义是：

- recovery 不再只是“做了恢复”
- 而是开始显式说明“恢复消费了什么 continuation state”

它仍然不是完整的 recovery executor integration：

- 现在更多是恢复路径的消费声明与观测增强
- 不是让 orchestration recovery 直接反序列化 `protocol_tail_messages` 去推进执行

但它把 recovery 也纳入了同一套 continuation-state 语义体系里：

- follow-up runtime turn 已经消费
- recovery 事件现在也开始声明消费

### 11.26 2026-04-25 新进展：orchestration recovery 已开始真实消费 checkpoint protocol tail

11.25 只补齐了 recovery 的消费声明，但执行面仍有明显缺口：

- interrupted orchestration recovery 在恢复 agent turn 时
- 还是主要依赖 `extra_context` 文本与 runtime snapshot 元数据
- 没有把 checkpoint 中保留下来的 protocol tail 真正送回 `TurnContextState`

这意味着：

- recovery event 已经会说自己消费了 `runtime_snapshot`
- 但恢复后的 prompt 未必真的带回最近一轮 tool-call assistant/tool message
- continuation state 在 recovery 路径里仍然停留在“声明过”，没有完全进入“执行输入”

这一轮把执行链补上：

- `_resume_interrupted_orchestration_task_run(...)`
  - 在恢复开始时继续构建 `recovery_checkpoint_snapshot`
  - 并把这份 snapshot 传进恢复用的 `_run_single_agent_turn(...)`

- `_run_single_agent_turn(...)`
  - 新增 `checkpoint_snapshot` 参数
  - turn state 初始化不再只用空白 `TurnContextState`
  - 而是先通过 `build_turn_state_from_checkpoint_snapshot(...)`
    回填：
    - continuation protocol tail
    - prior round summaries
    - previous agent work
  - 然后再合并当前调度阶段补充的 inter-agent messages

因此现在的 interrupted orchestration recovery：

- 不只是事件层声明自己消费了 continuation state
- 而是在真正恢复 agent turn 时
- 把 checkpoint 里的 recent protocol tail 一并送回 LLM 上下文

测试也同步收紧：

- recovery fixture 里显式注入一段 `tool_round_recorded`
  - assistant 发起 `read_file`
  - tool 返回 `Design checkpoint contents`
- startup recovery test 现在会断言：
  - `recovery_continuation_state.protocol_tail_message_count == 2`
  - 恢复后的首个 LLM 输入中确实包含：
    - 该 assistant tool-call message
    - 该 tool result message

这一步的意义很直接：

- recovery continuation state 终于不只是一份可观察元数据
- orchestration recovery 开始与 runtime approved replay follow-up 共享同一类 prompt rehydration 语义
- “checkpoint 保留了什么” 与 “恢复时真正喂回了什么” 开始对齐

### 11.27 2026-04-25 新进展：checkpoint turn state 已收口到 latest-turn window，并随 recovery 前滚

11.26 把 orchestration recovery 接上了 checkpoint protocol tail，但随即暴露出一个更细的正确性问题：

- `checkpoint_snapshot.turn_local_state`
  - 仍然是从“全量历史里最近一次 `tool_round_recorded`”直接派生
- 如果恢复后的下一步 turn 本身没有再跑 tool
  - 旧 turn 的 protocol tail 仍会继续留在 snapshot 里
- 这样在多 step recovery 里
  - 第一轮恢复消费是正确的
  - 第二轮开始就可能继续吃到上一轮 restart 前的 stale tool protocol

这和 Codex 风格 continuation 语义不一致：

- continuation state 应该描述“当前最近那个 turn 的可继续状态”
- 不是“这个 run 历史上最后一次出现过的 tool round”

这一轮做了两个收口动作：

- `build_task_run_checkpoint_snapshot(...)`
  - 新增 latest-turn window 识别逻辑
  - 先定位最近一个 turn 的事件窗口：
    - 若最后一个 `agent_turn_started` 还未完成，就取该 in-flight window
    - 否则取最近一个 `agent_turn_completed` 对应的 start/completed window
  - `latest_tool_round` / `latest_tool_blocked` / `latest_followup`
    以及 `turn_local_state`
    都改为只从这个窗口里派生

- `_resume_interrupted_orchestration_task_run(...)`
  - 不再把 recovery 起点那一份 snapshot 固定传给后续所有 step
  - 而是在每个恢复 step 开始前：
    - `refresh(task_run)`
    - 重新构建当前 step-local `checkpoint_snapshot`
  - 这样 continuation state 会随着恢复推进自然前滚

结果是：

- restart 前 analyst 留下的 tool protocol
  - 仍会进入 recovery 后的第一个 turn
- 但如果这个 turn 完成后没有新的 tool round
  - 后续 step 的 checkpoint state 就会清空旧 tail
- recovery 不会再把“已经被后续 turn 覆盖掉的旧 protocol”重复喂回模型

测试也相应补上：

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 验证 analyst 有 tool round、developer 后续无 tool 的情况下
  - monitor 看到的 latest checkpoint turn state 已经清空旧 protocol

- `test_startup_recovers_interrupted_orchestration_run`
  - 继续验证第一轮 recovery prompt 会吃到 checkpoint tool tail
  - 同时新增断言：第二轮 recovery prompt 不再包含旧的 `read_file` tool result

这一轮的意义在于：

- checkpoint continuation state 终于从“latest tool activity”
- 收口成“latest turn-local continuation window”
- recovery prompt rehydration 开始具备真正的前滚语义，而不是一次性把旧 tail 粘到底

### 11.28 2026-04-25 新进展：recovered dispatch 事件已暴露 step-local continuation snapshot

11.27 之后，恢复执行面已经具备 step-by-step 前滚语义，但观测面还有一个空洞：

- 我们知道 recovery executor 每一步都会重建 `checkpoint_snapshot`
- 也知道第一步和后续步吃到的 continuation state 可能不同
- 但事件流里还看不到“某一个 recovered dispatch 实际用了哪份 snapshot”

这会让排障停留在推断层：

- 你能从最终结果看出恢复成功了
- 但不容易直接确认：
  - 第一个恢复 step 是否真的吃到了旧 protocol tail
  - 第二个恢复 step 是否已经看到前滚后的空 tail

这一轮把这层 step-local observability 补上：

- `_resume_interrupted_orchestration_task_run(...)`
  - 在每个 recovery step dispatch 前
    先重建一次当前 `step_checkpoint_snapshot`
  - 并派生对应的 `step_recovery_continuation_state`

- `scheduler_step_dispatched`
  - 当 `recovered = true` 时
  - payload 现在额外带上：
    - `checkpoint_snapshot`
    - `recovery_continuation_state`

这样 recovery trace 现在可以逐步回答：

- 这个 step 恢复时看到的 latest agent turn 是谁
- 这个 step 恢复时 `turn_local_state` 里还有没有 protocol tail
- 这个 step 真正消费了多少条 tail message / prior summaries

测试也同步加强：

- `test_startup_recovers_interrupted_orchestration_run`
  - 现在会显式检查 recovered dispatch events
  - 第一条 recovered dispatch：
    - `assistant_content == "Open the design doc before continuing."`
    - `protocol_tail_message_count == 2`
  - 第二条 recovered dispatch：
    - `turn_local_state.protocol_messages == []`
    - `protocol_tail_message_count == 0`

这一步的意义是：

- recovery 的 continuation-state 前滚
  - 不再只是执行器内部行为
- 而是进入 durable event trace
- 让 monitor / 调试 / 审计都能按 step 精确看到“恢复时到底喂回了什么”

### 11.29 2026-04-25 新进展：pipeline resumed stage 已开始消费 checkpoint continuation state

11.28 之后，orchestration recovery 已经具备：

- step-local checkpoint snapshot
- step-local continuation-state observability
- prompt rehydration 前滚

但 pipeline 仍有一条残留分叉：

- pipeline blocked tool approve 后虽然会：
  - replay tool
  - 注入 boss instruction
  - 调 `pipeline_engine.resume(...)`
- 可真正恢复 stage 时
  - `_run_agent_stage(...)` 仍从空白 `TurnContextState()` 起步
  - 只能吃到 replay 后新增的 instruction / inter-agent message
  - 吃不到 checkpoint 里保留下来的最近一轮 tool protocol tail

这意味着 pipeline 虽然“能继续跑”：

- 但 resumed stage 首次 prompt
  - 不具备 runtime / orchestration recovery 已有的 continuation rehydration 语义
- 也就是：
  - approval replay 把执行结果接回来了
  - 但模型侧上下文并没有真正从 checkpoint continuation state 继续

这一轮把 pipeline 也接入同一条语义链：

- `_run_agent_stage(...)`
  - 在开始 stage turn loop 前
  - 先取关联 `task_run`
  - 构建 `checkpoint_snapshot`
  - 再通过 `build_turn_state_from_checkpoint_snapshot(...)`
    初始化 stage 的 `TurnContextState`

因此 resumed pipeline stage 现在不再只是：

- 从空白 turn state + 新注入 instruction 重新开跑

而是会额外带回：

- 最近一轮 assistant tool-call protocol
- 最近一轮 tool result message
- prior round summaries（若 snapshot 内存在）

测试也同步补上：

- `test_run_agent_stage_rehydrates_checkpoint_protocol_tail_on_resume`
  - 预先在关联 `task_run` 中种入：
    - `agent_turn_started`
    - `tool_round_recorded`
    - `tool_call_blocked`
    - `approval_queue_item_followup_triggered`
  - 然后直接恢复 pipeline stage
  - 断言首次 LLM 输入中确实重新出现：
    - `read_file` assistant tool-call
    - `Design checkpoint contents` tool result

这一步的意义是：

- pipeline 不再只是在治理面接入统一 approval queue
- 而是开始在执行输入面共享同一套 checkpoint continuation 语义
- `chat/runtime/orchestration/pipeline`
  - 四条主路径的 continuation rehydration 开始真正收敛

### 11.30 2026-04-25 新进展：streaming orchestration step 已开始消费 checkpoint continuation state

11.29 之后，pipeline resumed stage 已接上 checkpoint continuation，但 stream orchestration 仍留着一条偏旧分支：

- 多 agent streaming orchestration 的每个 step
  - 仍通过 `_iter_agent_turn_events(...)` 起一个新的 `TurnContextState`
- 它会带：
  - `previous_agent_work`
  - inter-agent messages
- 但不会带：
  - 上一位 agent 最近留下的 tool protocol tail

这意味着 streaming orchestration 虽然表面上已经能连续协作：

- 后续 agent 能看到文字摘要和 handoff
- 但看不到最近一轮真实 assistant/tool protocol
- 与 non-stream orchestration recovery、runtime follow-up、pipeline resumed stage 的 continuation 语义仍不一致

这一轮把 stream orchestration 也接回统一语义：

- `_iter_agent_turn_events(...)`
  - 新增 `checkpoint_snapshot` 参数
  - turn state 初始化改为：
    - `build_turn_state_from_checkpoint_snapshot(checkpoint_snapshot, previous_agent_work=...)`
  - 不再从纯空白 `TurnContextState(previous_agent_work=...)` 起步

- stream orchestration scheduler loop
  - 在每个 step dispatch 前
    - `refresh(task_run)`
    - 重新构建当前 step-local `checkpoint_snapshot`
  - 然后把它传给 `_iter_agent_turn_events(...)`

因此后续 streaming step 现在会同时看到：

- 上一阶段的文字工作摘要
- durable handoff / boss / inter-agent messages
- checkpoint 中保留下来的最近 tool protocol tail

测试也同步收紧：

- `test_standalone_multi_mention_stream_rebuilds_tool_loop_from_turn_state`
  - 原本只验证 developer step 能看到 analyst 的 inter-agent message
  - 现在继续断言：
    - developer 那一轮的 LLM 输入里
    - 还能重新看到 `read_file` 的 tool protocol message

这一步的意义是：

- stream orchestration 不再只是一条“摘要驱动”的协作链
- 而是开始共享同一套 checkpoint-backed prompt rehydration 语义
- `non-stream / stream / recovery / pipeline`
  - 几条主执行路径的 continuation model 又收敛了一步

### 11.31 2026-04-25 新进展：streaming single-agent 入口也已接入 checkpoint continuation state

11.30 之后，多 agent streaming orchestration 已不再从空白 turn state 起步，但还有两处单 agent stream 入口仍保留旧逻辑：

- standalone assistant stream
- project single-agent stream

这两条路径的问题一致：

- stream turn loop 启动前直接 new 一个空白 `TurnContextState()`
- 即便关联 `task_run` 已经带有 checkpoint continuation 信息
- 首次 streaming LLM 调用也不会把这份状态重新带回 prompt

这会形成一个不必要的行为分叉：

- non-stream single-agent 已接 checkpoint snapshot
- stream orchestration 已接 checkpoint snapshot
- pipeline resumed stage 已接 checkpoint snapshot
- 但 single-agent stream 仍停留在“每次都从空白 turn state 开始”

这一轮把这两个入口也接回统一语义：

- `_stream_standalone_assistant_response(...)`
  - 在启动 `iter_stream_turn_events(...)` 前
  - 先从 `task_run` 构建 `checkpoint_snapshot`
  - 再通过 `build_turn_state_from_checkpoint_snapshot(...)`
    初始化 stream turn state

- project single-agent stream 分支
  - 同样先从当前 `task_run` 构建 `checkpoint_snapshot`
  - 再用它初始化 streaming turn state

因此现在的 single-agent stream 入口：

- 不再强制从空白 `TurnContextState()` 起步
- 如果当前 run 已保留 recent protocol tail / prior summaries
  - 首个 streaming prompt 也会真实消费这份 continuation state

测试也同步补上：

- `test_project_single_agent_stream_rehydrates_checkpoint_state`
  - 在创建 streaming `task_run` 后立刻种入一段 `tool_round_recorded`
  - 然后发起 project single-agent stream
  - 断言首个 streaming LLM 输入中确实带回：
    - `read_file` assistant tool-call
    - `Design checkpoint contents` tool result

这一步的意义是：

- 剩余直接 new 空白 `TurnContextState()` 的主 stream 入口进一步减少
- single-agent / multi-agent / recovery / pipeline 开始共享更一致的 continuation rehydration 语义
- “streaming 是不是会丢 checkpoint continuation” 这类分叉行为又少了一层

### 11.32 2026-04-25 新进展：pipeline stage-start 事件已开始投影 checkpoint continuation consumption

11.31 之后，pipeline stage 在执行输入面已经会真实消费 checkpoint continuation state，但事件层还留着一处不对齐：

- `pipeline_stage_started`
  - 仍只记录 stage policy / gate / timeout / expected artifacts
- 看不到：
  - 这个 stage 开始时实际拿到的 `checkpoint_snapshot`
  - 以及它准备消费多少 protocol tail / prior summaries

于是 pipeline 在这一点上还落后于 orchestration recovery：

- recovery 的 step-dispatch event 已经能精确说明“这一步拿了什么 continuation state”
- pipeline resumed stage 虽然执行面已正确
- 但 monitor / 调试 / 审计层还不能直接看到这次 stage-start 的 continuation 输入

这一轮把这层事件投影补齐：

- `services.run_ledger.describe_checkpoint_continuation_state(...)`
  - 抽出通用 helper
  - 统一从 `checkpoint_snapshot` 派生：
    - `consumed`
    - `next_action`
    - `resume_strategy`
    - `consumed_layers`
    - `protocol_tail_message_count`
    - `prior_round_summary_count`

- `routes/api.py`
  - recovery 继续复用同一 helper
  - 避免 recovery / pipeline 各自维护一份 continuation-state 统计逻辑

- `pipeline_stage_started`
  - 现在会额外带上：
    - `checkpoint_snapshot`
    - `continuation_state`

这样 pipeline stage-start event 现在也能直接回答：

- 这次 stage 开始时是否带着 continuation state
- 最近 protocol tail 有多少条
- prior round summaries 有多少条
- 当前 snapshot 的 latest turn-local payload 是什么

测试也同步补上：

- 新增 `test_pipeline_stage_started_exposes_checkpoint_continuation_state`
  - 预先种入：
    - `agent_turn_started`
    - `tool_round_recorded`
    - `approval_queue_item_followup_triggered`
  - 然后启动 stage
  - 断言 `pipeline_stage_started` 事件中：
    - `checkpoint_snapshot.turn_local_state.assistant_content == "Open the design doc before continuing."`
    - `continuation_state.consumed is True`
    - `continuation_state.protocol_tail_message_count == 2`

- 既有 pipeline stage 测试也补充断言：
  - 新开 stage 的 `pipeline_stage_started` 明确显示：
    - `checkpoint_snapshot.event_count == 0`
    - `continuation_state.consumed is False`

这一步的意义是：

- pipeline 不只是执行器内部吃到了 checkpoint continuation
- 它的 stage-start event 现在也能把这份消费状态 durable 地投影出来
- execution semantics 与 observability semantics 又进一步对齐

### 11.33 2026-04-25 新进展：pipeline resume 事件已开始投影 checkpoint continuation consumption

11.32 把 `pipeline_stage_started` 的 checkpoint consumption 投影出来之后，pipeline 事件层还剩最后一个明显断点：

- `pipeline_resumed`
  - 仍然只说明“pipeline 恢复了”
- 但看不到：
  - 恢复那一刻 task run 上实际持有什么 `checkpoint_snapshot`
  - 以及 resume 将继续消费多少 protocol tail / prior summaries

这会让 approval replay 之后的观测链缺一段：

- `approval_queue_item_followup_triggered`
  - 会说 follow-up 原因是 `pipeline_resumed`
- `pipeline_stage_started`
  - 之后会说 stage 起步时拿到了什么 continuation state
- 但中间这个真正的 `resume` 动作本身
  - 还不能直接说明它恢复时看到的 checkpoint continuation 输入

这一轮把这段链补齐：

- `pipeline_engine.resume(...)`
  - 在把 run 状态切回 `running` 后
  - 先拿关联 `task_run` 构建 `checkpoint_snapshot`
  - 再用 `describe_checkpoint_continuation_state(...)`
    派生 `continuation_state`

- `pipeline_resumed`
  - payload 现在额外带上：
    - `checkpoint_snapshot`
    - `continuation_state`

这样 pipeline resume event 现在也能回答：

- 这次 resume 是不是带着 continuation state 恢复
- 当时 snapshot 中最近的 turn-local payload 是什么
- protocol tail / prior summaries 分别有多少

测试也同步补上：

- `test_resume_appends_checkpoint_continuation_state`
  - 预先种入：
    - `agent_turn_started`
    - `tool_round_recorded`
    - `approval_queue_item_followup_triggered`
  - 调 `engine.resume(...)`
  - 断言 `pipeline_resumed` 事件中：
    - `checkpoint_snapshot.turn_local_state.assistant_content == "Open the design doc before continuing."`
    - `continuation_state.consumed is True`
    - `continuation_state.protocol_tail_message_count == 2`

这一步的意义是：

- approval replay -> pipeline resume -> stage start
  - 这整条恢复链的每个关键事件
  - 现在都能投影 checkpoint continuation consumption
- pipeline 的执行语义与事件可观测语义又少了一层断点

### 11.34 2026-04-25 新进展：task-run checkpoint snapshot 已直接暴露 derived continuation state

11.33 之后，事件层对 continuation consumption 的投影已经补了不少，但 monitor / frontend 还有一个可用性缺口：

- `checkpoint_snapshot` 本身虽然已经同时带有：
  - `continuation_cursor`
  - `turn_local_state`
- 但如果前端想回答：
  - “当前 snapshot 是否真的消费了 continuation state？”
  - “具体消费了多少 tail message / prior summaries？”
- 还要自己再从 cursor + turn-local state 二次拼装判断逻辑

这会让同一份语义在多处重复实现：

- recovery 事件层会算一次
- pipeline 事件层会算一次
- monitor / frontend 如果也想展示摘要，又要再推一次

这一轮把这层派生结果直接放进 snapshot 主体里：

- `build_task_run_checkpoint_snapshot(...)`
  - 现在在构建完 snapshot 后
  - 会直接附带：
    - `continuation_state`

- 这份 `continuation_state`
  - 继续复用统一的 `describe_checkpoint_continuation_state(...)`
  - 因此它和 recovery / pipeline 事件里的统计口径保持完全一致

这样 monitor / frontend 现在不需要再自行推断：

- snapshot 本身就直接告诉你：
  - `consumed`
  - `next_action`
  - `resume_strategy`
  - `consumed_layers`
  - `protocol_tail_message_count`
  - `prior_round_summary_count`

测试也同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 继续验证 blocked-tool snapshot 时
  - `checkpoint_snapshot.continuation_state` 会直接显示：
    - `consumed is True`
    - `next_action == "await_approval"`
    - `protocol_tail_message_count == 4`
    - `prior_round_summary_count == 1`

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 同时验证 latest-turn 已无 continuation payload 时
  - `checkpoint_snapshot.continuation_state.consumed is False`

Monitor 侧也顺手做了投影：

- Checkpoint Snapshot 卡片新增 `Continuation State`
  - 直接显示：
    - resume strategy
    - tail message 数量
    - prior summary 数量
    - consumed layers
- 同时提供原始 payload 折叠查看

这一步的意义是：

- continuation-state 摘要第一次成为 checkpoint snapshot 的一等公民字段
- monitor / frontend 不再需要重复实现派生逻辑
- snapshot、event、UI 三层的 continuation 语义进一步统一

### 11.35 2026-04-25 新进展：Monitor run 列表与事件流已开始直接展示 continuation-state 摘要

11.34 之后，`checkpoint_snapshot` 本身已经直接带 `continuation_state`，但 Monitor 交互上还留着一个可见性问题：

- 详情页的 Checkpoint Snapshot 卡片里已经能看到 continuation-state
- 但 run 列表本身仍然只显示：
  - event count
  - latest event type
  - client turn id
- event 流也仍然主要依赖用户自己展开 raw payload

这导致一个常见排障动作仍然不够直接：

- 你想快速扫一批 run，找出哪些 run 带着 continuation state
- 或想在事件流里快速看出：
  - 这条 `pipeline_resumed` / `pipeline_stage_started` / `task_run_recovery_started`
    到底消费了什么 continuation payload
- 还是得点开详情或直接读 raw JSON

这一轮把这层 UI 摘要补上：

- Monitor task-run 列表
  - 如果该 run 的 `checkpoint_snapshot.continuation_state.consumed == true`
  - footer 现在会直接显示 continuation-state 摘要
  - 例如：
    - next action
    - resume strategy
    - tail message 数量
    - prior summary 数量
    - consumed layers

- Task-run 事件流
  - 新增通用 continuation-state 摘要提取逻辑
  - 会优先读取：
    - `recovery_continuation_state`
    - `continuation_state`
    - `checkpoint_snapshot.continuation_state`
  - 对这些事件直接显示一行可读摘要

因此现在像这些事件：

- `task_run_recovery_started`
- `scheduler_step_dispatched`（recovered step）
- `pipeline_resumed`
- `pipeline_stage_started`

在 UI 上都不再只是“有个 payload 可以点开”：

- 它们会直接把 continuation-state 摘要暴露在事件行里
- 让 operator 扫读时就能知道这一步到底吃了什么 continuation state

这一步的意义是：

- continuation-state 终于不只在 snapshot/detail/raw payload 里可见
- 它开始进入 run 列表与事件流的主阅读路径
- Monitor 对这类恢复/续跑语义的可读性又提升了一层

### 11.36 2026-04-25 新进展：task-run detail 事件已直接序列化 derived continuation state

11.35 之后，Monitor UI 已经会从 event payload 中提取 continuation-state 摘要，但这层逻辑本质上仍有重复：

- 后端事件 payload 里可能出现三种入口：
  - `recovery_continuation_state`
  - `continuation_state`
  - `checkpoint_snapshot.continuation_state`
- 前端为了展示摘要
  - 还得自己逐类判断这些分支

这意味着同一份语义仍然横跨两层：

- 后端负责产出 continuation-state 相关 payload
- 前端还要再负责“识别哪一种 payload 里藏着 continuation-state”

这一轮把这层派生逻辑继续前移到序列化层：

- `serialize_task_run_detail(...)`
  - 现在每条 event 除了原始 `payload`
  - 还会直接带上：
    - `continuation_state`
    - `continuation_state_summary`

- 提取逻辑统一为：
  - 优先读 `recovery_continuation_state`
  - 其次读 `continuation_state`
  - 再次读 `checkpoint_snapshot.continuation_state`
  - 若只有 `checkpoint_snapshot`，则按统一 helper 现场派生

同时补了一个通用 summary formatter：

- `summarize_continuation_state(...)`
  - 用统一口径输出：
    - next action
    - resume strategy
    - tail message 数量
    - prior summary 数量
    - consumed layers

测试也同步补上：

- `test_startup_recovers_interrupted_orchestration_run`
  - 继续验证 recovery started event 的原始 payload
  - 同时新增断言：
    - `continuation_state.consumed is True`
    - `continuation_state_summary == "resume scheduler · via rebuild_from_runtime_snapshot · 2 tail messages · runtime_snapshot, protocol_tail"`

前端也顺手收口：

- `TaskRunEvent` 类型现在直接声明：
  - `continuation_state`
  - `continuation_state_summary`
- Monitor event 列表优先使用后端直接给出的 summary
  - 只把原有 payload 解析逻辑保留为兜底兼容

这一步的意义是：

- event 级 continuation 语义第一次成为 task-run detail API 的显式字段
- 前端不再需要理解后端不同事件 payload 的分支结构
- continuation-state 的派生逻辑继续从 UI 层回收到序列化层

### 11.37 2026-04-25 新进展：checkpoint snapshot 也已直接序列化 continuation summary

11.36 之后，event 级 continuation 摘要已经直接由后端输出，但 snapshot 级展示还留着最后一层重复：

- `checkpoint_snapshot.continuation_state` 已经存在
- 前端虽然可以直接展示它
- 但 run 列表和 detail 卡片里那段简短摘要
  - 仍然还要自己做一遍字符串拼装

这意味着：

- event 摘要已经是后端产物
- snapshot 摘要却还停留在前端拼接
- 同一类 summary 在 snapshot / event 两侧仍有两套来源

这一轮把 snapshot 侧也收口：

- `build_task_run_checkpoint_snapshot(...)`
  - 在附加 `continuation_state` 之后
  - 继续直接附带：
    - `continuation_state_summary`

- 这份 summary 同样复用统一的：
  - `summarize_continuation_state(...)`

因此现在：

- event 级摘要
  - 来自后端 `continuation_state_summary`
- snapshot 级摘要
  - 也来自后端 `continuation_state_summary`

测试也同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 继续验证 blocked-tool snapshot
  - 新增断言：
    - `continuation_state_summary == "await approval · via replay_tool_then_continue_turn · 4 tail messages · 1 prior summaries · protocol_tail, prior_round_summaries"`

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 新增断言：
    - 无 continuation payload 时
    - `continuation_state_summary is None`

前端也相应收口：

- Monitor run 列表
  - 优先使用 `checkpoint_snapshot.continuation_state_summary`
- Checkpoint Snapshot detail 卡片
  - 也优先使用后端给出的 summary
- 原有本地拼装逻辑继续保留为兼容兜底

这一步的意义是：

- run 列表 / detail 卡片 / event 行
  - 三类 continuation 摘要现在都开始优先依赖后端统一产物
- 前端本地字符串拼装再次减少
- snapshot 与 event 的 continuation summary 来源进一步统一

### 11.38 2026-04-25 新进展：continuation summary 已提升为 task-run summary 顶层字段

11.37 之后，snapshot 与 event 都已经直接带 continuation summary，但 task-run summary 本身还保留着一层嵌套依赖：

- run 列表想读 continuation summary
- 仍然需要先走到：
  - `checkpoint_snapshot.continuation_state_summary`

虽然这已经比前端自己拼字符串好多了，但对 summary 视角来说仍然不够直接：

- task-run summary 本身就是列表接口的主对象
- continuation summary 既然已经是 summary 级语义
- 最自然的位置应该就是 summary 顶层字段

这一轮把它再往上提一层：

- `serialize_task_run_summary(...)`
  - 现在除了 `checkpoint_snapshot`
  - 还会直接附带：
    - `continuation_state`
    - `continuation_state_summary`

这样：

- task-run 列表接口
- task-run detail 接口
- monitor websocket detail payload

都会天然带上同一份顶层 continuation 摘要，而不需要调用方再钻进 checkpoint 子对象里找。

测试也同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 新增断言：
    - `entry["continuation_state"]["consumed"] is True`
    - `entry["continuation_state_summary"] == "await approval · via replay_tool_then_continue_turn · 4 tail messages · 1 prior summaries · protocol_tail, prior_round_summaries"`

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 新增断言：
    - `entry["continuation_state"]["consumed"] is False`
    - `entry["continuation_state_summary"] is None`

前端也顺手收口：

- Monitor run 列表
  - 优先读顶层 `run.continuation_state_summary`
- detail 卡片
  - 也优先读顶层 `selectedTaskRunDetail.continuation_state_summary`
- `checkpoint_snapshot.continuation_state_summary`
  - 仍保留为兼容与下钻视图来源

这一步的意义是：

- continuation summary 终于进入 task-run summary 顶层对象
- run 列表/详情页对嵌套 checkpoint 结构的依赖进一步降低
- summary / snapshot / event 三层对象的职责开始更清晰

### 11.39 2026-04-25 新进展：task-run summary 已直接暴露 latest continuation event 摘要

11.38 之后，task-run summary 已经直接带当前 snapshot 的 continuation 摘要，但仍有一个“当前状态 vs 最近动作”的语义差：

- `continuation_state_summary`
  - 描述的是当前 snapshot 的 continuation 状态
- 它不一定告诉你：
  - 最近一次真正发生的 continuation 相关事件是什么
  - 比如最近一次 recovery started / pipeline resumed / stage started 到底是哪一步

于是当 operator 想快速扫列表时，仍会遇到一个细小断点：

- 当前 snapshot 可能已经被后续 turn 前滚覆盖
- 但你还想知道“最近一次 continuation 动作”到底是什么
- 还是得点开完整事件流往回找

这一轮把这个“最近动作摘要”也提到 summary 顶层：

- `serialize_task_run_summary(...)`
  - 现在会回扫 events
  - 找到最近一条带 `continuation_state_summary` 的事件
  - 直接输出：
    - `latest_continuation_event_type`
    - `latest_continuation_event_summary`
    - `latest_continuation_event_at`

因此 task-run summary 现在同时有两类 continuation 语义：

- 当前 snapshot continuation
  - `continuation_state`
  - `continuation_state_summary`

- 最近一次 continuation 动作
  - `latest_continuation_event_type`
  - `latest_continuation_event_summary`
  - `latest_continuation_event_at`

测试也同步补上：

- `test_startup_recovers_interrupted_orchestration_run`
  - 新增断言：
    - `latest_continuation_event_type == "task_run_recovery_completed"`
    - `latest_continuation_event_summary == "resume scheduler · via rebuild_from_runtime_snapshot · 2 tail messages · runtime_snapshot, protocol_tail"`

前端也顺手接上：

- Monitor run 列表
  - 直接显示 `latest_continuation_event_summary`
- detail 卡片
  - 新增 `Latest Continuation Event`
  - 直接展示 event type + summary + 时间

这一步的意义是：

- task-run summary 不再只回答“当前 continuation 状态是什么”
- 也开始回答“最近一次 continuation 动作是什么”
- current state 与 recent action 两条线索在 summary 层开始并列可见

### 11.40 2026-04-27 新进展：task-run summary 已直接暴露 scheduler runtime 摘要

继续沿着 “summary 层先回答 operator 最常问的问题” 这个方向，当前 monitor 还有一个明显断点：

- `checkpoint_snapshot.latest_scheduler_runtime`
  - 已经有原始 runtime snapshot
- 但 run 列表和 run 详情如果只看 summary 顶层
  - 仍然不知道最近 scheduler 运行态到底是什么
  - 还得下钻到嵌套 checkpoint 或 raw payload

这会让编排态势判断停在半路：

- 你知道 run 现在能不能继续
- 但不知道 scheduler 当前到底积压了多少 ready / running / waiting step

这一轮把 scheduler runtime 也提到 task-run summary 顶层：

- `serialize_task_run_summary(...)`
  - 直接输出：
    - `latest_scheduler_runtime`
    - `scheduler_runtime_summary`
- `scheduler_runtime_summary`
  - 由后端统一格式化
  - 当前输出：
    - `completed`
    - `ready`
    - `running`
    - `waiting`
    - 如有 `step_count` 再补 `total`

测试同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 追加一条带 `runtime` payload 的 ledger event
  - 断言 task-run summary 会直接返回：
    - `latest_scheduler_runtime.completed_step_count == 1`
    - `latest_scheduler_runtime.ready_step_count == 2`
    - `scheduler_runtime_summary == "1 completed · 2 ready · 1 running · 0 waiting · 4 total"`

前端也顺手收口：

- Monitor run 列表
  - 直接显示 `run.scheduler_runtime_summary`
- Selected Run Detail
  - 新增 summary 级 `Scheduler Runtime`
  - 优先读顶层 summary 字段，缺失时再退回 checkpoint snapshot

这一步的意义是：

- scheduler 当前运行态开始成为 task-run summary 的一等字段
- operator 不必先点开 checkpoint raw payload 才能看出排队态势
- summary / checkpoint / raw event 三层的职责继续分离：summary 用来扫态势，checkpoint 用来恢复，raw event 用来追溯

### 11.41 2026-04-27 新进展：continuation cursor 摘要已提升到 task-run summary / snapshot

11.40 之后，task-run summary 顶层已经能直接回答 scheduler 当前运行态，但 monitor 上还有一块恢复语义仍然留在前端本地拼装：

- `checkpoint_snapshot.continuation_cursor`
  - 已经有结构化字段
- 但 run 列表和 detail 卡片想快速回答：
  - “这个 run 下一步到底准备怎么续？”
  - “是等 approval、继续 turn，还是 resume scheduler？”
- 仍然需要前端自己从：
  - `next_action`
  - `resume_strategy`
  - `tool_name`
  - `turn`
  - runtime counts
  再拼一遍短摘要

这跟前面已经收口的 continuation state / latest continuation event / scheduler runtime 一样，仍然会造成 summary 语义来源分叉：

- snapshot 有原始字段
- UI 再自行拼装字符串
- 不同列表 / 卡片很容易继续出现不同口径

这一轮把 cursor 摘要也正式提升出来：

- `build_task_run_checkpoint_snapshot(...)`
  - 现在直接附带：
    - `continuation_cursor_summary`
- `serialize_task_run_summary(...)`
  - 也把它提升到顶层：
    - `continuation_cursor`
    - `continuation_cursor_summary`

摘要规则保持轻量，只回答 operator 最关心的恢复入口：

- `await approval`
- `continue agent turn`
- `resume scheduler`
- 再按需补：
  - `via ...`
  - `tool ...`
  - `turn ...`
  - `ready / running / waiting` 计数

测试同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 新增断言：
    - `entry["continuation_cursor_summary"] == "await approval · via replay_tool_then_continue_turn · tool delete_file · turn 3"`
    - `entry["checkpoint_snapshot"]["continuation_cursor_summary"] == "await approval · via replay_tool_then_continue_turn · tool delete_file · turn 3"`
- 无 continuation cursor 的 summary / snapshot
  - 断言 `continuation_cursor_summary is None`

前端也继续收口：

- Monitor run 列表
  - 直接显示顶层 `run.continuation_cursor_summary`
- Selected Run Detail
  - 新增 summary 级 `Continuation Cursor`
  - 优先读顶层 summary，再回退到 snapshot
- Checkpoint Snapshot detail 卡片
  - 也优先消费后端给出的 `continuation_cursor_summary`

这一步的意义是：

- “下一步怎么续” 开始成为 summary 层的一等语义
- cursor 的短摘要不再散落在前端本地拼装逻辑里
- summary / snapshot / raw payload 的职责继续向 Codex 式编排靠拢：summary 先回答动作入口，snapshot 保留恢复所需结构，raw payload 保留追溯细节

### 11.42 2026-04-27 新进展：orchestration startup policy 已在 sync / stream / recovery 间共享

前面几轮主要都在收 observability 和 summary 层，但真正往 `P0` 靠的时候，一个很具体的 runner 分叉点开始明显：

- sync multi-agent orchestration
- stream multi-agent orchestration
- recovery rebuild

三条路径虽然最终都会：

- resolve targets
- build schedule
- compile orchestration runner policy

但这些动作之前还是各做各的，而且 startup 时刻的 policy projection 也不一致：

- `scheduler_plan_created`
  - 已经会带完整 `runner_policy`
- 但 `runtime_mode_selected`
  - 对 multi-agent orchestration 还只带：
    - `agents`
    - `project_id`
  - 还没有把编排 policy 提前暴露出来

这意味着 run startup 还没有真正成为统一 envelope：

- mode selection 只告诉你“选了 orchestration”
- 却没有同时告诉你“这次 orchestration 准备按什么 runner policy 跑”
- sync / stream / recovery 也仍在各自重建同一套 startup 准备逻辑

这一轮先做一个最小的 `P0.1` 收口：

- `backend/routes/api.py`
  - 新增共享的 orchestration startup 准备逻辑
  - 统一负责：
    - target resolution
    - schedule build
    - orchestration runner policy compile
- sync orchestration
  - 改为消费这份共享 prepared runtime
- stream orchestration
  - 改为消费同一份共享 prepared runtime
- recovery rebuild
  - 也开始复用同一份 orchestration startup 准备逻辑

同时把 policy projection 前移到 startup 事件：

- `runtime_mode_selected`
  - 对 multi-agent orchestration / streaming orchestration
  - 现在也直接输出：
    - `runner_policy`

这样当前 run 在一开始就能回答：

- 这次是不是 orchestration
- 是 linear blocking 还是 blocking-chain-with-sidecars
- 一共多少 step
- sidecar 数量多少
- tool policy pack 是什么

测试同步补上：

- sync multi-agent orchestration
  - 断言 `runtime_mode_selected.payload.runner_policy`
  - 与后续 `scheduler_plan_created.payload.runner_policy`
  - 在 `mode` / `stage_count` / `sidecar_step_count` 上保持一致
- stream multi-agent orchestration
  - 同样补上对应断言

这一步的意义是：

- `runtime_mode_selected` 不再只是“模式标签事件”
- 它开始变成真正的 runner startup envelope 投影
- orchestration 的 startup 准备逻辑第一次在 sync / stream / recovery 三条主链上开始共享
- 这还不是完整统一 runner，但已经是一个真实的 `P0` 内核收口点，而不再只是外围 summary 整理

### 11.43 2026-04-27 新进展：runtime mode selection envelope 已在 sync / stream 入口间共享

11.42 把 orchestration 的 startup 准备逻辑先收了一层，但继续往 `P0` 看，很快就会发现另一块明显重复：

- sync chat entry
- stream chat entry

两边在 mode selection 时都要重复做同样三件事：

- 更新 `task_run.run_kind`
- 视情况更新 `target_agent_name`
- 追加 `runtime_mode_selected` 事件
  - 写入 `project_id`
  - 写入 `runner_policy`
  - 某些模式再补 `agents` / `target_agent_name`

这类重复表面上只是几段 append/update，但实质上它正是 runner startup envelope 的最外层。

如果这一层继续散落在各分支里，后面会持续出现两个问题：

- sync / stream mode-selected 事件口径容易再分叉
- 新增 approval / policy / ownership 投影时，仍要在多处重复接线

这一轮继续做一个小收口：

- `backend/routes/api.py`
  - 提取统一的 mode-selection 写入辅助逻辑
  - 统一负责：
    - `run_kind` 更新
    - `target_agent_name` 更新
    - `runtime_mode_selected` payload 组装

覆盖的入口包括：

- standalone single-agent sync
- standalone single-agent stream
- project single-agent sync
- project single-agent stream
- standalone multi-agent orchestration sync / stream
- project multi-agent orchestration sync / stream

因此 `runtime_mode_selected` 现在开始更接近真正的 startup envelope：

- sync / stream 不再各自手拼一份 mode-selection event
- runner policy projection 也不再散落在八个分支里重复写入

这一步的意义是：

- run startup 的最外层信封开始从“分支代码”抽成“共享语义”
- 统一 runner 外壳虽然还没完全成形，但 mode selection 这一层已经开始具有单一入口味道
- 后续如果要把 approval / sandbox / ownership 等 runtime policy 再继续前移到 startup 阶段，挂载点会更稳定

### 11.44 2026-04-27 新进展：project target-agent resolution 已在 sync / stream 入口间共享

继续沿着 `P0.1` 收 startup envelope，除了 mode selection 之外，还有另一段很容易反复分叉的逻辑：

- project single-agent sync
- project single-agent stream

两条路径都要自己处理：

- `@mention` 指定的 agent 是否已经在项目里
- 如果不在项目里，是否从全局自动分配进项目
- 如果仍然找不到，是否退回默认 agent 或项目第一个 agent

这段逻辑虽然不长，但它本质上决定了 runner startup 的“执行主体是谁”。

如果 sync / stream 各自维护一份，很容易在后续继续出现细小漂移：

- auto-assign 时机不一致
- fallback 顺序不一致
- 后续如果要叠加 ownership / approval / target capability 检查
  - 也会被迫重复接两遍

这一轮把这层也收成共享辅助逻辑：

- `backend/routes/api.py`
  - 新增统一的 project target-agent resolution helper
  - 收口：
    - project-local lookup
    - global auto-assign into project
    - default fallback

这样 project single-agent 的 sync / stream 入口现在开始共享：

- 谁是本轮真正 target agent
- 什么时候把全局 agent 自动挂进项目
- 如果没有显式 mention，如何回落到默认 agent

这一步的意义是：

- runner startup 不再只共享“选了什么模式”
- 也开始共享“选中了谁来执行”
- 统一 runner 外壳又往前收了一层：mode selection 之外，target resolution 也开始脱离分支代码

### 11.45 2026-04-27 新进展：chat turn runtime preparation 已在多条执行路径间共享

`P0.2` 往 turn execution envelope 继续拆时，最容易看到的一块重复不是 LLM loop 本身，而是“进入 LLM loop 之前”的那层准备动作：

- 拿 `llm_client`
- 取 recent messages
- 从 checkpoint 构建 `turn_state`
- 注入 previous work / inter-agent messages
- 取 tool schemas
- 取 tool runtime kwargs

之前这些动作散落在至少四条路径里：

- project single-agent sync
- project single-agent stream
- orchestrated sync turn
- orchestrated stream turn

每条路径都大同小异，但分别自己准备一遍。  
这会带来两个问题：

- turn execution envelope 还停留在“每个入口自己攒材料”
- 后续如果要补统一的 budget / approval / ownership / checkpoint policy
  - 仍会被迫在多处重复接线

这一轮先把这层公共准备抽出来：

- `backend/routes/api.py`
  - 新增共享的 chat turn runtime preparation helper
  - 统一负责：
    - `llm_client`
    - `recent_messages`
    - `turn_state`
    - `tool_schemas`
    - `runtime_kwargs`

目前已接入：

- project single-agent sync
- project single-agent stream
- orchestrated sync turn
- orchestrated stream turn

这一步还没有把所有 turn loop 合并成一个函数，也没有覆盖 pipeline stage engine；但它的价值很明确：

- turn execution 不再只是共享底层 executor
- 连“进入 executor 之前的 runtime 准备层”也开始共享
- 这说明 P0 已经从 startup envelope 继续推进到 turn envelope，而不只是 mode / policy 的表层收口

### 11.46 2026-04-27 新进展：standalone turn runtime preparation 已在 sync / stream 间共享

11.45 把 chat turn runtime preparation 收到了 project single-agent 与 orchestration 路径，但还留着一条最“轻”的分支没并回来：

- standalone assistant sync
- standalone assistant stream

这两条路径虽然不走工具，也没有 project agent 选择，但依然重复了同一类 turn-runtime 准备动作：

- standalone target resolution
- default client fallback
- recent messages
- checkpoint turn state

如果这条分支继续单独维护，后面会出现一个典型问题：

- 统一 runner 收口时，最简单的 standalone 分支反而继续保留自己的准备语义
- 导致 “有工具 / 无工具、project / standalone” 之间的 envelope 继续分层

这一轮把 standalone 也拉回共享准备逻辑：

- `backend/routes/api.py`
  - 新增 standalone turn runtime preparation helper
  - 统一负责：
    - standalone target resolution
    - default client fallback
    - recent messages
    - checkpoint turn state

目前已接入：

- standalone sync
- standalone stream

这一步的意义是：

- turn runtime preparation 的共享范围不再只覆盖“带 project / 带工具”的路径
- 连最轻量的 standalone 分支也开始挂回统一 runtime 语义
- P0.2 的 turn envelope 收口因此更完整了一层：单 Agent project / orchestration / standalone 三类 chat 路径都开始脱离各自手工准备

### 11.47 2026-04-27 新进展：context compaction callback 语义已在 chat 与 pipeline 间共享

继续顺着 turn envelope 往下看，除了 runtime preparation 本身，还有一块很像“边角料”，但实际上很容易让运行时语义再次分叉：

- chat runtime 的 `context_compaction` 事件回调
- pipeline stage runtime 的 `context_compaction` 事件回调

之前两边各自维护了几乎同构的一段逻辑：

- 只在 `compacted=True` 时触发
- 根据 diagnostics 做去重
- 组装 payload
- 生成 summary 文案
- 再落到各自的 event sink

这类代码如果继续平行演化，问题不在“今天会不会坏”，而在：

- future 增加新的 compaction metadata 时
- chat / pipeline 很容易再次出现 payload 或 summary 口径漂移

这一轮把 compaction callback 的共享语义抽出来：

- `backend/services/runtime_event_helpers.py`
  - 新增统一的 context compaction callback helper
  - 收口：
    - 去重签名
    - `compacted` 判定
    - payload 基础字段
    - summary 文案生成

目前已接入：

- chat runtime callback
- pipeline stage runtime callback

两边现在只保留各自的“事件落点适配”：

- chat 走 `append_task_event(...)`
- pipeline 走 `_append_pipeline_task_event(...)`

并补了一层 helper 单测，确保：

- 相同 diagnostics 不会重复发 event
- `compacted=False` 或空 diagnostics 不会误发 event

这一步的意义是：

- turn envelope 不只是开始共享“怎么准备上下文”
- 也开始共享“上下文被压缩时怎么投影成运行时事件”
- 这让 compaction 从 UI 观测项，进一步变成统一 runner 语义的一部分

### 11.48 2026-04-27 新进展：runtime lifecycle event payload building 已开始共享

继续检查 `agent_turn_started` / `agent_turn_completed` 这一层时，当前重复点不在事件落库函数本身：

- `runner_lifecycle.py` 已经统一了事件类型和写入

真正分散的是各调用方手工拼基础 payload：

- `client_turn_id`
- `stage_policy`
- `target_agent_name`
- pipeline id / run id / stage name / display name

这些字段看似只是元数据，但它们是 runner envelope 的重要部分：

- operator 依赖这些字段知道一个 turn 属于哪个入口
- recovery / monitor 依赖这些字段把 turn 与 stage / policy 关联起来
- 后续如果要加入 ownership / approval / sandbox policy，也会继续挂在这里

这一轮把这层基础 payload 组装抽到共享 helper：

- `backend/services/runtime_event_helpers.py`
  - 新增 runtime lifecycle payload helper
  - 统一负责：
    - optional 字段过滤
    - `client_turn_id`
    - `stage_policy.to_payload()`
    - extra metadata merge

目前已接入：

- standalone sync / stream turn start
- project single-agent sync / stream turn start
- orchestrated sync / stream turn start
- pipeline stage turn start / completed

这一步的意义是：

- lifecycle event 不再只共享“写入函数”
- 也开始共享“基础 payload shape”
- P0.2 的 turn envelope 继续往下收：从 context preparation、compaction projection，推进到 lifecycle metadata projection

### 11.49 2026-04-27 新进展：approval replay follow-up resolution shape 已共享

P0.3 开始从 approved blocked-tool replay / follow-up 主链往统一 runner envelope 收口。

这轮先处理最小但高频的分叉点：replay 完成后是否继续执行，以及继续执行结果如何写回 approval resolution payload。

之前 runtime follow-up 与 pipeline follow-up 各自手工拼：

- `followup_attempted=false / followup_status=skipped / followup_reason=...`
- `followup_attempted=true / followup_status=failed / followup_error=...`
- `followup_attempted=true / followup_status=continued / followup_reason=...`
- replay 是否 actionable 的判断：成功且没有再次 blocked

这类 payload 是 monitor、approval queue、operator 审计共同消费的接口。它不应继续由 runtime 与 pipeline 分支各自维护。

本轮新增：

- `backend/services/approval_replay.py`
  - `replay_result_is_actionable(...)`
  - `build_followup_skipped_payload(...)`
  - `build_followup_failed_payload(...)`
  - `build_followup_continued_payload(...)`

并接入：

- chat runtime approved replay follow-up
- pipeline approved replay follow-up

这一步的意义是：

- approval replay 的 follow-up resolution shape 开始统一
- runtime 与 pipeline 仍保留不同的实际恢复动作，但不再各自定义“恢复结果怎么表达”
- 后续可以继续把 approval/sandbox/escalation 主链推进到更完整的 shared runner continuation policy

### 11.50 2026-04-27 新进展：approval replay resolution 与 replay round payload 已共享

继续推进 P0.3 时，approved blocked-tool replay 主链还有第二层重复：

- approval item resolve 时写入的 replay resolution payload
- replay 后写入 `tool_round_recorded` 的 replay round payload

这两类 payload 是同一个恢复动作的两面：

- resolution payload 给 approval queue / monitor / operator 看
- replay round payload 给 task ledger / recovery / continuation cursor 看

之前它们还留在 API route 内部手工拼装。这样会让后续 runtime / pipeline / sandbox approval 扩展时继续把 payload shape 分散到入口层。

本轮把这层语义也并入 `backend/services/approval_replay.py`：

- `build_queue_replay_resolution_payload(...)`
- `build_approval_queue_replay_round_payload(...)`

现在 API approve 路径只负责：

- 找到 queue item
- 执行 replay
- 触发 runtime 或 pipeline follow-up
- resolve approval item

而 replay resolution / replay round 的基础 shape 由共享 service 决定。

这一步的意义是：

- P0.3 的 approval replay envelope 从“后续执行结果”继续扩大到“replay 审计与 ledger 记录”
- API route 的 orchestration 职责更清晰，不再顺手定义 runtime payload 协议
- 后续如果引入 sandbox/escalation replay，只需要复用同一组 replay payload helpers

### 11.51 2026-04-27 新进展：blocked-tool approval queue request 语义已共享

P0.3 继续从 replay 后半段往前推进到 blocked-tool queue item 创建阶段。

之前 `runner_lifecycle.record_tool_round(...)` 在发现 blocked tool 后，直接在函数内部决定：

- queue kind：approval 还是 escalation
- title 文案：approval / escalation 的 operator 标题
- resume_supported 判定
- request_key 去重签名
- request_payload 的基础 shape 与 pipeline cursor 字段

这些字段共同决定一个 blocked tool 能否被 operator 批准、是否支持 replay、后续 replay 如何找到 pipeline/stage cursor。它们不是普通 route/ledger 细节，而是 approval runtime 的协议面。

本轮把这层语义并入 `backend/services/approval_replay.py`：

- `blocked_tool_queue_kind(...)`
- `blocked_tool_queue_title(...)`
- `blocked_tool_resume_supported(...)`
- `build_blocked_tool_request_key(...)`
- `build_blocked_tool_request_payload(...)`

现在 `runner_lifecycle.record_tool_round(...)` 只负责在 tool round 中发现 blocked tool 并创建 queue item；queue item 的 request 协议由共享 helper 生成。

这一步的意义是：

- approval / escalation queue request 的入口语义开始从 ledger 写入函数中拆出
- runtime 与 pipeline 的 blocked-tool cursor 字段保持同一套 request payload shape
- 后续引入 sandbox/escalation 更完整的 continuation policy 时，可以复用同一个 request envelope，而不是继续在 lifecycle 里加分支

### 11.52 2026-04-27 新进展：pipeline gate approval request/resolution 语义已共享

P0.3 继续把 approval 主链中另一类 pending item 收进共享语义：pipeline manual gate。

pipeline gate 与 blocked-tool replay 不同：它不需要重放 tool call，但同样会进入 approval queue，并且同样需要稳定的 request key、request payload、resolution payload：

- request key 用于避免重复 gate approval item
- request payload 携带 pipeline/run/stage cursor 与 stage policy
- resolution payload 记录 gate 被批准/拒绝时的 stage cursor

之前这部分仍在 `pipeline/engine.py` 内部手工定义，导致 approval queue 协议散落在 pipeline engine 与 runtime lifecycle 两侧。

本轮新增共享 helper：

- `build_pipeline_gate_request_key(...)`
- `build_pipeline_gate_request_payload(...)`
- `build_pipeline_gate_resolution_payload(...)`

并接入：

- `_queue_pipeline_gate_approval(...)`
- `_resolve_pipeline_gate_queue_item(...)`

这一步的意义是：

- approval queue 不再只有 blocked-tool replay 语义被共享，manual gate 也开始进入同一组 approval runtime helpers
- pipeline engine 保留 gate 状态推进职责，request/resolution payload shape 由共享 service 决定
- 后续把 approval queue 进一步抽成 runner continuation node 时，blocked tool 与 pipeline gate 已经有可复用的 request envelope 基础

### 11.53 2026-04-27 新进展：approval queue created/resolved event payload 已共享

P0.3 继续把 approval 主链的事件投影层收口。

在 blocked-tool、pipeline gate 的 request / replay payload 逐步共享后，仍有一层重复留在调用方：

- `approval_queue_item_created` event payload
- `approval_queue_item_resolved` event payload
- reject queue item 时的 resolution payload

这些事件是 runtime ledger、monitor、recovery cursor 共同消费的审计面。如果 created/resolved payload 继续由 route、pipeline engine、runner lifecycle 分别拼装，后续 approval/sandbox/escalation 字段仍会很容易漂移。

本轮新增共享 helper：

- `build_approval_queue_item_created_event_payload(...)`
- `build_approval_queue_item_resolved_event_payload(...)`
- `build_queue_rejection_resolution_payload(...)`

并接入：

- runtime blocked-tool queue item created event
- pipeline gate queue item created event
- runtime approve/reject resolved event
- pipeline approve/reject resolved event

这一步的意义是：

- approval queue 的 request、replay、follow-up、created/resolved event 四个面都开始有共享 payload helper
- route / engine / lifecycle 不再各自定义 approval ledger payload shape
- 这为下一步把 approval queue 抽象成更完整的 runner continuation node 减少了接口漂移风险

### 11.54 2026-04-27 新进展：approved tool replay follow-up context 已共享

P0.3 继续收口 approved blocked-tool replay 后的 continuation prompt。

之前 replay 成功后，runtime follow-up 与 pipeline follow-up 都依赖 API route 内的 `_build_tool_replay_followup_context(...)` 拼出一段额外上下文：

- replay 的 tool name
- replay status
- replay result preview
- 明确要求继续执行，不要无条件重跑同一个 tool call

这段文本虽然看起来只是 prompt，但实际上是 approved replay continuation 的核心协议：它决定 agent/pipeline 恢复时如何理解已经完成的工具结果。

本轮把它并入共享 approval runtime helper：

- `build_tool_replay_followup_context(...)`

并让：

- chat runtime approved replay follow-up
- pipeline approved replay follow-up

都复用同一段 context 构造逻辑。

这一步的意义是：

- approved replay 后的 continuation context 不再藏在 API route 内
- runtime 与 pipeline 恢复时收到同一套“已 replay tool result，继续但不要重复执行”的指令语义
- approval replay 主链现在从 request、resolution、ledger event 到 continuation prompt 都更接近统一 runner envelope
