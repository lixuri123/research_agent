import time
import pytest
from research_agent.persistence.repository import Repository
from research_agent.profile.store import ProfileStore
from research_agent.profile.schemas import AttributeDraft
from research_agent.context.schemas import ContextRequest, ContextOptions, RouteDecision, SourceSnapshot
from research_agent.context.sources import ContextSources, ContextReferenceError
from research_agent.context.selector import select_sources


@pytest.fixture
def repo(tmp_path):
    value = Repository('sqlite:///' + str(tmp_path / 'context.db'))
    yield value
    value.engine.dispose()


def finished(repo, title, answer, **kwargs):
    row = repo.submit(title, task_type='agent', **kwargs)
    leased = repo.claim('w')
    assert leased['run_id'] == row['run_id']
    repo.finish(row['run_id'], 'w', 'completed', {'content': answer})
    return row


def request(**kwargs):
    values = dict(question='Redis 是什么', owner_id='local', session_id='s', submitted_at=time.time() + 1, run_id='new')
    values.update(kwargs)
    return ContextRequest(**values)


def test_turns_scope_and_cutoff(repo):
    mine = finished(repo, '我的方案', '有效方案', session_id='s')
    cutoff = time.time()
    finished(repo, '他人', 'SECRET', owner_id='other', session_id='s')
    finished(repo, '其他空间', 'SPACE SECRET', session_id='s', workspace_id='x')
    project_a = finished(repo, '项目甲', 'PROJECT A',session_id='s',project_id='a')
    finished(repo, '项目乙', 'PROJECT B',session_id='s',project_id='b')
    src = ContextSources(repo, request(submitted_at=cutoff))
    assert [item['task_id'] for item in src.index().turns] == [mine['task_id']]
    assert 'SECRET' not in str(src.index().turns)
    project_source = ContextSources(repo,request(project_id='a'))
    assert [item['task_id'] for item in project_source.index().turns] == [project_a['task_id']]


def test_independent_never_loads_automatic_memory_or_task_body(repo, monkeypatch):
    finished(repo, '论文实验', 'UNRELATED RESULT', session_id='s')
    src = ContextSources(repo, request())
    monkeypatch.setattr(src, 'memory', lambda: pytest.fail('No automatic memory'))
    monkeypatch.setattr(src, 'tasks', lambda ids: pytest.fail('No task body'))
    snapshot = select_sources(src.request, RouteDecision(route='independent', rule_id='R09', reason_codes=('self_contained',)), src)
    assert snapshot.blocks == []


def test_explicit_references_validate_owner_and_history_scope(repo):
    other = finished(repo, '他人的任务', 'SECRET', owner_id='other', session_id='s')
    cross = finished(repo, '别的空间', '可明确引用的本人目标', workspace_id='other-space', session_id='other')
    with pytest.raises(ContextReferenceError):
        ContextSources(repo, request(context=ContextOptions(task_ids=[other['task_id']]))).validate_references()
    ContextSources(repo, request(context=ContextOptions(task_ids=[cross['task_id']]))).validate_references()
    with pytest.raises(ContextReferenceError):
        ContextSources(repo, request(context=ContextOptions(turn_ids=[cross['task_id']]))).validate_references()


def test_personal_attributes_active_scoped_and_revalidated(repo):
    store = ProfileStore(repo.engine)
    draft = AttributeDraft(path=['研究/开发', '每周时间'], value={'小时': 8}, source_refs=[{'type':'user_edit','id':'x'}])
    row = store.create(draft)
    row = store.change_status(row['id'], row['version'], 'active')
    req = request(context=ContextOptions(profile_ids=[row['id']]))
    src = ContextSources(repo, req)
    snap = select_sources(req, RouteDecision(route='independent', rule_id='R01', reason_codes=('explicit',)), src)
    assert len(snap.blocks) == 1 and '8' in snap.blocks[0].content
    store.change_status(row['id'], row['version'], 'stale')
    current = src.revalidate(snap)
    assert current.blocks == [] and current.omitted[0]['reason'] == 'source_changed'


def test_active_enumeration_does_not_use_default_fifty_limit(repo):
    for i in range(60):
        repo.submit(f'task{i}', task_type='agent')
    rows = repo.list_context_active_tasks('local', None, time.time() + 1, limit=11)
    assert len(rows) == 11
    assert ContextSources(repo, request(question='安排我正在做的所有任务')).index().active_overflow


def test_snapshot_roundtrip_does_not_load_new_history(repo):
    row = finished(repo, '原方案', '原方案内容', session_id='s')
    src = ContextSources(repo, request(question='这个方案详细一点'))
    decision = RouteDecision(route='followup', rule_id='R07', turn_ids=(row['task_id'],), reason_codes=('anchor',))
    snapshot = select_sources(src.request, decision, src)
    finished(repo, '新方案', '不应追加的内容', session_id='s')
    recovered = src.revalidate(SourceSnapshot.model_validate(snapshot.model_dump(mode='json')))
    assert '原方案内容' in str(recovered.blocks)
    assert '不应追加的内容' not in str(recovered.blocks)


def test_conflicting_profiles_are_not_silently_chosen(repo):
    store = ProfileStore(repo.engine)
    for value in (8, 20):
        row = store.create(AttributeDraft(path=['可用时间'], value=value, source_refs=[{'type':'user_edit','id':'x'}]))
        store.change_status(row['id'], row['version'], 'active')
    req = request(question='结合我的可用时间安排学习')
    src = ContextSources(repo, req)
    snap = select_sources(req, RouteDecision(route='independent', rule_id='R09', reason_codes=('self_contained',)), src)
    assert not snap.blocks
    assert {item['reason'] for item in snap.omitted} == {'profile_conflict'}
