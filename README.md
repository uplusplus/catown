# Catown — AI 软件工厂

输入原始需求，输出可发布产品。全流程自动化，BOSS 可实时监控和介入。

## 🌟 核心特性

- 🏭 **AI 软件工厂**：需求分析 → 架构设计 → 开发 → 测试 → 发布，全链路自动
- 🤖 **6 个专业 Agent**：analyst / architect / developer / tester / release / assistant
- 💬 **Agent 间实时消息**：Agent 可直接互相提问，BOSS 实时可见
- 🧠 **SOUL 体系**：三层 prompt 结构（灵魂 → 角色 → 规则 → 记忆注入）
- 🛡️ **安全隔离**：工具白名单 + Workspace 路径防护 + .catown 保护
- 📊 **Pipeline Dashboard**：实时进度、Agent 通信、产出物管理
- ⚙️ **全可配置**：LLM 模型、Pipeline 流程、Agent 角色全部由 JSON 驱动

## 🚀 快速开始

### 环境要求

- Python 3.10+

### 安装 & 启动

```bash
cd backend && pip install -r requirements.txt
```

配置 LLM（编辑 `backend/configs/agents.json`）：

```json
{
  "global_llm": {
    "provider": {
      "baseUrl": "https://api.openai.com/v1",
      "apiKey": "sk-your-key",
      "models": [{"id": "gpt-4", "name": "GPT-4", "maxTokens": 8192}]
    },
    "default_model": "gpt-4"
  }
}
```

启动：

```bash
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- 🌐 Web 界面：http://localhost:8000
- 📚 API 文档：http://localhost:8000/docs

## 📁 项目结构

```
catown/
├── backend/
│   ├── agents/          # Agent 核心（SOUL 体系、注册、协作）
│   ├── chatrooms/       # 聊天室系统
│   ├── configs/         # 配置文件（agents.json, pipelines.json, skills.json）
│   ├── llm/             # LLM 客户端（OpenAI 兼容，per-agent 配置）
│   ├── models/          # 数据库模型（含 Pipeline 表）
│   ├── pipeline/        # Pipeline 引擎（核心）
│   ├── routes/          # API 路由
│   ├── tools/           # 工具集合（文件、代码执行、浏览器、截图等）
│   └── main.py          # 应用入口
├── frontend/            # 前端（Vanilla JS + TailwindCSS 单文件）
├── docs/                # PRD + ADR
└── tests/               # 单元测试 + E2E 测试
```

## 🤖 Agent 角色

| 角色 | 职责 | Gate |
|------|------|------|
| `analyst` | 需求分析，输出 PRD.md | 人工审批 |
| `architect` | 架构设计，输出 tech-spec.md | 自动 |
| `developer` | 编写代码 + 单元测试 | 自动 |
| `tester` | 测试执行，输出 test_report.md | 自动 |
| `release` | 版本管理，输出 CHANGELOG.md | 人工审批 |
| `assistant` | 打杂，协助其他 Agent | — |

每个 Agent 有独立的 SOUL（灵魂）、角色、工具白名单、LLM 模型配置。

## 🛡️ 安全机制

- **工具白名单**：Agent 仅能调用 `agents.json` 中声明的工具
- **路径校验**：统一 `_validate_path()` — symlink 解析 + 目录穿越检测 + `.catown/` 保护
- **Workspace 隔离**：每个项目独立目录，Agent 无法访问其他项目数据

## 📊 测试状态

- Pipeline 测试：27/27 ✅（含 8 个安全专项测试）
- E2E 集成测试：35/35 ✅

## 📋 实施进度

### ✅ 已完成

| 模块 | 日期 |
|------|------|
| 数据模型 + 配置 | 2026-04-07 |
| Pipeline 引擎 + API | 2026-04-07 |
| 前端 Dashboard | 2026-04-07 |
| Git 集成 + 产出物查看 | 2026-04-07 |
| Agent SOUL 体系 | 2026-04-08 |
| Skills 三级注入 (ADR-008) | 2026-04-10 |
| 工具白名单 + Workspace 隔离 | 2026-04-10 |
| 知识图谱 Skill 定义 (ADR-004) | 2026-04-10 |

### ⏳ 待做

#### 阶段一：Agent 认知基础（P0）

| # | 模块 | 内容 |
|---|------|------|
| 1 | 短期记忆 | 内存 + JSON 落盘，Stage 结束摘要归档 |
| 2 | 项目记忆 | Markdown + grep 检索，`.catown/memory/` |
| 3 | Choice Box | 前端交互组件：单选/多选/确认/编辑 |
| 4 | Agent 操作可视化 | 折叠卡片：LLM/工具/记忆/通信 |

#### 阶段二：交互与流程（P1）

| # | 模块 | 内容 |
|---|------|------|
| 5 | 聊天框输入体验 | 历史↑↓、指令/、联想补全 |
| 6 | 工具临时授权 | Agent 请求 → Choice Box → BOSS 审批 |
| 7 | 审计日志 | audit_logs + audit_details 双表，滚动清理 |
| 8 | 知识图谱集成 | developer/architect 加入 KG skill，建图接入审批 |

#### 阶段三：记忆深化（P2）

| # | 模块 | 内容 |
|---|------|------|
| 9 | 长期记忆 (ChromaDB) | 向量数据库，语义检索，跨项目 |
| 10 | 睡眠整理调度器 | 空闲触发记忆压缩和迁移 |

#### 阶段四：高级能力（P3）

| # | 模块 | 内容 |
|---|------|------|
| 11 | OMNI 多模态 | 图片/视频/音频理解 |
| 12 | UI/UX Pro Max Phase 2 | 截图对比、ui-designer 角色 |
| 13 | Knowledge Graph 进阶 | 增量更新自动化、图谱可视化 |

## 📖 文档

- [PRD（产品需求文档）](docs/PRD.md)
- [Wiki](https://github.com/uplusplus/catown.wiki.git) — ADR 索引、架构决策、开发日志

## 📄 License

MIT License
