"""Durable Agent proposals; only an explicit human decision writes existing files."""
from contextlib import contextmanager
import difflib
import hashlib
import json
import time
from sqlalchemy import Column, Float, MetaData, String, Table, Text, insert, select, update
from research_agent.persistence.repository import runs
from research_agent.workspace.service import MAX_BYTES, WorkspaceConflict

metadata = MetaData()
changes = Table('agent_file_changes', metadata,
    Column('id', String(64), primary_key=True), Column('owner_id', String(255), nullable=False),
    Column('workspace_id', String(36), nullable=False), Column('run_id', String(36), nullable=False),
    Column('path', Text, nullable=False), Column('reason', Text, nullable=False),
    Column('base_version', String(64), nullable=False), Column('target_version', String(64), nullable=False),
    Column('original_content', Text, nullable=False), Column('proposed_content', Text, nullable=False),
    Column('request_hash', String(64), nullable=False), Column('review_hash', String(64), nullable=False),
    Column('status', String(24), nullable=False), Column('created_at', Float, nullable=False))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class FileChangeService:
    def __init__(self, workspaces):
        self.workspaces = workspaces
        self.engine, self.owner_id = workspaces.engine, workspaces.owner_id
        metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            if self.engine.dialect.name == 'sqlite':
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                conn.begin()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def row(self, conn, workspace_id, change_id):
        query = select(changes).where(changes.c.id == change_id,
            changes.c.workspace_id == workspace_id, changes.c.owner_id == self.owner_id)
        if self.engine.dialect.name == 'postgresql':
            query = query.with_for_update()
        row = conn.execute(query).mappings().first()
        if row is None:
            raise ValueError('文件修改提案不存在')
        return dict(row)

    @staticmethod
    def summary(row):
        return {key: row[key] for key in ('id', 'workspace_id', 'run_id', 'path', 'reason',
            'base_version', 'review_hash', 'status', 'created_at')}

    def propose(self, workspace_id, run_id, worker_id, operation_id, path, content, base_version, reason):
        if not all(isinstance(item, str) and item for item in
                   (run_id, worker_id, operation_id, path, base_version, reason)) or not isinstance(content, str):
            raise ValueError('修改提案参数无效')
        if len(reason) > 2000 or len(path) > 4096 or len(content.encode('utf-8')) > MAX_BYTES or '\x00' in content:
            raise ValueError('修改提案超过文本限制')
        workspace = self.workspaces.get(workspace_id)
        if not workspace['agent_read']:
            raise ValueError('工作空间未授权 Agent 读取')
        change_id = digest([self.owner_id, run_id, operation_id])
        request_hash = digest([workspace_id, path, content, base_version, reason])
        with self.transaction() as conn:
            query = select(runs).where(runs.c.run_id == run_id, runs.c.owner_id == self.owner_id)
            if self.engine.dialect.name == 'postgresql':
                query = query.with_for_update()
            run = conn.execute(query).mappings().first()
            if not run or run['status'] != 'running' or run['worker_id'] != worker_id or not run['lease_expires_at'] or run['lease_expires_at'] <= time.time() or run['payload'].get('task_type') != 'agent' or run['payload'].get('workspace_id') != workspace_id:
                raise ValueError('仅当前持有有效租约的 Agent 任务可以提交提案')
            old = conn.execute(select(changes).where(changes.c.id == change_id)).mappings().first()
            if old:
                if old['request_hash'] != request_hash:
                    raise WorkspaceConflict('同一操作的提案内容已改变')
                return self.summary(old)
            original = self.workspaces.read(workspace_id, path)
            if original['version'] != base_version:
                raise WorkspaceConflict('原文件已改变，请重新读取后提交提案')
            data = content.encode('utf-8-sig' if original['bom'] else 'utf-8')
            if len(data) > MAX_BYTES:
                raise ValueError('修改提案超过 128 KiB')
            row = dict(id=change_id, owner_id=self.owner_id, workspace_id=workspace_id, run_id=run_id,
                path=path, reason=reason, base_version=base_version,
                target_version=hashlib.sha256(data).hexdigest(), original_content=original['content'],
                proposed_content=content, request_hash=request_hash,
                review_hash=digest([change_id, request_hash, original['content']]), status='pending', created_at=time.time())
            conn.execute(insert(changes).values(**row))
            return self.summary(row)

    def list(self, workspace_id, run_id=None):
        self.workspaces.get(workspace_id)
        query = select(changes).where(changes.c.workspace_id == workspace_id, changes.c.owner_id == self.owner_id)
        if run_id:
            query = query.where(changes.c.run_id == run_id)
        with self.engine.connect() as conn:
            return [self.summary(row) for row in conn.execute(query.order_by(changes.c.created_at.desc()).limit(100)).mappings()]

    def get(self, workspace_id, change_id):
        self.workspaces.get(workspace_id)
        with self.engine.connect() as conn:
            row = self.row(conn, workspace_id, change_id)
        lines = difflib.unified_diff(row['original_content'].splitlines(keepends=True),
            row['proposed_content'].splitlines(keepends=True), fromfile=row['path'], tofile=row['path'], lineterm='\n')
        diff = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
        return {**self.summary(row), 'original_content': row['original_content'],
            'proposed_content': row['proposed_content'], 'diff': diff}

    def decide(self, workspace_id, change_id, approved, review_hash):
        self.workspaces.get(workspace_id)
        # Persist approval before touching the file, allowing recovery after a crash.
        with self.transaction() as conn:
            row = self.row(conn, workspace_id, change_id)
            if row['review_hash'] != review_hash:
                raise WorkspaceConflict('提案版本不匹配，请重新查看差异')
            if row['status'] == 'applied' and approved or row['status'] == 'rejected' and not approved:
                return self.summary(row)
            if row['status'] not in {'pending', 'applying'} or not approved and row['status'] == 'applying':
                raise WorkspaceConflict('提案已处理，不能改变决定')
            recovering = row['status'] == 'applying'
            state = 'applying' if approved else 'rejected'
            conn.execute(update(changes).where(changes.c.id == change_id).values(status=state))
            row['status'] = state
            if not approved:
                return self.summary(row)
        conflict = False
        with self.transaction() as conn:
            row = self.row(conn, workspace_id, change_id)
            if row['status'] == 'applied':
                return self.summary(row)
            if row['status'] != 'applying':
                raise WorkspaceConflict('提案已处理')
            try:
                current = self.workspaces.read(workspace_id, row['path'])
                if recovering and current['version'] == row['target_version']:
                    pass  # A previously approved write completed before the DB commit.
                elif current['version'] == row['base_version']:
                    self.workspaces.save(workspace_id, row['path'], row['proposed_content'], row['base_version'])
                else:
                    raise WorkspaceConflict('原文件已被修改，请生成新提案；未覆盖文件')
            except (WorkspaceConflict, ValueError):
                conflict = True
            row['status'] = 'conflict' if conflict else 'applied'
            conn.execute(update(changes).where(changes.c.id == change_id).values(status=row['status']))
        if conflict:
            raise WorkspaceConflict('原文件已改变或不可访问；未覆盖文件，请重新读取并生成提案')
        return self.summary(row)
