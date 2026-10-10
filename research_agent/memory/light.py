"""Durable conversation events and reviewed Markdown memories.

The SQL row controls visibility; Markdown is never used without a hash check.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine, insert, select, update


metadata = MetaData()
messages_table = Table('memory_session_messages', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('owner_id', String(255), nullable=False),
    Column('session_id', String(255), nullable=False),
    Column('role', String(32), nullable=False),
    Column('content', Text, nullable=False),
    Column('created_at', Float, nullable=False))
entries_table = Table('memory_entries', metadata,
    Column('id', String(36), primary_key=True),
    Column('owner_id', String(255), nullable=False),
    Column('project_id', String(255)),
    Column('session_id', String(255)),
    Column('scope', String(16), nullable=False),
    Column('kind', String(16), nullable=False),
    Column('title', String(255), nullable=False),
    Column('source_type', String(32), nullable=False),
    Column('source_id', String(255), nullable=False),
    Column('status', String(16), nullable=False),
    Column('evaluation_id', String(255)),
    Column('reviewed_by', String(255)),
    Column('version', Integer, nullable=False),
    Column('content_hash', String(64), nullable=False),
    Column('created_at', Float, nullable=False),
    Column('updated_at', Float, nullable=False))


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


_BODY_MARKER = '<!-- memory-body -->\n'


def _render(title: str, source_type: str, source_id: str, scope: str, content: str) -> str:
    clean = lambda value: value.replace('\r', ' ').replace('\n', ' ')
    return (f'# {clean(title)}\n\n来源：{clean(source_type)}:{clean(source_id)}\n'
            f'范围：{scope}\n\n{_BODY_MARKER}{content}')


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r'[a-z0-9_]+', query.casefold())
    for segment in re.findall(r'[\u4e00-\u9fff]+', query):
        terms.extend(segment[index:index + 2] for index in range(len(segment) - 1))
    return terms


class LightMemoryStore:
    def __init__(self, database_url: str, markdown_dir: str | Path):
        if not database_url or not markdown_dir:
            raise ValueError('database_url and markdown_dir are required')
        self.engine = create_engine(database_url)
        metadata.create_all(self.engine)
        self.markdown_dir = Path(markdown_dir).resolve()
        self.markdown_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _transaction(self):
        if self.engine.dialect.name == 'sqlite':
            with self.engine.connect() as conn:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        else:
            with self.engine.begin() as conn:
                yield conn

    def path_for(self, memory_id: str, version: int) -> Path:
        if str(uuid.UUID(memory_id)) != memory_id or version < 1:
            raise ValueError('Invalid memory path')
        return self.markdown_dir / f'{memory_id}-v{version}.md'

    def skill_path_for(self, memory_id: str, version: int) -> Path:
        self.path_for(memory_id, version)  # validates the generated identifier
        return self.markdown_dir / 'skills' / memory_id / f'v{version}' / 'SKILL.md'

    def _write(self, memory_id: str, version: int, content: str) -> None:
        path = self.path_for(memory_id, version)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def append_message(self, owner_id: str, session_id: str, role: str, content: str) -> dict:
        if not owner_id or not session_id or role not in {'user', 'assistant', 'tool', 'system'} or not content:
            raise ValueError('Invalid session message')
        with self.engine.begin() as conn:
            result = conn.execute(insert(messages_table).values(owner_id=owner_id, session_id=session_id,
                role=role, content=content, created_at=time.time()))
            row = conn.execute(select(messages_table).where(messages_table.c.id == result.inserted_primary_key[0])).mappings().one()
            return dict(row)

    def messages(self, owner_id: str, session_id: str) -> list[dict]:
        if not owner_id or not session_id:
            raise ValueError('owner_id and session_id are required')
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(messages_table).where(
                messages_table.c.owner_id == owner_id, messages_table.c.session_id == session_id
            ).order_by(messages_table.c.id)).mappings()]

    def recent(self, owner_id: str, session_id: str, limit: int = 20) -> list[dict]:
        if limit < 1:
            raise ValueError('limit must be positive')
        with self.engine.connect() as conn:
            rows = conn.execute(select(messages_table).where(
                messages_table.c.owner_id == owner_id, messages_table.c.session_id == session_id
            ).order_by(messages_table.c.id.desc()).limit(limit)).mappings().all()
            return [dict(row) for row in reversed(rows)]

    def create(self, owner_id: str, title: str, content: str, kind: str, scope: str,
               source_type: str, source_id: str, *, project_id: str | None = None,
               session_id: str | None = None, status: str = 'candidate') -> dict:
        if not all((owner_id, title, content, source_type, source_id)):
            raise ValueError('Memory content and provenance are required')
        if kind not in {'semantic', 'episodic', 'procedural'} or scope not in {'owner', 'project', 'session'}:
            raise ValueError('Invalid memory kind or scope')
        if status not in {'candidate', 'active'}:
            raise ValueError('Invalid initial status')
        if kind == 'procedural' and status != 'candidate':
            raise ValueError('Procedures require review before publication')
        if (scope == 'project' and not project_id) or (scope == 'session' and not session_id):
            raise ValueError('Scope identifier is required')
        memory_id = str(uuid.uuid4())
        now = time.time()
        document = _render(title, source_type, source_id, scope, content)
        row = dict(id=memory_id, owner_id=owner_id, project_id=project_id if scope == 'project' else None,
                   session_id=session_id if scope == 'session' else None, scope=scope, kind=kind,
                   title=title, source_type=source_type, source_id=source_id, status=status,
                   evaluation_id=None, reviewed_by=None,
                   version=1, content_hash=_digest(document), created_at=now, updated_at=now)
        self._write(memory_id, 1, document)
        with self.engine.begin() as conn:
            conn.execute(insert(entries_table).values(**row))
        return row

    def _owned(self, conn, owner_id: str, memory_id: str) -> dict:
        query = select(entries_table).where(entries_table.c.id == memory_id,
            entries_table.c.owner_id == owner_id)
        if self.engine.dialect.name == 'postgresql':
            query = query.with_for_update()
        row = conn.execute(query).mappings().first()
        if row is None:
            raise ValueError('Unknown memory or owner')
        return dict(row)

    def activate(self, owner_id: str, memory_id: str) -> dict:
        with self._transaction() as conn:
            row = self._owned(conn, owner_id, memory_id)
            if row['kind'] == 'procedural' or row['status'] != 'candidate' or self._read(row) is None:
                raise ValueError('Memory cannot be activated')
            conn.execute(update(entries_table).where(entries_table.c.id == memory_id).values(
                status='active', updated_at=time.time()))
            return self._owned(conn, owner_id, memory_id)

    def update(self, owner_id: str, memory_id: str, content: str, source_type: str, source_id: str) -> dict:
        if not all((content, source_type, source_id)):
            raise ValueError('Content and provenance are required')
        with self._transaction() as conn:
            row = self._owned(conn, owner_id, memory_id)
            if row['status'] == 'deleted':
                raise ValueError('Deleted memory cannot be updated')
            version = row['version'] + 1
            document = _render(row['title'], source_type, source_id, row['scope'], content)
            self._write(memory_id, version, document)
            changed = conn.execute(update(entries_table).where(entries_table.c.id == memory_id,
                entries_table.c.version == row['version']).values(version=version,
                content_hash=_digest(document), source_type=source_type, source_id=source_id,
                status='candidate', evaluation_id=None, reviewed_by=None, updated_at=time.time()))
            if changed.rowcount != 1:
                raise ValueError('Concurrent memory update')
            return self._owned(conn, owner_id, memory_id)

    def invalidate(self, owner_id: str, memory_id: str) -> None:
        with self._transaction() as conn:
            row = self._owned(conn, owner_id, memory_id)
            if row['status'] == 'deleted':
                raise ValueError('Deleted memory cannot be invalidated')
            conn.execute(update(entries_table).where(entries_table.c.id == memory_id).values(
                status='stale', updated_at=time.time()))

    def delete(self, owner_id: str, memory_id: str) -> None:
        with self._transaction() as conn:
            row = self._owned(conn, owner_id, memory_id)
            conn.execute(update(entries_table).where(entries_table.c.id == memory_id).values(
                status='deleted', updated_at=time.time()))
        for version in range(1, row['version'] + 1):
            self.path_for(memory_id, version).unlink(missing_ok=True)
            self.skill_path_for(memory_id, version).unlink(missing_ok=True)

    def _read(self, row: dict) -> str | None:
        try:
            content = self.path_for(row['id'], row['version']).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return None
        if _digest(content) != row['content_hash'] or _BODY_MARKER not in content:
            return None
        return content.split(_BODY_MARKER, 1)[1]

    def _published_valid(self, row: dict) -> bool:
        try:
            published = self.skill_path_for(row['id'], row['version']).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return False
        return _digest(published) == row['content_hash']

    def list_entries(self, owner_id: str, status: str = 'candidate') -> list[dict]:
        if not owner_id or status not in {'candidate', 'active', 'stale', 'deleted'}:
            raise ValueError('Invalid memory listing')
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(select(entries_table).where(
                entries_table.c.owner_id == owner_id, entries_table.c.status == status
            ).order_by(entries_table.c.updated_at.desc(), entries_table.c.id)).mappings()]
        return [{**row, 'content': self._read(row)} for row in rows]

    def publish_procedure(self, owner_id: str, memory_id: str, evaluation_id: str,
                          reviewed_by: str) -> dict:
        if not evaluation_id or not reviewed_by:
            raise ValueError('Procedure requires review and evaluation references')
        with self._transaction() as conn:
            row = self._owned(conn, owner_id, memory_id)
            if row['kind'] != 'procedural' or row['status'] != 'candidate':
                raise ValueError('Only candidate procedures can be published')
            if self._read(row) is None:
                raise ValueError('Procedure file is missing or modified')
            path = self.skill_path_for(memory_id, row['version'])
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name('SKILL.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(self.path_for(memory_id, row['version']).read_text(encoding='utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            conn.execute(update(entries_table).where(entries_table.c.id == memory_id).values(
                status='active', evaluation_id=evaluation_id, reviewed_by=reviewed_by,
                updated_at=time.time()))
            return self._owned(conn, owner_id, memory_id)

    def list_skills(self, owner_id: str, project_id: str | None = None,
                    session_id: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(select(entries_table).where(
                entries_table.c.owner_id == owner_id, entries_table.c.kind == 'procedural',
                entries_table.c.status == 'active')).mappings()]
        result = []
        for row in rows:
            if row['scope'] == 'project' and row['project_id'] != project_id:
                continue
            if row['scope'] == 'session' and row['session_id'] != session_id:
                continue
            content = self._read(row)
            if content is not None and self._published_valid(row):
                result.append({**row, 'content': content})
        return result

    def active_entry(self, owner_id: str, memory_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(entries_table).where(entries_table.c.owner_id == owner_id,
                entries_table.c.id == memory_id, entries_table.c.status == 'active')).mappings().first()
        if row is None:
            return None
        row = dict(row)
        content = self._read(row)
        if content is None or (row['kind'] == 'procedural' and not self._published_valid(row)):
            return None
        return {**row, 'content': content}

    def recall(self, owner_id: str, query: str, *, project_id: str | None = None,
               session_id: str | None = None, top_k: int = 5, include_procedural: bool = True,
               prioritize_scope: bool = False) -> list[dict]:
        if not owner_id or top_k < 1:
            raise ValueError('Invalid recall request')
        if not query.strip():
            return []
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(select(entries_table).where(
                entries_table.c.owner_id == owner_id, entries_table.c.status == 'active'
            )).mappings()]
        terms = _query_terms(query)
        found = []
        for row in rows:
            if not include_procedural and row['kind'] == 'procedural':
                continue
            if row['scope'] == 'project' and row['project_id'] != project_id:
                continue
            if row['scope'] == 'session' and row['session_id'] != session_id:
                continue
            content = self._read(row)
            if content is None:
                continue
            if row['kind'] == 'procedural' and not self._published_valid(row):
                continue
            haystack = (row['title'] + '\n' + content).casefold()
            score = (4 if query.casefold() in haystack else 0) + sum(1 for term in terms if term in haystack)
            if score:
                found.append((score, row['updated_at'], {**row, 'content': content}))
        ranks = {'owner': 0, 'project': 2, 'session': 1}
        found.sort(key=lambda item: ((ranks[item[2]['scope']] if prioritize_scope else 0), item[0], item[1]), reverse=True)
        return [item[2] for item in found[:top_k]]
