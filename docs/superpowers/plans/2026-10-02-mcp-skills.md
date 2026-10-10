# MCP 客户端与 Skill 运行接入

目标：在现有 FastAPI → 持久化队列 → Worker → 个人 Agent 链路中加入可配置 MCP 工具和按需加载的 Skill，不替换研究图。

约束：用户明确要求直接实施、只写代码、不运行测试或真实服务调用。本项目未建立 Git 仓库，不提交或创建工作树。

实施顺序：

- [x] `research_agent/integrations/mcp_client.py`：官方 MCP v1 SDK；stdio/Streamable HTTP/SSE；配置验证、初始化、分页发现、JSON Schema 参数校验、调用超时、结果归一化、配置级工具许可和任务级写授权；会话每次操作关闭。
- [x] `research_agent/skills.py`：扫描配置的本地目录和已有已发布程序记忆；提供 metadata/read/reference；路径与大小限制；加载完整正文而非截断的指令；版本哈希用于任务快照。
- [x] `research_agent/capabilities.py`：组合内置工具、MCP 工具、Skill 元工具；统一 object 参数与 observation，保留执行审计。
- [x] `research_agent/personal_agent.py`：结构化 decision 增加 arguments；持久化工具目录与已加载 Skill 版本；每轮加入已加载指令，参考文件作为材料；不允许 Skill 自行扩大授权。
- [x] `research_agent/api.py`：MCP 配置/发现/调用及 Skill 列表/详情/参考接口；任务可指定 Skill、项目/会话、MCP 工具授权。
- [x] `.env.example`、`config/mcp.example.json`、README、pyproject/uv.lock：可直接使用的配置示例与用户安装步骤；不启动示例 Server。

接口：`MCPClient.discover(server=None)` 返回工具 metadata 与 server errors；`call(name, arguments, authorized=False)` 返回 observation。`SkillCatalog.list()` 返回 metadata；`read(id)` 返回完整指令；`reference(id,path)` 返回 UTF-8 文本。`TaskCapabilities.catalog()` 返回工具目录；`call(name,args,query='')` 返回 observation。

验收由用户执行：stdio/HTTP/SSE 发现与调用、错误/超时、未授权写工具拒绝；本地与已发布 Skill 加载、参考路径越界拒绝、任务内版本固定；工作台发布 Skill 与任务执行轨迹。此次不声称运行验收通过。
