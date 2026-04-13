# ADR-013: 面向个人 APP 开发者的业务架构与项目状态流

**日期**: 2026-04-13
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/PRD.md`, `docs/ADR-011-chatroom-full-event-cards.md`, `docs/ADR-007-ui-ux-skill.md`

---

## 1. 背景

Catown 当前已经具备多 Agent、Pipeline、聊天室、审计可视化等基础能力，但讨论过程中逐渐暴露一个核心问题：

**系统的技术骨架已经成形，但业务骨架仍偏“多 Agent 聊天/流水线展示”，还没有完全收敛到一个明确的产品经营模型。**

本次讨论的目标，是先把 Catown 的业务架构钉死，再回头评估现有实现与目标架构的差距。

### 1.1 目标客户

Catown 的 P0/P1 目标客户定义为：

- 个人 APP 开发者
- 单兵或极小团队（1-3 人）产品建设者
- 具备一定技术能力，但产品、设计、测试、发布链路不完整的人
- 想并行推进多个小型 APP/Web 产品的人

### 1.2 产品目标

Catown 的目标不是“帮用户写代码”，而是：

**让个人开发者像拥有一支 AI 产品团队一样，把一个 APP 从想法推进到上线，并继续迭代。**

因此 Catown 的核心承诺是：

- 自动完成从设计到上线的全流程推进
- 尽量减少上下文切换和非核心劳动
- 只在关键、高风险节点请求用户确认
- 把整个项目过程沉淀为可持续运营的项目资产

### 1.3 核心判断

Catown 应该被定义为：

- AI 软件工厂
- AI 产品工作室
- 个人开发者的“AI 软件公司替身”

而不是：

- 通用聊天机器人
- 单点代码生成器
- 只做 Agent 展示的多智能体控制台

---

## 2. 决策

### 2.1 产品定位决策

**Catown 面向个人 APP 开发者，定位为“AI 软件工厂 / AI 产品工作室”，负责从 Idea 到 Launch 的完整项目推进。**

用户真正购买的价值不是：

- 单次回答
- 单次代码生成
- Agent 表演感

而是：

- 项目推进速度
- 端到端交付闭环
- 产物沉淀
- 人机协同下的可控自动化

### 2.2 业务主链路决策

Catown 的 P0 主链路定义为：

1. `Idea Input` - 输入原始想法、参考产品、目标平台
2. `MVP Definition` - 系统澄清需求并压缩出第一版范围
3. `Design + Blueprint` - 生成 PRD、UX Blueprint、Tech Spec
4. `Automated Production` - 多 Agent 协作生成代码、测试、构建结果
5. `Release Preparation` - 产出发布材料、检查表、合规说明
6. `Launch + Iteration` - 上线后回收反馈，生成下一版本 Brief

### 2.3 最小人工 Gate 决策

为了满足“默认自动推进”的产品体验，P0 仅保留最少人工确认点：

- `Gate 1: MVP Scope Confirmation`
  - 用户确认第一版做什么、不做什么
- `Gate 2: Release Approval`
  - 用户确认是否允许进入正式上线/发布
- `Optional Half-Gate: Direction Confirmation`
  - 仅当设计/产品方向明显偏差时触发

原则：

- 普通执行步骤默认自动推进
- 高风险、难逆转节点才暂停等待用户决策
- 不让用户在每个小步骤上反复点击确认

---

## 3. 产品结构决策

### 3.1 P0 核心模块

Catown 的 P0 结构收敛为以下模块：

#### 3.1.1 Dashboard / Home

作用：

- 展示项目队列
- 聚合待确认事项
- 呈现项目推进概览
- 提供“继续推进项目”的入口

首页不应以消息流为中心，而应以“项目推进状态”作为主视角。

#### 3.1.2 Project / Mission Board

这是 Catown 的核心页面，应承担：

- 项目当前阶段概览
- 当前重点任务
- 关键产物展示
- 待决策事项
- Agent 活动流（辅助信息）

Mission Board 的核心问题是：

**“这个项目现在在哪里、为什么卡住、下一步该做什么？”**

#### 3.1.3 Artifact Hub

作用：

- 管理项目正式资产
- 支持版本切换和资产追踪
- 展示资产依赖关系

注意：在 P0 中，Artifact Hub 更适合作为项目页中的一个重要区域，而不应喧宾夺主成为主入口。

#### 3.1.4 Release Center

作用：

- 构建结果查看
- 发布材料汇总
- 上线前 Checklist
- 发布最终确认

这是个人开发者价值极高的一页，因为很多项目死在发布前最后一公里。

#### 3.1.5 Iteration Loop

作用：

- 汇总上线后的反馈与问题
- 自动生成下一轮版本建议
- 形成持续推进闭环

---

## 4. 资产优先于消息：核心业务对象决策

### 4.1 核心原则

**Catown 的主业务对象不应是 message / chatroom / agent log，而应是项目资产（Project Assets）。**

消息和 Agent 日志是执行层与审计层；
资产才是产品交付层的骨架。

### 4.2 P0 核心资产类型

Catown P0 先固定 8 类核心资产：

1. `Project Brief`
2. `PRD`
3. `UX Blueprint`
4. `Tech Spec`
5. `Task Plan`
6. `Build Artifact`
7. `Test Report`
8. `Release Pack`

### 4.3 核心资产字段（摘要）

#### 4.3.1 Project Brief

字段建议：

- `project_name`
- `one_line_vision`
- `target_users`
- `core_use_case`
- `target_platforms`
- `mvp_scope`
- `out_of_scope`
- `monetization_hypothesis`
- `success_metrics`
- `references`
- `status`

作用：让系统与用户对第一版项目边界达成共识。

#### 4.3.2 PRD

字段建议：

- `problem_statement`
- `user_personas`
- `key_scenarios`
- `feature_list`
- `feature_priority`
- `user_flow_summary`
- `acceptance_criteria`
- `non_goals`
- `risks`
- `open_questions`
- `version`

#### 4.3.3 UX Blueprint

字段建议：

- `information_architecture`
- `page_map`
- `primary_navigation`
- `screen_specs`
- `interaction_notes`
- `tone_and_copy_guidelines`
- `visual_direction`
- `design_references`
- `ux_risks`

#### 4.3.4 Tech Spec

字段建议：

- `tech_stack`
- `system_architecture`
- `module_breakdown`
- `data_model`
- `api_design`
- `state_management_strategy`
- `third_party_services`
- `security_and_privacy_notes`
- `deployment_strategy`
- `known_constraints`

#### 4.3.5 Task Plan

字段建议：

- `milestones`
- `tasks`
- `task_dependencies`
- `owner_agent`
- `estimated_effort`
- `status`
- `blocking_issues`
- `current_focus`

#### 4.3.6 Build Artifact

字段建议：

- `platform`
- `build_version`
- `commit_sha`
- `build_status`
- `artifact_url_or_path`
- `generated_at`
- `environment`
- `notes`

#### 4.3.7 Test Report

字段建议：

- `test_scope`
- `test_results_summary`
- `failed_cases`
- `critical_bugs`
- `regression_status`
- `release_recommendation`
- `generated_at`

#### 4.3.8 Release Pack

字段建议：

- `release_version`
- `release_notes`
- `store_description`
- `subtitle_and_keywords`
- `screenshots_status`
- `icon_status`
- `privacy_policy_status`
- `permissions_notes`
- `submission_checklist`
- `release_decision`

### 4.4 统一资产元数据

所有资产建议共享以下元数据：

- `id`
- `project_id`
- `asset_type`
- `version`
- `status`
- `owner_agent`
- `source_inputs`
- `last_updated_at`
- `approved_by_user`
- `supersedes_asset_id`

### 4.5 统一资产状态机

P0 先统一为：

- `draft`
- `in_review`
- `approved`
- `superseded`
- `blocked`

### 4.6 资产依赖链

P0 固定资产生产链如下：

- `Project Brief` -> `PRD`
- `PRD` -> `UX Blueprint`
- `PRD + UX Blueprint` -> `Tech Spec`
- `Tech Spec` -> `Task Plan`
- `Task Plan` -> `Build Artifact`
- `Build Artifact` -> `Test Report`
- `Test Report + Product Metadata` -> `Release Pack`

原则：

**每个阶段输出，必须成为下一阶段的正式输入。**

---

## 5. 阶段接力模型决策

Catown 的执行逻辑不应是“多个 Agent 自由发挥”，而应是**标准化接力模型**。

每一阶段都必须明确：

- 输入是什么
- 谁负责处理
- 输出是什么
- 如何验证
- 是否需要人工 Gate

### 5.1 Stage 0: Briefing

- 输入：原始想法、用户补充信息
- 负责者：Founder/PM Agent
- 输出：`Project Brief`
- 验证：用户确认
- Gate：是

### 5.2 Stage 1: Product Definition

- 输入：`Project Brief`
- 负责者：PM Agent + UX Agent
- 输出：`PRD`、`User Flow`、`Page List`
- 验证：结构完整、范围合理
- Gate：通常否

### 5.3 Stage 2: Solution Design

- 输入：PRD、User Flow、Page List
- 负责者：Architect Agent
- 输出：`Tech Spec`、`Architecture`、`Task Breakdown`
- 验证：可实现、可交付
- Gate：否

### 5.4 Stage 3: Build Execution

- 输入：Task Breakdown、Tech Spec
- 负责者：Developer Agent
- 输出：`Code`、`Tests`、`Build Artifacts`
- 验证：编译、运行、基本测试通过
- Gate：否

### 5.5 Stage 4: QA Validation

- 输入：代码、构建结果、验收标准
- 负责者：QA Agent
- 输出：`Test Report`、`Bug List`、`Release Recommendation`
- 验证：关键路径通过
- Gate：否

### 5.6 Stage 5: Release Preparation

- 输入：测试通过版本、产品信息
- 负责者：Release Agent
- 输出：`Store Copy`、`Screenshots Checklist`、`Privacy Docs`、`Release Notes`
- 验证：发布材料齐全
- Gate：是

### 5.7 Stage 6: Post-Launch Loop

- 输入：用户反馈、问题列表、使用数据
- 负责者：PM Agent + Assistant Agent
- 输出：`Next Version Brief`
- 验证：是否形成下一轮计划
- Gate：否

---

## 6. 推进模式决策：Autopilot vs Checkpoint

### 6.1 决策

Catown 需要支持两种推进模式：

- `Autopilot`
- `Checkpoint`

这不是单纯的 UI 偏好，而是影响阶段转移规则的核心业务配置。

### 6.2 Autopilot

规则：

- 阶段完成后自动进入下一阶段
- 只有遇到 `Decision` 或 `failed` 时才暂停
- 适合熟悉系统、项目风险较低、重复性较强的场景

### 6.3 Checkpoint

规则：

- 每个阶段完成后先暂停
- 系统给出阶段摘要，再由用户决定是否继续
- 适合首次使用、高不确定性项目或高商业风险场景

### 6.4 状态转移差异

- `Autopilot`: `completed -> next stage queued`
- `Checkpoint`: `completed -> waiting_for_decision`

---

## 7. 数据对象与状态机决策

### 7.1 P0 核心业务对象

建议 Catown P0 先明确为 6 个主对象：

- `Project`
- `Asset`
- `StageRun`
- `Task`
- `Decision`
- `AgentRun`

### 7.2 Project

作用：项目容器与全局状态。

建议字段：

- `id`
- `name`
- `owner_id`
- `one_line_vision`
- `target_platforms`
- `current_stage`
- `execution_mode`
- `health_status`
- `created_at`
- `updated_at`

### 7.3 Asset

作用：所有正式产物的统一抽象。

建议字段：

- `id`
- `project_id`
- `asset_type`
- `title`
- `content_json`
- `version`
- `status`
- `owner_agent`
- `supersedes_asset_id`
- `approved_by_user`
- `created_at`
- `updated_at`

### 7.4 StageRun

作用：某一阶段的一次推进实例。

建议字段：

- `id`
- `project_id`
- `stage_type`
- `status`
- `started_at`
- `ended_at`
- `triggered_by`
- `summary`

### 7.5 Task

作用：执行层任务单元。

建议字段：

- `id`
- `project_id`
- `stage_run_id`
- `title`
- `description`
- `status`
- `priority`
- `owner_agent`
- `depends_on_task_ids`
- `related_asset_ids`
- `blocking_reason`

### 7.6 Decision

作用：承载人工 Gate。

建议字段：

- `id`
- `project_id`
- `decision_type`
- `title`
- `context_summary`
- `recommended_option`
- `alternative_options`
- `status`
- `related_asset_ids`
- `created_at`
- `resolved_at`

P0 先固定三类 Decision：

- `scope_confirmation`
- `direction_confirmation`
- `release_approval`

### 7.7 AgentRun

作用：审计与可观测层，记录 Agent 实际工作过程。

建议字段：

- `id`
- `project_id`
- `stage_run_id`
- `agent_name`
- `goal`
- `status`
- `input_refs`
- `output_refs`
- `started_at`
- `ended_at`
- `log_ref`

### 7.8 Project 状态机

P0 建议统一为：

- `draft`
- `brief_confirmed`
- `defining`
- `building`
- `testing`
- `release_ready`
- `released`
- `blocked`

### 7.9 StageRun 状态机

P0 建议统一为：

- `queued`
- `running`
- `waiting_for_decision`
- `completed`
- `failed`
- `cancelled`

---

## 8. 数据表设计草案

### 8.1 主表

建议 P0 先定义以下主表：

- `projects`
- `assets`
- `stage_runs`
- `tasks`
- `decisions`
- `agent_runs`

### 8.2 辅助表

建议增加：

- `asset_links`
- `stage_run_assets`
- `decision_assets`
- `project_members`（可预留）

### 8.3 设计原则

#### 8.3.1 资产正文先用 JSON 存储

在 P0 阶段，建议 `assets.content_json` 统一承载结构化正文，而不是一开始把每种资产拆成独立表。

理由：

- 业务模型仍在快速演化
- 先保证统一抽象和版本机制
- 避免过早 schema 细化锁死演化空间

#### 8.3.2 依赖关系外置

建议资产依赖、阶段输入输出关联采用独立关联表，而不是直接把所有关系塞入 JSON。

优点：

- 更容易追踪产物链路
- 更适合未来做依赖可视化
- 支持版本历史与生成链查询

---

## 9. API / 页面对象读写映射

### 9.1 Dashboard / Home

主要读取：

- `projects`
- `decisions`
- `assets`
- `stage_runs`

建议接口：

- `GET /projects?view=dashboard`
- `GET /decisions?status=pending`
- `GET /assets/recent`
- `GET /stage-runs/summary`

主要动作：

- 新建项目 -> `POST /projects`
- 继续推进 -> `POST /projects/:id/continue`
- 处理待确认 -> `POST /decisions/:id/resolve`

### 9.2 Project / Mission Board

主要读取：

- `projects/:id`
- `assets`
- `stage_runs`
- `tasks`
- `decisions`
- `agent_runs`

建议接口：

- `GET /projects/:id`
- `GET /projects/:id/assets`
- `GET /projects/:id/stage-runs`
- `GET /projects/:id/tasks`
- `GET /projects/:id/decisions`
- `GET /projects/:id/agent-runs`

主要动作：

- 批准 Brief -> `POST /assets/:id/approve`
- 继续推进 -> `POST /projects/:id/continue`
- 切换模式 -> `PATCH /projects/:id`
- 阶段重试 / 重新规划 -> `POST /stage-runs/:id/retry` / `POST /projects/:id/replan`

### 9.3 Intake / Project Creation

主要写入：

- `projects`
- `assets(project_brief)`
- `decisions(scope_confirmation)`

建议接口：

- `POST /projects`
- `POST /projects/:id/brief/generate`
- `PATCH /assets/:brief_id`
- `POST /decisions/:id/resolve`

### 9.4 Release Center

主要读取：

- `build_artifact`
- `test_report`
- `release_pack`
- `decisions(release_approval)`

建议接口：

- `GET /projects/:id/release`
- `POST /projects/:id/release-pack/generate`
- `POST /projects/:id/release/approve`
- `POST /projects/:id/release/reject`

### 9.5 Artifact Hub

主要围绕：

- `assets`
- `asset_links`

建议接口：

- `GET /projects/:id/assets?type=...`
- `GET /assets/:id`
- `GET /assets/:id/versions`
- `GET /assets/:id/links`
- `POST /assets/:id/regenerate`

---

## 10. 用户主路径的状态/API 流（创建项目 -> 发布）

### 10.1 Step A: 创建项目

用户动作：

- 输入一句话想法
- 补充平台、用户、参考产品等信息

系统写入：

- `projects` 新记录（状态：`draft`）
- 初始 `Project Brief` 草稿资产

建议 API：

- `POST /projects`
- `POST /projects/:id/brief/generate`

### 10.2 Step B: MVP 范围确认

系统行为：

- Founder/PM Agent 追问需求
- 汇总出 `Project Brief`
- 生成 `scope_confirmation` Decision

系统写入：

- `assets(Project Brief)`
- `decisions(scope_confirmation)`

状态转移：

- `draft -> brief_confirmed`（确认后）
- 若未确认：停在 `waiting_for_decision`

建议 API：

- `GET /projects/:id/brief`
- `POST /decisions/:id/resolve`

### 10.3 Step C: 产品定义与设计方案

系统行为：

- PM + UX Agent 生成 PRD / UX Blueprint
- Architect Agent 生成 Tech Spec / Task Plan

系统写入：

- `assets(PRD)`
- `assets(UX Blueprint)`
- `assets(Tech Spec)`
- `assets(Task Plan)`
- `stage_runs`（product_definition, solution_design）

状态转移：

- `brief_confirmed -> defining`

建议 API：

- `POST /projects/:id/continue`
- `GET /projects/:id/assets`
- `GET /projects/:id/stage-runs`

### 10.4 Step D: 自动生产

系统行为：

- Developer Agent 生成代码/构建
- QA Agent 跑测试并生成结果

系统写入：

- `assets(Build Artifact)`
- `assets(Test Report)`
- `tasks`
- `agent_runs`
- `stage_runs`（build_execution, qa_validation）

状态转移：

- `defining -> building -> testing`

建议 API：

- `GET /projects/:id/tasks`
- `GET /projects/:id/agent-runs`
- `GET /projects/:id/assets?type=build_artifact`
- `GET /projects/:id/assets?type=test_report`

### 10.5 Step E: 发布准备

系统行为：

- Release Agent 生成发布材料
- 生成 `release_approval` Decision

系统写入：

- `assets(Release Pack)`
- `decisions(release_approval)`
- `stage_runs(release_preparation)`

状态转移：

- `testing -> release_ready`

建议 API：

- `GET /projects/:id/release`
- `POST /projects/:id/release-pack/generate`
- `POST /decisions/:id/resolve`

### 10.6 Step F: 上线与迭代

系统行为：

- 记录发布通过
- 汇总反馈和下一版建议

系统写入：

- 发布后的 `Project` 状态变更
- 下一轮 `Project Brief` / `Next Version Brief`

状态转移：

- `release_ready -> released`
- 进入新一轮迭代时，可重新回到 `brief_confirmed` / `defining`

建议 API：

- `POST /projects/:id/release/approve`
- `POST /projects/:id/next-brief/generate`

---

## 11. 实施优先级建议

### 11.1 P0 必做

- `projects`
- `assets`
- `stage_runs`
- `decisions`
- 项目主状态机
- `scope_confirmation` / `release_approval`
- Project Brief -> PRD -> Tech Spec -> Build/Test/Release 产物链

### 11.2 P0.5 建议做

- `agent_runs`
- `asset_links`
- `stage_run_assets`
- Checkpoint 模式

### 11.3 P1 再做

- 更细粒度任务依赖
- 多用户/协作者机制
- 更复杂的审批流
- 发布后的反馈自动摄取

---

## 12. 后续工作

基于本 ADR，下一步应进行：

1. 对比现有 Catown 实现与本业务架构的差距
2. 判断当前系统是否仍以 chatroom/message/pipeline 为主对象
3. 设计从现有数据模型迁移到 `project/asset/decision/stage_run` 为中心的重构路径
4. 将 Dashboard / Mission Board / Release Center UI 与本业务模型对齐

---

## 13. 一句话总结

**Catown 的核心不是“多 Agent 会话系统”，而是“面向个人 APP 开发者的 AI 软件工厂”，其业务骨架应围绕项目资产、阶段接力、最小 Gate 和可控自动推进来设计。**
