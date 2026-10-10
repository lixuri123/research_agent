# 第一阶段验收记录

> 历史文档：保留设计与实施背景，当前结构请参阅 [目录说明](../architecture/directory-structure.md)。

## 实现范围

- SQLAlchemy 持久化任务、执行、不可变报告版本、阶段产物和审计事件。部署支持 Postgres，本地显式使用文件 SQLite；无内存持久化降级。
- API 与独立 Worker 分离；幂等提交、租约领取与续期、过期重新领取、阶段产物复用、持久化 LangGraph 检查点。
- 审批绑定报告版本与内容哈希。同意后异步恢复，拒绝不发布、不生成可信结论索引，改版必须重新审批。
- 原文/摘要片段的稳定版本与位置、阅读发现、论断及证据关联；未知引用保留为校验失败。
- 正文引用与论断证据来源一致性检查；完整报告与原文证据语义审核。所有论断有 supported verdict 且审核通过才能发布。
- 有限修订与反馈传递；空证据输出 insufficient_evidence；审核不通过或预算耗尽不产生 final_report。
- 文献数量、调用次数、Token 预留及总 deadline。模型/检索调用超时按剩余 deadline 限制；失败调用不返还预留。
- 期望来源精确率、证据结构合法率及 null 指标；修正假记忆注入指标。

## 测试证据

最终离线验证：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests -q
48 passed, 1 skipped

.venv/Scripts/python.exe -X utf8 scripts/regression.py
48 passed, 1 skipped

.venv/Scripts/python.exe -m pip check
No broken requirements found.

.venv/Scripts/python.exe -m compileall -q research_agent scripts
exit 0

docker compose --env-file .env.example config --no-env-resolution --quiet
exit 0（Docker 配置目录读取有权限警告）
```

测试覆盖真实 LangGraph 图、FastAPI、文件数据库及 SQLite 检查点；使用可控模型/检索替身，不发出真实模型或论文检索请求。包括审批等待后的 Worker/仓储/检查点实例重建，以及阅读完成后进程退出并从阶段恢复、不重复阅读。

独立代码审查发现并修正：review 阶段缓存必须保存更新后的论断核验状态；论断证据与展示引用必须匹配；节点内部及降级调用必须检查剩余 deadline。对应失败用例已转为通过。

## 未验证及后续范围

- 1 项 Postgres 集成测试因未配置 TEST_DATABASE_URL 而跳过，本机 Docker daemon 未启动。Postgres 连接、容器镜像构建与实际服务运行尚未验证。
- 没有调用真实模型，真实输出质量和来源充分性仍需端到端评测。模型核验不是事实正确性的绝对保证。
- 此阶段为本地单用户；多用户认证、取消/SSE、跨存储 outbox、人工标注语义评测及大规模并行属于后续阶段。
- 旧全局记忆与辩论路径未纳入发布流程；已发布结论仅保存版本与证据引用索引。
- 新 SQL schema 为首次初始化，无旧 Redis 任务导入和 SQL 历史迁移。当前目录无 Git 仓库，文件已保留，但没有 commit/PR。

## 运行入口

按 README 配置环境后，分别启动：

```text
.venv/Scripts/python.exe -m uvicorn research_agent.api:app --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe -m research_agent.worker
```
