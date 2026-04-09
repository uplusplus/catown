# ADR-001: 记忆系统架构决策

**日期**: 2026-04-09
**状态**: 已确认
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 需要为每个 Agent 实现三层记忆体系（短期/项目/长期），包含语义检索、泛化判定、BOSS 确认、睡眠整理等能力。需要决定是完全自研还是引入开源方案。

## 候选方案分析

### 方案 A：纯自研

从零实现 embedding、向量索引、语义检索、记忆管理全部逻辑。

| 维度 | 评估 |
|------|------|
| 控制力 | ✅ 完全掌控，三层隔离和判定矩阵可精确实现 |
| 工作量 | ❌ 语义检索从零实现需 1-2 周，且质量难保证 |
| 部署 | ✅ 无额外依赖 |
| 风险 | ❌ embedding 质量和检索效果需要大量调优 |

### 方案 B：引入通用开源记忆引擎

引入 Mem0、Letta/MemGPT、Zep、Cognee 等方案。

| 方案 | 特点 |
|------|------|
| Mem0 | 开源记忆层，支持语义记忆管理，需配 API 服务 + embedding 模型 + 向量库 |
| Letta/MemGPT | 管理上下文窗口+记忆，偏重对话场景 |
| Zep | 独立记忆服务，功能较全 |
| Cognee | 记忆引擎，支持多数据源 |

| 维度 | 评估 |
|------|------|
| 控制力 | ❌ 通用方案不理解三层隔离、泛化判定、Choice Box 确认等 Catown 特有逻辑 |
| 适配成本 | ❌ 需要大量适配工作，可能比自研还慢 |
| 部署 | ❌ 引入额外服务/依赖，跟"单进程 Docker"定位冲突 |
| 记忆模型 | ❌ 多数方案的记忆是扁平的（存/查），缺乏分层和决策流程 |
| 成熟度 | ✅ 语义检索质量有保障 |

### 方案 C：混合方案（推荐）

核心编排逻辑自研，存储层用轻量开源组件。

```
Catown Memory System
│
├── 记忆编排层（自研）
│   ├── 三层记忆管理逻辑
│   ├── 泛化判定矩阵
│   ├── Choice Box 决策流程
│   ├── 睡眠整理调度器
│   └── 记忆生命周期管理
│
├── 短期记忆
│   └── 内存 dict + JSON 文件落盘
│
├── 项目记忆
│   └── Markdown 文件 + grep/全文检索
│       └── projects/{id}/.catown/memory/*.md
│
└── 长期记忆
    ├── 写入：embedding 生成（sentence-transformers）
    ├── 存储：向量数据库（ChromaDB）
    └── 检索：相似度查询
```

| 维度 | 评估 |
|------|------|
| 控制力 | ✅ 编排层自研，三层逻辑完全可控 |
| 工作量 | ✅ 自研 2-3 天 + ChromaDB 集成半天 |
| 部署 | ✅ ChromaDB 嵌入式，零额外服务 |
| 语义质量 | ✅ ChromaDB + 成熟 embedding 模型 |
| 复杂度 | ✅ 最小化依赖，贴合 Catown 定位 |

## 决策

**采用方案 C：混合方案。**

## 长期记忆存储选型对比

| 方案 | 类型 | 部署 | 与 Catown 适配度 |
|------|------|------|------------------|
| **ChromaDB** ⭐ | 嵌入式向量库 | `pip install`，单文件持久化 | ✅ 最佳：零部署、Python 原生、collection 隔离 |
| SQLite + sqlite-vec | 扩展 | 随现有 DB | ⚠️ 可行：统一存储，但功能较新 |
| Qdrant | 独立服务 | Docker 容器 | ❌ 过重：额外服务实例 |
| FAISS | C++ 库 | 编译依赖 | ❌ 缺存储层：需自行封装 |
| Pinecone | 云端 SaaS | API 调用 | ❌ 外部依赖 + 费用 + 数据出境 |

**ChromaDB 选择理由**：
- 纯 Python，`pip install chromadb` 即可
- 持久化到本地目录，跟项目 workspace 放一起
- 天然支持 collection 隔离（每个 Agent 一个 collection，每个项目一个 namespace）
- Python API 5 行代码实现写入+检索
- 与 Catown "单进程 Docker 部署"定位一致
- 适合万级记忆规模，Catown 场景足够

## 记忆各层实现策略

### 短期记忆（最简单）

- 内存 dict，Stage 生命周期内驻留
- Stage 结束时 JSON 落盘到 `.catown/stage_context/`
- 无需额外依赖

### 项目记忆（中等）

- Markdown 文件存储：`decisions.md`、`conventions.md`、`issues.md`
- 全文检索用 grep 即可（项目级数据量不大）
- 无需额外依赖

### 长期记忆（需语义能力）

- Embedding：`sentence-transformers`（`all-MiniLM-L6-v2`，够用且轻量）
- 存储：ChromaDB 持久化到 `configs/agents/{agent_name}/memory/chroma/`
- 检索：ChromaDB query API，按相似度返回 top-k

## 睡眠整理实现

不需要开源方案，就是 Python 异步任务：

1. 定时触发（cron 或 idle 检测）
2. 调 LLM 做摘要 / 泛化判定
3. 调 ChromaDB 写入 / 删除
4. 需要 BOSS 确认时发 Choice Box

## 工作量估算

| 模块 | 工作量 | 依赖 |
|------|--------|------|
| 短期记忆 | 0.5 天 | 无 |
| 项目记忆 | 0.5 天 | 无 |
| 长期记忆（含 ChromaDB） | 1.5 天 | chromadb, sentence-transformers |
| 睡眠整理调度器 | 0.5 天 | 无 |
| Choice Box 集成 | 0.5 天 | 无 |
| **合计** | **3.5 天** | — |

## 不采用的方案

- **纯自研**：语义检索从零实现需 1-2 周，收益不值得投入
- **Mem0/Zep/Letta**：通用方案不理解 Catown 的三层隔离和决策流程，适配成本可能比自研还高，且引入额外服务部署

## 后续跟进

- [ ] 实现顺序：先短期+项目记忆（零依赖跑通流程）→ 再加长期记忆（ChromaDB）
- [ ] Embedding 模型选型验证：对比 `all-MiniLM-L6-v2` 与 `bge-small-zh` 在中英文混合场景的效果
- [ ] ChromaDB 单机容量测试：万级 document 的检索延迟和存储占用
