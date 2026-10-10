# MCP 客户端与 Skill 使用

本轮只写代码、更新依赖锁文件，没有安装依赖、连接 MCP Server、发起模型调用、运行测试或构建前端。先在根目录执行 `uv sync --extra browser --frozen`；如不用 Chromium，可以只执行 `uv sync --frozen`。工作台变更需在 frontend 目录执行 `npm run build`，然后重启 API 与 Worker。

## MCP 配置

复制 `config/mcp.example.json` 到 `config/mcp.json`，按实际 Server 修改地址、命令、参数与真实工具名，再将需要的 Server 设置为 `enabled: true`。示例默认全禁用，不会自动安装或运行第三方程序。`MCP_CONFIG_PATH` 指向这个文件。配置文件是受信任的本地操作员配置，不接受 Agent 修改命令或新增连接地址。

支持 `stdio`、`streamable-http` 和旧版 `sse`。实现使用官方 Python MCP SDK v1，依赖限制为 `mcp>=1.26,<2`。连接完成后进行 initialize、分页 list_tools；调用前重新发现该工具并校验 inputSchema，最后 call_tool。每次操作建立并关闭会话，stdio 子进程也随之关闭。`POST /mcp/servers/{server}/connect` 是连接检查并发现工具，不是永久保持连接。

当前会话按操作管理，适合搜索、读取和单次执行工具；不能依赖前一次会话保留浏览器页面或其他服务端会话状态。需要此类操作时，应由工具参数携带外部持久状态标识，或将相关步骤放入同一次服务端工具调用。当前不提供 OAuth 浏览器授权、sampling、elicitation、MCP resource/prompt 管理或二进制展示；image/audio/blob 的二进制数据不会放入文本模型上下文。

服务器 `allowed_tools` 必须列出允许的真实工具名，空列表不暴露任何工具。`read_only_tools` 是其中经你确认的只读工具。未列入只读列表的工具视为敏感工具，只有当前任务的 `authorized_mcp_tools` 包含其完整名称才供 Agent 使用。**不要仅根据 Server 的 readOnlyHint 就判断工具安全**；配置声明不提供进程或网络沙箱，stdio Server 仍具有启动它的操作系统账号权限。只使用可信 Server。

工具命名统一为 `mcp:server:tool`，例如 `mcp:research-http:save_note`。认证放在 headers/env 中，支持 `${VARIABLE_NAME}`；从进程环境读取，缺失时读取根目录 `.env`，仍缺失则返回配置错误。不要将密钥放入 URL、command 或 args。MCP 配置和凭证不进入工具目录、模型提示或配置查询响应；已解析的 header/env 值会在返回结果中脱敏。第三方 SDK 或 Server 自身的日志及业务内容仍需自行管理。

HTTP 可连接明确配置的 localhost/内网地址，因为这些地址由你提供，不接受模型动态指定。客户端不自动跟随 Streamable HTTP 重定向。单操作 timeout 与任务剩余时间取较小值；失败不自动重试写操作。任务执行仍为至少一次语义，调用成功但阶段记录前进程崩溃可能重复调用，涉及写入的 Server 应提供业务幂等参数。

接口：

- `GET /mcp/servers`：查看非敏感配置摘要，不建立连接。
- `GET /mcp/tools?server=research-http`：发现工具与 input_schema，`allowed` 标明是否在配置白名单，返回各 Server 的错误；不填 server 则检查所有启用项。未许可工具仅供查看真实名称和配置，不能调用或进入 Agent 工具目录。
- `POST /mcp/servers/research-http/connect`：初始化并发现工具，返回 connected/tools/errors。
- `POST /mcp/tools/call`：手工调用，body 为 `{"name":"mcp:research-http:search_papers","arguments":{"query":"RAG reliability"},"authorized":false}`。

手工调用敏感工具需显式 `authorized: true`，仍不能绕过配置白名单。API 是本地单用户入口，尚未提供身份认证，不能直接公开暴露这些接口。

## Skill 包

`SKILL_DIRS` 是 JSON 路径数组，默认 `["skills"]`，相对路径以 API/Worker 工作目录为基准。每个包放置 `skills/<包名>/SKILL.md`，可包含 `references/*.md` 等材料。支持 YAML frontmatter 中的 `name` 和 `description`；缺省时使用包目录名。

Skill 目录同时包括已发布、内容哈希有效且符合当前 owner/project/session 范围的程序记忆。候选程序记忆在工作台直接点击“发布 Skill”，提供已有评测记录 ID 与审核者。这里不会自动运行评测，也不会验证这些 ID 对应的外部记录。

接口：

- `GET /skills?project_id=...&session_id=...`：返回元数据和扫描错误，不自动加载所有正文。
- `GET /skills/{id}`：返回完整指令和版本；本地版本为内容哈希，已发布版本沿用程序记忆版本号。
- `GET /skills/{id}/reference?path=references/guide.md`：读取包内文本材料。拒绝绝对路径、`..`、越界 symlink、非文本类型和超限文件。

Agent 看到 Skill 名称/描述后可调用 `skill_read`（参数 `{"id":"目录返回的 Skill ID"}`），完整指令会加入后续各轮的指导上下文，并保存到持久化阶段和最终结果。`skill_reference`（参数 `{"id":"...","path":"references/guide.md"}`）读取已加载包的参考材料。已加载的 Skill 指令在任务恢复后沿用快照；若包版本改变，拒绝混用新版本参考文件。首次读取参考材料时记录其正文与哈希，但不能承诺尚未读取的参考文件在整个任务期间不变。

`SKILL_MAX_CHARS=24000` 限制单文件，`SKILL_CONTEXT_CHARS=48000` 限制同时加载的指令总量；指令过长会报错，不会静默截断。Skill 指令低于系统和用户要求，不作为事实证据，不能添加 MCP Server、扩大权限或自动运行 scripts。当前只实现指令与参考材料驱动的 Skill 运行方式，没有通用 shell 脚本执行器。

## 提交任务

普通 Agent 模式自动发现内置工具、已许可 MCP 工具和 Skill 目录。工作台可选 Skill，并通过“发现工具”查看配置工具；只读工具自动可用，敏感工具需要显式勾选授权。新建任务中的 project/session ID 只用于 Skill 范围与记录关联，不自动召回其他历史记忆。

`POST /tasks` 增加以下可选字段：

```json
{
  "task": "找与 RAG 可靠性相关的论文，并按已选 Skill 整理比较结果",
  "requirements": "标明实际阅读范围和来源",
  "project_id": null,
  "session_id": null,
  "skill_ids": [],
  "authorized_mcp_tools": [],
  "budget": {"require_approval": false}
}
```

`skill_ids` 从 `/skills` 获取；为空时 Agent 按需选择。工具调用通过 structured decision 的 `arguments` 对象传参，保留原内置工具的 query 字段兼容。能力目录、初始 Skill 指令、每步结果、动态 Skill 加载和错误均持久化；结果包含 loaded_skills、capability_errors 和 trace。MCP 工具的通用结构化内容作为 observation 供模型读取，尚不自动映射为研究图的 EvidenceChunk。

研究报告模式的 LangGraph 节点和审批流程保持不变；此轮新增 MCP/Skill 接入普通 Agent 任务，不把动态外部工具或 Skill 直接注入研究图。

Docker Compose 已为 API/Worker 同时只读挂载 `./config` 和 `./skills`。请先创建这些目录并放入实际配置；stdio 命令必须在容器内存在，主机上的 Windows 路径不能直接用在 Linux 容器配置中。
