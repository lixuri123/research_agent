"""Retriever 智能体：自主选择工具并检索。"""

from __future__ import annotations

from research_agent.models.gateway import LLMClient
from research_agent.core.schemas import RetrievalResult, SubTask
from research_agent.tools import list_tools, run_tool
from research_agent.core.config import get_settings
from urllib.parse import urlsplit


class RetrieverAgent:
    """根据子任务选择合适工具执行检索，聚合结果。"""

    TOOL_HINTS = {
        "paper_search": ["论文", "paper", "学术", "研究", "文献", "paper", "arxiv"],
        "knowledge_base": ["知识库", "本地", "知识", "内部资料"],
        "web_search": ["网页", "web", "最新", "新闻", "官网", "资料"],
        "pdf_parse": ["pdf", "文档", "文件"],
    }

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def _choose_tool(self, query: str) -> str:
        """简单规则选工具；可替换为 LLM 决策。"""
        q = query.lower()
        if q.strip().startswith(('https://', 'http://')):
            return 'browser_read'
        scores: dict[str, int] = {}
        for tool, keywords in self.TOOL_HINTS.items():
            scores[tool] = sum(1 for k in keywords if k in q)
        if not any(scores.values()):
            return "paper_search"  # 默认论文检索
        return max(scores, key=scores.get)

    def retrieve(self, subtask: SubTask) -> list[RetrievalResult]:
        results: list[RetrievalResult] = []
        tool = self._choose_tool(subtask.query)
        primary = run_tool(tool, subtask.query)
        results.append(primary)

        # 若论文检索失败/为空，追加网页检索兜底
        if tool == "paper_search" and (primary.error or not primary.documents):
            results.append(run_tool("web_search", subtask.query))
        # Enrich a bounded number of discovery results with actual page/PDF text.
        candidates = [doc for result in results for doc in result.documents]
        for doc in candidates[:max(0, get_settings().retrieval_read_limit)]:
            if doc.content or not doc.url.startswith(('https://', 'http://')):
                continue
            target = doc.url
            if doc.links:
                target = next((link['url'] for link in doc.links if link.get('title') == 'Open-access PDF'), target)
            parsed = urlsplit(target)
            if parsed.hostname in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'} and '/abs/' in parsed.path:
                target = 'https://arxiv.org/pdf/' + parsed.path.split('/abs/', 1)[1]
            read = run_tool('browser_read', target)
            results.append(read)
            if read.documents:
                doc.content = read.documents[0].content
                # Keep the paper's stable identity and metadata for citation mapping.
                read.documents = []
        return results
