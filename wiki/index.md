# Catown Wiki

## 概览

Catown — AI 软件工厂。输入原始需求，输出可发布的产品。

## 核心概念

- [Agent 角色体系](agents.md) — 6 个专业 Agent 的定义、配置、协作方式
- [Pipeline 工作流](pipeline.md) — 需求分析 → 架构设计 → 开发 → 测试 → 发布
- [Skills 体系](skills.md) — Agent 能力的高层封装，按需激活
- [记忆系统](memory.md) — 短期/项目/长期三层记忆
- [消息调度](messaging.md) — Agent 间实时通信与队列调度

## 架构决策记录 (ADR)

| # | 标题 | 日期 | 状态 |
|---|------|------|------|
| [ADR-003](../docs/ADR-queue-modes.md) | LLM 对话系统队列调度模式 | 2026-04-09 | 已确认 |
| [ADR-004](../docs/ADR-004-knowledge-graph.md) | 知识图谱集成方案 — Skills 模式 + 人控建图 | 2026-04-10 | 已确认 |
| [ADR-005](../docs/ADR-005-chat-input.md) | 聊天框输入体验优化 — 历史/指令/联想 | 2026-04-10 | 已确认 |
| [ADR-006](../docs/ADR-006-omni.md) | OMNI 多模态能力集成 — 图片/视频/音频 | 2026-04-10 | 草案 |

## 快速开始

```bash
cd backend && pip install -r requirements.txt
# 编辑 configs/agents.json 配置 LLM
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- Web 界面: http://localhost:8000
- API 文档: http://localhost:8000/docs

## 文档

- [PRD (产品需求文档)](../docs/PRD.md)
- [架构决策记录](../docs/)
