"""Embedding 封装层：调用 OpenAI 兼容 embedding 接口。"""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from research_agent.core.config import get_settings


@lru_cache
def _client() -> OpenAI:
    s = get_settings()
    return OpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化文本。"""
    if not texts:
        return []
    s = get_settings()
    resp = _client().embeddings.create(model=s.embedding_model, input=texts)
    return [d.embedding for d in resp.data]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def embedding_dim() -> int:
    """返回 embedding 维度（用于建集合）。"""
    return len(embed_text("dim probe"))
