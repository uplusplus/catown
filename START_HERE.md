# 🚀 Catown 快速启动指南

## ⚠️ 解决 404 错误

你看到的 404 错误是因为**后端服务还没有启动**。请按以下步骤启动：

## 📦 一、启动后端服务

### Windows 用户

**方法 1: 使用快速启动脚本**
```bash
cd catown
quick-start.bat
```

**方法 2: 手动启动**
```bash
cd catown/backend

# 安装依赖（首次运行）
pip3 install -r requirements.txt

# 启动服务
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Linux/Mac 用户

**方法 1: 使用快速启动脚本**
```bash
cd catown
chmod +x quick-start.sh
./quick-start.sh
```

**方法 2: 手动启动**
```bash
cd catown/backend

# 安装依赖（首次运行）
pip3 install -r requirements.txt

# 启动服务
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## ✅ 二、验证后端启动成功

启动成功后，你会看到：

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
✅ Database initialized successfully
✅ Registered 4 built-in agents
```

**访问 API 文档验证：**
- 打开浏览器访问: http://localhost:8000/docs
- 你应该能看到完整的 API 文档页面

## 🌐 三、启动前端服务（可选）

如果需要 Web 界面，启动前端：

```bash
# 在新终端窗口中
cd catown/frontend

# 安装依赖（首次运行）
npm install

# 启动前端
npm run dev
```

前端启动后访问: http://localhost:3000

## 🔧 四、配置 LLM（重要）

编辑 `backend/.env` 文件：

```env
# 方式 1: 使用环境变量
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=http://localhost:3008/opencode/assistant-agent-s/opencode/v1
LLM_MODEL=GLM-V5-128K

# 或使用配置文件 backend/configs/agents.json
```

## 🎯 五、快速测试

### 测试 API

```bash
# 检查系统状态
curl http://localhost:8000/api/status

# 查看可用 Agents
curl http://localhost:8000/api/agents

# 创建项目
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","agent_names":["assistant"]}'
```

### 测试 Web 界面

1. 访问 http://localhost:3000
2. 点击 "Create New Project"
3. 选择 Agents
4. 开始聊天

## 📋 常见问题

### Q: 端口 8000 被占用怎么办？

A: 修改启动命令使用其他端口：
```bash
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8001
```
然后更新前端配置的代理端口。

### Q: 模块导入错误？

A: 确保使用 Python 3.10+:
```bash
python3 --version  # 应该是 3.10 或更高
```

### Q: 依赖安装失败？

A: 尝试使用国内镜像：
```bash
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 🎊 完整启动流程

```bash
# 1. 进入项目目录
cd catown

# 2. 启动后端（Windows）
quick-start.bat
# 或 Linux/Mac
./quick-start.sh

# 3. 验证（在新浏览器标签）
# 打开 http://localhost:8000/docs

# 4. 启动前端（可选，新终端）
cd frontend
npm install  # 首次运行
npm run dev

# 5. 使用 Web 界面（可选）
# 打开 http://localhost:3000
```

## ✨ 成功标志

看到以下信息说明启动成功：

**后端:**
```
✅ Uvicorn running on http://0.0.0.0:8000
✅ Database initialized successfully
✅ Registered 4 built-in agents
```

**前端:**
```
✅ VITE v5.0.8  ready in xxx ms
✅ ➜  Local:   http://localhost:3000/
```

## 🎉 开始使用

现在你可以：

1. ✅ 通过 API 文档测试接口 (http://localhost:8000/docs)
2. ✅ 通过 Web 界面管理项目 (http://localhost:3000)
3. ✅ 创建项目和分配 Agents
4. ✅ 在聊天室与 Agents 交互
5. ✅ 查看系统状态和 Agent 信息

---

**🚀 现在就启动吧！**

遇到问题？查看 `PROJECT_STATUS.md` 或 `README.md`
