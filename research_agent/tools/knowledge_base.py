"""Milvus 向量库封装：本地知识库的写入与语义检索。

使用 PyMilvus 推荐的 MilvusClient API（ORM 旧式 API 已弃用）。
"""

from __future__ import annotations

import hashlib

from pymilvus import MilvusClient

from research_agent.core.config import get_settings
from research_agent.models.embeddings import embed_text, embed_texts
from research_agent.core.schemas import Document, RetrievalResult
from research_agent.tools import register_tool

_client: MilvusClient | None = None


def _get_client() -> MilvusClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = MilvusClient(uri=f"http://{s.milvus_host}:{s.milvus_port}")
    return _client


def _ensure_collection(dim: int) -> None:
    s = get_settings()
    client = _get_client()
    if client.has_collection(s.milvus_collection):
        return
    client.create_collection(
        collection_name=s.milvus_collection,
        dimension=dim,
        metric_type="COSINE",
        auto_id=False,
    )


def add_documents(docs: list[Document]) -> int:
    """将文档写入知识库，返回写入条数。"""
    if not docs:
        return 0
    s = get_settings()
    texts = [d.abstract or d.content or d.title for d in docs]
    vectors = embed_texts(texts)
    dim = len(vectors[0]) if vectors else 0
    _ensure_collection(dim)

    client = _get_client()
    rows = []
    for d, vec in zip(docs, vectors):
        doc_id = d.id or hashlib.md5(d.title.encode()).hexdigest()
        rows.append(
            {
                "id": doc_id,
                "vector": vec,
                "title": d.title[:512],
                "source": d.source,
                "content": (d.content or d.abstract or "")[:65535],
            }
        )
    client.insert(collection_name=s.milvus_collection, data=rows)
    return len(rows)


@register_tool("knowledge_base", description="本地知识库语义检索（只读）")
def knowledge_base_search(query: str, top_k: int = 5) -> RetrievalResult:
    """本地知识库语义检索，Milvus 不可用时降级。"""
    s = get_settings()
    try:
        client = _get_client()
        if not client.has_collection(s.milvus_collection):
            return RetrievalResult(
                query=query,
                tool_used="knowledge_base",
                error="知识库集合不存在，请先写入数据",
            )
        vec = embed_text(query)
        hits = client.search(
            collection_name=s.milvus_collection,
            data=[vec],
            limit=top_k,
            output_fields=["id", "title", "source", "content"],
        )
        docs = []
        for batch in hits:
            for h in batch:
                entity = h.get("entity", {})
                docs.append(
                    Document(
                        id=entity.get("id", ""),
                        title=entity.get("title", ""),
                        content=entity.get("content", ""),
                        source="knowledge_base",
                    )
                )
        if docs:
            return RetrievalResult(
                query=query, documents=docs, tool_used="knowledge_base"
            )
    except Exception as exc:
        # Milvus 不可用 → 降级并附错误信息
        return RetrievalResult(
            query=query,
            tool_used="knowledge_base",
            error=f"Milvus 不可用（{exc}），请先启动向量库或写入数据",
        )
    return RetrievalResult(query=query, tool_used="knowledge_base")


@register_tool(
    "knowledge_base_ingest",
    danger_level="sensitive",
    description="解析并写入文档到知识库（敏感写操作，需授权）",
)
def knowledge_base_ingest(path: str) -> RetrievalResult:
    """把本地 PDF/文本文件解析后写入知识库。属于敏感写操作，需授权。"""
    import os

    from research_agent.tools.pdf_parse import pdf_parse

    if not os.path.exists(path):
        return RetrievalResult(query=path, tool_used="knowledge_base_ingest", error=f"文件不存在: {path}")

    parsed = pdf_parse(path)
    if parsed.error or not parsed.documents:
        return RetrievalResult(
            query=path, tool_used="knowledge_base_ingest", error=parsed.error or "解析失败"
        )
    try:
        n = add_documents(parsed.documents)
        return RetrievalResult(
            query=path,
            documents=parsed.documents,
            tool_used="knowledge_base_ingest",
            error=None if n else "写入失败",
        )
    except Exception as exc:
        return RetrievalResult(query=path, tool_used="knowledge_base_ingest", error=str(exc))
