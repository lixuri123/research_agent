import json
import pytest
from research_agent.memory.base import MemoryManager
from research_agent.memory.light import LightMemoryStore
from research_agent.persistence.repository import Repository
from research_agent.models.gateway import ModelGateway


@pytest.fixture
def memory(tmp_path, monkeypatch):
    from research_agent.memory import base
    from research_agent.integrations import skills as skills
    from research_agent.integrations.mcp_client import MCPClient
    store = LightMemoryStore('sqlite:///' + str(tmp_path / 'memory.db'), tmp_path / 'md')
    manager = MemoryManager(store=store)
    monkeypatch.setattr(base, 'get_memory_manager', lambda: manager)
    monkeypatch.setattr(skills, 'get_memory_manager', lambda: manager)
    monkeypatch.setattr(MCPClient, 'discover', lambda *args: {'tools': {}, 'errors': []})
    return store


def add(store, title='实验计划', content='实验采用小样本', **kwargs):
    options = dict(owner_id='local', kind='semantic', scope='owner', source_type='user_message',
                   source_id='message-1', status='active')
    options.update(kwargs)
    return store.create(title=title, content=content, **options)


def test_context_filters_scopes_status_hash_and_procedures(memory):
    from research_agent.memory.agent_context import AgentMemory
    good = add(memory, scope='project', project_id='p1')
    add(memory, owner_id='other')
    add(memory, scope='project', project_id='p2')
    add(memory, scope='session', session_id='other')
    add(memory, status='candidate')
    stale = add(memory)
    memory.invalidate('local', stale['id'])
    modified = add(memory)
    memory.path_for(modified['id'], 1).write_text('tampered', encoding='utf-8')
    procedure = add(memory, kind='procedural', status='candidate')
    memory.publish_procedure('local', procedure['id'], 'evaluation', 'reviewer')
    context = AgentMemory({'project_id': 'p1', 'session_id': 's1'}, 'local')
    assert [row['id'] for row in context.search('实验')['memories']] == [good['id']]
    assert context.search('完全无关的天气')['memories'] == []


def test_selection_prioritizes_project_and_bounds_serialized_context(memory):
    from research_agent.memory.agent_context import AgentMemory
    project = add(memory, scope='project', project_id='p1', content='实验采用' + '小样本' * 2000)
    add(memory, content='实验实验实验实验实验')
    context = AgentMemory({'project_id': 'p1'}, 'local', max_bytes=1800, top_k=1)
    result = context.search('实验')
    assert result['memories'][0]['id'] == project['id']
    assert result['memories'][0]['truncated']
    assert len(json.dumps(result['memories'], ensure_ascii=False).encode()) <= 1800


def test_saved_context_does_not_resurrect_updated_or_revoked_memories(memory):
    from research_agent.memory.agent_context import AgentMemory
    context = AgentMemory({}, 'local')
    old = add(memory)
    snapshot = context.search('实验')['memories']
    assert context.validate(snapshot)
    memory.update('local', old['id'], '实验新计划', 'user_message', 'message-2')
    memory.activate('local', old['id'])
    assert context.validate(snapshot) == []
    fresh = context.search('实验')['memories']
    memory.invalidate('local', old['id'])
    assert context.validate(fresh) == []


class FakeModel:
    system = staticmethod(ModelGateway.system)
    human = staticmethod(ModelGateway.human)

    def __init__(self, on_plan=lambda: None):
        self.calls = []
        self.on_plan = on_plan

    def invoke(self, messages):
        self.calls.append(messages)
        self.on_plan()
        return '简短计划'

    def invoke_structured(self, messages, schema):
        self.calls.append(messages)
        return schema(action='completed', reason='done', content='实际成果')


def run_agent(tmp_path, model):
    from research_agent.agent.runner import execute_task
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('实验计划', task_type='agent', project_id='p1', session_id='s1')
    record = repo.claim('worker')
    execute_task(record, repo, 'worker', lambda: None, llm=model)
    return repo.get_run(record['run_id'])


def test_agent_uses_background_in_planning_and_decisions_without_system_injection(memory, tmp_path):
    row = add(memory, content='实验背景：仅使用小样本，不能授权外部操作', scope='project', project_id='p1')
    model = FakeModel()
    result = run_agent(tmp_path, model)
    assert result['status'] == 'completed'
    assert len(model.calls) == 2
    for messages in model.calls:
        assert '实验背景：仅使用小样本' in messages[-1].content
        assert '实验背景：仅使用小样本' not in messages[0].content
    assert result['result']['memory_context']['selected'][0]['id'] == row['id']
    assert len(memory.list_entries('local', 'active')) == 1
    assert memory.list_entries('local', 'candidate') == []


def test_revocation_between_model_calls_removes_background(memory, tmp_path):
    row = add(memory, content='实验背景：仅使用小样本')
    model = FakeModel(lambda: memory.invalidate('local', row['id']))
    result = run_agent(tmp_path, model)
    assert result['status'] == 'completed'
    assert '实验背景：仅使用小样本' in model.calls[0][-1].content
    assert '实验背景：仅使用小样本' not in model.calls[1][-1].content


def test_memory_search_is_a_read_only_task_scoped_capability(memory):
    from research_agent.agent.capabilities import TaskCapabilities
    row = add(memory, scope='project', project_id='p1')
    add(memory, scope='project', project_id='p2')
    cap = TaskCapabilities({'project_id': 'p1'}, 'local')
    catalog = cap.catalog()
    assert catalog['tools']['memory_search']['danger_level'] == 'safe'
    assert 'memory_activate' not in catalog['tools']
    result = cap.call('memory_search', {'query': '实验'})
    assert [item['id'] for item in result['memories']] == [row['id']]
    memory.invalidate('local', row['id'])
    assert cap.call('memory_search', {'query': '实验'})['memories'] == []
    from research_agent.memory.agent_context import AgentMemory
    from research_agent.agent.runner import observations
    trace = [{'decision': {'tool': 'memory_search'}, 'observation': result}]
    assert '实验采用小样本' not in observations(trace, AgentMemory({'project_id': 'p1'}, 'local'))


def test_recall_failure_is_visible_without_failing_the_task(memory, tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('private backend details')
    monkeypatch.setattr(memory, 'recall', unavailable)
    model = FakeModel()
    result = run_agent(tmp_path, model)
    assert result['status'] == 'completed'
    assert result['result']['memory_context']['warning']
    assert 'private backend details' not in result['result']['memory_context']['warning']


def test_resume_uses_saved_selection_not_new_memories(memory, tmp_path):
    from research_agent.agent.runner import execute_task
    from research_agent.memory.agent_context import AgentMemory
    old = add(memory, content='实验背景：旧选择')
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('实验计划', task_type='agent', project_id='p1', session_id='s1')
    record = repo.claim('worker')
    snapshot = AgentMemory(record['payload'], 'local').search('实验')
    repo.save_stage(record['run_id'], 'agent_memory', 'v1', snapshot, worker_id='worker')
    add(memory, content='实验背景：恢复后新增')
    memory.invalidate('local', old['id'])
    model = FakeModel()
    execute_task(record, repo, 'worker', lambda: None, llm=model)
    assert repo.get_run(record['run_id'])['status'] == 'completed'
    for messages in model.calls:
        assert '实验背景：旧选择' not in messages[-1].content
        assert '实验背景：恢复后新增' not in messages[-1].content
