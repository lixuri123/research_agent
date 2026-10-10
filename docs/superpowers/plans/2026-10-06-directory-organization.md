# Directory Organization Implementation Plan

**Goal:** 按职责组织源码、测试和文档，保持现有功能与启动方式。

**Architecture:** 按已批准的设计迁移 Python 模块并统一更新消费者。保留 API 与 Worker 入口、现有部署和运行数据路径。

**Tech Stack:** Python、FastAPI、LangGraph、pytest、React、TypeScript、Vite。

## Global Constraints

- 不更改业务行为、依赖和数据库 schema。
- 保留 `research_agent.api:app` 与 `python -m research_agent.worker`。
- 数据、凭证、环境文件、前端输出和原生 helper 路径不迁移。
- 当前无 Git 仓库，保存本地迁移前备份与文件清单。
- 本会话内直接执行，不创建新聊天或委派子 Agent。

## Tasks

- [x] 基线：创建 `.tmp/`，运行 `.venv/Scripts/python.exe -m pytest tests -q --basetemp .tmp/pytest-directory-baseline --tb=short`，记录已有失败。
- [x] 源码：按设计的目录边界迁移模块与科研角色；更新绝对导入、`from research_agent import ...` 和字符串 patch 目标。所有目标先检查不存在，所有移动源和目标确认位于项目根目录。
- [x] 测试：按职责移动现有测试，修正原生测试从测试文件定位项目根目录的深度；保留全局 fixture。
- [x] 文档：分类当前指南与历史文档，按迁移前的真实链接目标重新计算相对路径；保留历史设计记录文本，添加历史说明。
- [x] 临时产物：把根目录 `.pytest-*` 移至 `.tmp/legacy/`；添加忽略规则和 pytest 默认临时目录，不删除原有缓存。
- [x] 验证：运行全量 pytest；原生 helper 受目录 ACL 影响时使用原生目录下的 basetemp 单独验证。执行 `npm run build`、`.venv/Scripts/python.exe -m research_agent.worker --once`、导入和文档链接检查。
- [x] 交付：更新目录导航、迁移清单和验证结果；说明未通过或环境限制。

