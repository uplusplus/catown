# Skills 功能 PRD

**版本**: v0.1
**日期**: 2026-04-10
**状态**: 草案
**作者**: assistant

---

## 1. 背景

Catown 需要一套可扩展的 Skill 体系，让 Agent 能够快速获取、复用和沉淀能力。Skill 不只是“安装包”，还应支持从现成开源技能、开源项目、真实问题解决过程三种路径生成。

## 2. 目标

- 支持从 GitHub / Skill Hub 直接安装现成 skills
- 支持把开源项目转化为 skill
- 支持从问题解决过程总结生成 skill
- 统一管理 skills 的存储、加载、更新和发布
- 让 skill 可复用、可版本化、可追踪来源

## 3. 非目标

- 不做复杂的在线 Marketplace
- 不做多人协作编辑平台
- 不强制 skill 必须来自某个固定 hub
- 不在第一版里解决所有自动审核问题

## 4. 核心概念

- `Skill`：可被 Agent 调用的能力单元
- `Skill Hub`：提供现成 skill 的来源站点，包含 GitHub repo、网页 hub、内部仓库等
- `Source Type`：skill 的来源类型
  - `github/hub`
  - `project`
  - `experience`
- `Skill Package`：统一落盘后的 skill 结构
- `Skill Registry`：本地 skills 索引与状态记录

## 5. 用户场景

- 作为用户，我想直接安装 GitHub 上已有的 skill，省去自己整理
- 作为用户，我想把一个开源项目变成 skill，让它的使用方法可复用
- 作为用户，我想把一次排障/实现过程总结成 skill，方便下次直接用
- 作为系统，我想统一读取 skills，按需注入给 Agent

## 6. 功能范围

### 6.1 直接安装现成 Skill

输入来源：
- GitHub 上的 skills 项目
- ClawHub / SkillHub 这类 hub 页面
- 其他支持 manifest 的 skill 仓库

行为要求：
- 支持通过 URL 安装
- 自动识别 skill 元信息
- 拉取 README、manifest、prompt 文件等
- 转换为本地统一格式
- 记录来源、版本、许可信息
- 安装后可立即加载使用

输出：
- `skills.json` 中新增 skill 索引
- `.catown/skills/<skill-id>.md` 或等价文件
- 原始来源信息保留

### 6.2 基于开源项目编译安装后转化为 Skill

输入来源：
- GitHub 开源项目
- 本地项目目录
- 包含安装、构建、运行流程的代码仓库

行为要求：
- 能执行构建/安装验证流程
- 能分析项目的使用方式、约束、最佳实践
- 将项目能力抽象成可执行 skill
- 不直接把项目代码当 skill，而是总结它的方法和流程
- 支持人工确认转化结果

输出：
- skill 描述
- 使用步骤
- 依赖工具
- 适用场景
- 注意事项

### 6.3 基于解决问题过程生成 Skill

输入来源：
- 一段排障记录
- 一段开发过程日志
- 一次任务完成过程的对话或 trace
- 一个成功的执行轨迹

行为要求：
- 自动提取问题背景、步骤、结果
- 总结可复用的解决策略
- 生成结构化 skill 草案
- 支持用户编辑和审核后发布
- 记录该 skill 的来源上下文

输出：
- 问题定义
- 解决路径
- 关键命令/操作
- 常见误区
- 推荐流程

## 7. 统一数据结构

建议所有 skill 最终统一为同一结构：

```json
{
  "id": "string",
  "name": "string",
  "description": "string",
  "source_type": "github|hub|project|experience",
  "category": "string",
  "required_tools": ["string"],
  "status": "draft|active|deprecated",
  "version": "string",
  "origin": {
    "url": "string",
    "commit": "string",
    "license": "string"
  },
  "content": {
    "summary": "string",
    "steps": ["string"],
    "constraints": ["string"],
    "examples": ["string"]
  }
}
```

## 8. 主要流程

### 8.1 安装现成 skill

1. 用户输入 URL
2. 系统识别来源类型
3. 拉取 skill 文件
4. 解析元信息
5. 生成本地 skill 包
6. 写入 registry
7. 加载生效

### 8.2 项目转 skill

1. 用户指定 repo 或目录
2. 系统执行安装/构建/运行检查
3. 提取项目操作方式
4. 生成 skill 草案
5. 用户确认/修改
6. 保存并注册

### 8.3 经验总结生成 skill

1. 输入对话、日志或 trace
2. 提取目标、步骤、结果
3. 归纳为标准 skill 模板
4. 用户审核
5. 发布到 registry

## 9. 界面/交互要求

- 支持“导入来源”入口
- 支持 skill 预览
- 支持生成草案后编辑
- 支持一键启用/停用
- 支持查看来源和版本
- 支持重复 skill 检测提示

## 10. 验收标准

- 能从 GitHub URL 成功安装一个现成 skill
- 能把一个开源项目转成 skill 草案
- 能把一段问题解决记录转成 skill 草案
- 所有 skill 都能以统一结构落盘
- skills 可被 Agent 正常读取和调用
- 支持查看来源、版本、状态

## 11. 成功指标

- skill 安装成功率
- project-to-skill 转化率
- experience-to-skill 转化率
- 平均生成耗时
- 用户二次修改率
- skill 被复用次数

## 12. 风险与问题

- 来源不规范，解析失败
- 项目过于复杂，难以抽象成 skill
- 经验总结容易生成空泛描述
- 不同来源的 skill 质量差异大
- 版权和许可问题需要保留元信息

## 13. 建议的第一版范围

优先做：

1. 直接安装现成 skill
2. 统一 skill 存储和加载
3. 支持 project -> skill 的半自动转换
4. 经验总结生成 skill 先做草案，不直接自动发布

## 14. 后续迭代

- skill 推荐和搜索
- skill 评分和质量检测
- skill 版本升级与兼容处理
- skill hub 聚合
- 经验总结自动去重和合并
