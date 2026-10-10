# 当前项目目录与职责

项目采用 React 工作台、FastAPI 服务与独立 Worker。普通 Agent 和 LangGraph 研究流程共用 SQL 存储、模型网关、预算与观测机制。

## 源码

```text
research_agent/
├── api.py                  # FastAPI 入口及通用 API 路由
├── worker.py               # 持久化任务领取、续租和执行入口
├── __main__.py             # CLI 演示入口
├── api_routes/
│   └── providers.py        # 单服务兼容接口与多 Provider 设置接口
├── agent/
│   ├── runner.py           # 普通 Agent 模型/工具循环与阶段恢复
│   ├── capabilities.py     # 按权限组装并执行任务工具
│   └── delegation.py       # 一层子 Agent 委派及共享预算
├── research/
│   ├── graph.py            # LangGraph 研究流程
│   ├── evidence.py         # 证据构建、引用结构校验
│   └── agents/             # 规划、检索、阅读、写作、审核角色
├── models/
│   ├── gateway.py          # 模型调用、重试、结构化输出校验
│   ├── router.py           # 按研究角色选择模型
│   ├── provider_settings.py
│   ├── provider_registry.py
│   └── embeddings.py       # 可选知识检索使用的嵌入接口
├── persistence/
│   ├── repository.py       # 任务、事件、阶段和报告版本 SQL 存储
│   ├── queue.py            # Repository 访问与队列兼容辅助函数
│   └── checkpoint.py      # LangGraph 检查点生命周期
├── core/
│   ├── config.py           # 环境设置与任务模型配置作用域
│   ├── schemas.py          # 业务数据结构，原 models.py
│   ├── budget.py           # 调用、Token、截止时间预算
│   ├── observability.py    # 可选 LangSmith 追踪
│   └── audit.py            # 工具审计
├── workspace/
│   ├── service.py          # 本地目录绑定、授权、文件读写
│   └── file_changes.py     # Agent 修改提案及确认应用
├── memory/                 # 会话、Markdown 记忆、范围匹配和召回
├── context/                # 普通 Agent 规则路由、来源选择、容量组装与恢复
├── profile/                # 开放人物属性、来源、确认状态及版本历史
├── execution/              # 默认关闭的本地受限文本检索
├── integrations/
│   ├── mcp_client.py       # MCP Server 连接、发现与调用
│   └── skills.py           # 本地和已发布 Skill 发现、按需读取
├── tools/                  # 论文/网页检索、浏览器、PDF、知识库
└── evaluation/
    └── runner.py           # 评测集执行与评分
```

`core/schemas.py` 是业务数据模型，`models/` 是 LLM 服务集成，两者分开。`api.py` 保留通用路由，进一步拆分路由可在后续独立改动中进行。

## 其他目录

| 目录 | 内容 |
|---|---|
| `frontend/` | React、TypeScript、Vite 工作台；当前入口是 `src/AgentApp.tsx` |
| `tests/` | 按 agent、research、models、persistence、core、memory、workspace、execution、tools、evaluation 分组；根 conftest 提供共同 fixture |
| `docs/architecture/` | 当前目录与已落地模块架构说明 |
| `docs/guides/` | 功能使用、配置和限制说明 |
| `docs/archive/` | 历史设计与验收报告，不能直接当作当前实现 |
| `docs/superpowers/` | 历次设计和实施记录，保留当时的路径背景 |
| `scripts/` | CLI 演示、评测、回归和界面检查脚本 |
| `config/` | 配置模板及本地 MCP 配置 |
| `native/` | Windows 原生文本检索 helper 源码与构建脚本 |
| `data/` | 运行数据库、记忆、Provider 配置和评测数据 |
| `.tmp/` | pytest 临时数据、历史测试产物、本次迁移备份与清单 |

## 执行关系

前端提交普通任务到 `/tasks`，提交研究任务到 `/research`。API 写入 SQL 任务，Worker 领取后按任务类型执行 `agent.runner` 或 `research.graph`。执行产物与事件保存回 Repository；研究流程另保存 LangGraph 检查点。动态子 Agent 位于领取父任务的 Worker 线程池中。

普通 Agent 使用 `TaskCapabilities` 选择只读工具、按授权使用 MCP 和读取 Skill，可为已有文件提出修改提案。记忆提供历史背景，不能自动成为研究证据或操作授权。研究流程负责证据片段、论断引用、审核修订和可选版本审批。

## 启动和测试

现有 API/Worker 入口和运行数据路径保持不变：

日常启动只需在根目录运行 `.\start.bat`。它调用 `scripts/start.py`，在同一终端管理 API 和 Worker，按 `Ctrl+C` 一起停止。可传 `--port 8001` 更换端口。

```powershell
uv run python -m uvicorn research_agent.api:app --host 127.0.0.1 --port 8000
uv run python -m research_agent.worker
uv run python -m pytest
```

pytest 默认将临时文件写到 `.tmp/pytest`，全局测试配置会自动创建父目录；pytest 缓存位于 `.tmp/pytest-cache`。可通过 `--basetemp` 选择其他目录。Windows 原生 helper 具有目录 ACL 限制，原生诊断可按 [原生组件说明](../../native/windows-sandbox/README.md) 选择其构建目录内的测试路径。

旧内部 Python 模块路径已统一替换；自行编写的脚本需参照本文件使用新导入路径。HTTP 接口、数据库 schema、环境变量、凭证和前端输出路径保持原样。
