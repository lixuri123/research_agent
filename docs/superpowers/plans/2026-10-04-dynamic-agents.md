# Dynamic Agents Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for isolated budget work and final review. Follow test-driven-development; changes stay in place because this directory is not a Git repository.

**Goal:** 主 Agent 按需并行委派、观察、取消和接收子任务，并根据结果追加工作。

**Architecture:** 在现有个人 Agent 循环上增加协作控制器，复用 Repository 阶段产物和 Worker 租约。通用子 Agent 使用独立工具循环，研究子任务调用现有 LangGraph，所有调用共享父预算。

**Tech Stack:** Python 3.10、ThreadPoolExecutor、Pydantic、现有 LangGraph、SQLAlchemy。

## Global Constraints

- 不增加外部依赖、不调用真实模型、不初始化 Git 仓库。
- 默认并发 2、总子任务 6、每个子 Agent 最多 8 步；单层委派。
- 子 Agent 只能继承父工具目录中的只读权限，禁止写入提案、有副作用 MCP 和递归委派。
- 父子共用模型调用、token 和 deadline 预算，预算必须线程安全且先持久化预留再调用模型。
- 子任务状态、决策、观察可恢复；相同 spawn operation_id 不重复创建。
- 父任务完成前必须处理尚未结束的子任务；取消在调用边界生效。

## Task 1: Atomic shared budget

Files: research_agent/budget.py; tests/test_budget.py.

- [x] 写并运行失败测试：多个线程并发 reserve 时保存回调互斥且预算不被超用；持久化失败不消耗内存额度。
- [x] Budget 使用 RLock 串行化 reserve/settle 和持久化；候选 usage 保存成功后才发布。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/test_budget.py tests/test_model_gateway.py -q`。

## Task 2: Durable dynamic delegation

Files: research_agent/delegation.py; tests/test_delegation.py; research_agent/config.py.

- [x] 写失败测试：两个子任务真正重叠执行、稳定 ID/恢复、权限缩小、取消、步数上限、等待超时及 pending 状态。
- [x] 实现 DelegationController，catalog/call/snapshot/close 接口；子任务与决策、工具观察都保存在父 run 的独立阶段命名空间。
- [x] 实现独立子 Agent 循环及研究 workflow runner；研究 runner 用独立 checkpoint thread_id、父 run 租约和共享预算，不发布报告。
- [x] 添加上述三个有界配置并验证配置范围。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/test_delegation.py -q`。

## Task 3: Main agent integration and acceptance

Files: research_agent/personal_agent.py; tests/test_dynamic_agent.py; README.md; docs/dynamic_agents.md.

- [x] 写失败测试：主 Agent 派两个阅读任务、wait、再派核查、汇总；未完成子任务时不能交付，恢复旧 catalog 仍获得委派工具。
- [x] 合并协作工具目录并分派调用，向每轮模型提供最新子任务状态；完成时检查 pending；终态结果附带子任务产物及来源。
- [x] 关闭协作线程再提交父任务终态，保证没有后台继续执行任务。
- [x] 文档说明动态执行方式、限制、恢复和取消语义。
- [x] 回归 Budget、Agent、Memory、Workflow、Worker、Storage、API 和全部测试；独立代码审核并修复重要问题。

## 完成记录

2026-10-04 全部任务完成。最终验证：`.venv/Scripts/python.exe -m pytest -q --basetemp .pytest-dynamic-final`，125 passed、1 skipped；三个委派配置的上下界已单独验证。跳过的是未设置 TEST_DATABASE_URL 的数据库集成测试。

独立最终复审见 `2026-10-04-dynamic-final-review.md`，没有剩余可操作问题。没有调用真实付费模型，没有新增外部依赖。当前目录不是 Git 仓库，改动保留在原目录，没有创建分支或提交。

