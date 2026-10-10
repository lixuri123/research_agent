"""Discover trusted local/published Skill instructions and bounded references."""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from research_agent.core.config import get_settings
from research_agent.memory.base import MemoryManager, get_memory_manager


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class SkillCatalog:
    def __init__(self, *, owner_id='local', project_id=None, session_id=None):
        self.settings = get_settings()
        self.manager = MemoryManager(store=get_memory_manager().store, owner_id=owner_id,
                                     project_id=project_id, session_id=session_id)
        self.errors: list[str] = []
        self._entries: dict[str, dict] = {}
        self._scan()

    def _text(self, path: Path) -> str:
        if path.stat().st_size > self.settings.skill_max_chars * 4:
            raise ValueError('Skill 文件超过大小上限')
        text = path.read_text(encoding='utf-8-sig')
        if len(text) > self.settings.skill_max_chars:
            raise ValueError('Skill 文件超过字符上限；不会截断指令')
        return text

    def _scan(self):
        for configured in self.settings.skill_dirs:
            root = Path(configured).resolve()
            if not root.is_dir():
                continue
            for path in root.rglob('SKILL.md'):
                if len(self._entries) >= 100:
                    self.errors.append('本地 Skill 数量超过 100 个上限')
                    break
                try:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root):
                        raise ValueError('Skill 路径越过配置目录')
                    text = self._text(resolved)
                    metadata = {}
                    if text.startswith('---\n'):
                        parts = text.split('\n---', 1)
                        if len(parts) != 2:
                            raise ValueError('Skill frontmatter 缺少结束标记')
                        metadata = yaml.safe_load(parts[0][4:]) or {}
                        if not isinstance(metadata, dict):
                            raise ValueError('Skill frontmatter 必须是对象')
                    name = str(metadata.get('name') or resolved.parent.name)
                    description = str(metadata.get('description') or name)
                    skill_id = 'local-' + digest(str(resolved))[:20]
                    self._entries[skill_id] = {'id': skill_id, 'name': name, 'title': name,
                        'description': description, 'version': digest(text), 'source': 'local',
                        'path': str(resolved), 'content': text, '_root': resolved.parent}
                except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                    self.errors.append(f'{path.name}: {type(exc).__name__}: {exc}')
        for row in self.manager.skills():
            text = row['content']
            if len(text) > self.settings.skill_max_chars:
                self.errors.append(f"已发布 Skill {row['id']} 超过字符上限")
                continue
            path = self.manager.store.skill_path_for(row['id'], row['version']).resolve()
            self._entries[row['id']] = {'id': row['id'], 'name': row['title'], 'title': row['title'],
                'description': row['title'], 'version': row['version'], 'content_hash': digest(text),
                'source': 'published', 'path': str(path), 'content': text, '_root': path.parent}

    def list(self) -> list[dict]:
        return [{key: value for key, value in item.items() if key not in {'content', '_root'}}
                for item in self._entries.values()]

    def read(self, skill_id: str) -> dict:
        entry = self._entries.get(skill_id)
        if entry is None:
            raise ValueError('Skill 不存在、未发布或不属于当前范围')
        return {key: value for key, value in entry.items() if key != '_root'}

    def reference(self, skill_id: str, relative_path: str) -> dict:
        entry = self._entries.get(skill_id)
        if entry is None:
            raise ValueError('Skill 不存在或不属于当前范围')
        supplied = Path(relative_path)
        if not relative_path or supplied.is_absolute() or supplied.drive or '..' in supplied.parts:
            raise ValueError('Skill 参考文件必须使用包内相对路径')
        target = (entry['_root'] / supplied).resolve()
        if not target.is_relative_to(entry['_root']) or not target.is_file():
            raise ValueError('参考文件不存在或越过 Skill 包目录')
        # References are material, never executable programs or environment secrets.
        if target.suffix.lower() not in {'.md', '.txt', '.json', '.yaml', '.yml', '.csv'}:
            raise ValueError('参考接口只读取 Markdown/文本/JSON/YAML/CSV，不执行脚本')
        text = self._text(target)
        return {'skill_id': skill_id, 'path': relative_path, 'content': text, 'content_hash': digest(text)}
