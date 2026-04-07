# Catown 项目结构

## 目录结构

```
catown/
├── backend/                    # 后端服务
│   ├── agents/                # Agent 核心模块
│   │   ├── core.py           # Agent 核心类
│   │   └── registry.py       # Agent 注册表
│   ├── chatrooms/            # 聊天室系统
│   │   └── manager.py        # 聊天室管理器
│   ├── llm/                  # LLM 集成
│   │   └── client.py         # LLM 客户端
│   ├── models/               # 数据模型
│   │   └── database.py       # 数据库定义
│   ├── routes/               # API 路由
│   │   ├── api.py           # REST API
│   │   └── websocket.py     # WebSocket 处理
│   ├── tools/                # 工具集合（可扩展）
│   ├── examples/             # 示例代码
│   │   └── demo.py          # 演示脚本
│   ├── tests/                # 单元测试
│   │   └── test_agent.py    # Agent 测试
│   ├── data/                 # 数据存储
│   ├── logs/                 # 日志文件
│   ├── main.py              # 应用入口
│   ├── requirements.txt     # 依赖列表
│   └── .env.example         # 环境变量示例
│
├── frontend/                 # 前端应用
│   ├── src/
│   │   ├── pages/           # 页面组件
│   │   │   ├── Dashboard.tsx      # 首页
│   │   │   ├── ChatRoom.tsx       # 聊天室
│   │   │   ├── ProjectDetail.tsx  # 项目详情
│   │   │   ├── Agents.tsx         # Agent 列表
│   │   │   └── Status.tsx         # 状态页面
│   │   ├── components/      # 可复用组件
│   │   ├── hooks/           # 自定义 Hooks
│   │   ├── App.tsx          # 主应用
│   │   ├── main.tsx         # 入口文件
│   │   └── index.css        # 全局样式
│   ├── public/              # 静态资源
│   ├── index.html           # HTML 模板
│   ├── package.json         # 依赖列表
│   ├── vite.config.ts       # Vite 配置
│   ├── tailwind.config.js   # Tailwind 配置
│   └── tsconfig.json        # TypeScript 配置
│
├── data/                     # 全局数据存储
├── logs/                     # 全局日志
├── start.sh                 # Linux/Mac启动脚本
├── start.bat                # Windows 启动脚本
└── README.md                # 项目说明

```

## 核心模块说明

### 1. Agent 系统 (backend/agents/)

**core.py** - Agent 核心功能
- `Agent` 类：封装 Agent 的所有功能
- 记忆系统：短期、长期、程序性记忆
- 工具调用：注册、执行、结果处理
- 对话管理：上下文构建、历史维护

**registry.py** - Agent 注册和管理
- 内置 Agent 定义（assistant, coder, reviewer, researcher）
- Agent 创建和配置
- 工具自动注册

### 2. 聊天室系统 (backend/chatrooms/)

**manager.py** - 聊天室管理
- 聊天室创建和生命周期管理
- 消息路由和分发
- Agent 协作协调
- 消息历史记录

### 3. LLM 集成 (backend/llm/)

**client.py** - LLM 客户端
- OpenAI 兼容接口封装
- 支持自定义 base_url
- 工具调用支持
- 错误处理和重试

### 4. 数据模型 (backend/models/)

**database.py** - SQLAlchemy 模型
- `Agent`: Agent 定义
- `Project`: 项目信息
- `Chatroom`: 聊天室
- `Message`: 消息记录
- `Memory`: Agent 记忆
- `AgentAssignment`: Agent 项目分配

### 5. API 路由 (backend/routes/)

**api.py** - RESTful API
- Agent 相关：列表、详情、记忆
- 项目相关：CRUD 操作
- 聊天相关：消息收发
- 状态查询：系统健康检查

**websocket.py** - WebSocket 通信
- 实时消息推送
- 房间管理
- 连接管理

### 6. 前端页面 (frontend/src/pages/)

**Dashboard.tsx** - 首页
- 项目列表展示
- 创建新项目
- 快速导航

**ChatRoom.tsx** - 聊天室
- 实时消息显示
- 用户输入界面
- Agent 响应展示

**ProjectDetail.tsx** - 项目详情
- 项目信息
- 分配的 Agent
- 操作按钮

**Agents.tsx** - Agent 管理
- Agent 列表
- 记忆查看
- 状态监控

**Status.tsx** - 系统状态
- 统计数据
- 功能开关
- 系统信息

## 数据流

### 1. 创建项目流程
```
用户 → Dashboard → POST /api/projects 
→ 创建 Project 记录 → 创建 Chatroom 
→ 分配 Agents → 返回项目信息
```

### 2. 发送消息流程
```
用户 → ChatRoom → POST /api/chatrooms/{id}/messages
→ 保存 Message → 通知相关 Agents 
→ Agent 处理 → 生成响应 → 保存响应消息
→ WebSocket 推送 → 前端更新
```

### 3. Agent 处理流程
```
接收消息 → 构建上下文（系统提示 + 记忆 + 历史）
→ 调用 LLM → 检查工具调用 
→ 执行工具 → 整合结果 → 生成最终响应
→ 更新记忆 → 返回响应
```

## 技术亮点

1. **模块化设计**: 各模块职责清晰，易于扩展
2. **类型安全**: TypeScript + Pydantic 提供类型检查
3. **实时通信**: WebSocket 实现双向实时通信
4. **记忆系统**: 多层次记忆支持 Agent 学习
5. **工具调用**: 灵活的插件化工具系统
6. **响应式 UI**: React + TailwindCSS 现代化界面
7. **API 文档**: FastAPI 自动生成 OpenAPI 文档

## 扩展指南

### 添加新 Agent
1. 在 `agents/registry.py` 中添加配置
2. 定义 system_prompt
3. 注册需要的工具

### 添加新工具
1. 在 `agents/core.py` 中使用 `register_tool()`
2. 实现工具函数
3. 提供清晰的描述

### 添加新页面
1. 在 `frontend/src/pages/` 创建组件
2. 在 `App.tsx` 中添加路由
3. 添加导航链接

### 添加新 API
1. 在 `routes/api.py` 中添加端点
2. 定义 Pydantic 模型
3. 实现业务逻辑
4. 测试 API
