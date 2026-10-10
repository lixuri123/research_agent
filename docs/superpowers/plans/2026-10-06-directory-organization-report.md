# 目录整理验收记录

按用户批准的职责分组方案迁移 68 个源码、测试和文档文件。当前导航见 `docs/architecture/directory-structure.md` 与 `docs/README.md`。

## 验证

- 迁移前基线：186 passed，1 skipped。
- 迁移后显式临时目录测试：186 passed，1 skipped。
- 最终默认 pytest 配置：186 passed，1 skipped；默认临时数据与缓存进入 `.tmp/`。
- `npm run build`：TypeScript 与 Vite 生产构建通过。
- 结构检查：68 个迁移目标、93 个 Python 文件及 32 个本地文档链接通过。
- API：临时数据库上的生命周期、health、OpenAPI 路由、静态首页检查通过。
- Worker：临时空数据库上的 `python -m research_agent.worker --once` 通过；未执行用户任务或调用模型。
- 本地事件循环在 Windows 沙盒中阻塞，离线入口检查经批准在沙盒外重跑通过。未修改业务代码解决此环境限制。

## 保留事项

- `.pytest-langsmith-order-recheck` 的数据库文件被其他进程占用，原目录保留；归档副本可能已部分复制。不终止该进程或强制删除。
- 其他历史 `.pytest-*` 产物归档于 `.tmp/legacy/`，原 pytest 缓存也已归档。
- 迁移清单：`.tmp/directory-organization-manifest.json`；源码迁移前备份：`.tmp/directory-organization-backup/`。本目录无 Git 元数据，因此没有提交。
- API、Worker 启动入口和运行数据路径保留；旧内部 Python 导入路径已更换，外部自编脚本需同步调整。
- 前端组件结构和通用 API 路由未作进一步拆分；本次完成的是职责归类。
