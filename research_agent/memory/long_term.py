"""长期记忆：跨会话语义记忆，Milvus 向量库存储（内存关键词兜底）。"""

from __future__ import annotations

import time
import uuid

from pymilvus import MilvusClient

from research_agent.core.config import get_settings
from research_agent.models.embeddings import embed_text, embed_texts
from research_agent.memory.base import MemoryStore
from research_agent.core.schemas import MemoryItem

_client: MilvusClient | None = None
_milvus_ok = True

# 内存兜底
_mem: list[MemoryItem] = []


def _get_client() -> MilvusClient | None:
    global _client, _milvus_ok
    if not _milvus_ok:
        return None
    if _client is None:
        try:
            s = get_settings()
            _client = MilvusClient(uri=f"http://{s.milvus_host}:{s.milvus_port}")
        except Exception:
            _milvus_ok = False
            _client = None
    return _client


def _ensure_collection(dim: int) -> None:
    s = get_settings()
    client = _get_client()
    if client is None:
        return
    name = s.milvus_collection + "_memory"
    if client.has_collection(name):
        return
    client.create_collection(
        collection_name=name,
        dimension=dim,
        metric_type="COSINE",
        auto_id=False,
    )


class LongTermMemory(MemoryStore):
    """长期记忆：写入语义记忆，按 query 向量检索召回。"""

    def add(self, items: list[MemoryItem]) -> int:
        if not items:
            return 0
        for it in items:
            if not it.id:
                it.id = uuid.uuid4().hex
            if not it.created_at:
                it.created_at = time.time()

        client = _get_client()
        if client is None:
            _mem.extend(items)
            return len(items)

        try:
            texts = [i.content for i in items]
            vectors = embed_texts(texts)
            _ensure_collection(len(vectors[0]))
            s = get_settings()
            rows = [
                {
                    "id": it.id,
                    "vector": vec,
                    "content": it.content[:65535],
                    "memory_type": it.memory_type,
                    "importance": it.importance,
                    "metadata": str(it.metadata)[:2048],
                }
                for it, vec in zip(items, vectors)
            ]
            client.insert(collection_name=s.milvus_collection + "_memory", data=rows)
            return len(rows)
        except Exception:
            # Milvus/embedding 失败降级内存
            _mem.extend(items)
            return len(items)

    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        client = _get_client()
        if client is not None:
            try:
                s = get_settings()
                name = s.milvus_collection + "_memory"
                if not client.has_collection(name):
                    return []
                vec = embed_text(query)
                hits = client.search(
                    collection_name=name,
                    data=[vec],
                    limit=top_k,
                    output_fields=["content", "memory_type", "importance"],
                )
                out: list[MemoryItem] = []
                for batch in hits:
                    for h in batch:
                        e = h.get("entity", {})
                        out.append(
                            MemoryItem(
                                id=str(h.get("id", uuid.uuid4().hex)),
                                content=e.get("content", ""),
                                memory_type=e.get("memory_type", "long_term"),
                                importance=float(e.get("importance", 0.5)),
                            )
                        )
                return out
            except Exception:
                pass  # 降级关键词匹配

        # 关键词兜底
        if not _mem:
            return []
        scored = []
        for it in _mem:
            score = sum(1 for w in query.split() if w in it.content)
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [it for _, it in scored[:top_k] if _ > 0]

    def clear(self) -> None:
        client = _get_client()
        if client is not None:
            try:
                s = get_settings()
                client.drop_collection(s.milvus_collection + "_memory")
            except Exception:
                pass
        _mem.clear()
