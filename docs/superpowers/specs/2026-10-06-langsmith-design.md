# LangSmith 执行追踪接入设计

日期：2026-10-06
状态：用户已确认；实现及离线验证完成

## 目标

发送“你好”后，能够在 LangSmith 中查看一次 Worker 执行及其规划、决策模型调用；复杂任务能够查看工具调用、研究图节点及子 Agent，并用 task_id、run_id、session_id 与本地记录对应。

## 当前问题

- observability.py 提供 setup_langsmith，但 Worker 没有调用它。
- Settings 定义 langsmith_tracing，但初始化函数只检查 API Key，没有遵循该开关。
- 内置 run_tool 已有 traceable 装饰器；普通 Agent、MCP、Skill、工作空间和委派调用缺少统一父级追踪。
- 模型通过 ChatOpenAI 调用，可以利用 LangChain 原生追踪；现有数据库仍负责任务状态与恢复。

## 接入方式比较

1. 仅设置环境变量：改动少，模型和研究图能追踪，但普通 Agent 缺少统一执行入口和业务 ID。不能满足本次目标。
2. Worker 执行追踪 + 原生 LangChain 追踪 + 工具和子任务打点（推荐）：沿用现有执行结构，覆盖两种任务并关联本地记录。
3. 迁移执行到 LangSmith 部署平台：改变部署、队列和恢复机制，超出本次范围。

## 推荐设计

### 配置

沿用 LANGSMITH_TRACING、LANGSMITH_API_KEY、LANGSMITH_PROJECT，支持 LANGSMITH_ENDPOINT 与可选 LANGSMITH_WORKSPACE_ID。遵循显式开关，默认关闭。启用但未配置 Key 时明确提示配置问题，业务任务仍可正常运行。不得回显或将 Key、Provider 凭证写入追踪元数据。

使用官方 SDK 的 tracing_context 和追踪接口建立执行上下文，避免单纯依赖全局环境变量导致关闭开关失效。直接声明使用的 langsmith 依赖，约束范围与现有依赖和 lockfile 相容。

### 执行入口

Worker 领取任务后，为本次执行建立顶层 trace。元数据只选取 task_id、run_id、session_id、workspace_id、worker_id、task_type 和所选模型的非敏感标识。普通 Agent 与研究图均在此上下文内执行。

一个 run_id 可以因租约恢复产生多个执行 trace；每个 trace 用相同业务 run_id 关联，避免把恢复执行误认为新的用户请求。记录本地 trace 关联事件，使运行记录可对应远端追踪。关闭追踪时不产生伪关联。

### 子调用

复用 ChatOpenAI 和 LangGraph 的原生追踪。在普通工具能力分发入口、委派操作和子 Agent 执行入口补充 SDK 打点，使 MCP、Skill、工作空间读取及子任务进入父 trace。沿用现有 copy_context 传递线程上下文，测试跨线程父子关系。

自定义入口只记录必要的用户输入、工具参数和交付结果，不序列化 Repository、Worker、客户端对象或整个配置。启用追踪后模型提示词、回复和工具结果会发送到配置的 LangSmith 服务，文档说明这一行为。

### 错误处理

追踪初始化和上报问题不得改变业务结果、触发任务重复执行或覆盖原始业务异常。已有预算、权限、租约、检查点和审批逻辑保持原执行语义。任务 blocked/failed 在追踪结果中保留明确状态。

## 验证

- 开关关闭时不创建客户端上报或网络请求，即使外部存在旧版 tracing 环境变量也遵循项目开关。
- 启用时使用配置的项目、Endpoint 和 Workspace，且元数据不含凭证。
- 通过本地捕获 SDK 追踪请求，验证普通问候的父 trace 与两次模型调用、工具调用及子 Agent 的父子关系，不依赖线上模型费用。
- 验证研究图任务及恢复执行的业务 ID 关联。
- 验证追踪故障不改变 completed/failed 和原始错误语义。
- 运行对应 Worker、普通 Agent、动态协作和模型网关回归测试。
- 更新 .env.example 和 README 的配置及查看追踪步骤。

## 交付与边界

完成代码、自动化验证及使用说明。保留用户已有 .env 内容；没有 LangSmith 凭证时提供配置步骤，不声称云端联通已经验证。此接入不新增评测数据集、前端配置面板或部署迁移。

## 官方参考

- https://docs.langchain.com/langsmith/trace-with-langchain
- https://docs.langchain.com/langsmith/annotate-code
