"""Execution records are separate from research-report approval and file writes."""
from contextlib import contextmanager
import time
from sqlalchemy import JSON, Boolean, Column, Float, MetaData, String, Table, insert, select, update
from research_agent.persistence.repository import runs

metadata = MetaData()
permissions = Table('workspace_execution_permissions', metadata,
    Column('owner_id', String(255), primary_key=True), Column('workspace_id', String(36), primary_key=True),
    Column('enabled', Boolean, nullable=False), Column('updated_at', Float, nullable=False))
executions = Table('local_executions', metadata,
    Column('id', String(36), primary_key=True), Column('owner_id', String(255), nullable=False),
    Column('workspace_id', String(36), nullable=False), Column('run_id', String(36), nullable=False),
    Column('operation_id', String(255), nullable=False), Column('request_hash', String(64), nullable=False),
    Column('arguments', JSON, nullable=False), Column('status', String(40), nullable=False),
    Column('manifest', JSON, nullable=False), Column('result', JSON),
    Column('created_at', Float, nullable=False), Column('finished_at', Float))


class ExecutionStore:
    def __init__(self, engine, owner_id):
        self.engine, self.owner_id = engine, owner_id
        metadata.create_all(engine)

    @contextmanager
    def transaction(self):
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

    def permission(self, workspace_id, conn=None):
        if conn is None:
            with self.engine.connect() as connection:
                return self.permission(workspace_id, connection)
        row = conn.execute(select(permissions).where(permissions.c.owner_id == self.owner_id,
            permissions.c.workspace_id == workspace_id)).mappings().first()
        return bool(row and row['enabled'])

    def set_permission(self, workspace_id, enabled):
        with self.transaction() as conn:
            condition = (permissions.c.owner_id == self.owner_id, permissions.c.workspace_id == workspace_id)
            query = select(permissions).where(*condition)
            if self.engine.dialect.name == 'postgresql':
                query = query.with_for_update()
            old = conn.execute(query).first()
            if old:
                conn.execute(update(permissions).where(*condition).values(enabled=enabled, updated_at=time.time()))
            else:
                conn.execute(insert(permissions).values(owner_id=self.owner_id, workspace_id=workspace_id,
                    enabled=enabled, updated_at=time.time()))

    def guard(self, conn, workspace_id, run_id, worker_id):
        query = select(runs).where(runs.c.run_id == run_id)
        if self.engine.dialect.name == 'postgresql':
            query = query.with_for_update()
        row = conn.execute(query).mappings().first()
        if not row or row['owner_id'] != self.owner_id or row['status'] != 'running' or row['worker_id'] != worker_id or not row['lease_expires_at'] or row['lease_expires_at'] <= time.time() or row['payload'].get('task_type') != 'agent' or row['payload'].get('workspace_id') != workspace_id:
            raise ValueError('执行需要属于当前工作空间的有效 Agent worker 租约')
        if not self.permission(workspace_id, conn):
            raise PermissionError('工作空间未授权本地受限执行')

    def prepare(self, identity, workspace_id, run_id, worker_id, operation_id, request_hash, arguments):
        with self.transaction() as conn:
            self.guard(conn, workspace_id, run_id, worker_id)
            query = select(executions).where(executions.c.id == identity)
            if self.engine.dialect.name == 'postgresql':
                query = query.with_for_update()
            old = conn.execute(query).mappings().first()
            if old:
                if old['request_hash'] != request_hash:
                    raise ValueError('同一步骤的执行参数已改变，不能复用原调用身份')
                if old['result'] is not None:
                    return old['result']
                # Caller holds a non-inheritable OS lock; previous helper must be gone.
                conn.execute(update(executions).where(executions.c.id == identity).values(status='prepared'))
            else:
                conn.execute(insert(executions).values(id=identity, owner_id=self.owner_id,
                    workspace_id=workspace_id, run_id=run_id, operation_id=operation_id,
                    request_hash=request_hash, arguments=arguments, manifest=[], status='prepared',
                    result=None, created_at=time.time()))
        return None

    def started(self, identity, workspace_id, run_id, worker_id, manifest):
        with self.transaction() as conn:
            self.guard(conn, workspace_id, run_id, worker_id)
            conn.execute(update(executions).where(executions.c.id == identity,
                executions.c.owner_id == self.owner_id).values(status='running', manifest=manifest))

    def finish(self, identity, result):
        # Audit a cancelled/lost-lease process, without publishing a task outcome.
        with self.transaction() as conn:
            conn.execute(update(executions).where(executions.c.id == identity,
                executions.c.owner_id == self.owner_id).values(status=result['status'],
                    manifest=result.get('manifest', []), result=result, finished_at=time.time()))

    def list(self, workspace_id, run_id):
        with self.engine.connect() as conn:
            rows = conn.execute(select(executions).where(executions.c.owner_id == self.owner_id,
                executions.c.workspace_id == workspace_id, executions.c.run_id == run_id)
                .order_by(executions.c.created_at.desc()).limit(50)).mappings()
            return [dict(row) for row in rows]
