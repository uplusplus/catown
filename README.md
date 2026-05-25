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

也可以编辑 `${CATOWN_HOME:-~/.catown}/config/agents.json` 做更细粒度的 per-agent 模型配置：

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

### 运行时目录结构

首次启动后，`~/.catown/` 目录结构如下：

```
~/.catown/
├── .env                  # 环境变量（LLM 密钥等）
├── config/
│   ├── agents.json       # Agent 角色 + LLM 配置
│   ├── pipelines.json    # Pipeline 流程定义
│   └── skills.json       # Skills 注册表
├── state/
│   ├── catown.db         # SQLite 数据库
│   └── tee/              # 工具输出原始日志（ADR-028）
├── projects/             # 项目数据
└── workspaces/           # Agent 工作目录
```

### Docker（可选）

```bash
# 构建
docker build -t catown .

# 运行
docker run -p 8000:8000 \
  -e LLM_API_KEY=sk-xxx \
  -e LLM_BASE_URL=https://api.openai.com/v1 \
  -e LLM_MODEL=gpt-4o \
  catown
```

### 开发模式

```bash
# 后端热重载（默认开启）
CATOWN_RELOAD=1 ./run.sh

# 仅运行后端测试
cd backend && python -m pytest -v

# 并行测试加速
cd backend && python -m pytest -n auto --dist loadscope

# 运行特定测试
cd backend && python -m pytest tests/test_adr028_context_compression.py -v
```

### 环境变量

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
| `MONITOR_NETWORK_RETENTION_HOURS` | `168` | 网络事件保留天数 |

`/monitor/network` 的网络事件会落盘到 `${CATOWN_HOME:-~/.catown}/state/catown.db`，默认仅保留最近 7 天（`MONITOR_NETWORK_RETENTION_HOURS=168`）。

## 📁 项目结构

```
catown/
├── backend/
│   ├── agents/          # Agent 核心（SOUL 体系、注册、协作）
│   ├── chatrooms/       # 聊天室系统
│   ├── configs/         # 配置模板（首次启动复制到 ~/.catown/config/）
│   ├── llm/             # LLM 客户端（OpenAI 兼容，per-agent 配置）
│   ├── models/          # 数据库模型（含 Pipeline 表）
│   ├── pipeline/        # Pipeline 引擎（核心）
│   ├── routes/          # API 路由
│   ├── tools/           # 工具集合（21 个：文件操作、代码执行、浏览器、搜索、抓取等）
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
| `tester` | 测试执行，输出 `reports/tests/<timestamp>--<subject>.md` | 自动 |
| `release` | 版本管理，输出 CHANGELOG.md | 人工审批 |
| `assistant` | 打杂，协助其他 Agent | — |

每个 Agent 有独立的 SOUL（灵魂）、角色、工具白名单、LLM 模型配置。

### 内置工具（21 个）

| 类别 | 工具 |
|------|------|
| 文件操作 | `read_file`, `write_file`, `list_files`, `delete_file`, `search_files` |
| 代码执行 | `execute_code`（Python + Node.js 沙箱） |
| 网络 | `web_search`（DuckDuckGo）, `web_fetch`（网页抓取） |
| 协作 | `send_direct_message`, `consult_agent`, `delegate_task`, `broadcast_message` |
| 其他 | `retrieve_memory`, `save_memory`, `browser`, `screenshot`, `github_manager` |

## 🛡️ 安全机制

- **工具白名单**：Agent 仅能调用 `agents.json` 中声明的工具
- **路径校验**：统一 `_validate_path()` — symlink 解析 + 目录穿越检测 + `.catown/` 保护
- **Workspace 隔离**：每个项目独立目录，Agent 无法访问其他项目数据

## 📊 测试状态

- Pipeline 测试：27/27 ✅（含 8 个安全专项测试）
- E2E 集成测试：35/35 ✅

### 测试执行

默认后端测试：

```bash
python -m pytest
```

并行提速（推荐先用保守并发，尤其是 Windows 本地环境）：

```bash
python -m pytest -n 2 --dist loadscope
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
| 短期记忆 | ⏳ 待做 | — |
| 项目记忆 | ⏳ 待做 | — |
| Choice Box 交互组件 | ⏳ 待做 | — |
| Agent 操作可视化 | ⏳ 待做 | — |
| 聊天框输入体验 | ⏳ 待做 | — |
| 工具临时授权流程 | ⏳ 待做 | — |
| 审计日志 | ⏳ 待做 | — |
| 知识图谱集成（接 Choice Box） | ⏳ 待做 | — |
| 长期记忆 (ChromaDB) | ⏳ 待做 | — |
| 睡眠整理调度器 | ⏳ 待做 | — |
| OMNI 多模态集成 | ⏳ 待做 | — |
| UI/UX Pro Max Phase 2 | ⏳ 待做 | — |
| Knowledge Graph 进阶 | ⏳ 待做 | — |

## 📖 文档

- [PRD（产品需求文档）](docs/PRD.md)
- [Wiki](https://github.com/uplusplus/catown.wiki.git) — ADR 索引、架构决策、开发日志

## 📄 License

MIT License
