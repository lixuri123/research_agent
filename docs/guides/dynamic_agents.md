# 动态 Agent 协作

## 使用方式

在工作台选择普通 Agent 任务，或使用现有 `POST /tasks`，按目标描述任务，例如：

> 分别阅读这两篇论文（附链接），比较方法和实验条件。如果比较需要更多证据，再核查原文。给出结论、局限与来源。

主 Agent 决定自己处理还是委派。独立任务可并行启动；主 Agent 观察真实结果后继续行动，不要求固定执行“规划—检索—阅读—写作”顺序。

已有 API 请求结构无需改变。例如：

```json
{
  "task": "阅读并比较我提供的两篇论文，必要时追加实验条件核查",
  "requirements": "返回真实来源，区分摘要与全文阅读",
  "budget": {"max_calls": 100, "max_tokens": 200000, "max_documents": 20, "deadline_seconds": 900}
}
```

更新 Python 代码后重启 API 和 Worker 即可，无需新增依赖或执行数据库迁移。选择研究报告模式仍运行原有研究图及其审批机制。

## 主 Agent 的协作工具

| 工具 | 输入 | 行为 |
|---|---|---|
| `agent_spawn` | `goal`、`completion_criteria`、可选 `context` 和 `mode` | 持久化一个子任务，立即返回稳定 ID |
| `agent_status` | 可选 `agent_id` | 返回当前任务内子 Agent 的进度、成果和来源；空 ID 查看全部 |
| `agent_wait` | 可选 `agent_id`、`timeout_seconds`（0–10，默认 5） | 等待指定子任务或全部任务，返回结果与尚未结束的 `pending` ID |
| `agent_cancel` | 可选 `agent_id` | 取消指定子任务，空 ID 取消全部 |

`mode=agent` 是通用子 Agent，拥有独立模型/工具循环。任务上下文只进入任务消息，不变成系统指令。工具目录继承父任务中的只读能力；子 Agent 不能提出文件写入提案、调用有副作用的 MCP 或再次委派。

`mode=research` 调用原有 LangGraph 研究流程，保留证据片段、论断及审核结果，并受同样的父工具白名单与共享预算约束。子流程使用独立检查点、不在父任务数据库发布报告，也不触发子流程内人工审批。它向主 Agent 返回真实的 `completed`、`partial` 或 `insufficient_evidence` 状态。普通任务最终汇总仍没有逐条论断审核及报告审批；需要这些保证时使用研究报告模式。

## 状态、恢复与取消

子任务状态为 `queued`、`running`、`completed`、`blocked`、`cancelled`，研究子任务还可能返回研究流程的其他终态。主 Agent 每轮收到最新状态，完成前必须等待或取消全部未结束的子任务。

注册表存入父 run 的 `delegation/v1` 阶段；决策及工具观察以 `child:<id>:...` 独立保存。恢复时继续 queued/running 子任务，复用已保存的观察与终态成果。相同委派操作 ID 对应同一子任务，输入不一致会被拒绝。旧版普通任务的历史文献也会迁入共享额度。

Worker 租约覆盖父子执行；失效 Worker 不能写入新的子任务成果。取消在实际模型/工具请求边界检查，包括模型重试和研究阅读循环。已开始的请求无法强制撤回，但结果不会继续触发子任务动作或覆盖取消状态。父任务结束前关闭并等待本地线程，等待时间取决于在途请求的超时。

子任务阶段记录是权威状态；进度事件写入失败会记录诊断日志，不会撤销已经保存的任务或成果。

模型调用预算由父子共享，原子预留先持久化再发起请求；存储失败不发布新的内存额度。文献数量按来源身份统一记账，同一来源正文更新不占用新的名额，研究证据提取前也检查共享额度。

外部调用采用至少一次语义：请求完成后、观察持久化前进程崩溃，恢复可能重复该请求。子任务并非独立的长期后台服务；第一版运行在领取父任务的 Worker 的有界线程池内。

## 配置与查看结果

| 环境变量 | 默认值 | 范围 |
|---|---|---|
| `AGENT_MAX_CHILDREN` | 6 | 0–20；0 禁止创建子任务 |
| `AGENT_CHILD_CONCURRENCY` | 2 | 1–4 |
| `SUBAGENT_MAX_STEPS` | 8 | 1–50 |
| `AGENT_MAX_STEPS` | 12 | 现有主 Agent 决策步数；等待也占一轮 |

这些限制不会给每个子 Agent 重置完整调用/token/deadline 预算。

`GET /tasks/{task_id}/events` 提供 `agent_spawned`、`agent_started`、`agent_progress`、`agent_finished`、`agent_cancelled` 事件。完成后的 `GET /tasks/{task_id}` 中，`result.agents` 保存子任务目标、状态、执行步骤、成果和来源；`result.documents` 汇集父子获取的来源，`result.budget_usage` 是整体用量。模型提示词里的成果摘录会标注截断，任务最终结果保留完整已保存产物。

目前只有主 Agent 向子 Agent 委派、子 Agent 返回结果这一层关系，没有任意 agent 消息网或递归团队生成。

## 验证

`tests/agent/test_dynamic_agent.py` 使用离线模型演示“两项阅读 → 接收结果 → 追加核查 → 交付”，并覆盖真实 Worker 领取、旧目录恢复、旧文献额度迁移及提前交付拦截。

`tests/agent/test_delegation.py` 覆盖真实线程并行、共享预算与来源额度、幂等、进度恢复、权限缩小、取消竞态、在途请求后停止、失效租约以及真实 LangGraph 研究子流程的证据核验。测试不调用付费模型或真实检索服务。
