# Changelog

## 2026-04-07

### 前端
- **修复**: Mention Agent 按钮点击后弹出 agent 列表下拉框
- **优化**: 点击 "Mention Agent" 时自动从后端加载可用 agent 列表
- **清理**: 移除未使用的 React/Vite 前端代码（src/、tsconfig、vite.config.ts 等）
- **清理**: 前端仅保留 `index.html` 单文件，移除 npm 依赖

### 项目结构
- 测试文件统一移至 `tests/` 目录
- 文档（除 README.md）统一移至 `docs/` 目录
- 删除非项目文件：room.html、room_decoded.html
- 删除残留文件：frontend/public/、frontend/src/、tailwind.config.js 等

### 文档
- 更新 README.md：移除 Node.js 依赖说明，更新项目结构和启动方式
