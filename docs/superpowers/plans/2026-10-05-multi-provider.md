# Multiple Provider Implementation Plan

**Goal:** 管理多个模型服务，并固定每次任务的模型选择。
**Architecture:** Provider registry + immutable revisions in shared JSON; non-secret task selection + ContextVar in Worker; React management and grouped composer selector.
**Tech Stack:** Existing Pydantic/FastAPI/React/TypeScript/OpenAI-compatible gateway.

## Verification

- Provider、单服务兼容、模型网关、Worker、子 Agent、存储和动态 Agent 相关测试：74 passed、1 skipped、1 deselected。排除项是前次已经用原始配置读取路径复现的 Worker 0.1 秒租约恢复测试。
- `npm run build` 成功。
- `python -m scripts.check_provider_ui` 使用 Edge、真实设置和任务路由、临时数据库及假模型验证两个服务的新增编辑、默认切换、删除、独立密钥、普通/研究任务的真实提交和手机布局；没有访问真实模型服务。
- 已验证删除后恢复固定版本、跨进程恢复以及真实 LangGraph 节点和子线程传播；任务数据不含密钥。
- 独立审查发现并修复空注册表旧接口恢复和配置变化后的提交幂等性；浏览器发现并修复迁移服务 ID 与默认接口路由冲突。修复后补充回归并通过复查。
- 当前目录无 Git 元数据，未进行提交或分支操作。

- [x] 在 tests/test_multi_provider.py 写 API CRUD、旧文件迁移、密钥隔离、无效输入脱敏、任务版本固定测试，运行验证新增接口缺失。
- [x] 新增 ProviderEntry/ProviderRegistry/ModelSelection 到 provider_registry.py；load_registry/persist/pin_selection/selection_overrides 管理共享注册表与历史版本。旧 read_overrides 与 save_provider 兼容版本 1 和版本 2。
- [x] provider_api.py 增加 /settings/providers GET/POST、/{id} PUT/DELETE、/default/selection PUT 与 /test POST；输入统一经过模型校验，保持脱敏。
- [x] config.py 增加 model_settings_scope；api.py 添加 model_selection 到两种任务请求，storage.py 在首次接收时固定版本；storage.py 的 submit 将非秘密 selection 纳入持久化与幂等哈希；worker.py 在 try/finally 设置和恢复作用域。
- [x] ProviderSettings.tsx 增加列表、新建/编辑表单、删除与默认选择。AgentApp.tsx 增加按服务分组的模型选择并带入提交请求；types/api 更新；设置面板关闭后刷新模型列表。
- [x] 测试新功能、单服务兼容与运行回归，构建前端，浏览器检查模型切换和任务提交，更新说明。已有 Worker 0.1 秒租约测试基线问题单独记录。

