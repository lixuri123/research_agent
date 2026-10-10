"""Owner-scoped adapters. Routing indexes never include old task bodies."""
from sqlalchemy import select, or_, and_
from research_agent.profile.store import ProfileStore, ProfileNotFound, attributes
from research_agent.context.schemas import ContextIndex, SourceSnapshot


class ContextReferenceError(ValueError):
    pass


def answer(row):
    result = row.get('result') or {}
    return result.get('content') or (result.get('final_report') or {}).get('content') or (result.get('draft') or {}).get('content') or ''


def visible_profile(row, request):
    return (row['scope'] == 'person' or
        row['scope'] == 'project' and row['scope_id'] == (request.workspace_id or request.project_id) or
        row['scope'] == 'session' and row['scope_id'] == request.session_id)


def visible_turn(row, request):
    payload = row['payload']
    return (payload.get('session_id') == request.session_id and
        (payload.get('workspace_id') or None) == request.workspace_id and
        (bool(request.workspace_id) or (payload.get('project_id') or None) == request.project_id))


class ContextSources:
    def __init__(self, repo, request):
        self.repo, self.request = repo, request
        self._memory = None

    def profiles(self):
        r = self.request
        ProfileStore(self.repo.engine,r.owner_id)
        scopes = [attributes.c.scope == 'person']
        if r.workspace_id or r.project_id:
            scopes.append(and_(attributes.c.scope == 'project', attributes.c.scope_id == (r.workspace_id or r.project_id)))
        if r.session_id:
            scopes.append(and_(attributes.c.scope == 'session', attributes.c.scope_id == r.session_id))
        with self.repo.engine.connect() as conn:
            return [ProfileStore.public(row) for row in conn.execute(select(attributes).where(
                attributes.c.owner_id == r.owner_id, or_(*scopes))).mappings()]

    def tasks(self, ids):
        rows = self.repo.get_context_tasks(self.request.owner_id, ids)
        if len(rows) != len(set(ids)) or any(row['created_at'] >= self.request.submitted_at for row in rows):
            raise ContextReferenceError('引用的上下文不存在或不可访问')
        return rows

    def turns(self, ids=None):
        r = self.request
        if ids:
            rows = self.tasks(ids)
            if any(not visible_turn(row,r) or
                   row['created_at'] >= r.submitted_at or row['run_id'] == r.run_id or
                   row['status'] not in {'completed', 'partial', 'insufficient_evidence'} or not answer(row) for row in rows):
                raise ContextReferenceError('引用的上下文不存在或不可访问')
            for row in rows:
                if not any(event['event'] in {'completed','partial','insufficient_evidence'} and event['created_at'] < r.submitted_at
                           for event in self.repo.events(row['run_id'])):
                    raise ContextReferenceError('引用的上下文不存在或不可访问')
            return rows
        return [row for row in self.repo.get_context_turns(r.owner_id, r.session_id, r.workspace_id,
            r.submitted_at, r.run_id, project_id=r.project_id) if answer(row)]

    def validate_references(self):
        r = self.request
        self.tasks(r.context.task_ids)
        if r.context.turn_ids:
            self.turns(r.context.turn_ids)
        if r.context.profile_ids:
            profiles = {row['id']: row for row in self.profiles()}
            if any(identifier not in profiles or not visible_profile(profiles[identifier], r) for identifier in r.context.profile_ids):
                raise ContextReferenceError('引用的上下文不存在或不可访问')

    def index(self):
        r = self.request
        turns = []
        # Explicit independent mode does not even read conversation excerpts.
        if r.context.mode != 'independent':
            for row in self.turns(r.context.turn_ids or None):
                saved = (row.get('result') or {}).get('context', {}).get('route', {})
                turns.append({'id': row['task_id'], 'task_id': row['task_id'], 'user': row['payload']['topic'][:1000],
                    'assistant': answer(row)[:2000], 'associated_task_ids': saved.get('task_ids', []),
                    'status': row['status'], 'title': row['payload']['topic'], 'created_at': row['created_at']})
        tasks = self.repo.context_task_index(r.owner_id, r.workspace_id, r.submitted_at, project_id=r.project_id) if r.context.mode != 'independent' else []
        active = self.repo.list_context_active_tasks(r.owner_id, r.workspace_id, r.submitted_at, project_id=r.project_id) if r.context.mode != 'independent' else []
        return ContextIndex(turns=turns, tasks=tasks, active_tasks=active[:10], active_overflow=len(active) > 10)

    def memory(self):
        if self._memory is None:
            from research_agent.memory.agent_context import AgentMemory
            r = self.request
            self._memory = AgentMemory({'workspace_id': r.workspace_id, 'project_id':r.project_id, 'session_id': r.session_id}, r.owner_id)
        return self._memory

    def revalidate(self, snapshot):
        blocks, omitted = [], list(snapshot.omitted)
        for block in snapshot.blocks:
            valid = True
            try:
                if block.kind == 'profile':
                    row = ProfileStore(self.repo.engine, self.request.owner_id).get(block.id)
                    valid = row['status'] == 'active' and row['version'] == block.version and visible_profile(row, self.request)
                elif block.kind == 'memory':
                    valid = bool(self.memory().validate([block.metadata['reference']]))
                elif block.kind in {'history', 'task'}:
                    # Keep original content; deletion/owner changes must not resurrect a saved body.
                    row = self.repo.get(block.id, self.request.owner_id)
                    valid = bool(row)
                    if valid and block.kind == 'history':
                        valid = visible_turn(row,self.request)
            except (ProfileNotFound, KeyError):
                valid = False
            except Exception:
                valid = False
            if valid:
                blocks.append(block)
            else:
                entry = {'kind':block.kind, 'id':block.id, 'reason':'source_changed'}
                if entry not in omitted:
                    omitted.append(entry)
        return SourceSnapshot(blocks=blocks, omitted=omitted, warnings=snapshot.warnings)
