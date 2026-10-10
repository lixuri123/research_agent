"""论文搜索工具：Semantic Scholar API + ArXiv 兜底。"""

from __future__ import annotations

import httpx

from research_agent.core.config import get_settings
from research_agent.core.schemas import Document, RetrievalResult
from research_agent.tools import register_tool
from research_agent.core.budget import remaining_timeout, BudgetExceeded


@register_tool("paper_search", description="学术论文检索（Semantic Scholar → ArXiv 降级）")
def paper_search(query: str) -> RetrievalResult:
    """通过 Semantic Scholar 检索论文，失败时降级到 ArXiv。"""
    s = get_settings()
    docs: list[Document] = []
    tool = "semantic_scholar"
    errors = []

    try:
        headers = {"x-api-key": s.s2_api_key} if s.s2_api_key else {}
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": 10,
            "fields": "title,abstract,url,year,authors,externalIds,openAccessPdf",
        }
        resp = httpx.get(url, params=params, headers=headers, timeout=remaining_timeout(s.request_timeout))
        resp.raise_for_status()
        data = resp.json()
        for p in data.get("data", []):
            authors = [a.get("name", "") for a in p.get("authors", [])]
            docs.append(
                Document(
                    id=f"ss-{p.get('paperId', '')}",
                    title=p.get("title", ""),
                    url=p.get("url", ""),
                    abstract=p.get("abstract") or "",
                    year=p.get("year"),
                    authors=authors,
                    source="semantic_scholar",
                    links=[{'url': p['openAccessPdf']['url'], 'title': 'Open-access PDF'}]
                        if (p.get('openAccessPdf') or {}).get('url') else [],
                )
            )
        if docs:
            return RetrievalResult(query=query, documents=docs, tool_used=tool)
    except BudgetExceeded:
        raise
    except Exception as exc:
        errors.append(f'Semantic Scholar: {type(exc).__name__}: {exc}')

    tool = "arxiv"
    try:
        url = 'https://export.arxiv.org/api/query'
        words = query.replace('"', '').split()
        search_query = ' AND '.join('all:"' + word + '"' for word in words)
        resp = httpx.get(url, params={'search_query': search_query, 'max_results': 10,
            'sortBy': 'relevance'}, timeout=remaining_timeout(s.request_timeout), follow_redirects=True)
        resp.raise_for_status()
        import xml.etree.ElementTree as ET

        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.text)
        for entry in root.findall("a:entry", ns):
            title = entry.findtext("a:title", default="", namespaces=ns).strip()
            summary = entry.findtext("a:summary", default="", namespaces=ns).strip()
            link = entry.find("a:id", ns)
            published = entry.findtext('a:published', default='', namespaces=ns)
            authors = [a.findtext('a:name', default='', namespaces=ns) for a in entry.findall('a:author', ns)]
            docs.append(
                Document(
                    id=f"arxiv-{link.text if link is not None else title}",
                    title=title,
                    url=link.text if link is not None else "",
                    abstract=summary,
                    year=int(published[:4]) if published[:4].isdigit() else None,
                    authors=authors,
                    source="arxiv",
                )
            )
    except BudgetExceeded:
        raise
    except Exception as exc:
        errors.append(f'ArXiv: {type(exc).__name__}: {exc}')
        return RetrievalResult(query=query, tool_used=tool, error='; '.join(errors))

    return RetrievalResult(query=query, documents=docs, tool_used=tool,
        error=None if docs else '论文搜索没有返回结果；' + '; '.join(errors))
