"""Route allowlists, relevant profiles and immutable task/background excerpts."""
import json
import re
from collections import defaultdict
from research_agent.context.schemas import SourceBlock, SourceSnapshot
from research_agent.context.sources import answer, visible_profile


def personalized(question):
    return bool(re.search(r'(结合|根据|按照|按|考虑).{0,12}我|适合我|我的(背景|时间|偏好|经历|习惯)|我(以前|之前|过去)', question))


def personal_history(question):
    return bool(re.search(r'我(以前|之前|过去)|之前.{0,8}(我|我们)|记得.{0,8}(我|我们)|回忆.{0,8}(我|我们)', question))


def relevance(query, content):
    # Deterministic lexical matching, including adjacent CJK pairs, no semantic guessing.
    terms = set(re.findall(r'[a-zA-Z0-9_]{2,}', query.casefold()))
    for fragment in re.findall(r'[\u4e00-\u9fff]+', query):
        terms.update(fragment[i:i+2] for i in range(len(fragment)-1))
    text = content.casefold()
    return sum(term in text for term in terms)


def select_sources(request, decision, sources):
    blocks, omitted, warnings = [], [], []
    if decision.needs_clarification:
        return SourceSnapshot()
    targets = sources.tasks(decision.task_ids) if decision.task_ids else []
    for row in targets:
        payload = row['payload']
        # Only approved persisted fields; neither keys nor raw provider config belongs here.
        summary = {'id':row['task_id'], 'title':payload['topic'], 'status':row['status'],
            'workspace_id':payload.get('workspace_id'), 'requirements':payload.get('requirements', '')[:1500],
            'result':answer(row)[:5000], 'historical_constraints':True}
        summary['progress'] = sources.repo.context_task_progress(row['run_id'], request.owner_id, request.submitted_at)
        events = [event for event in sources.repo.events(row['run_id']) if event['created_at'] < request.submitted_at]
        states = {'submitted':'queued','claimed':'running', **{s:s for s in
            ('completed','partial','insufficient_evidence','waiting_approval','failed','rejected','cancelled')}}
        for event in sorted(events,key=lambda e:e['created_at']):
            if event['event'] in states:
                summary['status'] = states[event['event']]
        ended = any(event['event'] in {'completed','partial','insufficient_evidence'} for event in events)
        if not ended:
            summary['result'] = ''
        blocks.append(SourceBlock(kind='task', id=row['task_id'], version=row['revision'],
            scope='project' if payload.get('workspace_id') else 'person', scope_id=payload.get('workspace_id'),
            content=json.dumps(summary, ensure_ascii=False), metadata={'title':payload['topic'][:300], 'status':summary['status']},
            priority=80, truncated=len(answer(row)) > 5000 or len(payload.get('requirements','')) > 1500))
    if decision.turn_ids:
        for row in sources.turns(decision.turn_ids):
            if row['task_id'] in decision.task_ids:
                omitted.append({'kind':'history','id':row['task_id'],'reason':'duplicate_task_result'})
                continue
            user, assistant = row['payload']['topic'], answer(row)
            content = json.dumps({'user':user[:1000], 'assistant':assistant[:3000]}, ensure_ascii=False)
            blocks.append(SourceBlock(kind='history', id=row['task_id'], version=row['revision'], scope='session',
                scope_id=request.session_id, content=content, priority=70, truncated=len(user)>1000 or len(assistant)>3000))
    user_text = request.question + '\n' + request.requirements
    if request.context.profile_ids or personalized(user_text):
        explicit = set(request.context.profile_ids)
        groups = defaultdict(list)
        try:
            profiles = sources.profiles()
        except Exception as exc:
            profiles = []
            warnings.append('profile_unavailable:' + type(exc).__name__)
        for row in profiles:
            if row['id'] in explicit:
                if row['status'] != 'active' or row['basis'] != 'user_confirmed':
                    omitted.append({'kind':'profile','id':row['id'],'reason':'unconfirmed_or_stale'})
                elif row['updated_at'] >= request.submitted_at:
                    omitted.append({'kind':'profile','id':row['id'],'reason':'after_submission'})
            if row['status'] == 'active' and row['basis'] == 'user_confirmed' and visible_profile(row, request) and row['updated_at'] < request.submitted_at:
                groups[(tuple(row['path']),row['scope'],row['scope_id'])].append(row)
        candidates = []
        for rows in groups.values():
            conflict = len({json.dumps(row['value'], sort_keys=True, ensure_ascii=False) for row in rows}) > 1
            for row in rows:
                content = json.dumps({key:row[key] for key in ('path','value','scope','scope_id','basis','source_refs')}, ensure_ascii=False)
                score = relevance(user_text, json.dumps({'path':row['path'],'value':row['value']},ensure_ascii=False))
                if row['id'] not in explicit and not score:
                    continue
                if conflict and row['id'] not in explicit:
                    omitted.append({'kind':'profile','id':row['id'],'reason':'profile_conflict'})
                    continue
                candidates.append((row['id'] in explicit, score, row, content, conflict))
        candidates.sort(key=lambda x:(x[0],x[1],x[2]['updated_at'],x[2]['id']), reverse=True)
        auto_count = 0
        for is_explicit, _, row, content, conflict in candidates:
            if not is_explicit and auto_count >= 3:
                omitted.append({'kind':'profile','id':row['id'],'reason':'selection_limit'})
                continue
            if not is_explicit:
                auto_count += 1
            blocks.append(SourceBlock(kind='profile', id=row['id'], version=row['version'], scope=row['scope'],
                scope_id=row['scope_id'], content=content, priority=90 if is_explicit else 40,
                metadata={'conflict':conflict,'explicit':is_explicit}))
    use_memory = decision.route != 'independent' or personal_history(user_text)
    if request.context.mode == 'independent':
        use_memory = False
    if use_memory:
        query = request.question
        if targets:
            query += '\n' + '\n'.join(row['payload']['topic'] for row in targets)
        if decision.turn_ids:
            query += '\n' + '\n'.join(row['payload']['topic'] for row in sources.turns(decision.turn_ids))
        try:
            memory = sources.memory()
            result = memory.search(query[:4000])
            for row in result['memories']:
                if row.get('updated_at', request.submitted_at) >= request.submitted_at:
                    continue
                blocks.append(SourceBlock(kind='memory', id=row['id'], version=row['version'], scope=row['scope'],
                    scope_id=row.get('project_id') or row.get('session_id'), content=json.dumps(row,ensure_ascii=False),
                    metadata={'reference':row}, priority=30, truncated=row.get('truncated',False)))
        except Exception as exc:
            warnings.append('memory_unavailable:' + type(exc).__name__)
    return SourceSnapshot(blocks=blocks, omitted=omitted, warnings=warnings)
