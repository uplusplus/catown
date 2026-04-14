# ADR-014: 项目流转、API 边界与状态机草案

**日期**: 2026-04-13
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-013-business-architecture-solo-app-factory.md`, `docs/PRD.md`

---

## 1. 目标

本 ADR 不再讨论 Catown 的产品定位，而是专门回答一个更偏实现的问题：

**如果 Catown 要成为面向个人 APP 开发者的 AI 软件工厂，那么一个项目从创建到发布，系统内部应该如何流转？**

本文档聚焦三件事：

- 项目阶段如何推进
- 各阶段读写哪些业务对象
- API 和状态机应该如何收敛

为了降低实现复杂度，本文档以 P0 为边界，不覆盖多租户、复杂协作者权限、多环境发布编排等后续能力。

---

## 2. 核心业务对象回顾

项目推进过程中，P0 默认围绕以下对象运转：

- `Project`
- `Asset`
- `StageRun`
- `Decision`
- `Task`
- `AgentRun`

其中：

- `Project` 负责项目全局状态
- `Asset` 负责正式产物
- `StageRun` 负责阶段推进实例
- `Decision` 负责人工确认点
- `Task` 负责执行拆解
- `AgentRun` 负责审计与可观测

---

## 3. 项目层状态机

### 3.1 Project 状态定义

P0 统一项目主状态为：

- `draft`
- `brief_confirmed`
- `defining`
- `building`
- `testing`
- `release_ready`
- `released`
- `blocked`

### 3.2 Project 状态含义

| 状态 | 含义 | 用户感知 |
|------|------|----------|
| `draft` | 只有原始想法，尚未形成正式 Brief | 项目刚创建，尚未收敛 |
| `brief_confirmed` | MVP 范围已确认 | 可以进入正式设计与方案阶段 |
| `defining` | 正在生成 PRD / UX / Tech Spec / Task Plan | 项目蓝图正在成形 |
| `building` | 正在进行开发/构建 | 项目已进入生产 |
| `testing` | 正在做验证、回归、修复 | 项目接近发布 |
| `release_ready` | 发布材料齐备，等待最后确认 | 已到上线门口 |
| `released` | 已完成发布/上线 | 项目进入运营和迭代 |
| `blocked` | 因缺失信息、执行失败或人工阻塞而停滞 | 需要处理问题 |

### 3.3 项目主状态流

```text
create project
  -> draft
  -> brief_confirmed
  -> defining
  -> building
  -> testing
  -> release_ready
  -> released
```

异常流：

```text
draft / defining / building / testing / release_ready
  -> blocked
```

恢复流：

```text
blocked
  -> previous logical state
  -> 或进入 defining（重新规划）
```

### 3.4 关键规则

- `draft` 不能直接跳到 `building`
- `release_ready` 必须有 `Release Pack` 且至少有一份 `Test Report`
- `released` 不意味着流程终止，可以再派生下一轮 `Project Brief`
- `blocked` 是横切状态，不代表项目失败，而代表“当前无法继续推进”

---

## 4. 阶段层状态机

### 4.1 StageRun 状态定义

每个阶段实例（`StageRun`）建议使用统一状态机：

- `queued`
- `running`
- `waiting_for_decision`
- `completed`
- `failed`
- `cancelled`

### 4.2 状态含义

| 状态 | 含义 |
|------|------|
| `queued` | 已进入待执行队列 |
| `running` | 正由一个或多个 Agent 执行 |
| `waiting_for_decision` | 已完成当前阶段工作，但等待用户决策 |
| `completed` | 阶段正常结束 |
| `failed` | 阶段执行失败 |
| `cancelled` | 因人工停止或策略切换而终止 |

### 4.3 Autopilot 与 Checkpoint 的差异

#### Autopilot

```text
completed -> next stage queued
failed -> blocked
waiting_for_decision -> only when a real Decision exists
```

#### Checkpoint

```text
completed -> waiting_for_decision
user continue -> next stage queued
failed -> blocked
```

这里的核心不是页面行为，而是流程推进策略：

- Autopilot 尽量不停
- Checkpoint 阶段性停靠，先摘要再继续

---

## 5. 决策点（Decision）状态机

### 5.1 Decision 类型

P0 先固定三类：

- `scope_confirmation`
- `direction_confirmation`
- `release_approval`

### 5.2 Decision 状态

建议状态：

- `pending`
- `approved`
- `rejected`
- `expired`

### 5.3 状态流

```text
create decision -> pending
pending -> approved
pending -> rejected
pending -> expired
```

### 5.4 业务规则

- 只有 `pending` 的 Decision 会出现在首页待办区
- `approved` 通常触发下一阶段推进或资产状态更新
- `rejected` 通常触发重新生成、重新规划或项目停留在当前阶段
- `expired` 一般用于未来更复杂的异步场景，P0 可先保留接口不强实现

---

## 6. 从创建项目到发布的完整流转

### 6.1 Step A: 创建项目（Idea Intake）

#### 用户动作

- 输入一句话产品想法
- 补充目标平台、目标用户、参考产品、变现预期等信息

#### 系统动作

- 创建 `Project`
- 创建第一版 `Project Brief` 草稿资产
- 准备进入 Brief 生成或澄清阶段

#### 写入对象

- `Project(status=draft)`
- `Asset(type=project_brief, status=draft)`

#### API 建议

- `POST /projects`
- `POST /projects/:id/brief/generate`

#### 返回前端的核心信息

- 项目 ID
- 当前状态 `draft`
- Brief 生成是否已开始
- 下一步建议：补充信息 / 等待 Brief 草稿

---

### 6.2 Step B: MVP 范围确认（Briefing）

#### 系统动作

- Founder/PM Agent 提问并整合需求
- 生成结构化 `Project Brief`
- 生成 `scope_confirmation` Decision

#### 写入对象

- `Asset(type=project_brief, status=in_review)`
- `Decision(type=scope_confirmation, status=pending)`
- `StageRun(stage_type=briefing)`

#### 状态变化

- `Project`: 仍可能保持 `draft`
- 当用户确认后：`draft -> brief_confirmed`

#### API 建议

- `GET /projects/:id/brief`
- `PATCH /assets/:brief_id`
- `POST /decisions/:id/resolve`

#### 成功出口

- Brief 被批准
- 当前项目进入 `brief_confirmed`

#### 失败/回退出口

- 用户拒绝 Brief
- 回到 Brief 修订
- `Decision` 进入 `rejected`
- `Asset` 生成新版本

---

### 6.3 Step C: 产品定义（Product Definition）

#### 系统动作

- PM Agent 生成 PRD
- UX Agent 生成用户流、页面结构、交互草案
- 必要时触发 `direction_confirmation`

#### 写入对象

- `StageRun(stage_type=product_definition)`
- `Asset(type=prd)`
- `Asset(type=ux_blueprint)`
- 可选 `Decision(type=direction_confirmation)`

#### 状态变化

- `Project: brief_confirmed -> defining`

#### API 建议

- `POST /projects/:id/continue`
- `GET /projects/:id/assets?type=prd`
- `GET /projects/:id/assets?type=ux_blueprint`
- `GET /projects/:id/decisions`

#### 关键规则

- 该阶段默认自动推进
- 只有方向明显不一致时才打断等待确认

---

### 6.4 Step D: 技术方案（Solution Design）

#### 系统动作

- Architect Agent 读取 PRD / UX Blueprint
- 生成 `Tech Spec`
- 生成 `Task Plan`

#### 写入对象

- `StageRun(stage_type=solution_design)`
- `Asset(type=tech_spec)`
- `Asset(type=task_plan)`
- `asset_links` 记录依赖

#### 状态变化

- `Project` 维持在 `defining`

#### API 建议

- `GET /projects/:id/assets?type=tech_spec`
- `GET /projects/:id/assets?type=task_plan`
- `GET /assets/:id/links`

#### 成功出口

- 技术方案和任务拆解完成
- 项目进入可执行状态

---

### 6.5 Step E: 自动生产（Build Execution）

#### 系统动作

- Developer Agent 按 `Task Plan` 开始实现
- 产生代码提交、构建产物、执行日志
- 如需要，Assistant Agent 做资料补位

#### 写入对象

- `StageRun(stage_type=build_execution)`
- `Task` 多条记录
- `AgentRun` 多条记录
- `Asset(type=build_artifact)`

#### 状态变化

- `Project: defining -> building`

#### API 建议

- `GET /projects/:id/tasks`
- `GET /projects/:id/agent-runs`
- `GET /projects/:id/assets?type=build_artifact`

#### 失败出口

- 构建失败 / 实现阻塞
- `StageRun -> failed`
- `Project -> blocked`

---

### 6.6 Step F: 测试验证（QA Validation）

#### 系统动作

- QA Agent 读取 Build Artifact 和验收标准
- 跑测试、记录失败项、生成发布建议

#### 写入对象

- `StageRun(stage_type=qa_validation)`
- `Asset(type=test_report)`
- 可选新增修复任务 `Task`

#### 状态变化

- `Project: building -> testing`

#### API 建议

- `GET /projects/:id/assets?type=test_report`
- `GET /projects/:id/tasks?status=open`

#### 成功出口

- 得到 `release_recommendation = go`

#### 失败出口

- 如果关键测试未通过，可回退到 `building`
- 或进入 `blocked`

---

### 6.7 Step G: 发布准备（Release Preparation）

#### 系统动作

- Release Agent 生成商店文案、发布说明、截图检查项、隐私文档检查项
- 整理为 `Release Pack`
- 创建 `release_approval` Decision

#### 写入对象

- `StageRun(stage_type=release_preparation)`
- `Asset(type=release_pack)`
- `Decision(type=release_approval, status=pending)`

#### 状态变化

- `Project: testing -> release_ready`

#### API 建议

- `GET /projects/:id/release`
- `POST /projects/:id/release-pack/generate`
- `POST /decisions/:id/resolve`

#### 核心规则

- 该阶段结束不代表自动发布
- 必须经过 `release_approval`

---

### 6.8 Step H: 发布与迭代（Launch + Iterate）

#### 系统动作

- 记录发布批准
- 将项目标记为已发布
- 可继续生成下一轮迭代 Brief

#### 写入对象

- `Project(status=released)`
- 可选 `Asset(type=project_brief)` 的新版本，作为下一轮迭代入口

#### API 建议

- `POST /projects/:id/release/approve`
- `POST /projects/:id/next-brief/generate`

#### 状态变化

- `Project: release_ready -> released`
- 下一轮可重新进入 `brief_confirmed` / `defining`

---

## 7. API 边界草案

### 7.1 Project APIs

- `POST /projects`
- `GET /projects`
- `GET /projects/:id`
- `PATCH /projects/:id`
- `POST /projects/:id/continue`
- `POST /projects/:id/replan`

### 7.2 Asset APIs

- `GET /projects/:id/assets`
- `GET /assets/:id`
- `GET /assets/:id/versions`
- `GET /assets/:id/links`
- `POST /assets/:id/approve`
- `POST /assets/:id/regenerate`

### 7.3 Decision APIs

- `GET /projects/:id/decisions`
- `GET /decisions?status=pending`
- `POST /decisions/:id/resolve`

### 7.4 Stage APIs

- `GET /projects/:id/stage-runs`
- `GET /stage-runs/:id`
- `POST /stage-runs/:id/retry`
- `POST /stage-runs/:id/cancel`

### 7.5 Execution / Audit APIs

- `GET /projects/:id/tasks`
- `GET /projects/:id/agent-runs`
- `GET /projects/:id/release`

---

## 8. 页面与对象映射

### 8.1 Dashboard / Home

主要读取：

- `Project`
- `Decision`
- `StageRun`
- 最近 `Asset`

核心动作：

- 创建项目
- 继续推进项目
- 处理待确认事项

### 8.2 Mission Board

主要读取：

- 单个 `Project`
- 当前阶段相关 `StageRun`
- 关键 `Asset`
- `Decision`
- `Task`
- `AgentRun`

核心动作：

- 看当前阶段
- 看关键产物
- 继续自动推进
- 拍板决策

### 8.3 Artifact Hub

主要读取：

- `Asset`
- `asset_links`
- 版本链

核心动作：

- 切换当前资产版本
- 查看资产依赖
- 重新生成资产

### 8.4 Release Center

主要读取：

- `Build Artifact`
- `Test Report`
- `Release Pack`
- `release_approval`

核心动作：

- 检查是否已满足发布条件
- 审阅发布材料
- 最终批准发布

---

## 9. 异常流与回退规则

### 9.1 Brief 被拒绝

- 现象：`scope_confirmation` 被拒绝
- 处理：重新生成或编辑 `Project Brief`
- 结果：项目停留在 `draft`

### 9.2 设计方向被打回

- 现象：`direction_confirmation` 被拒绝
- 处理：重新生成 PRD / UX Blueprint
- 结果：项目保持在 `defining`

### 9.3 构建或测试失败

- 现象：Build / QA 阶段失败
- 处理：回到 `building` 或进入 `blocked`
- 结果：生成新任务、重新执行阶段

### 9.4 发布被拒绝

- 现象：`release_approval` 被拒绝
- 处理：更新 Release Pack、补材料、修问题
- 结果：项目保留在 `release_ready` 或回退到 `testing`

---

## 10. 重构策略决策

### 10.1 重构总原则

在评估当前 Catown 实现后，进一步明确以下策略：

- 以 `best architecture first` 为原则推进下一阶段设计
- 不以兼容旧业务抽象为默认目标
- 旧实现最多作为底层能力来源，不作为新业务骨架的约束

换句话说，后续设计应优先确保：

- 业务对象正确
- 主流程正确
- 系统长期可演进

而不是优先保证：

- 与旧 `chatroom/message` 心智兼容
- 与旧 `pipeline` 暴露方式兼容
- 为了少改代码而保留错误抽象

### 10.2 重建核心、复用底层

本 ADR 进一步做出实现策略判断：

**不建议继续在当前主结构上做兼容式改造，而应采用“重建核心、复用底层”的路线。**

原因：

- 现有系统主轴更接近 `chatroom/message + pipeline`
- 目标系统主轴应为 `project/asset/decision/stage_run`
- 如果在旧主轴上继续堆业务概念，容易形成双重模型和长期复杂度债务

因此：

#### 建议重建的部分

- 新业务数据模型
- 主 API 边界
- 项目主流程状态机
- Dashboard / Mission Board / Release Center 的主信息架构

#### 建议复用或吸收的部分

- Agent runtime
- 工具调用体系
- 事件总线 / WebSocket 推送能力
- 审计可视化能力
- 部分 Pipeline 执行能力（作为底层执行引擎，而非产品主轴）

### 10.3 分阶段重构路线图

建议按以下阶段推进：

#### Phase 0: 冻结旧世界观

- 停止继续强化 `chatroom/message` 作为主业务入口
- 停止把旧 Pipeline 继续包装成产品主轴
- 旧结构保留运行价值，但不再承载新业务抽象

#### Phase 1: 建立新内核

优先落地以下四个核心对象：

- `Project`
- `Asset`
- `Decision`
- `StageRun`

这是第一刀，也是最关键的一刀。

#### Phase 2: 重做主 API

围绕新内核建立最小闭环 API：

- `POST /projects`
- `GET /projects/:id`
- `GET /projects/:id/assets`
- `GET /projects/:id/decisions`
- `POST /projects/:id/continue`
- `POST /decisions/:id/resolve`

#### Phase 3: 重做主前端

把前端主入口切换到：

- Dashboard
- Mission Board
- Release Center

使其围绕项目状态、资产、决策运作，而不是围绕聊天室消息运作。

#### Phase 4: 吸收旧执行能力

- 将旧 Agent 执行能力映射到 `StageRun/AgentRun`
- 将旧 Pipeline 拆解为可复用执行引擎
- 将聊天与协作信息降级为辅助视图/审计视图

#### Phase 5: 清理旧壳

当新主链路稳定后：

- 降级旧 `chatroom` 主路径
- 降级或移除旧 Pipeline 主入口
- 清理只服务旧世界观的耦合代码

### 10.4 第一阶段的实施顺序

建议第一阶段按如下顺序执行：

1. 新增数据模型和迁移草案，只加新表，不急于删除旧表
2. 新增并行 API，不先替换所有旧 API
3. 打通最小闭环：
   - 创建项目
   - 生成 `Project Brief`
   - 创建 `scope_confirmation`
   - 用户确认
   - 进入下一阶段
4. 再让前端接入这条最小闭环
5. 最后逐步挂入 PRD / Tech Spec / Release Pack 等资产链

### 10.5 近期里程碑建议

- `M1`：`Project / Asset / Decision / StageRun` 模型与 API 落地
- `M2`：新建项目到 Brief 确认流程跑通
- `M3`：Mission Board 基于新对象可展示项目推进
- `M4`：Build / Test / Release 资产接入新链路
- `M5`：旧 `chatroom` 主路径降级为辅助视图

## 11. 实施建议

### 11.1 P0 最小闭环

为了尽快让业务架构落地，建议优先实现：

- 项目创建
- Brief 生成与确认
- PRD / Tech Spec / Task Plan 生成
- Build Artifact / Test Report / Release Pack 产出
- `scope_confirmation` 与 `release_approval`
- Project / StageRun / Decision 状态机

### 10.2 暂缓项

以下可在 P1 处理：

- 多用户协作和权限
- 更复杂的审批类型
- 更精细的任务依赖图
- 自动采集真实商店反馈 / 评价
- 多环境发布编排

---

## 12. 与 ADR-013 的关系

- `ADR-013` 回答“Catown 应该成为什么”
- `ADR-014` 回答“Catown 内部应该怎么推进”

这两个文档应配套使用：

- 产品、PRD、战略讨论优先引用 `ADR-013`
- 数据模型、API、流程编排、状态机设计优先引用 `ADR-014`

---

## 13. 一句话总结

**Catown 的项目推进应围绕 Project / Asset / StageRun / Decision 四个主轴建立标准化状态流，使“创建项目 -> 确认 MVP -> 生成蓝图 -> 自动生产 -> 发布准备 -> 发布迭代”成为可执行、可追踪、可恢复的业务流程。**
