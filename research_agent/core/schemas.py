"""核心类型定义：多智能体状态、任务分解、文档、检索结果等结构化协议。"""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

# 智能体角色标识
AgentRole = Literal["planner", "retriever", "reader", "writer", "reviewer", "human"]


class SubTask(BaseModel):
    """Planner 分解出的子任务。"""

    id: str = Field(description="子任务唯一编号")
    description: str = Field(description="子任务描述")
    query: str = Field(description="对应的检索 query")
    status: Literal["pending", "done", "failed"] = "pending"


class Document(BaseModel):
    """一篇被检索/解析出的文献。"""

    id: str = Field(description="文档唯一标识")
    title: str = ""
    url: str = ""
    source: Literal["arxiv", "semantic_scholar", "web", "pdf", "knowledge_base"] = "web"
    abstract: str = ""
    content: str = ""
    year: Optional[int] = None
    authors: list[str] = []
    links: list[dict[str, str]] = Field(default_factory=list, description='Discovered page and open-access PDF links')


class RetrievalResult(BaseModel):
    """Retriever 返回的结果：文档 + 相关度 + 来源工具。"""

    documents: list[Document] = []
    query: str = ""
    tool_used: str = ""
    error: Optional[str] = None


class Citation(BaseModel):
    """Writer 产出中的引用条目。"""

    index: int = Field(description="正文中的引用编号")
    source_id: str = Field(description="对应 Document.id")
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceChunk(BaseModel):
    id: str
    document_id: str
    version_id: str
    text: str
    start: int
    end: int
    scope: Literal["full_text", "abstract"]
    source_url: str = ""


class Finding(BaseModel):
    id: str
    document_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: str = ""


class Claim(BaseModel):
    id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    kind: Literal["fact", "inference", "recommendation"] = "fact"
    verification: Literal["unverified", "supported", "unsupported", "uncertain"] = "unverified"


class DraftReport(BaseModel):
    """Writer 产出的报告草稿。"""

    title: str = ""
    content: str = Field(description="Markdown 格式正文")
    citations: list[Citation] = []
    claims: list[Claim] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class ReviewFeedback(BaseModel):
    """Reviewer 产出的审核反馈。"""

    approved: bool = False
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[str] = []
    suggestions: str = ""
    action: Literal["retrieve_more", "extract_again", "rewrite", "stop"] = "rewrite"
    claim_verdicts: dict[str, str] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """智能体间通信协议：统一封装结构化输出。"""

    role: AgentRole
    payload: dict[str, Any] = {}
    error: Optional[str] = None


# ---- 记忆系统类型 ----
MemoryType = Literal["short_term", "long_term", "entity", "episodic", "semantic", "procedural"]


class MemoryItem(BaseModel):
    """一条记忆单元：写入记忆层的最小数据结构。"""

    id: str = Field(description="记忆唯一标识")
    content: str = Field(description="记忆内容")
    memory_type: MemoryType = "long_term"
    metadata: dict[str, Any] = {}
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要度，用于压缩/遗忘")
    created_at: float = Field(default=0.0, description="Unix 时间戳")


# ---- LangGraph 状态（TypedDict，供图节点读写）----
class ResearchState(TypedDict, total=False):
    # 输入
    topic: str
    requirements: str

    # 记忆（阶段一新增）
    context: str  # 记忆召回后拼接的上下文
    recalled_memories: list[MemoryItem]  # 召回的记忆片段
    session_id: str  # 会话标识，用于短期记忆隔离

    # 规划
    plan: list[SubTask]
    current_subtask_index: int

    # 检索
    retrieval_results: list[RetrievalResult]
    documents: list[Document]

    # 阅读
    summaries: dict[str, str]  # doc_id -> 摘要
    extracted_notes: str

    # 写作
    draft: Optional[DraftReport]

    # 审核 / 循环控制
    review: Optional[ReviewFeedback]
    iteration: int
    human_approved: bool
    need_revision: bool

    # 终态
    final_report: Optional[DraftReport]
    status: str
    errors: list[str]
    run_id: str
    task_id: str
    owner_id: str
    evidence: list[EvidenceChunk]
    findings: list[Finding]
    context_used: bool
    max_revisions: int
    require_approval: bool
    budget: dict[str, Any]
    budget_usage: dict[str, Any]
