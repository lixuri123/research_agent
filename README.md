# 个人 Agent 与科研工作台

项目提供普通个人 Agent 和基于 LangGraph 的科研工作流，共用持久化异步任务、独立 Worker、模型网关和预算机制。科研模式支持报告版本审批及原文证据关联。当前结构见 [项目目录与职责](docs/architecture/directory-structure.md)，功能指南与历史方案见 [文档导航](docs/README.md)。

## 当前能力

手机远程访问默认关闭。在桌面设置中配置可信 HTTPS Relay、生成邀请，并在桌面对照手机验证码后授权个人会话或指定工作空间。手机可查看会话、提交任务及确认报告/文件提案；部署与恢复方式见 [手机远程访问](docs/guides/mobile-remote.md)。Relay 由你部署，桌面需保持运行与联网。

左下角头像设置中的“关于我”支持开放的结构化人物属性：动态名称与层次、文本/数字/列表/对象内容、确认、失效、修改历史和删除。普通 Agent 已按规则选择性使用已确认人物属性，尚未自动提炼；见 [上下文组装](docs/guides/context-assembly.md)。更新后重启 API、Worker 并刷新网页，详见 [结构化人物描述](docs/guides/personal-profile.md)。

前端以对话为入口，支持绑定真实本地目录的工作空间：一个空间内保留多个会话，右侧浏览、预览与编辑文本文件；授权后普通 Agent 可按需读取文件、提出修改，用户查看差异并确认后写入原文件。不提供直接自主写入或通用终端。另有默认关闭的 Windows 固定文本检索，采用 dsh 风格受限令牌后端；配置与边界见 [本地工作空间](docs/guides/workspaces.md) 和 [本地受限执行](docs/guides/local-execution.md)。

普通 Agent 任务在原有 API、队列、Worker、SQL 存储中执行，不替换研究图。工作台选择“普通 Agent 任务”，链路为：提交 → 排队领取 → 简短规划 → 模型选择工具 → 搜索/阅读 → 观察结果并继续 → 交付结果或说明阻塞。每步结果持久化；Worker 中断后可复用已完成步骤。外部请求仍按至少一次语义处理。

普通 Agent 可按需启动并行子 Agent，通过 `agent_spawn`、`agent_status`、`agent_wait`、`agent_cancel` 委派、观察和收集成果，再根据结果追加核查。通用子 Agent 自主选择只读工具；`research` 子任务调用现有证据审核工作流。父子共享任务预算和文献额度，默认并发 2、最多 6 个子任务，状态及执行步骤持久化。详见 [动态 Agent 协作](docs/guides/dynamic_agents.md)。

普通 Agent 已增加 MCP 客户端（stdio / Streamable HTTP / SSE）和本地/已发布 Skill 的发现、按需加载与参考材料读取。配置与接口说明见 [MCP 与 Skill 使用](docs/guides/mcp_skills.md)。复制 `config/mcp.example.json` 为 `config/mcp.json` 后填入实际 Server 并启用；默认不连接任何第三方 Server。Skill 默认从 `skills/**/SKILL.md` 发现。

可用工具：`paper_search`（Semantic Scholar，失败回退 HTTPS arXiv）、`web_search`（Tavily / SerpAPI / 无密钥 DuckDuckGo 回退）、`browser_read`（网页正文、页面链接和 PDF 文本）。搜索结果与实际正文分开记录。原有研究模式会对每个子任务的前 `RETRIEVAL_READ_LIMIT` 个结果尝试读取正文，随后继续原有证据审核流程。

搜索服务可能限流、要求验证码或被网络阻断；登录页面、付费墙及扫描 PDF 的 OCR 尚未支持。普通任务未接入研究模式的逐条论断审核，现已支持有范围及预算限制的长期记忆自动召回和只读搜索，见 [普通 Agent 记忆](docs/guides/agent_memory.md)。Agent 文件提案须人工确认后才能写入；当前没有代码执行、复现程序运行或招聘网站登录能力，不会声称完成这些行动。

### 本次代码更新后启动

### 模型 Provider 设置

左下角头像设置中的“模型 Provider”支持新增、编辑、删除多个 OpenAI 兼容服务，每个服务独立配置名称、API 基础地址、API Key、模型列表和默认模型；模型列表每行一个 ID。输入框旁“使用模型”按服务分组选择，普通 Agent 和深度研究都会使用提交时选定的服务与模型。本地免密钥服务可留空；编辑已有服务时密钥留空只保留该服务的密钥，新增服务不会复用其他服务的密钥。

“测试连接”使用当前表单的默认模型发送一次最多 16 个输出 Token 的聊天请求，不保存配置，可能产生少量费用；“保存服务”写入 `data/provider.json`，不修改 `.env`。接口不会回显密钥，但文件中包含明文凭证，须按本地密钥文件管理。旧单服务文件自动显示为“原有服务”，首次编辑或选择任务模型时迁移，原有密钥及角色模型保留。每个服务最多 100 个模型，最多 30 个服务。

API 与 Worker 须使用相同工作目录，或将 `PROVIDER_CONFIG_PATH` 设为相同的绝对路径。任务只记录服务 ID、模型和配置版本，不记录密钥；Worker、研究节点和子 Agent 恢复对应版本，排队、执行及恢复均不会因后续修改或删除服务而切换配置。删除服务将其移出选择列表；已有任务依赖的历史配置及凭证仍保留在文件中。首次更新代码须重启 API 与 Worker；之后保存服务无需重启。

多服务接口：`GET/POST /settings/providers`、`PUT/DELETE /settings/providers/{id}`、`PUT /settings/providers/default/selection`、`POST /settings/providers/test`。两种任务接口可传 `model_selection: {provider_id, model}`，仅首次接收固定版本，使用相同幂等键重试保留原任务。旧 `/settings/provider` 读取/保存/测试接口继续支持；省略模型选择的旧任务调用保持默认服务及角色路由规则。

### LangSmith 执行追踪

在已有 `.env` 中配置以下字段（不要覆盖其他配置）：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的_LangSmith_API_Key
LANGSMITH_PROJECT=research-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_WORKSPACE_ID=
```

`LANGSMITH_WORKSPACE_ID` 可选；组织级或关联多个 Workspace 的 Key 按账号要求填写。Endpoint 必须对应账号所在区域或自建服务；例如欧盟区域使用 `https://eu.api.smith.langchain.com`。默认关闭追踪，`LANGSMITH_TRACING=false` 时 Worker 不上报，即使进程中存在旧的 `LANGCHAIN_TRACING_V2=true`。启用但 Key 缺失时记录提示并继续执行任务。

修改后重启 Worker；API 与 Worker 共用 `.env` 时也重启 API。发送无附加要求的“你好”，在 [LangSmith](https://smith.langchain.com/) 中打开 `research-agent` 项目，可看到 `agent_task → model_text → ChatOpenAI`，只调用一次模型，不加载工具或检索记忆；重复问候的会话历史也适用。带真实任务、其他历史背景或显式 Skill/MCP 授权时仍执行完整任务流程。复杂任务包含独立文本规划、`model_structured`（决策）、`agent_tool`、`agent_delegation`、`child_agent`；研究模式记录 `research_task` 和 LangGraph 节点。

模型输出校验记录为 `output_validation`，规划校验记录为 `plan_validation`，解析错误可在对应节点查看。格式错误默认只纠正一次（`LLM_MAX_FORMAT_REPAIRS=1`），会追加明确反馈；网络错误单独按 `LLM_MAX_ATTEMPTS` 重试，每次请求均计入预算。结构化输出默认使用提示词约束（`LLM_STRUCTURED_OUTPUT_MODE=prompt`）；确认模型服务支持后，可设置为 `json_object`，通过 API 的 `response_format` 强制 JSON 对象，再进行本地 Schema 校验。服务不支持时会报告错误，不自动切换模式。普通文本调用不发送此参数。

追踪包含实际模型提示词、会话背景、回复，以及工具参数和结果（包括读取的网页、记忆和本地材料）；启用后会发送到上述 Endpoint。入口不序列化 Repository、Worker、Provider 配置或凭证对象。追踪元数据包含 `task_id`、业务 `run_id`、`session_id` 和模型标识；`GET /tasks/{task_id}/events` 的 `langsmith_trace_started` 事件记录尝试创建的远端 `trace_id` 和项目名称，它不代表上报已被服务端接收。同一任务恢复执行会创建新的追踪，通过相同业务 `run_id` 关联。

上报初始化或提交失败会保留本地任务结果，不重新执行业务操作。云端没有追踪时先检查 Worker 日志、Key、区域 Endpoint 和网络连通性。配置和追踪接口参见 [官方文档](https://docs.langchain.com/langsmith/trace-with-langchain)。

### 启动命令

已有 `.venv`、`.env` 和前端构建时，在项目根目录执行：

```powershell
.\start.bat
```

脚本在同一终端启动 API、Worker 和远程连接器，API 就绪后再启动后两者。连接器默认空闲，桌面设置启用远程访问后才连接 Relay；连接器退出会每 5 秒尝试重启，不影响 API/Worker。访问 `http://127.0.0.1:8000/`；按 `Ctrl+C` 会一起停止。端口占用或 API/Worker 退出时会停止本次启动，不会复用已有服务。可用 `.\start.bat --port 8001` 指定其他端口。也可在文件管理器中双击 `start.bat`。

以下安装步骤仅首次准备环境或更新依赖时需要。

以下命令由你执行；请勿覆盖已有 `.env`。本地受限执行的启用步骤另见 [说明](docs/guides/local-execution.md)。

```powershell
# 在项目根目录安装浏览器可选依赖
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv sync --extra browser --frozen
uv run --extra browser playwright install chromium

# 更新工作台静态文件，否则 8000 端口仍显示旧界面
Set-Location frontend
npm install --cache .npm-cache
npm run build
Set-Location ..
```

在已有 `.env` 添加 `BROWSER_BACKEND=chromium` 可启用 JavaScript 页面渲染；默认 `http` 无需浏览器安装，适合静态网页和 PDF。`LLM_API_KEY` / `LLM_BASE_URL` 兼容现有 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，两者同时设置时优先使用前者。支持 `LLM_TEMPERATURE`。`AGENT_MAX_STEPS=12` 控制普通任务执行步数。

分别在两个终端（都位于项目根目录）启动：

```powershell
# 终端一
uv run --extra browser python -m uvicorn research_agent.api:app --host 127.0.0.1 --port 8000
# 终端二
uv run --extra browser python -m research_agent.worker
```

访问 <http://127.0.0.1:8000/>，选择“新建任务”→“普通 Agent 任务”，输入目标并提交。详情展示持久化工具调用事件、获取的来源、最终成果和错误原因。

普通任务 API 示例（需 API 和 Worker 已启动）：

```powershell
$body = @{task='找 5 篇与 RAG 可靠性相关的论文，阅读可访问正文，整理方法、局限和来源链接'; requirements='用中文输出，标明哪些只读到摘要'; budget=@{max_documents=20; require_approval=$false}} | ConvertTo-Json -Depth 4
Invoke-RestMethod http://127.0.0.1:8000/tasks -Method Post -ContentType 'application/json' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

`GET /tasks` 列出两种任务，`GET /tasks/{task_id}` 查询结果，`GET /tasks/{task_id}/events` 查询执行记录。原有 `/research` 路由保持兼容。普通任务不支持完成前审批；如需版本审批请用研究报告模式。北邮网关的网络连通性仍需在你的运行环境中确认。

- Postgres 作为部署模式的任务/报告/阶段记录权威存储；本地开发可显式选择文件 SQLite。
- API 只提交/查询任务，独立 Worker 领取任务、续租、执行并恢复持久化检查点。
- 任务、执行、会话和报告版本分别标识；审批绑定不可变版本与哈希。
- 文档按内容生成版本与片段 ID，保留字符位置、来源和全文/摘要范围。
- 阅读发现关联证据；写作论断关联原文，结构校验与语义核验分别执行。
- 审核修订有限次；高评分不覆盖显式拒绝，人工拒绝不发布或写入可信结论。
- 总 deadline、模型调用、保守 Token 预留、输出长度、文献数量均有预算。
- 阶段输出可复用，预算账本在模型调用前持久化；外部请求按至少一次考虑。

流程：规划 → 检索/分块 → 片段阅读 → 论断写作 → 引用及证据审核 → 有限修订 → 可选人工审批 → 完成。

状态：queued、running、waiting_approval、completed、partial、insufficient_evidence、rejected、failed。
partial 的草稿可查看，但不会放入 final_report。仅检索到摘要时不得描述为全文阅读。

## 本地运行（Python 3.10–3.12）

```powershell
uv sync --extra dev
Copy-Item .env.example .env
# 编辑 .env 中的模型及检索配置；不要提交密钥。
```

如果系统级 `UV_CACHE_DIR` 指向不可写目录，可仅对当前终端使用项目缓存：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv sync --extra dev --frozen
```

在项目目录开两个终端，使用相同环境与存储配置：

```powershell
uv run python -m uvicorn research_agent.api:app --host 127.0.0.1 --port 8000
uv run python -m research_agent.worker
```

### Web 工作台

生产构建由 FastAPI 在同一端口提供：

```powershell
Set-Location frontend
npm install --cache .npm-cache
npm run build
Set-Location ..
uv run python -m uvicorn research_agent.api:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>。开发时可另开终端运行 `npm run dev`，访问 <http://127.0.0.1:5173/>；Vite 会把 API 请求代理到 8000 端口。

提交任务（重复相同幂等键与输入返回原任务，输入不同返回 409）：

```powershell
$body = @{topic='检索增强生成技术综述'; budget=@{require_approval=$true}} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/research -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key'='rag-review-1'} -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

查询 `GET /research/{task_id}`，待审批时读取 current_report_version_id，并调用：

```text
POST /research/{task_id}/approve
{"approved": true, "report_version_id": "当前版本 ID"}
```

同意后返回 queued，由 Worker 恢复；拒绝立即进入 rejected。旧版本或冲突决定返回 409。
`GET /reports/{report_version_id}` 返回报告版本，`GET /research/{task_id}/events` 返回持久化状态事件。

会话与轻量记忆接口：`POST /sessions/{session_id}/messages` 写入 `{ "role": "user|assistant|tool|system", "content": "..." }`；`GET /sessions/{session_id}/messages` 读取完整历史，加 `?limit=20` 只读取近期窗口。`POST /memories` 提交候选条目，必填 `title`、`content`、`source_type`、`source_id`，可选 `kind=semantic|episodic|procedural`、`scope=owner|project|session` 和对应范围 ID。`GET /memories?status=candidate` 用于审核，`POST /memories/{id}/activate` 激活普通记忆；`PUT /memories/{id}` 更新后重新进入候选，`POST /memories/{id}/invalidate` 使其失效，`DELETE /memories/{id}` 删除。`GET /memories/search?query=...` 只返回当前范围内有效且 Markdown 哈希一致的条目。程序记忆需使用 `POST /memories/{id}/publish-skill` 提供 `evaluation_id` 和 `reviewed_by`，之后可由 `GET /skills` 查看版本化 Skill 文件索引。

默认 `MEMORY_MARKDOWN_DIR=data/memories`。API 与 Worker 如使用同一数据库，必须挂载同一 Markdown 目录；Docker 配置已使用共享持久卷。当前 API 仍是本地单用户入口，不对公网提供身份认证。`MemoryManager.assemble(...)` 可按上下文总量、输出预留和安全余量组装近期消息与记忆；研究工作流尚不自动调用它。
来源、评测及审核者 ID 当前由调用者提供，系统尚未自动核验其对应记录；普通 Agent 已可按需加载 Skill 指令和参考材料，但研究图尚未接入 Skill，且 Skill scripts 不自动执行。

CLI 使用相同任务存储，独立 Worker 必须已启动：

```powershell
uv run python scripts/demo.py --topic '检索增强生成技术综述' --wait
```

## Docker / Postgres

```text
docker compose up --build -d postgres api worker
```

API 只映射到宿主机 127.0.0.1:8000；postgres 数据使用持久卷。首次启动创建本阶段新表及检查点 schema。存储不可用时请求失败，不降级到内存。
Milvus 等检索基础设施可按需要使用 `docker compose --profile knowledge up -d` 启动。

## 离线验收

```powershell
uv sync --extra dev --frozen
uv run pytest tests -q
uv run python scripts/regression.py
```

测试通过可控的模型/检索替身验证真实图、API、文件数据库及检查点，不消耗模型额度。
Postgres 集成检查需设置 TEST_DATABASE_URL；没有服务器时该项显式跳过。真实模型输出质量需另行做端到端评测。

评测：`uv run python scripts/evaluate.py --dataset data/eval_dataset.json`。expected_sources 用于期望来源精确率；无可评分引用记 null，不把失败或无引用记满分。citation_validity 是结构合法性，不等于论断的语义支持率。

## 第一阶段边界

- 当前 API 是本地单用户入口，owner_id 固定 local；多用户身份与权限接入属于后续加固。
- 抓取工具返回的正文/摘要存入阶段快照；当前不自动下载全部论文 PDF，来源不足时报告只能限定在已获取材料范围。
- 已发布结论只保存带版本及证据引用的索引，旧的全局记忆与辩论入口不参与发布流程。
- 轻量记忆提供 SQLite 会话日志、Markdown 长期记忆正文和来源/范围/版本/状态管理；候选记忆需显式激活。设计见 [docs/architecture/memory_architecture_light.md](docs/architecture/memory_architecture_light.md)。研究图的召回节点仍未接入，历史结论不会自动成为报告证据。
- 不承诺模型或工具请求恰好一次；进程在外部调用后崩溃可能重复调用。业务阶段产物与最终发布通过持久化记录约束。
- 取消、SSE、跨存储 outbox、语义支持率人工标注校准和大规模并行是后续阶段。
- 新 schema 使用 create_all 初始化，尚无历史 SQL schema 升级迁移；旧 Redis 状态不会自动导入。上线升级需制定迁移与备份。
- 配额的输入 Token 预留采用 UTF-8 字节上界，成功调用按实际用量结算；超时/失败保留预留，避免重启后预算增加。
