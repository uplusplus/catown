# Mission Board 用户使用指南

_Updated: 2026-04-15_

## 目的

这份文档说明 **当前已实现的 Catown Mission Board** 应该怎么使用。

它描述的是：

- 当前 React + Vite + TypeScript 前端
- 当前 project-first `/api/v2/*` 主链路
- 当前用户真正能执行的操作和推荐工作流

它**不**描述旧的 Pipeline Dashboard / chatroom 主工作流。

相关文档：

- `docs/ADR-023-frontend-react-mission-board-architecture.md`
- `docs/Mission-Board-Information-Architecture.md`
- `docs/Mission-Board-Minimum-V2-Contract.md`

---

## 一句话理解

Mission Board 的核心工作方式是：

**创建项目 → 理解当前状态 → 在需要时处理决策 → 推进项目继续执行 → 检查新产出和新阶段状态。**

---

## 页面结构

当前页面分成三块：

### 1. 左侧 Project Rail

用途：

- 创建项目
- 查看项目列表
- 切换当前项目

你会看到：

- 项目名称
- 当前阶段
- 项目状态
- 健康状态
- 最近活动时间
- 是否 blocked

---

### 2. 中间 Main Board

用途：

- 看懂这个项目现在处于什么状态
- 判断下一步该做什么
- 执行关键操作

主要区域包括：

- `ProjectHero`
- `NextActionStrip`
- `StageLane`
- `DecisionPanel`
- `AssetPanel`
- `ActivityFeed`

---

### 3. 右侧 Detail Rail

用途：

- 深入查看当前选中的对象细节

当前支持查看：

- stage detail
- decision detail
- asset detail
- event detail

顶部会显示一条上下文路径，帮助你知道自己当前正在看：

- 项目
- 阶段
- 当前聚焦对象

---

## 当前可执行的主要操作

当前版本不是“只看不动”，但也还不是完整控制台。

### 已支持

- 创建项目
- 切换项目
- 查看项目概览
- 查看 stage run 列表与详情
- 查看 decision 列表与详情
- 批准 / 拒绝 decision
- 查看 asset 列表与详情
- 查看 event 列表与详情
- 执行 `Continue Project`

### 还未作为主前端操作开放

- 更完整的 stage instruction 入口
- tasks / agent_runs / audit 操作面板
- 审计卡片流 / token 面板
- 旧 pipeline/chatroom 控制面板
- 更细的执行控制操作

---

## 推荐用户工作流

## 第 1 步，创建项目

如果系统里还没有你要操作的项目：

1. 在左侧 `Project Rail` 顶部找到创建表单
2. 输入：
   - `New project name`
   - `One-line vision (optional)`
3. 点击 `Create Project`

系统会：

- 创建项目
- 自动刷新项目列表
- 自动选中新项目
- 将 Mission Board 切到这个新项目

### 建议

- `Project name` 用一个清晰的产品名或任务名
- `One-line vision` 用一句话说明目标，不要太长

例如：

- Project name: `FitPet MVP`
- One-line vision: `Help pet owners manage feeding and exercise routines.`

---

## 第 2 步，先看懂当前状态

创建或选中项目后，先不要急着点按钮。

先看这几个地方：

### A. ProjectHero

这里告诉你：

- 项目名
- 当前阶段
- 当前 focus
- 最近动态
- 是否 blocked
- release readiness 概况

### B. NextActionStrip

这里告诉你：

- 当前系统建议的下一步是什么
- 为什么这是当前最值得做的动作

### C. StageLane

这里告诉你：

- 当前项目运行到了哪个 stage
- 当前 stage 是 active / waiting / completed / failed 中哪种状态

### D. DecisionPanel

这里告诉你：

- 当前有没有待人工处理的 decision

### E. AssetPanel / ActivityFeed

这里告诉你：

- 已经产出了什么资产
- 最近发生了什么事件

---

## 第 3 步，遇到待决策项时，先处理 Decision

如果 `DecisionPanel` 里有 pending decision，通常应该先处理它。

### 操作方式

1. 在 `DecisionPanel` 中点击一条 decision
2. 右侧 `DetailRail` 会显示：
   - 标题
   - 背景上下文
   - 推荐选项
   - 影响说明
   - 备选项
3. 在 decision 卡片上执行：
   - approve
   - reject

### 语义

`Decision` 代表的是：

- 人工拍板点
- 需要用户明确给出判断的地方

### 处理后会发生什么

系统会：

- 调用 `POST /api/v2/decisions/{id}/resolve`
- 刷新整个 board 的项目数据
- 更新当前 decision 状态
- 如果项目因此解锁，下一步通常就会变成可继续推进

---

## 第 4 步，项目可推进时，执行 Continue Project

当项目不再卡在待决策点，或者系统建议你继续推进时，可以点击：

- `Continue Project`

这个按钮在 `ProjectHero` 里。

### 这个按钮真正的语义

它不是“前端切下一页”，而是：

**请求后端把项目继续往下执行一次。**

也就是：

- 推进当前项目
- 触发下一步执行
- 然后前端重新拉最新状态

### 当前前端工流

点击后前端会：

1. 调用 `POST /api/v2/projects/{id}/continue`
2. 成功后重新拉取：
   - project overview
   - stage runs
   - decisions
   - assets
3. 将 detail focus 回到当前 stage
4. 显示成功提示：
   - `Project advanced. Mission board refreshed.`

### 什么时候适合点 Continue

适合：

- 当前没有必须先拍板的 pending decision
- 你想让项目继续推进
- 你想看系统产生新的阶段结果或资产结果

不适合：

- 项目明显还卡在一个你没处理的 decision 上
- 你还没看清当前输出和风险

---

## 第 5 步，检查推进结果

在 `Continue Project` 或 `Resolve Decision` 之后，建议立刻检查：

### 看 StageLane

确认：

- 当前 stage 有没有变化
- 是否进入新 stage
- 旧 stage 是否完成 / blocked / failed

### 看 DecisionPanel

确认：

- 原来的 pending decision 是否消失
- 是否出现了新的 decision

### 看 AssetPanel

确认：

- 有没有新的关键资产产出
- 是否出现新的 PRD / definition / task plan 等结果

### 看 ActivityFeed

确认：

- 最近事件有没有明显异常
- 有没有能帮助你理解推进结果的活动记录

### 看 DetailRail

需要时点开：

- 当前 stage
- 某条 decision
- 某个 asset
- 某条 event

做更细检查

---

## Detail Rail 的使用方式

右侧 `DetailRail` 是当前板上的深入检查区。

### 你可以从这些位置进入 DetailRail

- 点击 stage
- 点击 decision
- 点击 asset
- 点击 event
- 在 stage detail 里继续点输入/输出/linked decision/recent events

### 当前支持的 detail 类型

#### 1. Stage detail

可看：

- status
- lifecycle phase
- inputs
- outputs
- linked decisions
- recent events

#### 2. Decision detail

可看：

- context summary
- decision type
- recommended option
- impact summary
- alternative options

#### 3. Asset detail

可看：

- asset type
- version
- status
- relationships
- content / markdown

#### 4. Event detail

可看：

- event summary
- stage / agent / stage run / asset 关联
- payload

### Context Trail 的意义

DetailRail 顶部的路径提示用于避免“我点来点去不知道自己在哪”。

你会看到类似：

- `Project > Stage > Decision`
- `Project > Stage > Asset`
- `Project > Stage > Event`

---

## 当前最推荐的日常使用方式

如果你是第一次使用当前版本，最稳妥的节奏是：

1. 创建项目
2. 读一遍 ProjectHero + NextActionStrip
3. 看 DecisionPanel 有没有 pending decision
4. 如果有，先 resolve decision
5. 如果没有明显阻塞，就点 Continue Project
6. 检查 stage / assets / activity 的变化
7. 重复以上循环

这就是当前版本最核心、最稳定的用户路径。

---

## 空态时怎么办

如果页面看起来“没法操作”，先检查这几点：

### 情况 1，没有项目

表现：

- 左侧列表为空
- 中间板面显示 `No mission selected`

做法：

- 先在左侧创建项目

### 情况 2，有项目但没选中

表现：

- 左边有项目
- 中间仍然没有主板内容

做法：

- 点击左侧项目卡片

### 情况 3，项目卡在 decision

表现：

- `NextActionStrip` 指向一个 resolve 类动作
- `DecisionPanel` 有 pending item

做法：

- 先 resolve decision，再继续推进

### 情况 4，点击后没看到想要的内容

做法：

- 查看右侧 `DetailRail`
- 注意顶部 context trail
- 看当前是不是已经切到别的 focus（decision / asset / event）

---

## 你应该怎样理解这个产品

当前 Mission Board 的心智模型不是：

- chatroom first
- pipeline dashboard first
- message log first

而是：

- **Project** 是主对象
- **StageRun** 是推进过程
- **Decision** 是人工拍板点
- **Asset** 是产出物
- **Event** 是执行痕迹
- **Mission Board** 是统一工作面

---

## 当前版本的限制

为了避免误解，这里明确一下当前版本还没有做到的部分。

### 还不算完整控制台

当前前端已经能做关键动作，但还没有做到：

- 完整 stage instruction 控制
- 完整任务/运行/Audit 面板
- token / audit timeline / SSE event card 流
- 全套旧 pipeline/chatroom 兼容操作

### 当前更接近

- 一个 project-first 主工作面
- 以阅读状态 + 处理关键动作 + 推进项目为中心
- 不是所有系统能力的全量暴露

---

## 建议给新用户的最短说明

如果你只想给新用户一句很短的说明，可以直接用这段：

> 先在左侧创建或选择一个项目，先看中间板上的当前状态和推荐下一步；如果有待决策项先处理决策，没有阻塞再点 Continue Project，让系统继续推进，然后检查新的阶段、资产和活动变化。

---

## 未来会继续增强的方向

后续前端很可能继续增强：

- 更明确的 action CTA
- 更完整的 stage control
- 更丰富的空态 / 引导态
- 更强的审计 / 事件流 / 运行可观测性
- 更多“从 board 直接执行”的控制入口

但在当前版本里，最稳的用户工作流仍然是：

**Create → Read → Resolve → Continue → Inspect → Repeat**
