# Session / Project Flow

## 目标

定义 Catown 的一套简洁业务流程，让用户可以先聊天，再在合适时机进入项目执行态，同时避免 `chat`、`project`、`workspace` 三者之间的职责混乱。

这版规则的核心目标是：

- 首次进入产品时，用户可以立即聊天，不被迫先建项目
- 普通聊天和项目执行态明确分离
- 项目一旦创建，必须具备自己的主会话和工作空间
- 项目内部会话不污染全局 `Chats` 列表

---

## 核心对象

系统包含 3 个核心对象：

### Session

用户的对话容器，分为两类：

- `standalone`
  普通聊天。用户显式创建，出现在 `Chats` 列表中。
- `project-bound`
  项目内部主聊天。系统自动创建，不出现在 `Chats` 列表中。

补充约束：

- 每个 `session` 都是一个多 agent 对话容器
- 一个 session 内可以同时加入多个 agent
- 用户与多个 agent 的协作发生在同一个 session 中
- `standalone` 与 `project-bound` 的区别在于是否绑定 project，而不是是否支持多 agent

### Project

项目级业务容器，负责承载：

- 项目名称与说明
- 项目状态
- 项目成员与 agent 关系
- 项目级任务和资产
- 默认主会话
- 默认工作空间

### Workspace

项目工作空间，属于 `Project`，不属于 `standalone session`。

它承载项目执行过程中的共享事实层，例如：

- 文件
- 任务状态
- 操作日志
- 项目级状态

---

## 实体关系

```text
User
├─ Sessions
│  ├─ standalone session A
│  ├─ standalone session B
│  └─ ...
└─ Projects
   ├─ Project A
   │  ├─ Workspace A
   │  └─ hidden project-bound session
   └─ Project B
      ├─ Workspace B
      └─ hidden project-bound session
```

关系约束：

- `standalone session` 不绑定 workspace
- `project-bound session` 必须属于某个 project
- 每个 project 默认拥有一个 workspace
- 每个 project 默认拥有一个 hidden main chat
- 每个 session 都可以挂载多个 agent

---

## 展示规则

### Chats

`Chats` 列表只展示 `standalone session`。

它不展示：

- `project-bound session`
- project
- workspace

### Projects

`Projects` 列表只展示 `project`。

它不展示：

- 普通 standalone session
- hidden project-bound session

### Project 内部主聊天

用户进入某个 project 后，系统默认打开该 project 的 hidden main chat。

这个会话存在于系统中，但不出现在全局 `Chats` 列表里。

无论是 `standalone session` 还是 `project-bound session`，都允许多个 agent 同时参与对话。

### Workspace

`Workspace` 不单独出现在全局导航里，只在 project 内部作为项目执行环境存在。

---

## 首次进入流程

用户首次进入系统时：

1. 系统自动创建一个默认 `standalone session`
2. 默认进入这个 chat
3. 当前没有 project
4. 当前没有 workspace

因此首页是一个“可直接聊天”的状态，而不是“必须先新建项目”的状态。

---

## 新建 Chat 流程

`Chats` 旁边的 `+` 号只负责一件事：

- 新建一个 `standalone session`

它不会：

- 创建 project
- 创建 workspace
- 触发项目执行态

但它可以：

- 预置或动态加入多个 agent
- 在一个 chat 内形成多 agent 协作讨论

这样 `Chats +` 的含义对用户始终稳定：

“新建一个聊天。”

---

## 新建 Project 流程

用户可以通过两种方式创建项目：

1. 在 chat 对话中发起
2. 点击显式的新建项目入口发起

无论从哪个入口进入，系统都执行同一条业务流程：

1. 创建 `Project`
2. 创建 `Workspace`
3. 创建一个 `project-bound session`
4. 将该 session 设为 project 默认主会话
5. 跳转到该 project

创建成功后，系统必须同时拥有：

- 一个 project
- 一个 workspace
- 一个 hidden main chat

这是一条强约束，不做可选。

同时，这个 hidden main chat 仍然是一个多 agent session，可以承载项目内的多 agent 协作。

---

## 从 Chat 孵化为 Project

当用户在 `standalone session` 中触发“创建项目”时，采用如下规则：

- 当前 `standalone session` 保留
- 系统新建一个 `Project`
- 系统新建 `Workspace`
- 系统新建一个 hidden `project-bound session`
- 将当前 chat 的必要上下文复制到这个 hidden session
- 系统切换到新创建的 project

### 为什么不直接把原 chat 升级成 project chat

因为这样会引发用户感知问题：

- 原来在 `Chats` 列表里的会话突然消失
- 用户容易产生“刚才那个聊天去哪了”的困惑

因此推荐策略是：

- 原 chat 保留
- 新建 project main chat
- 做上下文迁移，而不是对象身份转型

### 建议迁移的上下文

从原 `standalone session` 迁移到 hidden project chat 的内容可包括：

- 当前会话标题
- 最近若干轮关键消息
- 系统生成的项目摘要
- 用户目标、约束、范围

---

## 默认主会话规则

每个 project 默认创建一个 hidden main chat。

这个 main chat：

- 属于 `project-bound session`
- 不出现在 `Chats` 列表中
- 作为项目的默认对话入口
- 与该项目 workspace 绑定
- 可同时加入多个 agent

第一版建议先坚持：

- 一个 project 只有一个默认 hidden main chat

后续如果有需要，再扩展为：

- 一个 project 下支持多个 project-bound sessions

---

## 列表计数规则

为了避免认知混乱，计数规则统一如下：

- `Chats` 计数：只统计 `standalone session`
- `Projects` 计数：只统计 `project`
- hidden `project-bound session` 不计入 `Chats`

---

## 推荐状态流转

```text
standalone session
  ├─ continue chatting
  ├─ create another standalone session
  └─ create project
       ↓
project created
  ├─ create workspace
  ├─ create hidden project-bound session
  └─ enter project main chat
```

---

## 建议数据字段

### Session

- `id`
- `title`
- `session_type` = `standalone | project-bound`
- `project_id` nullable
- `source_session_id` nullable
- `is_visible_in_chat_list`
- `agent_strategy` nullable
- `created_at`
- `updated_at`

### SessionAgent

- `id`
- `session_id`
- `agent_id`
- `role_in_session`
- `joined_at`

### Project

- `id`
- `name`
- `description`
- `status`
- `default_session_id`
- `workspace_id`
- `created_from_session_id` nullable
- `created_at`

### Workspace

- `id`
- `project_id`
- `root_path` or logical workspace key
- `state_json`
- `created_at`

---

## MVP 规则

第一版建议严格按以下规则落地：

1. 首次进入系统时，自动创建并进入一个默认 standalone chat。
2. `Chats +` 只创建 standalone chat。
3. 用户可通过聊天或显式入口创建 project。
4. 创建 project 时必须同时创建 workspace 和 hidden project main chat。
5. hidden project main chat 不出现在 `Chats` 列表。
6. 进入 project 时默认打开 hidden project main chat。
7. 从 standalone chat 创建 project 时，原 chat 保留，项目获得一份上下文副本。
8. 每个 session 都支持多个 agent 同时参与，不论它是否绑定 project。

---

## 一句话总结

`Chats` 负责“聊”，`Projects` 负责“做”。

用户可以先从无项目聊天开始，等目标明确后再升级为带 workspace 的项目执行态，而项目内部的主聊天由系统自动创建并隐藏管理。

---

## 与既有文档对齐

本文件不是引入一套全新的会话模型，而是把现有 PRD / ADR 中已经隐含存在的规则显式化。

### 多 agent session 不是新设定

历史文档已经明确表明 Catown 的聊天体系是多 agent 协作容器，而不是单助手对话：

- [PRD.md](./PRD.md)
  已定义多种 agent 角色，如 analyst、architect、developer、tester、release、assistant。
- [ADR-011-chatroom-full-event-cards.md](./ADR-011-chatroom-full-event-cards.md)
  已将 Agent 间消息视为聊天室中的核心事件类型之一。
- [ADR-012-llm-session-context-management.md](./ADR-012-llm-session-context-management.md)
  已讨论多 agent 协作时的上下文注入策略。

因此，本文件中“每个 session 都支持多个 agent”属于对既有设计的归纳和收口，而不是新增方向。

### 本文件新增的内容

本文件真正新增并明确化的是以下规则：

- `standalone session` 与 `project-bound session` 的区分
- `Chats` 列表与 `Projects` 列表的展示边界
- 创建 project 时自动生成 hidden main chat 和 workspace
- 从 standalone chat 孵化 project 时保留原 session，而不是直接转型

这些内容用于把已有“多 agent chatroom”能力，整合进更清晰的 `session / project / workspace` 业务结构。
