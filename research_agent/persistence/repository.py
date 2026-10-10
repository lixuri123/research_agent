"""Durable SQLAlchemy Core task repository; storage errors propagate to callers."""
from contextlib import contextmanager
import hashlib
import json
import time
import uuid
from sqlalchemy import (JSON, Boolean, Column, Float, Integer, MetaData, String, Table,
                        UniqueConstraint, and_, create_engine, insert, or_, select, update)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
runs = Table('research_runs', metadata,
    Column('run_id', String(36), primary_key=True), Column('task_id', String(36), unique=True),
    Column('owner_id', String(255), nullable=False), Column('idempotency_key', String(255)),
    Column('request_hash', String(64)), Column('payload', JSON, nullable=False),
    Column('status', String(40), nullable=False), Column('worker_id', String(255)),
    Column('lease_expires_at', Float), Column('result', JSON), Column('approval', JSON), Column('approval_required', Boolean, nullable=False),
    Column('current_report_version_id', String(36)), Column('revision', Integer, nullable=False),
    Column('created_at', Float, nullable=False),
    UniqueConstraint('owner_id', 'idempotency_key'))
reports = Table('research_reports', metadata,
    Column('report_version_id', String(36), primary_key=True), Column('run_id', String(36), nullable=False),
    Column('owner_id', String(255), nullable=False), Column('content_hash', String(64), nullable=False),
    Column('report', JSON, nullable=False), Column('created_at', Float, nullable=False))
stages = Table('research_stages', metadata,
    Column('run_id', String(36), primary_key=True), Column('stage', String(255), primary_key=True),
    Column('input_hash', String(255), primary_key=True), Column('result', JSON, nullable=False))
event_table = Table('research_events', metadata,
    Column('event_id', String(36), primary_key=True), Column('run_id', String(36), nullable=False),
    Column('event', String(60), nullable=False), Column('created_at', Float, nullable=False), Column('data', JSON))

def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def _json(value):
    return json.loads(json.dumps(value, allow_nan=False))

class Repository:
    def __init__(self, database_url):
        if not database_url:
            raise ValueError('database_url is required')
        self.engine = create_engine(database_url)
        metadata.create_all(self.engine)

    @contextmanager
    def _transaction(self):
        # SQLite has no row locks: serialize writers before reading lease state.
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

    def _event(self, conn, run_id, event, data=None):
        conn.execute(insert(event_table).values(event_id=str(uuid.uuid4()), run_id=run_id,
            event=event, created_at=time.time(), data=data))

    def _row(self, conn, run_id):
        query = select(runs).where(runs.c.run_id == run_id)
        if self.engine.dialect.name == 'postgresql': query = query.with_for_update()
        row = conn.execute(query).mappings().first()
        if row is None: raise ValueError('Unknown run')
        return dict(row)

    def record_event(self, run_id, worker_id, event, data=None):
        with self._transaction() as conn:
            self._leased(conn, run_id, worker_id)
            self._event(conn, run_id, event, _json(data) if data is not None else None)

    def _cas(self, conn, row, **values):
        conditions = [runs.c.run_id == row['run_id'], runs.c.revision == row['revision']]
        if row['status'] == 'running':
            conditions.extend([runs.c.worker_id == row['worker_id'], runs.c.lease_expires_at > time.time()])
        changed = conn.execute(update(runs).where(*conditions).values(revision=row['revision'] + 1, **values))
        if changed.rowcount != 1: raise ValueError('Concurrent state change')

    def submit(self, topic, requirements='', session_id=None, owner_id='local', idempotency_key=None, budget=None, task_type='research', project_id=None, skill_ids=None, authorized_mcp_tools=None, workspace_id=None, model_selection=None, context=None):
        payload = _json(dict(topic=topic, requirements=requirements, session_id=session_id, budget=budget))
        if model_selection is not None:
            payload['model_selection'] = _json(model_selection)
        if workspace_id:
            payload['workspace_id'] = workspace_id
        if context is not None:
            from research_agent.context.schemas import ContextOptions
            payload['context'] = ContextOptions.model_validate(context).model_dump(mode='json')
            payload['context_version'] = 1
        if task_type not in {'research', 'agent'}:
            raise ValueError('Unknown task type')
        if task_type != 'research':
            payload['task_type'] = task_type
            payload['project_id'] = project_id
            payload['skill_ids'] = list(skill_ids or [])
            payload['authorized_mcp_tools'] = list(authorized_mcp_tools or [])
        # Idempotency reflects the client request, not a changing server revision.
        request_payload = dict(payload)
        if model_selection is not None:
            request_payload['model_selection'] = {key: model_selection[key] for key in ('provider_id', 'model')}
        digest = _hash(request_payload)
        def existing():
            with self.engine.connect() as conn:
                row = conn.execute(select(runs).where(runs.c.owner_id == owner_id,
                    runs.c.idempotency_key == idempotency_key)).mappings().first()
                if row is None: return None
                if row['request_hash'] != digest: raise ValueError('Idempotency key input mismatch')
                return dict(row)
        if idempotency_key is not None:
            found = existing()
            if found: return found
        if context is not None:
            from research_agent.context.schemas import ContextRequest
            from research_agent.context.sources import ContextSources
            ContextSources(self, ContextRequest(question=topic,requirements=requirements,owner_id=owner_id,
                session_id=session_id,workspace_id=workspace_id,project_id=project_id,
                submitted_at=time.time(),run_id='',context=payload['context'])).validate_references()
        if model_selection is not None and 'revision' not in model_selection:
            from research_agent.models.provider_registry import pin_selection
            payload['model_selection'] = pin_selection(model_selection)
        row = dict(task_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), owner_id=owner_id,
            idempotency_key=idempotency_key, request_hash=digest, payload=payload, status='queued',
            worker_id=None, lease_expires_at=None, result=None, approval=None, approval_required=False,
            current_report_version_id=None, revision=0, created_at=time.time())
        try:
            with self._transaction() as conn:
                conn.execute(insert(runs).values(**row)); self._event(conn, row['run_id'], 'submitted')
        except IntegrityError:
            if idempotency_key is None: raise
            found = existing()
            if found: return found
            raise
        return row

    def get(self, task_id, owner_id='local'):
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.task_id == task_id, runs.c.owner_id == owner_id)).mappings().first()
            return dict(row) if row else None

    def list_runs(self, owner_id='local', limit=50, offset=0, workspace_id=None):
        with self.engine.connect() as conn:
            query = (select(runs)
                .where(runs.c.owner_id == owner_id)
                .order_by(runs.c.created_at.desc(), runs.c.run_id.desc())
                .limit(limit)
                .offset(offset))
            if workspace_id is not None:
                workspace = runs.c.payload['workspace_id'].as_string()
                query = query.where(workspace == workspace_id if workspace_id else or_(workspace.is_(None), workspace == ''))
            return [dict(row) for row in conn.execute(query).mappings()]

    def get_run(self, run_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
            return dict(row) if row else None

    def get_context_turns(self, owner_id, session_id, workspace_id, before, exclude_run_id, limit=6, *, project_id=None):
        if not session_id:
            return []
        workspace = runs.c.payload['workspace_id'].as_string()
        query = select(runs).where(runs.c.owner_id == owner_id,
            runs.c.payload['session_id'].as_string() == session_id,
            runs.c.created_at < before, runs.c.run_id != exclude_run_id,
            runs.c.status.in_(['completed', 'partial', 'insufficient_evidence']),
            select(event_table.c.event_id).where(event_table.c.run_id == runs.c.run_id,
                event_table.c.event.in_(['completed', 'partial', 'insufficient_evidence']),
                event_table.c.created_at < before).exists(),
            workspace == workspace_id if workspace_id else or_(workspace.is_(None), workspace == ''))
        project = runs.c.payload['project_id'].as_string()
        if not workspace_id:
            query = query.where(project == project_id if project_id else or_(project.is_(None),project == ''))
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query.order_by(runs.c.created_at.desc(), runs.c.run_id.desc()).limit(limit)).mappings()]

    def get_context_tasks(self, owner_id, task_ids):
        if not task_ids:
            return []
        with self.engine.connect() as conn:
            rows = {row['task_id']: dict(row) for row in conn.execute(select(runs).where(
                runs.c.owner_id == owner_id, runs.c.task_id.in_(task_ids))).mappings()}
        return [rows[identifier] for identifier in task_ids if identifier in rows]

    def list_context_active_tasks(self, owner_id, workspace_id, before, limit=11, *, project_id=None):
        return self.context_task_index(owner_id, workspace_id, before, active=True, limit=limit, project_id=project_id)

    def context_task_index(self, owner_id, workspace_id, before, *, active=False, limit=None, project_id=None):
        workspace = runs.c.payload['workspace_id'].as_string()
        query = select(runs.c.task_id, runs.c.status, runs.c.created_at,
            runs.c.payload['topic'].as_string().label('title'),
            workspace.label('workspace_id'), runs.c.payload['session_id'].as_string().label('session_id')).where(
                runs.c.owner_id == owner_id, runs.c.created_at < before,
                workspace == workspace_id if workspace_id else or_(workspace.is_(None), workspace == ''))
        if active:
            query = query.where(runs.c.status.in_(['queued', 'running', 'waiting_approval']))
        if project_id and not workspace_id:
            query = query.where(runs.c.payload['project_id'].as_string() == project_id)
        query = query.order_by(runs.c.created_at.desc(), runs.c.run_id.desc())
        if limit is not None:
            query = query.limit(limit)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()]

    def context_task_progress(self, run_id, owner_id, before):
        completed_steps = {event['data']['step']-1 for event in self.events(run_id)
            if event['event'] == 'tool_finished' and event['created_at'] < before and event.get('data', {}).get('step')}
        with self.engine.connect() as conn:
            query = select(stages.c.stage, stages.c.result).join(runs, runs.c.run_id == stages.c.run_id).where(
                runs.c.run_id == run_id, runs.c.owner_id == owner_id,
                stages.c.stage.like('agent_step_%'), stages.c.input_hash == 'v3')
            saved = [row for row in conn.execute(query).mappings()
                if int(row['stage'].rsplit('_',1)[-1]) in completed_steps]
        if not saved:
            return {}
        last = max(saved, key=lambda row:int(row['stage'].rsplit('_',1)[-1]))
        decision = last['result'].get('decision') or {}
        return {'step':last['stage'], 'action':decision.get('action'), 'tool':decision.get('tool'),
                'reason':str(decision.get('reason',''))[:600]}

    def claim(self, worker_id, lease_seconds=120):
        if lease_seconds <= 0: raise ValueError('Lease must be positive')
        for _ in range(10):
            now = time.time()
            with self._transaction() as conn:
                eligible = or_(runs.c.status == 'queued', and_(runs.c.status == 'running', runs.c.lease_expires_at <= now))
                query = select(runs).where(eligible).order_by(runs.c.created_at, runs.c.run_id).limit(1)
                if self.engine.dialect.name == 'postgresql': query = query.with_for_update(skip_locked=True)
                row = conn.execute(query).mappings().first()
                if not row: return None
                changed = conn.execute(update(runs).where(runs.c.run_id == row['run_id'],
                    runs.c.revision == row['revision'], eligible).values(status='running', worker_id=worker_id,
                    lease_expires_at=now + lease_seconds, revision=row['revision'] + 1))
                if changed.rowcount != 1: continue
                self._event(conn, row['run_id'], 'claimed', {'worker_id': worker_id})
                return self._row(conn, row['run_id'])
        return None

    def _leased(self, conn, run_id, worker_id):
        row = self._row(conn, run_id)
        if row['status'] != 'running' or row['worker_id'] != worker_id or (row['lease_expires_at'] or 0) <= time.time():
            raise ValueError('Worker lease is absent, expired or superseded')
        return row

    def heartbeat(self, run_id, worker_id, lease_seconds=120):
        if lease_seconds <= 0: raise ValueError('Lease must be positive')
        with self._transaction() as conn:
            row = self._leased(conn, run_id, worker_id)
            self._cas(conn, row, lease_expires_at=time.time() + lease_seconds)
            return self._row(conn, run_id)

    def finish(self, run_id, worker_id, status, result):
        if status not in {'waiting_approval', 'completed', 'rejected', 'partial', 'insufficient_evidence', 'failed', 'cancelled'}:
            raise ValueError('Invalid finish status')
        result = _json(result)
        with self._transaction() as conn:
            row = self._leased(conn, run_id, worker_id)
            if status == 'waiting_approval' and not row['current_report_version_id']: raise ValueError('A saved report is required')
            if status == 'completed' and row['current_report_version_id']:
                decision = row['approval']
                if row['approval_required'] and decision is None:
                    raise ValueError('Current report requires approval')
                if decision is not None and (not decision['approved'] or decision['report_version_id'] != row['current_report_version_id']):
                    raise ValueError('Approval does not authorize current report')
            if status == 'rejected' and isinstance(result, dict): result.pop('final_report', None)
            self._cas(conn, row, status=status, result=result, worker_id=None, lease_expires_at=None,
                approval_required=row['approval_required'] or status == 'waiting_approval')
            self._event(conn, run_id, status)
            return self._row(conn, run_id)

    def save_report(self, run_id, report, worker_id=None):
        report = _json(report)
        with self._transaction() as conn:
            row = self._leased(conn, run_id, worker_id) if worker_id is not None else self._row(conn, run_id)
            if row['status'] not in {'running', 'waiting_approval'}: raise ValueError('Run cannot accept reports')
            version = dict(report_version_id=str(uuid.uuid4()), run_id=run_id, owner_id=row['owner_id'],
                report=report, content_hash=_hash(report), created_at=time.time())
            conn.execute(insert(reports).values(**version))
            self._cas(conn, row, current_report_version_id=version['report_version_id'], approval=None)
            self._event(conn, run_id, 'report_saved', {'report_version_id': version['report_version_id']})
            return version

    def approve(self, report_version_id, approved, owner_id='local'):
        if type(approved) is not bool: raise ValueError('approved must be boolean')
        with self._transaction() as conn:
            version = conn.execute(select(reports).where(reports.c.report_version_id == report_version_id,
                reports.c.owner_id == owner_id)).mappings().first()
            if not version: raise ValueError('Unknown report or owner')
            row = self._row(conn, version['run_id'])
            if row['current_report_version_id'] != report_version_id or _hash(version['report']) != version['content_hash']:
                raise ValueError('Approval version mismatch')
            decision = dict(approved=approved, report_version_id=report_version_id, content_hash=version['content_hash'])
            if row['approval'] is not None:
                if row['approval'] != decision: raise ValueError('Conflicting approval')
                return row
            if row['status'] != 'waiting_approval': raise ValueError('Run is not waiting for approval')
            result = row['result']
            if not approved and isinstance(result, dict):
                result = dict(result); result.pop('final_report', None)
            status = 'queued' if approved else 'rejected'
            self._cas(conn, row, approval=decision, status=status, result=result, worker_id=None, lease_expires_at=None)
            self._event(conn, row['run_id'], 'approved' if approved else 'rejected', decision)
            return self._row(conn, row['run_id'])

    def get_report(self, version_id, owner_id='local'):
        with self.engine.connect() as conn:
            row = conn.execute(select(reports).where(reports.c.report_version_id == version_id,
                reports.c.owner_id == owner_id)).mappings().first()
            return dict(row) if row else None

    def reports_for_run(self, run_id):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(reports).where(reports.c.run_id == run_id).order_by(reports.c.created_at)).mappings()]

    def events(self, run_id):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(event_table).where(event_table.c.run_id == run_id).order_by(event_table.c.created_at, event_table.c.event_id)).mappings()]

    def save_stage(self, run_id, stage, input_hash, result, worker_id=None):
        result = _json(result)
        with self._transaction() as conn:
            row = self._leased(conn, run_id, worker_id) if worker_id is not None else self._row(conn, run_id)
            if worker_id is not None: self._cas(conn, row)
            key = and_(stages.c.run_id == run_id, stages.c.stage == stage, stages.c.input_hash == input_hash)
            values = dict(run_id=run_id, stage=stage, input_hash=input_hash, result=result)
            if self.engine.dialect.name == 'postgresql':
                from sqlalchemy.dialects.postgresql import insert as upsert
            elif self.engine.dialect.name == 'sqlite':
                from sqlalchemy.dialects.sqlite import insert as upsert
            else: raise ValueError('Stages require PostgreSQL or SQLite')
            conn.execute(upsert(stages).values(**values).on_conflict_do_update(
                index_elements=['run_id', 'stage', 'input_hash'], set_={'result': result}))
            return result

    def get_stage(self, run_id, stage, input_hash):
        with self.engine.connect() as conn:
            row = conn.execute(select(stages.c.result).where(stages.c.run_id == run_id,
                stages.c.stage == stage, stages.c.input_hash == input_hash)).first()
            return row[0] if row else None
