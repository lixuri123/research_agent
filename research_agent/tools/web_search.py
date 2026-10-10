"""网页检索：Tavily、SerpAPI 与无需密钥的公开搜索回退。"""

from __future__ import annotations

import httpx

from research_agent.core.config import get_settings
from research_agent.core.schemas import Document, RetrievalResult
from research_agent.tools import register_tool
from research_agent.core.budget import remaining_timeout, BudgetExceeded


@register_tool("web_search", description="搜索公开网页，返回标题、链接和摘要。输入搜索词；随后 browser_read 阅读正文")
def web_search(query: str) -> RetrievalResult:
    s = get_settings()
    docs: list[Document] = []
    tool = "tavily"
    errors = []

    if s.tavily_api_key:
        try:
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": s.tavily_api_key, "query": query, "max_results": 8},
                timeout=remaining_timeout(s.request_timeout),
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                docs.append(
                    Document(
                        id=f"tavily-{r.get('url', '')}",
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        abstract=r.get("content", ""),
                        source="web",
                    )
                )
            if docs:
                return RetrievalResult(query=query, documents=docs, tool_used=tool)
        except BudgetExceeded:
            raise
        except Exception as exc:
            errors.append(f'Tavily: {type(exc).__name__}')

    tool = "serpapi"
    if s.serpapi_api_key:
        try:
            resp = httpx.get(
                "https://serpapi.com/search.json",
                params={"api_key": s.serpapi_api_key, "q": query, "num": 8},
                timeout=remaining_timeout(s.request_timeout),
            )
            resp.raise_for_status()
            for r in resp.json().get("organic_results", []):
                docs.append(
                    Document(
                        id=f"serpapi-{r.get('link', '')}",
                        title=r.get("title", ""),
                        url=r.get("link", ""),
                        abstract=r.get("snippet", ""),
                        source="web",
                    )
                )
        except BudgetExceeded:
            raise
        except Exception as exc:
            errors.append(f'SerpAPI: {type(exc).__name__}')

    if not docs:
        from research_agent.tools.browser import public_web_search
        tool = 'duckduckgo'
        try:
            docs = public_web_search(query)
        except BudgetExceeded:
            raise
        except Exception as exc:
            errors.append(f'DuckDuckGo: {type(exc).__name__}: {exc}')
    if not docs:
        return RetrievalResult(query=query, tool_used='web_search',
            error='网页搜索没有可用结果；' + '; '.join(errors or ['搜索服务未返回结果，可能限流或要求验证码']))
    return RetrievalResult(query=query, documents=docs, tool_used=tool)
