"""短期记忆：会话上下文滑动窗口，Redis 存储（内存兜底）。"""

from __future__ import annotations

import json
import time
import uuid
from collections import deque

import redis

from research_agent.core.config import get_settings
from research_agent.memory.base import MemoryStore
from research_agent.core.schemas import MemoryItem

# Redis 连接（失败降级内存）
try:
    _r = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    _r.ping()
    _use_redis = True
except Exception:
    _r = None
    _use_redis = False

# 内存兜底：session_id -> deque[MemoryItem]
_mem: dict[str, deque] = {}

WINDOW_SIZE = 20  # 短期记忆窗口大小（条数）


class ShortTermMemory(MemoryStore):
    """会话级短期记忆，按 session_id 隔离的滑动窗口。"""

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self.window_size = window_size

    def _key(self, session_id: str) -> str:
        return f"short_mem:{session_id}"

    def add_message(
        self,
        content: str,
        session_id: str = "default",
        memory_type: str = "short_term",
        **meta,
    ) -> MemoryItem:
        item = MemoryItem(
            id=uuid.uuid4().hex,
            content=content,
            memory_type=memory_type,  # type: ignore[arg-type]
            metadata={"session_id": session_id, **meta},
            created_at=time.time(),
        )
        self.add([item])
        return item

    def add(self, items: list[MemoryItem]) -> int:
        for item in items:
            session = item.metadata.get("session_id", "default")
            self._push(session, item)
        return len(items)

    def _push(self, session_id: str, item: MemoryItem) -> None:
        data = item.model_dump()
        if _use_redis and _r:
            _r.rpush(self._key(session_id), json.dumps(data))
            _r.ltrim(self._key(session_id), -self.window_size, -1)  # 滑动窗口
        else:
            dq = _mem.setdefault(session_id, deque(maxlen=self.window_size))
            dq.append(item)

    def get_window(self, session_id: str = "default") -> list[MemoryItem]:
        if _use_redis and _r:
            raw = _r.lrange(self._key(session_id), 0, -1)
            return [MemoryItem(**json.loads(x)) for x in raw]
        return list(_mem.get(session_id, []))

    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        # 短期记忆不做语义检索，返回最近窗口内容（按时间倒序）
        sessions = list(_mem.keys()) if not _use_redis else self._all_sessions()
        items: list[MemoryItem] = []
        for sid in sessions:
            items.extend(self.get_window(sid))
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:top_k]

    def _all_sessions(self) -> list[str]:
        if _use_redis and _r:
            keys = _r.keys("short_mem:*")
            return [k.decode() if isinstance(k, bytes) else k for k in keys]
        return list(_mem.keys())

    def clear(self) -> None:
        if _use_redis and _r:
            for k in self._all_sessions():
                _r.delete(k)
        else:
            _mem.clear()
