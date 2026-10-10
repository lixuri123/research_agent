"""Reader 智能体：阅读并总结检索到的文献。"""

from __future__ import annotations

from research_agent.models.gateway import LLMClient
from research_agent.models.router import get_llm_for
from research_agent.core.schemas import Document
from research_agent.core.budget import BudgetExceeded


class ReaderAgent:
    """对每篇文档提炼摘要与关键信息，形成研究笔记。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm_for("reader")

    def summarize(self, doc: Document, topic: str) -> str:
        content = doc.content or doc.abstract or ""
        if not content:
            return ""
        # 控制长度，避免超出上下文
        content = content[:6000]
        prompt = f"""你是文献阅读智能体（Reader）。

研究主题：{topic}
文献标题：{doc.title}
文献作者：{', '.join(doc.authors) if doc.authors else '未知'}
文献年份：{doc.year or '未知'}

文献内容（摘要/正文）：
{content}

请用 150-250 字总结该文献的核心贡献、方法、结论，并指出其与研究主题的关联。
"""
        return self.llm.invoke(
            [LLMClient.system("你是严谨的学术文献阅读专家。"), LLMClient.human(prompt)]
        )

    def read_documents(self, documents: list[Document], topic: str) -> dict[str, str]:
        summaries: dict[str, str] = {}
        for doc in documents:
            try:
                s = self.summarize(doc, topic)
                if s:
                    summaries[doc.id] = s
            except BudgetExceeded:
                raise
            except Exception:
                summaries[doc.id] = doc.abstract or doc.content[:500]
        return summaries
