"""PDF 解析工具：本地 PDF 文件 → 文本。"""

from __future__ import annotations

import os

from research_agent.core.schemas import Document, RetrievalResult
from research_agent.tools import register_tool


@register_tool("pdf_parse", description="本地 PDF 解析为文本")
def pdf_parse(path_or_query: str) -> RetrievalResult:
    """query 传 PDF 本地路径，解析出全文文本。

    返回单个 Document，source=pdf。
    """
    path = path_or_query.strip()
    if path.startswith(('https://', 'http://')):
        from research_agent.tools.browser import browser_read
        result = browser_read(path)
        result.tool_used = 'pdf_parse'
        return result
    if not os.path.exists(path):
        return RetrievalResult(query=path, tool_used="pdf_parse", error=f"文件不存在: {path}")

    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        text = "\n".join(
            (page.extract_text() or "") for page in reader.pages
        )
        title = os.path.basename(path)
        doc = Document(
            id=f"pdf-{os.path.basename(path)}",
            title=title,
            url=path,
            content=text,
            source="pdf",
        )
        return RetrievalResult(query=path, documents=[doc], tool_used="pdf_parse")
    except Exception as exc:
        return RetrievalResult(query=path, tool_used="pdf_parse", error=str(exc))
