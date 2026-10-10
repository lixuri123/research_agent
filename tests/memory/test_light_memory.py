import pytest

from research_agent.memory.light import LightMemoryStore
from research_agent.memory.base import MemoryManager


@pytest.fixture
def storage(tmp_path):
    return LightMemoryStore('sqlite:///' + str(tmp_path / 'memory.db'), tmp_path / 'md')


def test_session_history_survives_restart_and_window_does_not_delete(storage, tmp_path):
    for index in range(25):
        storage.append_message('local', 's1', 'user', f'message {index}')
    storage.append_message('other', 's1', 'user', 'secret')
    reopened = LightMemoryStore('sqlite:///' + str(tmp_path / 'memory.db'), tmp_path / 'md')
    assert len(reopened.messages('local', 's1')) == 25
    assert [m['content'] for m in reopened.recent('local', 's1', 2)] == ['message 23', 'message 24']
    assert len(reopened.messages('other', 's1')) == 1
    assert reopened.messages('local', 's2') == []


def test_candidate_scope_and_lifecycle(storage):
    memory = storage.create('local', 'pref', '请用中文回答', 'semantic', 'owner',
                            'user_message', 's1:1', status='candidate')
    assert storage.recall('local', '中文') == []
    storage.activate('local', memory['id'])
    assert [m['id'] for m in storage.recall('local', '中文')] == [memory['id']]
    from research_agent.core.schemas import MemoryItem
    assert isinstance(MemoryManager(store=storage).recall('中文')[0], MemoryItem)
    assert storage.recall('other', '中文') == []
    project = storage.create('local', 'project', '蛋白质检索', 'episodic', 'project',
                             'run_event', 'run-1', project_id='p1', status='active')
    assert project['id'] not in [m['id'] for m in storage.recall('local', '蛋白质')]
    assert project['id'] in [m['id'] for m in storage.recall('local', '蛋白质', project_id='p1')]
    session = storage.create('local', '约束', '本会话只看综述', 'semantic', 'session',
                             'user_message', 's1:3', session_id='s1', status='active')
    assert session['id'] not in [m['id'] for m in storage.recall('local', '综述', session_id='s2')]
    assert session['id'] in [m['id'] for m in storage.recall('local', '综述', session_id='s1')]
    storage.invalidate('local', memory['id'])
    assert storage.recall('local', '中文') == []


def test_changed_or_missing_markdown_never_enters_recall(storage):
    memory = storage.create('local', 'preference', '用中文', 'semantic', 'owner',
                            'user_message', 's:2', status='active')
    path = storage.path_for(memory['id'], 1)
    assert 'preference' in path.read_text(encoding='utf-8')
    assert 'user_message:s:2' in path.read_text(encoding='utf-8')
    path.write_text('篡改', encoding='utf-8')
    assert storage.recall('local', '中文') == []
    path.unlink()
    assert storage.recall('local', '中文') == []


def test_update_versions_and_delete(storage):
    memory = storage.create('local', 'preference', '中文', 'semantic', 'owner',
                            'user_message', 's:1', status='active')
    updated = storage.update('local', memory['id'], '英文', 'user_message', 's:2')
    assert updated['version'] == 2
    assert storage.recall('local', '中文') == []
    assert storage.recall('local', '英文') == []  # New revisions require review.
    storage.activate('local', memory['id'])
    assert [m['content'] for m in storage.recall('local', '英文')] == ['英文']
    storage.delete('local', memory['id'])
    assert storage.recall('local', '英文') == []
    assert not storage.path_for(memory['id'], 2).exists()


def test_parallel_revisions_keep_separate_files(storage, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    memory = storage.create('local', 'preference', 'start', 'semantic', 'owner',
                            'user_message', 's:1', status='active')
    original = storage._write
    def delayed_write(memory_id, version, content):
        if version == 2:
            time.sleep(.04)
        return original(memory_id, version, content)
    monkeypatch.setattr(storage, '_write', delayed_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(lambda n: storage.update('local', memory['id'], f'value {n}',
            'user_message', f's:{n+2}'), range(2)))
    assert sorted(row['version'] for row in revisions) == [2, 3]
    assert storage.path_for(memory['id'], 2).exists()
    assert storage.path_for(memory['id'], 3).exists()


def test_chinese_partial_query_retrieves_relevant_memory(storage):
    storage.create('local', '写作偏好', '研究报告应使用中文表达', 'semantic', 'owner',
                   'user_message', 's:1', status='active')
    assert [m['content'] for m in storage.recall('local', '中文研究报告')] == ['研究报告应使用中文表达']
    assert storage.recall('local', '天文望远镜') == []


def test_manager_assembles_with_budget_and_preserves_current_input(storage):
    manager = MemoryManager(store=storage, owner_id='local', session_id='s1')
    manager.append_message('user', '早期问题')
    manager.append_message('assistant', '早期回答')
    manager.remember('偏好中文', title='偏好', source_type='user_message', source_id='s1:1', status='active')
    pack = manager.assemble('中文研究', context_limit=130, system_text='系统', output_reserve=20,
                            safety_margin=10, token_counter=lambda value: len(value))
    assert pack.estimated_tokens <= 130
    assert '中文研究' in pack.rendered_text
    assert pack.selected_ids
    with pytest.raises(ValueError, match='当前输入'):
        manager.assemble('x' * 100, context_limit=30, output_reserve=10,
                         token_counter=lambda value: len(value))


def test_assembler_keeps_latest_message_when_window_is_tight(storage):
    manager = MemoryManager(store=storage, session_id='s1')
    manager.append_message('user', 'old message only')
    manager.append_message('assistant', 'latest answer')
    pack = manager.assemble('new question', context_limit=55, output_reserve=0,
                            safety_margin=0, token_counter=len)
    assert 'latest answer' in pack.rendered_text
    assert 'old message only' not in pack.rendered_text


def test_api_session_and_memory_lifecycle(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from research_agent import api
    monkeypatch.setattr(api, 'get_memory_manager', lambda: MemoryManager(store=storage))
    client = TestClient(api.app)
    assert client.post('/sessions/s1/messages', json={'role':'user', 'content':'请用中文'}).status_code == 200
    assert [row['content'] for row in client.get('/sessions/s1/messages').json()['messages']] == ['请用中文']
    created = client.post('/memories', json={'title':'语言', 'content':'请用中文', 'kind':'semantic',
        'scope':'owner', 'source_type':'user_message', 'source_id':'s1:1'}).json()
    assert created['status'] == 'candidate'
    assert [m['id'] for m in client.get('/memories', params={'status':'candidate'}).json()['memories']] == [created['id']]
    assert client.get('/memories/search', params={'query':'中文'}).json()['memories'] == []
    assert client.post(f"/memories/{created['id']}/activate").status_code == 200
    assert len(client.get('/memories/search', params={'query':'中文'}).json()['memories']) == 1
    assert client.put(f"/memories/{created['id']}", json={'content':'请用英文',
        'source_type':'user_message', 'source_id':'s1:2'}).json()['status'] == 'candidate'
    assert client.get('/memories/search', params={'query':'英文'}).json()['memories'] == []
    assert client.post(f"/memories/{created['id']}/activate").status_code == 200
    assert len(client.get('/memories/search', params={'query':'英文'}).json()['memories']) == 1
    assert client.post(f"/memories/{created['id']}/invalidate").status_code == 200
    assert client.get('/memories/search', params={'query':'英文'}).json()['memories'] == []
    assert client.delete(f"/memories/{created['id']}").status_code == 200
    assert client.get('/memories/search', params={'query':'中文'}).json()['memories'] == []


def test_procedure_requires_review_and_evaluation_before_skill_publish(storage):
    manager = MemoryManager(store=storage, owner_id='local', project_id='p1')
    candidate = manager.remember_procedure('paper-search', '# Search papers\n\n1. Verify sources.',
        source_id='run-1')
    assert candidate['status'] == 'candidate'
    assert manager.skills() == []
    with pytest.raises(ValueError):
        manager.activate(candidate['id'])
    with pytest.raises(ValueError, match='review'):
        manager.publish_procedure(candidate['id'], evaluation_id='', reviewed_by='')
    published = manager.publish_procedure(candidate['id'], evaluation_id='eval-1', reviewed_by='local')
    assert published['status'] == 'active'
    assert (storage.markdown_dir / 'skills' / candidate['id'] / 'v1' / 'SKILL.md').exists()
    assert [item['id'] for item in manager.skills()] == [candidate['id']]
    assert MemoryManager(store=storage, owner_id='other', project_id='p1').skills() == []
    (storage.markdown_dir / 'skills' / candidate['id'] / 'v1' / 'SKILL.md').write_text('tampered', encoding='utf-8')
    assert manager.skills() == []
    assert manager.recall('Search papers') == []


def test_api_publishes_reviewed_skill(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from research_agent import api
    from research_agent.integrations import skills as skills
    monkeypatch.setattr(api, 'get_memory_manager', lambda: MemoryManager(store=storage))
    monkeypatch.setattr(skills, 'get_memory_manager', lambda: MemoryManager(store=storage))
    client = TestClient(api.app)
    created = client.post('/memories', json={'title':'paper-search', 'content':'Verify paper sources',
        'kind':'procedural', 'scope':'owner', 'source_type':'run_event', 'source_id':'run-1'}).json()
    assert client.post(f"/memories/{created['id']}/activate").status_code == 409
    assert client.post(f"/memories/{created['id']}/publish-skill", json={
        'evaluation_id':'eval-1', 'reviewed_by':'local'}).status_code == 200
    assert [row['id'] for row in client.get('/skills').json()['skills']] == [created['id']]
