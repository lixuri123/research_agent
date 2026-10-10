"""Task-scoped historical background; never a source of tool authorization."""
import json
from research_agent.core.config import get_settings
from research_agent.memory.base import MemoryManager

FIELDS = ('id', 'title', 'kind', 'scope', 'project_id', 'session_id', 'source_type',
          'source_id', 'version', 'content_hash', 'updated_at')


class AgentMemory:
    def __init__(self, payload: dict, owner_id: str, *, max_bytes=None, top_k=None):
        from research_agent.memory.base import get_memory_manager
        self.manager = MemoryManager(store=get_memory_manager().store, owner_id=owner_id,
            project_id=payload.get('workspace_id') or payload.get('project_id'), session_id=payload.get('session_id'))
        settings = get_settings()
        self.max_bytes = settings.agent_memory_max_bytes if max_bytes is None else max_bytes
        self.top_k = settings.agent_memory_top_k if top_k is None else top_k

    @staticmethod
    def reference(item: dict) -> dict:
        return {key: item.get(key) for key in FIELDS}

    def _visible(self, row):
        return (row['kind'] != 'procedural'
                and (row['scope'] != 'project' or row['project_id'] == self.manager.project_id)
                and (row['scope'] != 'session' or row['session_id'] == self.manager.session_id))

    def _bound(self, rows):
        selected = []
        for row in rows:
            if len(selected) >= self.top_k:
                break
            content = row['content'][:2000]
            while content:
                item = {**self.reference(row), 'content': content, 'truncated': len(content) < len(row['content'])}
                size = len(json.dumps(selected + [item], ensure_ascii=False).encode('utf-8'))
                if size <= self.max_bytes:
                    selected.append(item)
                    break
                content = content[:len(content) // 2]
        return selected

    def search(self, query: str) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError('记忆查询必须是 1 到 4000 个字符')
        if self.top_k == 0:
            return {'memories': [], 'omitted_count': 0}
        manager = self.manager
        rows = manager.store.recall(manager.owner_id, query, project_id=manager.project_id,
            session_id=manager.session_id, top_k=self.top_k, include_procedural=False, prioritize_scope=True)
        selected = self._bound(rows)
        return {'memories': selected, 'omitted_count': len(rows) - len(selected)}

    def validate(self, snapshot: list[dict]) -> list[dict]:
        rows = []
        for item in snapshot[:self.top_k]:
            row = self.manager.store.active_entry(self.manager.owner_id, item['id'])
            if row and self._visible(row) and row['version'] == item['version'] and row['content_hash'] == item['content_hash']:
                rows.append(row)
        return self._bound(rows)
