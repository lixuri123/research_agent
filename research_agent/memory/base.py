"""Unified, owner-scoped facade over durable session and Markdown memory."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from research_agent.core.config import get_settings
from research_agent.memory.light import LightMemoryStore
from research_agent.core.schemas import MemoryItem


class MemoryStore(ABC):
    """Legacy backend protocol, retained for callers migrating to LightMemoryStore."""

    @abstractmethod
    def add(self, items: list[MemoryItem]) -> int: ...

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]: ...

    @abstractmethod
    def clear(self) -> None: ...


@dataclass(frozen=True)
class ContextPack:
    rendered_text: str
    selected_ids: tuple[str, ...]
    estimated_tokens: int
    omitted_count: int
    selection_reasons: tuple[str, ...]


class MemoryManager:
    def __init__(self, store: LightMemoryStore | None = None, *, owner_id: str = 'local',
                 project_id: str | None = None, session_id: str | None = None):
        if not owner_id:
            raise ValueError('owner_id is required')
        settings = get_settings()
        self.store = store or LightMemoryStore(settings.database_url, settings.memory_markdown_dir)
        self.owner_id = owner_id
        self.project_id = project_id
        self.session_id = session_id

    def append_message(self, role: str, content: str, *, session_id: str | None = None) -> dict:
        return self.store.append_message(self.owner_id, session_id or self.session_id, role, content)

    def history(self, *, session_id: str | None = None) -> list[dict]:
        return self.store.messages(self.owner_id, session_id or self.session_id)

    def recent(self, limit: int = 20, *, session_id: str | None = None) -> list[dict]:
        return self.store.recent(self.owner_id, session_id or self.session_id, limit)

    def remember(self, content: str, *, title: str, kind: str = 'semantic',
                 scope: str = 'owner', source_type: str, source_id: str,
                 status: str = 'candidate', project_id: str | None = None,
                 session_id: str | None = None) -> dict:
        return self.store.create(self.owner_id, title, content, kind, scope, source_type, source_id,
            status=status, project_id=project_id or self.project_id, session_id=session_id or self.session_id)

    def remember_episode(self, topic: str, outcome: str, *, source_id: str,
                         project_id: str | None = None, status: str = 'candidate') -> dict:
        return self.remember(f'任务：{topic}\n结果：{outcome}', title=topic, kind='episodic',
            scope='project' if (project_id or self.project_id) else 'owner',
            source_type='run_event', source_id=source_id, status=status, project_id=project_id)

    def consolidate(self, items: list[MemoryItem], *, source_id: str) -> list[dict]:
        """Import legacy items as candidates; consolidation never grants trust."""
        return [self.remember(item.content, title=item.content[:80],
            source_type='imported_record', source_id=f'{source_id}:{item.id}') for item in items]

    def remember_procedure(self, title: str, content: str, *, source_id: str,
                           project_id: str | None = None) -> dict:
        return self.remember(content, title=title, kind='procedural',
            scope='project' if (project_id or self.project_id) else 'owner',
            source_type='run_event', source_id=source_id, project_id=project_id)

    def publish_procedure(self, memory_id: str, *, evaluation_id: str, reviewed_by: str) -> dict:
        return self.store.publish_procedure(self.owner_id, memory_id, evaluation_id, reviewed_by)

    def skills(self) -> list[dict]:
        return self.store.list_skills(self.owner_id, self.project_id, self.session_id)

    def activate(self, memory_id: str) -> dict:
        return self.store.activate(self.owner_id, memory_id)

    def update(self, memory_id: str, content: str, *, source_type: str, source_id: str) -> dict:
        return self.store.update(self.owner_id, memory_id, content, source_type, source_id)

    def invalidate(self, memory_id: str) -> None:
        self.store.invalidate(self.owner_id, memory_id)

    def forget(self, memory_id: str) -> None:
        self.store.delete(self.owner_id, memory_id)

    def recall_details(self, query: str, top_k: int = 5) -> list[dict]:
        return self.store.recall(self.owner_id, query, project_id=self.project_id,
                                 session_id=self.session_id, top_k=top_k)

    def recall(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        return [MemoryItem(id=row['id'], content=row['content'], memory_type=row['kind'],
            metadata={key: row[key] for key in ('title', 'scope', 'project_id', 'session_id',
                'source_type', 'source_id', 'version', 'status')}, created_at=row['created_at'])
            for row in self.recall_details(query, top_k)]

    def list_entries(self, status: str = 'candidate') -> list[dict]:
        return self.store.list_entries(self.owner_id, status)

    def assemble(self, query: str, *, context_limit: int, system_text: str = '',
                 other_text: str = '', output_reserve: int = 4096, safety_margin: int = 512,
                 recent_limit: int = 20, top_k: int = 5,
                 token_counter: Callable[[str], int] | None = None) -> ContextPack:
        if context_limit <= 0 or output_reserve < 0 or safety_margin < 0:
            raise ValueError('Invalid context budget')
        count = token_counter or (lambda value: len(value.encode('utf-8')) + 16)
        mandatory = [text for text in (system_text, other_text, f'当前输入：{query}') if text]
        if count('\n\n'.join(mandatory)) + output_reserve + safety_margin > context_limit:
            raise ValueError('当前输入与必需内容超过上下文预算')
        recent = list(reversed(self.recent(recent_limit))) if self.session_id else []
        memories = self.recall_details(query, top_k)
        recent_blocks, memory_blocks = [], []
        selected, reasons, omitted = [], [], 0
        for row in recent:
            text = f"[{row['role']}] {row['content']}"
            proposed = '\n\n'.join(mandatory + list(reversed(recent_blocks + [text])) + memory_blocks)
            if count(proposed) + output_reserve + safety_margin <= context_limit:
                recent_blocks.append(text)
                selected.append(f"message:{row['id']}")
                reasons.append('近期会话')
            else:
                omitted += 1
        for row in memories:
            text = f"[历史记忆 {row['id']}] {row['content']}"
            proposed = '\n\n'.join(mandatory + list(reversed(recent_blocks)) + memory_blocks + [text])
            if count(proposed) + output_reserve + safety_margin <= context_limit:
                memory_blocks.append(text)
                selected.append(row['id'])
                reasons.append(f"匹配查询；来源 {row['source_type']}:{row['source_id']}")
            else:
                omitted += 1
        rendered = '\n\n'.join(mandatory + list(reversed(recent_blocks)) + memory_blocks)
        return ContextPack(rendered, tuple(selected), count(rendered) + output_reserve + safety_margin,
                           omitted, tuple(reasons))


@lru_cache
def get_memory_manager() -> MemoryManager:
    """Local single-user convenience only; multi-owner callers instantiate explicitly."""
    return MemoryManager()
