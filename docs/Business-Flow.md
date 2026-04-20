# 对话驱动项目管理 — 架构设计讨论

## 一、核心实体关系

先回答你的两个问题，再展开架构。

---

## 问题1：Project 是否必须？层级关系？

**Project 是必须的，层级高于 Conversation。**

原因很简单：Conversation 是短暂的、任务导向的；而你需要一个**持久化的容器**来承载共享状态。没有 Project，多个 Conversation 之间就失去了协作的锚点。

```
Project (持久化容器)
 ├── 共享工作空间 (workspace)
 ├── 成员 / 权限
 ├── 全局状态
 │
 ├── Conversation A  (独立上下文, agents: [a1, a2])
 ├── Conversation B  (独立上下文, agents: [b1, b3])
 └── Conversation C  (独立上下文, agents: [c1])
```

**类比帮助理解：**

| 类比 | Project | Conversation | Workspace |
|------|---------|--------------|-----------|
| Git | 仓库 (Repository) | 分支 (Branch) | 工作目录 |
| 办公室 | 项目组 | 一场会议 | 共享白板/文件柜 |
| 写作 | 一本书 | 一个章节的写作会话 | 共享素材库 |

关键洞察：**Conversation 是 Project 的工作切面**。同一个项目里，一个 Conversation 在改代码，另一个在跑测试，第三个在写文档 —— 它们共享同一份工作空间，但各自有独立的讨论上下文。

---

## 问题2：共享工作空间 vs 独立上下文 — 如何处理

这是整个设计的核心难点。需要拆成两个层面理解：

### 概念分离

```
┌─────────────────────────────────────────────────┐
│                  Project                         │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │         Shared Workspace (事实层)         │   │
│  │  文件、数据库、状态 —— 所有Conversation可见 │   │
│  └──────────────────────────────────────────┘   │
│     ▲ read/write    ▲ read/write                │
│     │                │                           │
│  ┌──┴──────┐   ┌────┴───────┐                   │
│  │ Conv A   │   │ Conv B     │                   │
│  │ 上下文:   │   │ 上下文:     │                   │
│  │ - 对话历史│   │ - 对话历史  │  ← 互不可见       │
│  │ - 意图追踪│   │ - 意图追踪  │                   │
│  │ - Agent记忆│  │ - Agent记忆 │                   │
│  └──────────┘   └────────────┘                   │
└─────────────────────────────────────────────────┘
```

**关键区分：**
- **工作空间** = 项目的**事实状态**（文件内容、任务列表、数据）—— 共享
- **对话上下文** = **认知状态**（对话历史、意图、推理过程、Agent记忆）—— 隔离

就像两个人可以看同一份文件（共享工作空间），但各自的思考和讨论是独立的（独立上下文）。

### 并发写入的处理策略

多个 Conversation 同时写工作空间，有三种策略：

**策略A：乐观锁 + 冲突解决（推荐）**
```
Conversation A 读取 config.yml
Conversation B 读取 config.yml
Conversation A 写入 config.yml (version 2)  ← 成功
Conversation B 写入 config.yml (version 2)  ← 冲突！
  → 系统通知 Conv B 的 agents: "文件已被修改，需要合并"
  → Agent 自行决定 merge/rebase/overwrite
```
适合对话驱动的场景，因为 Agent 本身有智能处理冲突的能力。

**策略B：资源级锁**
```
Conversation A 锁定 /src/auth.py → 修改 → 释放
Conversation B 尝试锁定 /src/auth.py → 等待或提示
```
适合代码编辑等需要原子性的操作。

**策略C：事件溯源（Event Sourcing）**
```
所有工作空间变更 → 写入 Operation Log
  op_001: Conv-A, write, /src/main.py, content_hash_xxx
  op_002: Conv-B, create, /docs/api.md, content_hash_yyy
  op_003: Conv-A, delete, /tmp/old.py

每个 Conversation 看到的是: 基线 + 自己的操作 + 已提交的他人操作
```
最强大，但复杂度高。适合需要 undo/redo/audit 的场景。

---

## 二、完整数据模型

```python
# ============ 持久层 ============

class Project:
    id: str
    name: str
    workspace: Workspace          # 共享工作空间
    members: list[Member]         # 人和Agent
    created_at: datetime

class Workspace:
    """项目的事实层 — 共享"""
    id: str
    project_id: str
    files: FileSystem             # 虚拟文件系统 / 对象存储
    state: dict                   # 项目级状态（任务列表、配置等）
    operation_log: list[Operation]  # 变更日志

class Operation:
    """工作空间的原子操作记录"""
    id: str
    conversation_id: str          # 谁发起的
    agent_id: str                 # 哪个Agent执行的
    type: OpType                  # create/read/update/delete
    target: str                   # 操作目标路径
    timestamp: datetime
    content_hash: str

# ============ 会话层 ============

class Conversation:
    id: str
    project_id: str               # 归属项目
    name: str                     # "前端开发"、"测试"、"文档撰写"
    topic_type: str               # 事务类型标签
    
    # 独立上下文
    context: ConversationContext
    
    # Agent团队
    agents: list[AgentInstance]
    
    created_at: datetime
    status: ConvStatus            # active/paused/completed

class ConversationContext:
    """认知层 — 每个Conversation独有"""
    conversation_id: str
    messages: list[Message]       # 对话历史
    working_memory: dict          # 当前推理状态
    intent_stack: list[Intent]    # 意图追踪
    agent_memories: dict[str, AgentMemory]  # 每个Agent的私人记忆

class AgentInstance:
    """Conversation中的Agent实例"""
    id: str
    agent_definition: AgentDef    # 引用Agent定义
    role_in_conversation: str     # 在这个Conversation中的角色
    private_memory: AgentMemory   # Agent私人记忆（不共享）
    active_tools: list[Tool]
```

---

## 三、工作空间同步机制（核心架构）

```
                    ┌─────────────┐
                    │  Workspace  │
                    │   (事实层)   │
                    └──────▲──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴────┐ ┌────┴─────┐
        │ Sync Layer │ │Sync Lyr│ │ Sync Lyr │
        └─────▲─────┘ └───▲────┘ └────▲─────┘
              │            │            │
        ┌─────┴─────┐ ┌───┴────┐ ┌────┴─────┐
        │ Conv A     │ │Conv B  │ │ Conv C   │
        │ 上下文隔离  │ │上下文隔离│ │ 上下文隔离 │
        └───────────┘ └────────┘ └──────────┘
```

### Sync Layer 的职责

```python
class SyncLayer:
    """每个Conversation拥有一个SyncLayer实例"""
    
    def __init__(self, conversation_id, workspace):
        self.conversation_id = conversation_id
        self.workspace = workspace
        self.last_sync_version = 0  # 上次同步的版本号
        self.local_changes = []     # 本地未提交的变更
    
    def read(self, path: str):
        """读取工作空间 — 直接读最新版本"""
        return self.workspace.files.read(path)
    
    def write(self, path: str, content):
        """写入 — 先检查冲突"""
        remote_version = self.workspace.get_version(path)
        if remote_version > self.last_sync_version:
            # 有人改过，需要通知Agent
            diff = self.workspace.get_changes_since(self.last_sync_version, path)
            raise ConflictDetected(path, diff)
        
        self.local_changes.append(Operation(
            conversation_id=self.conversation_id,
            type=OpType.UPDATE,
            target=path,
            content=content
        ))
    
    def commit(self):
        """将本地变更提交到工作空间"""
        for op in self.local_changes:
            self.workspace.apply(op)
        self.last_sync_version = self.workspace.version
        self.local_changes.clear()
    
    def sync_incoming(self):
        """获取其他Conversation产生的变更"""
        changes = self.workspace.get_changes_since(self.last_sync_version)
        # 过滤掉自己的变更
        other_changes = [c for c in changes 
                        if c.conversation_id != self.conversation_id]
        return other_changes
```

### Agent 的工作流

```
用户: "把登录接口改成支持OAuth"

Agent (Conv-A) 的执行流程:
  1. sync_incoming() → 检查有没有其他人改了相关文件
  2. read("/src/auth.py") → 读取当前内容
  3. [推理、生成代码]
  4. write("/src/auth.py", new_content) → 检查冲突
  5. commit() → 提交变更
  6. 记录到 ConversationContext（对话历史 + 意图）
```

---

## 四、一个具体场景

假设你在做一个电商项目：

```
Project: "电商系统 v2"
├── Workspace:
│   ├── /src/          (代码)
│   ├── /docs/         (文档)
│   ├── /tests/        (测试)
│   └── task_board     (任务列表状态)
│
├── Conversation: "用户模块开发"
│   Agents: [架构师Agent, 编码Agent]
│   Context: 讨论了用户模型设计、决定用JWT...
│   ↔ 读写 /src/users/, /docs/user-api.md
│
├── Conversation: "支付集成"
│   Agents: [编码Agent, 安全审查Agent]
│   Context: 对接支付宝API、处理回调...
│   ↔ 读写 /src/payments/
│
└── Conversation: "项目管理"
    Agents: [PM Agent]
    Context: 跟踪进度、分配任务...
    ↔ 读写 task_board, /docs/sprint-plan.md
```

三个 Conversation 各自独立思考，但都操作同一个项目的工作空间。PM Agent 在 Conversation-3 里更新了任务状态，Conversation-1 的 Agent 下次 sync 时就能看到。

---

## 五、关于"通过会话创建项目"

这是一个 chicken-and-egg 的问题：用户没有 Project 的时候，第一个 Conversation 在哪里？

**解决方案：Conversation-0（种子会话）**

```
用户打开软件 → 自动创建一个"无主"Conversation
  用户: "帮我创建一个电商项目"
  Agent: 创建 Project → 创建 Workspace → 返回 project_id
  这个 Conversation 自动归属到新建的 Project
```

所以 Project 的创建流程是：
```
Conversation(无项目) → 用户意图 → 创建Project → Conversation归属到Project
```

**Project 是必须的**，但用户不需要手动创建它 —— 第一个 Conversation 天然触发 Project 的诞生。

---

## 总结回答你的两个问题

| 问题 | 结论 |
|------|------|
| Project 是否必须？ | **必须**。它是共享状态的容器，没有它多Conversation无法协作。层级上 Project > Conversation |
| 工作空间 vs 独立上下文？ | **工作空间是事实层（共享），上下文是认知层（隔离）**。通过 SyncLayer 中间层处理并发，利用 Agent 的智能处理冲突 |

如果你要深入某个方面（比如 Agent 的具体调度、冲突解决的 UI 交互、或者权限模型），可以继续聊。




## TODO LIST
下面我直接把这件事拆成一版可以开工的开发任务清单，尽量贴近你当前这个仓库的结构来讲。

**目标**
先把产品主骨架跑通：

1. 首次进入默认打开一个 `standalone chat`
2. `Chats +` 只新建普通 chat
3. 用户可从按钮或当前 chat 创建 project
4. 创建 project 时自动创建 hidden main chat 和 workspace 关联
5. hidden project chat 不出现在 `Chats` 列表
6. 点 project 时直接进入它的 hidden main chat

---

**第一批改动：后端模型**
建议优先做数据库层，不然前后端都会写成临时逻辑。

重点文件：
- [database.py](C:/Users/sun/AI/catown/backend/models/database.py)

建议新增字段：

`Chatroom`
- `session_type = Column(String, default="standalone")`
- `is_visible_in_chat_list = Column(Boolean, default=True)`
- `source_chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=True)`

`Project`
- `default_chatroom_id = Column(Integer, ForeignKey("chatrooms.id"), nullable=True)`
- `workspace_path = Column(String, nullable=True)`  
  如果你准备把目录直接作为项目工作目录，这个字段很有必要

为什么这样改：
- 你当前系统已经广泛使用 `chatroom`
- 先把 `chatroom` 视作 `session`
- 这是最小成本过渡，不用一上来新建 `sessions` 表

如果你们已经有 migration 体系，建议补 migration；如果现在还是启动建表，也至少先把模型改正确。

---

**第二批改动：后端服务层**
不要把这些流程继续堆在 route 里，建议新增一个 session/service 层。

建议新增文件：
- `backend/services/session_service.py`

建议最少有这几个方法：

`SessionService`
- `get_or_create_default_standalone_chat()`
- `create_standalone_chat(title: str | None = None)`
- `list_visible_chats()`
- `create_hidden_project_chat(project_id: int, source_chatroom_id: int | None = None)`

`ProjectCreationService`
- `create_project_from_chat(source_chatroom_id, name, description, agent_names, workspace_path=None)`
- `create_project_directly(name, description, agent_names, workspace_path=None)`

核心规则要落进服务层：
- standalone chat 保留
- project 创建时新建 hidden chat
- hidden chat 不出现在 chats 列表
- `project.default_chatroom_id` 必须写入
- 如有来源 chat，复制上下文

---

**第三批改动：后端接口**
你当前真正生效的老接口在：
- [api.py](C:/Users/sun/AI/catown/backend/routes/api.py)

建议不要继续把“chat 列表”和“project 列表”混在一个 `projects` 视角里，要补出独立 chat 接口。

建议新增接口：

`GET /api/chats`
- 返回所有 `session_type=standalone && is_visible_in_chat_list=true` 的 chatrooms

`POST /api/chats`
- 创建 standalone chat

`POST /api/projects/from-chat`
- 入参：
  - `source_chatroom_id`
  - `name`
  - `description`
  - `agent_names`
  - `workspace_path` 可选
- 动作：
  - 创建 project
  - 创建 hidden project chat
  - 复制上下文
  - 返回 project + default_chatroom_id

`POST /api/projects`
- 保留
- 但逻辑要改成创建 project 时自动附带 hidden project chat
- 如果是按钮直接创建项目，也应该和 from-chat 走同一套底层服务

`GET /api/projects/{id}/chat`
- 返回该项目的 hidden main chat

如果想保守一点，也可以先不单独加 `GET /api/projects/{id}/chat`，而是在 `GET /api/projects` 里把 `default_chatroom_id` 返回给前端。

---

**第四批改动：返回模型**
你现在前端 `ProjectSummary` 里只有：
- `id`
- `name`
- `description`
- `status`
- `chatroom_id`
- `agents`

这已经不够用了。建议后端先把返回结构改成更明确：

项目返回值建议新增：
- `default_chatroom_id`
- `workspace_path` 可选
- `created_from_chatroom_id` 可选

chat 返回值建议包含：
- `id`
- `title`
- `session_type`
- `is_visible_in_chat_list`
- `project_id`
- `agent_count`
- `updated_at`

---

**第五批改动：前端 API 层**
重点文件：
- [client.ts](C:/Users/sun/AI/catown/frontend/src/api/client.ts)
- [types.ts](C:/Users/sun/AI/catown/frontend/src/types.ts)

建议新增类型：

```ts
export type ChatSummary = {
  id: number;
  title: string;
  session_type: "standalone" | "project-bound";
  is_visible_in_chat_list: boolean;
  project_id?: number | null;
  updated_at?: string;
};

export type ProjectSummary = {
  id: number;
  name: string;
  description?: string | null;
  status: string;
  default_chatroom_id: number;
  agents: AgentInfo[];
  workspace_path?: string | null;
};
```

建议新增 API：

- `getChats()`
- `createChat()`
- `createProjectFromChat(payload)`
- `getProjectChat(projectId)`  
  如果后端有这个接口

这里的原则是：
前端不要再把 `projects` 当成 `chats` 用，也不要把 `chatroom_id` 继续混当“任何会话入口”。

---

**第六批改动：前端应用状态**
重点文件：
- [App.tsx](C:/Users/sun/AI/catown/frontend/src/App.tsx)

建议从现在的状态继续拆：

当前你有：
- `projects`
- `selectedProjectId`
- `messages`

建议补成：

- `chats`
- `selectedChatId`
- `selectedProjectId`
- `activeView` 或继续用 `activeTab`
- `messages`
- `projectChatIdMap` 可选，或者直接从项目对象取 `default_chatroom_id`

核心逻辑要改成：

进入系统时：
- 先拉 `chats`
- 如果没有 visible chats，调用 `createChat()`
- 默认选中第一个 chat
- 拉这个 chat 的消息

点击 `Chats +`
- 调 `createChat()`
- 创建后选中新 chat
- 打开该 chat 消息

点击某个 project
- 读取 `project.default_chatroom_id`
- 打开这个 hidden chat
- 同时把当前上下文切到 project 态

这一步最重要的是：
`selectedChatId` 要成为一等状态，不能再完全依附 `selectedProjectId`。

---

**第七批改动：左侧 UI 结构**
重点文件：
- [AppSidebar.tsx](C:/Users/sun/AI/catown/frontend/src/components/AppSidebar.tsx)

建议左侧最终数据结构是：

- `Chats`
  - 列出 visible standalone chats
  - `+` 创建 standalone chat
- `Projects`
  - 列出 projects
  - `+` 创建 project

你前面刚改过 `Projects +` 选目录直接创建项目，这条可以保留。  
但从业务上说，`Chats +` 和 `Projects +` 现在要彻底分工：

- `Chats +` → 创建 standalone chat
- `Projects +` → 选目录并创建 project

这是很好的。

---

**第八批改动：从 chat 创建 project**
这里是本次开发的关键路径。

建议先做按钮触发，再做自然语言触发。

MVP 按钮方案：
- 当前打开 standalone chat 时，页面里有一个 `Create Project` 入口
- 点击后：
  - 可弹出轻量确认框
  - 填项目名，或者直接从目录名取
  - 调 `createProjectFromChat`

自然语言方案先别做复杂：
- 先只保留一个显式入口
- 之后再做“聊天中识别为建项目意图”
- 识别到了，本质仍调用同一个接口

这样可以避免一开始就把意图识别、对话解析和主流程耦死。

---

**第九批改动：目录选择**
你刚刚要求的“选目录立即创建项目”，当前前端已经有浏览器目录选择能力雏形。

建议下一步把它分成两层：

MVP：
- 前端 `showDirectoryPicker()`
- 取目录名做 project name
- `workspace_path` 先空，或者前端不上传真实路径
- 只完成产品交互

完整版：
- 如果桌面环境允许，把选中的目录路径真正传给后端
- 写入 `projects.workspace_path`
- 后续 workspace 真实绑定到该路径

这里有个现实点：
浏览器标准的 `showDirectoryPicker()` 通常拿不到可直接持久化的本地绝对路径。  
所以如果你想“真正保存本地目录路径”，大概率要走桌面壳能力或后端本地文件选择能力，而不是纯 Web API。

因此建议：
- 先把“交互和业务模型”做对
- 再单独决定目录路径持久化实现

---

**第十批改动：消息复制**
从 standalone chat 孵化 project 时，至少需要一个简单上下文迁移策略。

建议先做最轻版本：
- 复制最近 `N` 条消息到 hidden project chat
- 再加一条系统消息：

```text
This project was created from standalone chat #123.
The following messages were copied as seed context.
```

后续增强版再加：
- 自动摘要
- 目标提取
- 约束提取
- agent roster 建议

---

**第十一批改动：测试**
这个改动跨业务主链路，测试一定要补。

建议至少覆盖：

后端：
- 创建默认 standalone chat
- 创建 standalone chat
- 创建 project 时自动创建 hidden project chat
- hidden project chat 不在 `GET /api/chats` 中返回
- `GET /api/projects` 返回 `default_chatroom_id`
- 从 chat 创建 project 时原 chat 保留

前端：
- 首次进入无 chats 时自动创建默认 chat
- `Chats +` 只创建 chat
- `Projects +` 只创建 project
- 点 project 打开 hidden main chat
- chat 和 project 两套列表互不污染

---

**推荐实施顺序**
为了减少返工，我建议按这个顺序做：

1. 改后端模型字段
2. 改后端项目创建服务
3. 增加 `/api/chats` 与 `createProjectFromChat`
4. 改前端 API/types
5. 改 `App.tsx` 的状态管理
6. 改左侧 `Chats / Projects` 两套列表
7. 接通 `Chats +`
8. 接通 `Projects +`
9. 接通“从当前 chat 创建 project”
10. 补测试

---

**我建议的第一步落地范围**
如果我们下一步就开始真的改代码，我建议这轮只做最核心的第一批：

- 后端新增 `chatrooms.session_type`
- 后端新增 `chatrooms.is_visible_in_chat_list`
- 后端新增 `projects.default_chatroom_id`
- 新增 `GET /api/chats`
- 新增 `POST /api/chats`
- 修改 `POST /api/projects`，让它自动创建 hidden project chat
- 前端左栏真正拆成 `Chats` 和 `Projects`

这一步做完，主骨架就成立了。

如果你愿意，我下一步可以直接开始实施这第一批改动，不再只是讨论。
