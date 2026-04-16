# ADR-025: UI 现状与设计对比分析

**日期**: 2026-04-16
**状态**: 待确认
**决策人**: BOSS
**相关**: PRD §8, ADR-007, ADR-010, ADR-011

---

## 1. 架构变迁

`feature/business-architecture-refactor` 分支对前端做了根本性重构：

| 维度 | 旧版 (master) | 新版 (此分支) |
|------|--------------|--------------|
| 技术栈 | Vanilla JS + TailwindCSS 单文件 | React + TypeScript + Vite |
| 组件 | 全部塞在 `index.html` | 12 个独立 `.tsx` 组件 + 4 个 hooks |
| 字体 | — | Space Grotesk + Noto Sans SC |
| 交互范式 | 聊天室为中心 | Cockpit-First Mission Board |

---

## 2. 设计文档定义的 UI 愿景

PRD + ADR-010 + ADR-011 共同定义的核心交互模型：

**BOSS 的唯一观察入口是聊天室。** 所有 LLM / TOOL / SKILLS / AGENT 调用，不论是通过聊天室 SSE 还是 Pipeline 引擎触发，都必须在聊天室窗口中以卡片形式完整展示。

8 种卡片类型：

| # | 卡片 | 图标 | 默认状态 | 用途 |
|---|------|------|---------|------|
| 1 | LLM 调用 | 🧠 | 折叠 | 展示 system_prompt / model / tokens / response |
| 2 | 工具调用 | 🔧📖✏️▶️🔍 | 折叠（失败展开） | 展示 arguments / result / success / duration |
| 3 | Skill 注入 | ⚡ | 折叠 | 展示 hint / guide 内容 |
| 4 | Agent 间消息 | 💬 | 展开 | Agent 间 ask/reply |
| 5 | 阶段事件 | ▶️/✅ | 展开 | 阶段开始/完成 |
| 6 | Gate 事件 | 🚧 | 展开 | 审批阻塞 + Approve/Reject 按钮 |
| 7 | BOSS 指令 | 👤 | 展开 | BOSS 发出的指令 |
| 8 | Agent 输出 | 💬 | 展开 | Agent 文本回复 |

Pipeline 来源的卡片有左侧蓝色竖线 + `🔧 Pipeline` 标签，聊天室来源的卡片无特殊标记。

关键原则（ADR-011）：**后端执行路径（SSE / Pipeline）对前端透明，卡片渲染逻辑统一。**

---

## 3. 当前实现的 UI

### 3.1 组件清单

| 组件 | 大小 | 功能 |
|------|------|------|
| `App.tsx` | 10.8KB | 主布局 + 数据流 + 交互协调 |
| `NavigationCore.tsx` | 8.5KB | 项目概览 + 推荐操作 + CTA 按钮 |
| `DetailRail.tsx` | 7.7KB | 聚焦检查面板（阶段/决策/产出物/事件） |
| `CurrentSegment.tsx` | 5.6KB | 当前阶段详情 |
| `ProjectHero.tsx` | 4.1KB | 项目头部 |
| `StatusRail.tsx` | 4.1KB | 右栏状态面板 |
| `StageLane.tsx` | 4.3KB | 阶段路线可视化 |
| `NextActionStrip.tsx` | 3.3KB | 下一步操作条 |
| `ProjectRail.tsx` | 3.4KB | 左栏项目列表 |
| `HelpPanel.tsx` | 3.1KB | 帮助面板 |
| `ActivityFeed.tsx` | 2.5KB | 活动流 |
| `DecisionPanel.tsx` | 2.7KB | 决策面板 |
| `AssetPanel.tsx` | 1.5KB | 产出物面板 |

自定义 hooks：`useProjectBoardData`、`useBoardSelection`、`useBoardTransitions`、`useDetailFeedback`

### 3.2 布局结构

```
三栏布局:
├── 左栏 (ProjectRail)     — 项目列表 + 创建
├── 中栏 (主面板)
│   ├── NavigationCore     — 项目名 + 状态 + 阶段路线图
│   ├── StageLane          — 阶段横向可视化
│   ├── CurrentSegment     — 当前阶段详情（事件/产出物/决策）
│   ├── DecisionPanel      — 待决策列表
│   ├── AssetPanel         — 产出物列表
│   └── ActivityFeed       — 活动流
└── 右栏 (StatusRail)      — 状态指标 + Release readiness
```

---

## 4. 设计 vs 实现对比

| 维度 | 设计文档（聊天室卡片流） | 此分支（Cockpit 仪表盘） | 评价 |
|------|------------------------|------------------------|------|
| 信息密度 | 低——线性时间流，卡片堆叠 | 高——三栏并行展示 | 仪表盘更好 |
| BOSS 可操作性 | 中——卡片内有按钮 | 高——专门的 Decision Panel + CTA | 仪表盘更好 |
| LLM 对话可见性 | 核心——8 种卡片展示完整对话 | ❌ **完全缺失** | 聊天室方案更好 |
| 工具调用可视化 | 核心——🔧卡片展示参数/结果 | ⚠️ 只有 ActivityFeed 事件 | 聊天室方案更好 |
| Skill 注入可视化 | 核心——⚡卡片展示 hint/guide | ❌ 缺失 | 聊天室方案更好 |
| Agent 间消息 | 核心——💬卡片 from→to | ❌ 缺失 | 聊天室方案更好 |
| Gate 审批 | 🚧卡片 + 按钮 | ✅ DecisionPanel + Resolve 按钮 | 都有，仪表盘更结构化 |
| 阶段进度 | ▶️/✅ 卡片 | ✅ StageLane 可视化 | 仪表盘更好 |
| 代码质量 | N/A（设计文档无前端代码） | React + TS + hooks 分离，不错 | — |

### 4.1 此分支缺失的（设计文档有、代码没有）

1. **聊天室/对话视图** — 没有任何地方能看 Agent 的 LLM 对话、system prompt、token 消耗
2. **8 种卡片系统** — 完全没有实现。ActivityFeed 只是事件列表，不是卡片流
3. **工具调用可视化** — 没有展示 tool_call 的参数/结果
4. **Agent 间通信可视化** — 没有
5. **Skill 注入可视化** — 没有
6. **SSE 事件扩展** — 此分支走 REST API 轮询模式，不是 SSE/WebSocket 推送

### 4.2 此分支新增的（设计文档没有、代码有）

1. **三栏 Cockpit 布局** — 合理的 UI 决策，信息密度高于线性卡片流
2. **推荐操作（`recommended_next_action`）** — 根据状态动态推荐 BOSS 下一步做什么
3. **StageLane 路线图** — 阶段可视化比卡片流更直观
4. **Release readiness 信号** — 右栏直接显示 PRD/Release Pack 就绪状态
5. **`useProjectBoardData` + hooks 架构** — 比 Vanilla JS 方案工程化得多

---

## 5. 结论

**此分支的 UI 在项目管理层面比设计文档更好**——三栏布局、阶段路线图、推荐操作、Release readiness 信号是精心设计的。

**但此分支在"可观测"层面完全空白**——BOSS 能管理项目决策，却看不到 Agent 实际在做什么。设计文档中的 8 种卡片系统的核心洞见是对的：BOSS 需要一个视角看到 Agent 的全部内部行为。

**理想的方案是两者融合：**

- Cockpit 视角（此分支）：管理项目、做决策、看阶段进度
- Agent Telemetry 视角（需新增）：看 Agent 内部状态、实时行为流、LLM 对话详情
- Comm Channel 视角（需新增）：Agent 间通信 + BOSS 指令

两层/三层切换，而非二选一。
