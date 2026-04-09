# ADR-003: LLM 对话系统队列调度模式

**日期**: 2026-04-09
**状态**: 已确认
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 的 Pipeline 中，多个 Agent 同时运行时会产生大量消息交互：BOSS 给 Agent 发指令、Agent 之间互相提问、Pipeline Engine 自动触发任务。当前系统缺乏消息调度机制，面临以下问题：

1. **无优先级区分**：BOSS 的紧急指令和 Agent 间的普通协作消息同等对待
2. **无消息合并**：BOSS 连发多条补充需求，每条都触发一次 LLM 调用，浪费 token
3. **无中断能力**：Agent 正在执行任务时，BOSS 无法中途干预
4. **无背压控制**：多项目并行时，消息洪峰可能导致 API 过载
5. **无过期丢弃**：已失效的消息（如 Pipeline 已推进到下一阶段）仍在队列中等待处理

## 决策

**基于 OpenClaw 的 4 种用户面队列模式（steer / followup / collect / steer-backlog），结合 Catown 多 Agent 场景，实现动态模式选择策略。**

## 队列模式定义

### 用户面模式（面向交互体验）

| 模式 | 核心行为 | Catown 场景 |
|------|----------|------------|
| **`steer`** | 立即干预，新消息注入当前流程，可能中断正在进行的任务 | BOSS 发送紧急指令（暂停、修改方向） |
| **`followup`** | 排队等候，Agent 完成当前轮次后再处理 | Agent 正在推理或写文件，BOSS 发了非紧急备注 |
| **`collect`** | 短时间内多条消息合并为一条再处理 | BOSS 连续补充需求细节 |
| **`steer-backlog`** | 立即干预 + 保留到下一轮 | BOSS 修改决策，既要打断当前行为又要在后续步骤中记住 |

### 底层策略（面向系统资源）

| 策略 | 行为 | Catown 场景 |
|------|------|------------|
| **`queue`** | 严格 FIFO | 测试用例批量处理 |
| **`debounce`** | 时间窗口内只取最后一条 | 实时参数调整 |
| **`concurrent`** | 全部并行 | 互不依赖的独立任务 |
| **`drop`** | 系统繁忙时直接丢弃 | 非关键的状态更新 |

## 动态模式选择策略

Catown 不全局固定一种模式，而是根据**消息来源 + Agent 当前状态**动态选择：

### BOSS → Agent

```
消息到达 Agent
    │
    ├─ 含 stop/pause/rollback 关键词 → steer（立即中断）
    ├─ Agent 空闲 → 直接处理
    └─ Agent 忙 → steer-backlog（BOSS 指令最高优先级）
```

### Agent → Agent

```
消息到达目标 Agent
    │
    ├─ 目标 Agent 空闲 → 直接处理
    ├─ 目标 Agent 在 LLM 推理中 → followup（排队，不打断推理）
    └─ 目标 Agent 在工具执行中 → collect（等工具完成再合并处理）
```

**Agent 间消息合并窗口**：1-3 秒（比用户打字的 collect 窗口短）

### Pipeline Engine → Agent

```
引擎自动触发
    │
    ├─ 下一阶段启动 → followup（排在已有消息之后）
    └─ 超时/错误恢复 → steer（需要立即处理）
```

## 公平调度

多个 Agent 向同一 Agent 发消息时，需防止饿死：

- 按来源 Agent 轮转处理，避免单个 Agent 垄断目标 Agent 的注意力
- BOSS 消息始终插队到最前

## 优先级矩阵

| 优先级 | 消息来源 | 处理策略 |
|--------|---------|---------|
| P0 | BOSS 紧急指令（含停止关键词） | steer |
| P1 | BOSS 普通指令 | steer-backlog |
| P1 | Pipeline Engine（错误恢复） | steer |
| P2 | Agent 间协作 | followup / collect |
| P3 | Pipeline Engine（阶段推进） | followup |

## 与现有模块的关系

| 模块 | 关系 | 改动 |
|------|------|------|
| `pipeline/engine.py` | 引擎触发消息时指定优先级 | 新增消息路由层 |
| `agents/collaboration.py` | Agent 间消息经过队列调度 | 新增调度器 |
| `routes/pipeline.py` | BOSS 指令 API 支持 priority 参数 | 小改 |
| `configs/agents.json` | 新增队列相关配置（合并窗口、背压阈值） | 新增字段 |

## 决策理由

1. **复用 OpenClaw 成熟模式**：steer / followup / collect / steer-backlog 已有工程实践验证
2. **动态选择优于固定模式**：Catown 的消息来源和 Agent 状态多样，单一模式无法覆盖所有场景
3. **BOSS 体验优先**：BOSS 指令始终最高优先级，确保"可介入"核心价值
4. **渐进实现**：先实现 steer + followup，再逐步添加 collect 和 steer-backlog
