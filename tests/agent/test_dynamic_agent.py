import threading

import pytest

from research_agent.models.gateway import ModelGateway
from research_agent.persistence.repository import Repository


class Memory:
    def __init__(self, *args, **kwargs):
        pass

    def search(self, query):
        return {'memories': [], 'omitted_count': 0}

    def validate(self, memories):
        return []

    reference = staticmethod(lambda item: item)


class Capabilities:
    def __init__(self, *args, **kwargs):
        self.loaded, self.allowed = {}, set()

    def catalog(self):
        return {'tools': {'browser_read': {'danger_level': 'safe'}}, 'skills': [], 'errors': []}

    def instructions(self):
        return ''

    def restore(self, steps):
        pass

    def call(self, name, arguments, query='', operation_id=None):
        return {'documents': [{'id': arguments['query'], 'title': '论文',
            'url': 'https://paper.test/' + arguments['query'], 'content': '原文'}], 'error': None}


class Model:
    system = staticmethod(ModelGateway.system)
    human = staticmethod(ModelGateway.human)

    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.prompts = []

    def invoke(self, messages):
        return '比较两篇论文，必要时核查'

    def invoke_structured(self, messages, schema):
        self.prompts.append(messages)
        item = next(self.decisions)
        return schema(**(item(messages) if callable(item) else item))


def tool(name, **arguments):
    return {'action': 'tool', 'reason': '继续推进', 'tool': name, 'arguments': arguments}


def completed(content='最终比较与核查成果'):
    return {'action': 'completed', 'reason': '完成', 'content': content}


def spawn(goal):
    return tool('agent_spawn', goal=goal, context='用户提供的论文链接', completion_criteria='提供方法、局限及来源')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import research_agent.agent.capabilities as capabilities
    import research_agent.agent.delegation as delegation
    import research_agent.memory.agent_context as context
    monkeypatch.setattr(capabilities, 'TaskCapabilities', Capabilities)
    monkeypatch.setattr(delegation, 'TaskCapabilities', Capabilities)
    monkeypatch.setattr(context, 'AgentMemory', Memory)
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('比较两篇论文', task_type='agent')
    record = repo.claim('worker')
    return repo, record


def test_main_delegates_two_readings_then_adds_verification_and_delivers(setup):
    from research_agent.agent.runner import execute_task
    repo, record = setup
    main = Model([spawn('论文A'), spawn('论文B'), tool('agent_wait', timeout_seconds=5),
                  spawn('核查实验条件'), tool('agent_wait', timeout_seconds=5), completed()])
    children = iter([Model([tool('browser_read', query=paper), completed('已阅读' + paper)])
                     for paper in ['论文A', '论文B', '实验条件']])
    execute_task(record, repo, 'worker', lambda: None, llm=main,
                 child_llm_factory=lambda: next(children))
    row = repo.get_run(record['run_id'])
    assert row['status'] == 'completed'
    result = row['result']
    assert result['content'] == '最终比较与核查成果'
    assert len(result['agents']) == 3
    assert all(item['status'] == 'completed' for item in result['agents'])
    assert len(result['documents']) == 3
    assert '已阅读论文A' in main.prompts[3][-1].content
    assert '已阅读实验条件' in main.prompts[-1][-1].content
    assert len([e for e in repo.events(record['run_id']) if e['event'] == 'agent_spawned']) == 3


def test_main_cannot_complete_while_children_are_pending(setup):
    from research_agent.agent.runner import execute_task
    repo, record = setup
    entered, release = threading.Event(), threading.Event()
    class SlowChild(Model):
        def invoke_structured(self, messages, schema):
            entered.set()
            assert release.wait(5)
            return schema(**completed('未采用的成果'))
    def premature(messages):
        assert entered.wait(5)
        return completed('尚未等待的交付')
    def cancel(messages):
        assert '子任务尚未结束' in messages[-1].content
        return tool('agent_cancel')
    def deliver(messages):
        # Release the child only after agent_cancel is committed. Otherwise it
        # can legitimately finish between producing and executing cancellation.
        release.set()
        return completed('取消后交付已有成果')
    main = Model([spawn('慢任务'), premature, cancel, deliver])
    execute_task(record, repo, 'worker', lambda: None, llm=main,
                 child_llm_factory=lambda: SlowChild([]))
    row = repo.get_run(record['run_id'])
    assert row['result']['content'] == '取消后交付已有成果'
    assert row['result']['trace'][1]['observation']['error']
    assert row['result']['agents'][0]['status'] == 'cancelled'


def test_old_saved_capability_catalog_gets_delegation_tools_on_resume(setup):
    from research_agent.agent.runner import execute_task
    repo, record = setup
    repo.save_stage(record['run_id'], 'agent_capabilities', 'v1', Capabilities().catalog(), worker_id='worker')
    main = Model([spawn('补充核查'), tool('agent_wait', timeout_seconds=5), completed()])
    execute_task(record, repo, 'worker', lambda: None, llm=main,
                 child_llm_factory=lambda: Model([completed('已核查')]))
    assert 'agent_spawn' in main.prompts[0][0].content
    assert repo.get_run(record['run_id'])['status'] == 'completed'


def test_failed_parent_closes_children_before_finishing(setup):
    from research_agent.agent.runner import execute_task
    repo, record = setup
    main = Model([spawn('子任务'), {'action': 'blocked', 'reason': '缺少材料', 'content': '需要用户补充'}])
    execute_task(record, repo, 'worker', lambda: None, llm=main,
                 child_llm_factory=lambda: Model([completed()]))
    row = repo.get_run(record['run_id'])
    assert row['status'] == 'failed'
    assert all(item['status'] in {'completed', 'blocked', 'cancelled'} for item in row['result']['agents'])


def test_worker_runs_dynamic_agent_using_existing_lease_and_repository(setup, monkeypatch):
    from research_agent.agent import runner as personal_agent
    from research_agent.agent import delegation as delegation
    from research_agent.worker import Worker
    repo, record = setup
    # Return the fixture's already leased task to the queue for the real Worker.
    from sqlalchemy import update
    from research_agent.persistence.repository import runs
    with repo.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == record['run_id']).values(status='queued', worker_id=None))
    main = Model([spawn('论文A'), spawn('论文B'), tool('agent_wait', timeout_seconds=5), completed()])
    monkeypatch.setattr(personal_agent, 'LLMClient', lambda: main)
    monkeypatch.setattr(delegation, 'LLMClient', lambda: Model([completed('阅读结果')]))
    assert Worker(repo).run_once()
    row = repo.get_run(record['run_id'])
    assert row['status'] == 'completed'
    assert len(row['result']['agents']) == 2
    assert row['worker_id'] is None


def test_recovered_old_task_keeps_existing_sources_in_shared_allowance(setup):
    from research_agent.agent.runner import execute_task
    repo, record = setup
    record['payload']['budget'] = {'max_documents': 1}
    repo.save_stage(record['run_id'], 'agent_step_0', 'v3', {
        'decision': tool('browser_read', query='existing'),
        'observation': {'documents': [{'id': 'existing', 'url': 'https://paper.test/existing', 'content': '原文'}]}}, worker_id='worker')
    main = Model([tool('browser_read', query='new'), completed()])
    execute_task(record, repo, 'worker', lambda: None, llm=main)
    row = repo.get_run(record['run_id'])
    assert row['status'] == 'completed'
    assert [doc['id'] for doc in row['result']['documents']] == ['existing']
    assert '预算' in row['result']['trace'][1]['observation']['error']
