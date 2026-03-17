# Catown 快速启动指南

## 🎯 5 分钟快速开始

### 第一步：安装依赖

#### 后端依赖
```bash
cd backend
pip install -r requirements.txt
```

#### 前端依赖
```bash
cd frontend
npm install
```

### 第二步：配置 LLM

编辑 `backend/.env` 文件（先复制 `.env.example`）：

```bash
cd backend
cp .env.example .env
```

然后编辑 `.env`，设置你的 API 密钥：

```env
LLM_API_KEY=sk-your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4
```

**注意**: 如果你使用其他 OpenAI 兼容的服务（如本地部署的 Llama），修改 `LLM_BASE_URL` 即可。

### 第三步：启动服务

#### 方式一：使用启动脚本（最简单）

**Linux/Mac:**
```bash
./start.sh
```

**Windows:**
```bash
start.bat
```

选择选项 `3` 同时启动前后端。

#### 方式二：分别启动

**终端 1 - 启动后端:**
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**终端 2 - 启动前端:**
```bash
cd frontend
npm run dev
```

### 第四步：访问应用

打开浏览器访问：
- **Web 界面**: http://localhost:3000
- **API 文档**: http://localhost:8000/docs

### 第五步：创建第一个项目

1. 点击 "Create New Project" 按钮
2. 输入项目名称，例如 "My First Project"
3. 选择要使用的 Agents（建议至少选择 assistant）
4. 点击 "Create Project"

### 第六步：开始聊天

1. 点击项目的 "Chat" 按钮
2. 在输入框中输入消息，例如："Hello, can you help me write a Python function?"
3. 按 Enter 发送
4. 等待 Agent 回复

## 🎉 完成！

你现在已经成功运行了 Catown 平台！

## 📚 下一步

- 探索 **Agents** 页面查看 Agent 信息和记忆
- 查看 **Status** 页面了解系统状态
- 阅读完整 [README.md](README.md) 了解更多功能
- 查看 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) 了解项目架构
- 运行示例脚本：`cd backend/examples && python demo.py`

## ⚠️ 常见问题

### Q: 启动后端时提示模块未找到
**A**: 确保已在 backend 目录下运行 `pip install -r requirements.txt`

### Q: 前端页面空白
**A**: 
1. 检查后端是否正常运行
2. 查看浏览器控制台错误
3. 确保前端代理配置正确（vite.config.ts）

### Q: Agent 没有回复
**A**: 
1. 检查 LLM_API_KEY 是否正确配置
2. 检查网络连接
3. 查看后端日志了解详细错误

### Q: 如何测试不使用真实 LLM？
**A**: 暂时可以修改 `agents/core.py` 中的 `_generate_response` 方法返回模拟响应：

```python
async def _generate_response(self, messages: List[Dict]) -> str:
    # 返回模拟响应用于测试
    return "This is a simulated response for testing."
```

## 🔧 开发模式提示

- 后端使用 `--reload` 参数，代码修改后会自动重启
- 前端 Vite 开发服务器支持热更新
- 数据库文件存储在 `backend/data/catown.db`
- 日志文件在 `backend/logs/` 目录

## 📞 需要帮助？

- 查看 API 文档：http://localhost:8000/docs
- 阅读完整文档：README.md
- 检查示例代码：backend/examples/demo.py
- 运行测试：`cd backend && pytest tests/ -v`
