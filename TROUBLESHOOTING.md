# 🚨 问题诊断和解决方案

## 问题分析

你看到的错误：
```
GET http://localhost:3000/api/config 404 (Not Found)
```

**原因**: 前端请求发送到 `localhost:3000` 而不是 `localhost:8000`

这有两种可能：
1. **后端没有启动** - 最可能的原因
2. **前端代理配置问题**

## ✅ 解决方案

### 方案 1: 启动后端（推荐）

**后端必须先启动，前端代理才能工作！**

#### Windows:
```bash
cd catown
run-backend.bat
```

#### Linux/Mac:
```bash
cd catown
./run-backend.sh
```

#### 验证后端启动:
在浏览器中访问:
- http://localhost:8000/api/config
- http://localhost:8000/docs

**看到 JSON 响应就说明后端已启动成功！**

### 方案 2: 修改前端配置（如果代理仍有问题）

如果后端已启动但代理仍不工作，修改前端直接连接后端：