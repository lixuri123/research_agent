# 架构设计说明

> 历史文档：保留设计与实施背景，当前结构请参阅 [目录说明](../architecture/directory-structure.md)。

## 1. 总体设计

系统采用 **多智能体 + 有状态工作流** 架构。LangGraph 将五个智能体组织为一张有向状态图，通过共享的 `ResearchState` 传递中间产物，实现可观测、可恢复、可循环的协作流程。

### 核心设计原则

1. **职责单一**：每个智能体只做一件事，通过结构化协议通信。
2. **显式状态**：所有中间产物都写入 `ResearchState`，不依赖隐式全局变量。
3. **健壮优先**：超时重试、异常降级、迭代上限、人工确认四层防线防止失控。
4. **可评测**：任务完成率、引用准确率、响应延迟三维度可量化。

## 2. 工作流

```
plan ──▶ retrieve ──▶ read ──▶ write ──▶ review
                                            │
                      ┌─────────────────────┤
                      │ 未通过且 iteration < MAX_ITERATIONS
                      │      └──▶ retrieve（补充材料）──▶ read ──▶ write ──▶ review
                      │
                      │ 未通过且 iteration >= MAX_ITERATIONS ──▶ finalize（强制结束）
                      │
                      │ 通过 ──▶ human_confirm ──▶ finalize
```

### 循环反馈机制

`review` 节点后的条件路由 `route_after_review`：

- **通过**（`approved` 或 `score >= review_threshold`）→ 进入人工确认
- **未通过且未达上限** → 回到 `retrieve` 补充检索，重新阅读写作
- **未通过且达上限** → 强制 `finalize`，避免死循环

每次循环 `iteration` 递增，上限由 `MAX_ITERATIONS` 控制。

## 3. 智能体通信协议

智能体之间通过 Pydantic 模型约束结构：

| 接口 | 输入 | 输出 |
|------|------|------|
| `PlannerAgent.plan` | topic, requirements | `list[SubTask]` |
| `RetrieverAgent.retrieve` | SubTask | `list[RetrievalResult]` |
| `ReaderAgent.read_documents` | documents, topic | `dict[str, str]` |
| `WriterAgent.write` | topic, summaries, documents | `DraftReport` |
| `ReviewerAgent.review` | DraftReport, topic | `ReviewFeedback` |

LLM 结构化输出通过 `with_structured_output(schema)` 约束，另配 `parse_json_block` 兜底解析。

## 4. 工具调用与降级

工具注册中心 `tools/__init__.py` 维护 `_registry`，`run_tool` 统一捕获异常降级：

```python
def run_tool(name, query) -> RetrievalResult:
    try:
        return fn(query)
    except Exception as exc:
        return RetrievalResult(query=query, tool_used=name, error=...)
```

Retriever 根据 query 关键词选择工具，论文检索为空时自动追加网页检索兜底。

## 5. 健壮性设计

| 机制 | 实现位置 | 说明 |
|------|---------|------|
| 超时重试 | `llm.py` (tenacity) | 3 次指数退避 |
| 工具异常降级 | `tools/__init__.py` | 统一 try/except |
| 最大迭代 | `graph.py` `route_after_review` | `MAX_ITERATIONS` |
| 人工确认 | `graph.py` `human_confirm_node` | 可挂接审批 |
| 队列降级 | `queue.py` | Redis 不可用→内存 |

## 6. 部署

- `docker-compose.yml`：Milvus（standalone + etcd + minio）+ Redis
- `Dockerfile`：应用镜像，`uvicorn` 启动
- API 通过 `/research` 提交异步任务，`/research/{id}` 轮询结果
