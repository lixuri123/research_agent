# 项目目录按职责分组

用户已确认采用按职责分组方案。此次只调整物理目录、导入、文档链接和测试组织，不改变业务行为或数据库结构。

## 目录边界

- `api.py`、`worker.py` 和 `__main__.py` 保留为现有启动入口；Provider 路由归入 `api_routes/providers.py`。通用 API 路由的进一步拆分不属于本次迁移。
- `agent/` 保存普通 Agent 执行、能力目录、委派控制。
- `research/` 保存研究图、证据校验和 `agents/` 科研角色。
- `models/` 保存模型网关、角色路由、Provider 设置和注册表；原业务 `models.py` 移至 `core/schemas.py`，消除命名冲突。
- `persistence/` 保存 Repository、任务队列访问和检查点。
- `core/` 保存配置、预算、观测、审计和公共数据结构。
- `workspace/` 保存工作空间服务和修改提案。
- `integrations/` 保存 MCP 与 Skill；`evaluation/` 保存评测；现有 `memory/`、`tools/`、`execution/` 保持职责边界。
- 测试按相应职责放入子目录，保留全局 `tests/conftest.py`。
- 文档分为 `architecture/`、`guides/`、`archive/`，保留 `superpowers/` 历史设计记录；提供当前架构和目录导航。

## 路径与兼容

统一更新项目内导入和测试 monkeypatch 目标，不增加大量旧路径代理文件。原公开 API 路径、API/Worker 启动命令、环境变量、数据库、记忆、配置及前端构建输出路径保持不变。

历史 `.pytest-*` 目录和截图移入 `.tmp/legacy/`，不删除。测试默认临时目录统一至 `.tmp/pytest`；运行数据和凭证不迁移、不读出。迁移前保存原始文件和清单到 `.tmp/directory-organization-backup/`，因为当前目录没有 Git 元数据。

## 验证

先运行现有测试建立基线，再运行迁移后的完整测试；Windows 原生测试单独核对沙盒目录限制。执行前端生产构建、Worker 单次启动以及文档本地链接和旧模块引用检查。不安装依赖、不访问真实模型。
