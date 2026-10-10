# 模型网关

`research_agent.models.gateway.ModelGateway` 负责配置模型、调用与输出校验、预算和统一重试。`LLMClient` 是兼容别名，现有 Agent 自动使用同一实现。

- 模型名称、地址、凭证、温度、超时和输出上限来自 Settings；角色模型选择由 `research_agent.models.router` 提供。
- `invoke()` 返回非空文本；`invoke_structured()` 返回通过 Pydantic Schema 校验的实例。代码围栏 JSON 可解析，无效 JSON 不静默转成空对象。
- `LLM_MAX_ATTEMPTS` 默认 3，包含首次调用；`LLM_RETRY_DELAY` 默认 1 秒，指数退避且单次等待最多 30 秒。底层 SDK 重试关闭，避免重复重试。
- 网络连接失败、超时、HTTP 408/429/5xx 和无效输出可以重试。认证、权限、其他客户端请求错误和预算耗尽不重试。
- 使用 `budget_scope(Budget(...))` 注入调用方预算。每次实际尝试前预留次数和 Token，成功响应按可靠 usage 结算；失败调用保留预留。输出校验失败也计费。不注入预算时仍受尝试数、请求超时和输出上限约束。
- 重试等待和每次调用均检查任务 deadline。网关不修改任务最终状态，不决定业务授权，不自动切换模型。
- `transport` 和 `settings` 可注入，以便进行离线检查，无需访问实际模型服务。

重试不能保证远端请求恰好一次。断线或超时后，远端可能已经完成推理并产生费用。

旧调用方使用的 `parse_json_block()` 保留原有宽松语义；需要严格结构校验的调用应使用 `invoke_structured()`。
