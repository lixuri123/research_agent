"""Local, single-user workspaces. Directory access is allowlisted, never arbitrary."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import time
import uuid
from itertools import islice
from pathlib import Path
from sqlalchemy import Boolean, Column, Float, MetaData, String, Table, insert, select
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
workspace_table = Table('agent_workspaces', metadata,
    Column('id', String(36), primary_key=True), Column('owner_id', String(255), nullable=False),
    Column('name', String(100), nullable=False), Column('root', String(4096), nullable=False),
    Column('agent_read', Boolean, nullable=False), Column('created_at', Float, nullable=False))
session_table = Table('agent_workspace_sessions', metadata,
    Column('owner_id', String(255), primary_key=True), Column('session_id', String(128), primary_key=True),
    Column('workspace_id', String(36), nullable=False))

TEXT_SUFFIXES = {'.md', '.txt', '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json',
                 '.yaml', '.yml', '.toml', '.csv', '.tex', '.bib', '.rst', '.ipynb', '.sql'}
HIDDEN_DIRS = {'node_modules', '__pycache__', 'dist', 'venv', 'env'}
MAX_BYTES = 128 * 1024


class WorkspaceConflict(ValueError):
    pass


def protected(name: str) -> bool:
    lowered = name.lower()
    return (lowered.startswith('.') or lowered in HIDDEN_DIRS or ':' in name
            or any(word in lowered for word in ('secret', 'credential', 'private_key', 'id_rsa', 'id_ed25519'))
            or lowered in {'mcp.json', 'auth.json', 'token.json', 'tokens.json'}
            or lowered.endswith(('.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db')))


def linked(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        return True
    try:
        # Python 3.10 has no Path.is_junction; reject Windows reparse points too.
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
    except FileNotFoundError:
        return False


class WorkspaceService:
    def __init__(self, engine, allowed_roots, owner_id='local'):
        self.engine, self.owner_id = engine, owner_id
        self.allowed_roots = [Path(root).resolve(strict=True) for root in allowed_roots]
        metadata.create_all(engine)

    def _root(self, raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError('请输入本地目录的绝对路径')
        if any(linked(part) for part in [path, *path.parents]):
            raise ValueError('工作空间不支持符号链接或目录联接')
        try:
            root = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError('工作空间目录不存在或不可访问') from exc
        if not root.is_dir() or not any(root.is_relative_to(base) for base in self.allowed_roots):
            raise ValueError('目录不在服务端 WORKSPACE_ALLOWED_ROOTS 白名单内')
        if any(protected(part) for base in self.allowed_roots if root.is_relative_to(base)
               for part in root.relative_to(base).parts):
            raise ValueError('不能绑定受保护目录下的工作空间')
        if protected(root.name):
            raise ValueError('不能绑定受保护目录')
        return root

    def bind(self, name: str, root: str, agent_read: bool = False) -> dict:
        if not name.strip() or len(name.strip()) > 100:
            raise ValueError('工作空间名称须为 1 到 100 个字符')
        root_path = self._root(root)
        with self.engine.begin() as conn:
            existing = conn.execute(select(workspace_table).where(
                workspace_table.c.owner_id == self.owner_id, workspace_table.c.root == str(root_path))).mappings().first()
            if existing:
                if existing['agent_read'] != agent_read:
                    raise WorkspaceConflict('此目录已绑定，不能通过重复绑定改变读取授权')
                return dict(existing)
            row = dict(id=str(uuid.uuid4()), owner_id=self.owner_id, name=name.strip(), root=str(root_path),
                       agent_read=agent_read, created_at=time.time())
            conn.execute(insert(workspace_table).values(**row))
            return row

    def list(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(workspace_table).where(
                workspace_table.c.owner_id == self.owner_id).order_by(workspace_table.c.created_at.desc())).mappings()]

    def get(self, workspace_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(workspace_table).where(workspace_table.c.id == workspace_id,
                workspace_table.c.owner_id == self.owner_id)).mappings().first()
        if row is None:
            raise ValueError('工作空间不存在')
        self._root(row['root'])  # Recheck allowlist and links even after a restart.
        return dict(row)

    def associate(self, session_id: str | None, workspace_id: str | None):
        if workspace_id:
            self.get(workspace_id)
        if not session_id:
            return
        if len(session_id) > 128:
            raise ValueError('会话标识过长')
        conditions = (session_table.c.owner_id == self.owner_id, session_table.c.session_id == session_id)
        with self.engine.connect() as conn:
            old = conn.execute(select(session_table).where(*conditions)).mappings().first()
            if old is None:
                from research_agent.persistence.repository import runs
                prior = conn.execute(select(runs.c.payload).where(runs.c.owner_id == self.owner_id,
                    runs.c.payload['session_id'].as_string() == session_id)).scalars()
                if any((payload.get('workspace_id') or '') != (workspace_id or '') for payload in prior):
                    raise WorkspaceConflict('历史会话属于其他空间，请新建对话')
        if old is None:
            try:
                with self.engine.begin() as conn:
                    conn.execute(insert(session_table).values(owner_id=self.owner_id,
                        session_id=session_id, workspace_id=workspace_id or ''))
                return
            except IntegrityError:
                with self.engine.connect() as conn:
                    old = conn.execute(select(session_table).where(*conditions)).mappings().one()
        if old['workspace_id'] != (workspace_id or ''):
            raise WorkspaceConflict('会话已属于另一个工作空间，请新建对话')

    def _path(self, workspace_id: str, relative: str) -> tuple[Path, Path]:
        root = Path(self.get(workspace_id)['root'])
        relative_path = Path(relative or '.')
        if relative_path.is_absolute() or relative_path.drive or '\x00' in relative or ':' in relative:
            raise ValueError('只能使用工作空间内的相对路径')
        current = root
        for part in relative_path.parts:
            if part == '..' or protected(part):
                raise ValueError('路径越界或属于受保护文件')
            current = current / part
            if linked(current):
                raise ValueError('不能访问符号链接或目录联接')
        try:
            path = current.resolve(strict=True)
        except OSError as exc:
            raise ValueError('文件或目录不存在') from exc
        if not path.is_relative_to(root):
            raise ValueError('路径超出工作空间')
        if path.is_file() and path.stat().st_nlink > 1:
            raise ValueError('不能访问硬链接文件')
        return root, path

    def entries(self, workspace_id: str, path: str = '') -> dict:
        root, directory = self._path(workspace_id, path)
        if not directory.is_dir():
            raise ValueError('不是目录')
        candidates = list(islice(directory.iterdir(), 1001))
        items = []
        for child in sorted(candidates[:1000], key=lambda p: (not p.is_dir(), p.name.lower())):
            if protected(child.name) or linked(child):
                continue
            if not child.is_dir() and not child.is_file():
                continue
            items.append({'name': child.name, 'path': child.relative_to(root).as_posix(),
                'kind': 'directory' if child.is_dir() else 'file',
                'text': child.is_file() and child.suffix.lower() in TEXT_SUFFIXES})
        return {'path': directory.relative_to(root).as_posix(), 'entries': items[:300],
                'truncated': len(candidates) > 1000 or len(items) > 300}

    def read(self, workspace_id: str, path: str) -> dict:
        _, target = self._path(workspace_id, path)
        if not target.is_file() or target.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError('暂只支持常见文本、代码文件，不支持 PDF 或二进制预览')
        with target.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('文件超过 128 KiB 文本预览上限')
        try:
            content = data.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ValueError('文件不是 UTF-8 文本') from exc
        if '\x00' in content:
            raise ValueError('文件包含二进制内容')
        return {'path': path, 'content': content, 'version': hashlib.sha256(data).hexdigest(),
                'size': len(data), 'bom': data.startswith(b'\xef\xbb\xbf')}

    def save(self, workspace_id: str, path: str, content: str, version: str) -> dict:
        # Local cooperative editing, not a sandbox against hostile concurrent OS changes.
        original = self.read(workspace_id, path)
        if version != original['version']:
            raise WorkspaceConflict('文件已被外部修改，请重新读取并合并；未覆盖磁盘内容')
        data = content.encode('utf-8-sig' if original['bom'] else 'utf-8')
        if len(data) > MAX_BYTES or '\x00' in content:
            raise ValueError('保存内容必须是 128 KiB 以内的文本')
        _, target = self._path(workspace_id, path)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.agent-save-', delete=False) as temp:
                temp_name = temp.name
                temp.write(data)
            os.chmod(temp_name, target.stat().st_mode)
            if self.read(workspace_id, path)['version'] != version:
                raise WorkspaceConflict('保存前文件已被修改，未覆盖磁盘内容')
            os.replace(temp_name, target)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
        return self.read(workspace_id, path)


def get_workspaces(repository=None) -> WorkspaceService:
    from research_agent.core.config import get_settings
    from research_agent.persistence.queue import get_repository
    roots = get_settings().workspace_allowed_roots or [str(Path.cwd())]
    return WorkspaceService((repository or get_repository()).engine, roots)
