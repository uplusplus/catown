# Catown — AI 软件工厂

输入原始需求，输出可发布产品。全流程自动化，BOSS 可实时监控和介入。

## 🌟 核心特性

- 🏭 **AI 软件工厂**：需求分析 → 架构设计 → 开发 → 测试 → 发布，全链路自动
- 🤖 **6 个专业 Agent**：analyst / architect / developer / tester / release / valet
- 💬 **Agent 间实时消息**：Agent 可直接互相提问，BOSS 实时可见
- 🧠 **SOUL 体系**：三层 prompt 结构（灵魂 → 角色 → 规则 → 记忆注入）
- 🛡️ **安全隔离**：工具白名单 + Workspace 路径防护 + .catown 保护
- 📊 **Pipeline Dashboard**：实时进度、Agent 通信、产出物管理
- 🔧 **24 个内置工具**：文件、代码执行、Shell、浏览器、搜索、协作、记忆等
- ⚙️ **全可配置**：LLM 模型、Pipeline 流程、Agent 角色、Skills 全部由 JSON 驱动
- 📦 **Skills 三级披露**：hint → guide → full，按需注入 Agent 上下文

## 🚀 快速开始

### 环境要求

- Python 3.10+（推荐 3.12）
- Git
- 一个 OpenAI 兼容的 LLM API（OpenAI / DeepSeek / Ollama / vLLM 等）

### 方式一：一键启动（推荐）

```bash
git clone https://github.com/uplusplus/catown.git
cd catown
./run.sh        # Linux / macOS
run.bat         # Windows
```

`run.sh` 会自动完成：
1. 检测 Python 3.10+
2. 创建虚拟环境（`backend/.venv`）
3. 安装依赖
4. 初始化运行时目录 `${CATOWN_HOME:-~/.catown}`
5. 启动 uvicorn（默认 `--reload` 热重载）

启动后访问：
- 🌐 Web 界面：http://localhost:8000
- 📚 API 文档：http://localhost:8000/docs

运行中可输入 `q` 退出、`r` 重启。

### 方式二：手动启动

```bash
# 1. 克隆
git clone https://github.com/uplusplus/catown.git
cd catown

# 2. 创建虚拟环境
python3 -m venv backend/.venv
source backend/.venv/bin/activate   # Linux/macOS
# backend\.venv\Scripts\activate    # Windows

# 3. 安装依赖
cd backend
pip install -r requirements.txt

# 4. 初始化运行时目录
mkdir -p ~/.catown/{config,state,projects,workspaces}
cp configs/*.json ~/.catown/config/
cp .env.example ~/.catown/.env

# 5. 配置 LLM（见下方）
vim ~/.catown/.env

# 6. 启动
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 方式三：Docker

```bash
# docker-compose（含可选 PostgreSQL）
docker compose up -d

# 或纯 Docker
docker build -t catown .
docker run -p 8000:8000 \
  -v catown-home:/var/lib/catown \
  -e LLM_API_KEY=sk-xxx \
  -e LLM_BASE_URL=https://api.openai.com/v1 \
  -e LLM_MODEL=gpt-4o \
  catown
```

### 配置 LLM

编辑 `${CATOWN_HOME:-~/.catown}/.env`：

```bash
# OpenAI
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o

# DeepSeek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# Ollama（本地）
LLM_API_KEY=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:72b
```

也可以编辑 `${CATOWN_HOME:-~/.catown}/config/agents.json` 做 per-agent 模型配置：

```json
{
  "global_llm": {
    "provider": {
      "baseUrl": "https://your-openai-compatible-endpoint.example/v1",
      "apiKey": "${LLM_API_KEY}",
      "models": [{"id": "your-model-id", "name": "Your Model"}]
    },
    "default_model": "your-model-id"
  }
}
```

## 📁 项目结构

```
catown/
├── backend/
│   ├── agents/            # Agent 核心（SOUL 体系、注册、协作）
│   ├── chatrooms/         # 聊天室系统
│   ├── configs/           # 配置模板（首次启动复制到 ~/.catown/config/）
│   │   ├── agents.json    # Agent 角色 + LLM 配置
│   │   ├── pipelines.json # Pipeline 流程定义
│   │   ├── skills.json    # Skills 注册表
│   │   └── skill_marketplaces.json
│   ├── llm/               # LLM 客户端（OpenAI 兼容，per-agent 配置）
│   ├── models/            # SQLAlchemy 数据库模型
│   ├── pipeline/          # Pipeline 引擎（核心调度）
│   ├── routes/            # FastAPI 路由（114 个端点）
│   ├── services/          # 业务逻辑层（含 project_memory, short_term_memory, choice_box）
│   ├── tools/             # 工具集合（24 个）
│   ├── tests/             # 单元测试（94 个文件）
│   └── main.py            # 应用入口
├── frontend/              # React + TypeScript + Vite
│   ├── src/               # 前端源码
│   ├── index.html         # 主界面
│   └── monitor.html       # 监控面板
├── docs/                  # PRD + ADR（28 篇）
├── Dockerfile
├── docker-compose.yml
├── run.sh                 # Linux/macOS 启动器
└── run.bat                # Windows 启动器
```

## 🤖 Agent 角色

| 角色 | 职责 | Gate |
|------|------|------|
| `analyst` | 需求分析，输出 `docs/prd/<subject>.md` | 人工审批 |
| `architect` | 架构设计，输出 `docs/specs/<subject>.md` | 自动 |
| `developer` | 编写代码 + 单元测试，代码写 `src/` | 自动 |
| `tester` | 测试执行，输出 `reports/tests/<timestamp>--<subject>.md` | 自动 |
| `release` | 版本管理，生成 CHANGELOG.md + Git tag | 人工审批 |
| `valet` | 助理，协助其他 Agent，处理临时任务 | — |

每个 Agent 有独立的 SOUL（灵魂）、角色、工具白名单、LLM 模型配置。

### Pipeline 流程

默认 Pipeline：`analysis → architecture → development → testing → release`

配置文件：`~/.catown/config/pipelines.json`

### 内置工具（24 个）

| 类别 | 工具 |
|------|------|
| 文件操作 | `read_file`, `write_file`, `list_files`, `delete_file`, `search_files`, `open_file_for_user` |
| 代码执行 | `execute_code`（Python + Node.js 沙箱）, `run_shell` |
| 网络 | `web_search`, `web_fetch`, `browser`, `screenshot` |
| 协作 | `send_direct_message`, `consult_agent`, `delegate_task`, `broadcast_message`, `invite_agent`, `list_agents`, `list_collaborators`, `check_task_status` |
| 记忆 | `retrieve_memory`, `save_memory` |
| 其他 | `github_manager`, `skill_manager`, `user_file_interaction` |

## 🧠 Skills 体系

Skills 按三级渐进披露，按需注入 Agent 上下文（ADR-008）：

| 级别 | 内容 | 场景 |
|------|------|------|
| `hint` | 一句话提示 | 默认注入，不占太多 token |
| `guide` | 简要指南 | Agent 需要更多指导时 |
| `full` | 完整文档 | 复杂任务，需要详细参考 |

内置 Skills：代码生成、单元测试、重构等。可通过 `skill_manager` 从 marketplace 安装更多。

## 🛡️ 安全机制

- **工具白名单**：Agent 仅能调用 `agents.json` 中声明的工具
- **路径校验**：统一 `_validate_path()` — symlink 解析 + 目录穿越检测 + `.catown/` 保护
- **Workspace 隔离**：每个项目独立目录，Agent 无法访问其他项目数据
- **工具执行授权**：可配置 per-tool 授权策略（自动 / 需审批 / 禁止）
- **审批队列**：敏感操作需 BOSS 审批，支持 TTL 自动过期

## 📊 可观测性

- **Pipeline Dashboard**：实时进度、Agent 通信、产出物管理
- **Monitor 面板**：`/monitor` — Agent 状态、事件流、网络监控
- **上下文压缩诊断**：`/context-compactions` — 查看每次压缩的原因、scope 分布、token 节省
- **工具输出过滤**：工具结果 metadata 包含 `output_filter` 字段，可查过滤节省量
- **LLM 调用审计**：`LLMCall` / `ToolCall` / `Event` 表全量记录

## 🧪 测试

```bash
# 运行全部测试
cd backend && python -m pytest -v

# 并行加速
cd backend && python -m pytest -n auto --dist loadscope

# 运行特定测试
cd backend && python -m pytest tests/test_adr028_context_compression.py -v

# 仅收集（不执行）
cd backend && python -m pytest --collect-only
```

- 测试文件：94 个
- 测试框架：pytest + pytest-asyncio + pytest-xdist

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CATOWN_HOME` | `~/.catown` | 运行时根目录 |
| `CATOWN_RELOAD` | `1` | uvicorn 热重载 |
| `RUN_HOST` | `0.0.0.0` | 监听地址 |
| `RUN_PORT` | `8000` | 监听端口 |
| `LLM_API_KEY` | — | LLM API 密钥 |
| `LLM_BASE_URL` | — | LLM API 地址 |
| `LLM_MODEL` | — | 默认模型 ID |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `DATABASE_URL` | SQLite | 数据库 URL（支持 PostgreSQL） |
| `MONITOR_NETWORK_RETENTION_HOURS` | `168` | 网络事件保留天数 |
| `CORS_ORIGINS` | `*` | CORS 允许源 |

## 📁 运行时目录

首次启动后，`~/.catown/` 目录结构：

```
~/.catown/
├── .env                      # 环境变量（LLM 密钥等）
├── config/
│   ├── agents.json           # Agent 角色 + LLM 配置
│   ├── pipelines.json        # Pipeline 流程定义
│   ├── skills.json           # Skills 注册表
│   └── skill_marketplaces.json
├── state/
│   ├── catown.db             # SQLite 数据库
│   └── tee/                  # 工具输出原始日志（ADR-028）
├── projects/                 # 项目数据
└── workspaces/               # Agent 工作目录
```

## 📋 实施进度

| 模块 | 状态 | 日期 |
|------|------|------|
| 数据模型 + 配置 | ✅ 完成 | 2026-04-07 |
| Pipeline 引擎 + API | ✅ 完成 | 2026-04-07 |
| 前端 Dashboard | ✅ 完成 | 2026-04-07 |
| Git 集成 + 产出物查看 | ✅ 完成 | 2026-04-07 |
| Agent SOUL 体系 | ✅ 完成 | 2026-04-08 |
| Skills 三级注入 (ADR-008) | ✅ 完成 | 2026-04-10 |
| 工具白名单 + Workspace 隔离 | ✅ 完成 | 2026-04-10 |
| 知识图谱 Skill 定义 (ADR-004) | ✅ 完成 | 2026-04-10 |
| 上下文压缩修复 (ADR-028) | ✅ 完成 | 2026-05-25 |
| Chat Runtime 状态机加固 (ADR-027) | ✅ 完成 | 2026-05-25 |
| 短期记忆 | ✅ 完成 | 2026-05-25 |
| 项目记忆 | ✅ 完成 | 2026-05-25 |
| Choice Box 交互组件 | ✅ 完成 | 2026-05-25 |
| Agent 操作可视化 | ✅ 完成 | 2026-05-25 |
| 聊天框输入体验 | ✅ 完成 | 2026-05-25 |
| 工具授权流程 | ✅ 完成 | 2026-05-26 |
| 审计日志 | ✅ 完成 | 2026-05-25 |
| 知识图谱集成（接 Choice Box） | ✅ 完成 | 2026-05-26 |
| 长期记忆 (ChromaDB) | ✅ 完成 | 2026-05-26 |
| 睡眠整理调度器 | ✅ 完成 | 2026-05-26 |
| OMNI 多模态集成 | ✅ 完成 | 2026-05-26 |
| UI/UX Pro Max Phase 2 | ⏳ 待做 | — |
| Knowledge Graph 进阶 | ⏳ 待做 | — |

## 📖 文档

| 文档 | 说明 |
|------|------|
| [PRD](docs/PRD.md) | 产品需求文档 |
| [Architecture](docs/Architecture.md) | 架构设计 |
| [Business Flow](docs/Business-Flow.md) | 业务流程 |
| [ADR 索引](docs/) | 28 篇架构决策记录 |
| [Wiki](https://github.com/uplusplus/catown.wiki.git) | ADR 索引、开发日志 |

### ADR 列表（28 篇）

| ADR | 主题 |
|-----|------|
| ADR-004 | 知识图谱 |
| ADR-008 | Skills 渐进式披露 |
| ADR-009 | 上下文压缩策略 |
| ADR-012 | LLM 会话上下文管理 |
| ADR-026 | 动态上下文控制触发流 |
| ADR-027 | Chat Runtime 状态机加固 |
| ADR-028 | 上下文压缩提前触发修复 |
| ... | 其余见 `docs/` 目录 |

## 📄 License

MIT License
