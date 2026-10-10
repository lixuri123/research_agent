import time
import pytest
from fastapi.testclient import TestClient
from research_agent import api
from research_agent.persistence.repository import Repository
from research_agent.agent.runner import execute_task
from research_agent.models.gateway import ModelGateway


class Model:
    system = staticmethod(ModelGateway.system)
    human = staticmethod(ModelGateway.human)
    def __init__(self): self.calls = []
    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return '本次执行计划'
    def invoke_structured(self, messages, schema):
        self.calls.append(messages)
        return schema(action='completed',reason='done',content='本次回答')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from research_agent.agent import capabilities
    from research_agent.context.sources import ContextSources
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    monkeypatch.setattr(api, 'get_repository', lambda:repo)
    class Capabilities:
        def __init__(self,*args): self.loaded = {}
        def catalog(self): return {'tools':{},'skills':[],'errors':[]}
        def instructions(self): return ''
        def restore(self,*args): pass
    monkeypatch.setattr(capabilities, 'TaskCapabilities', Capabilities)
    monkeypatch.setattr(ContextSources,'memory',lambda self: (_ for _ in ()).throw(RuntimeError('offline-memory')))
    with TestClient(api.app) as client:
        yield repo,client
    repo.engine.dispose()


def prior(repo, title, content, **kwargs):
    row = repo.submit(title, task_type='agent', session_id='s', **kwargs)
    claimed = repo.claim('w')
    assert row['run_id'] == claimed['run_id']
    repo.finish(row['run_id'], 'w', 'completed', {'content':content})
    return row


def run(repo, client, question, context=None):
    body = {'task':question, 'session_id':'s'}
    if context is not None: body['context'] = context
    response = client.post('/tasks',json=body)
    assert response.status_code == 200, response.text
    row = repo.claim('w')
    model = Model()
    execute_task(row,repo,'w',lambda:None,llm=model)
    return row,repo.get_run(row['run_id']),model


def test_new_api_tasks_route_independent_then_relevant_followup(setup):
    repo, client = setup
    prior(repo,'论文实验','UNRELATED PAPER TASK')
    _, row, model = run(repo,client,'Redis 是什么？')
    assert row['status'] == 'completed'
    assert row['payload']['context_version'] == 1
    assert row['result']['context']['route']['route'] == 'independent'
    assert 'UNRELATED PAPER TASK' not in str(model.calls)
    _, follow, follow_model = run(repo,client,'这个回答再详细一点')
    assert follow['status'] == 'completed'
    assert follow['result']['context']['route']['route'] == 'followup'
    assert '本次回答' in str(follow_model.calls)
    assert 'UNRELATED PAPER TASK' not in str(follow_model.calls)


def test_ambiguity_finishes_without_model_or_tools(setup):
    repo,client = setup
    row,result,model = run(repo,client,'继续那个任务')
    assert result['status'] == 'completed'
    assert result['result']['context']['route']['needs_clarification']
    assert not model.calls
    assert repo.get_stage(row['run_id'],'agent_plan','v3') is None


def test_options_idempotency_conflict_and_unowned_reference(setup):
    repo,client = setup
    body = {'task':'问题','session_id':'s'}
    headers = {'Idempotency-Key':'context-key'}
    a = client.post('/tasks',json=body,headers=headers)
    b = client.post('/tasks',json={**body,'context':{'mode':'auto'}},headers=headers)
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert client.post('/tasks',json={**body,'context':{'mode':'independent'}},headers=headers).status_code == 409
    other = repo.submit('SECRET',owner_id='other',task_type='agent')
    assert client.post('/tasks',json={**body,'context':{'task_ids':[other['task_id']]}}).status_code == 404
    assert client.post('/tasks',json={**body,'context':{'mode':'independent','task_ids':['x']}}).status_code == 422


def test_explicit_target_route_and_resume_keep_fixed_sources(setup):
    repo,client = setup
    target = prior(repo,'论文实验','TARGET RESULT')
    row,result,model = run(repo,client,'解释进展',{'task_ids':[target['task_id']]})
    assert result['status'] == 'completed'
    assert result['result']['context']['route']['route'] == 'task'
    assert 'TARGET RESULT' in str(model.calls)
    assert repo.get_stage(row['run_id'],'agent_context_route','v1')['task_ids'] == [target['task_id']]
    assert repo.get_stage(row['run_id'],'agent_context_sources','v1')['blocks']
    # Recover a prepared request after another process changes the target result.
    from sqlalchemy import update, delete
    from research_agent.persistence.repository import runs
    from research_agent.context.session import ContextSession
    from research_agent.context.schemas import RuntimeContext
    from research_agent.core.config import Settings
    next_row = repo.submit('解释进展',task_type='agent',session_id='s',context={'task_ids':[target['task_id']]})
    leased = repo.claim('w')
    context = ContextSession(leased,repo,'w',lambda:None,Settings(_env_file=None))
    with repo.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id==target['run_id']).values(result={'content':'LATER TARGET RESULT'}))
    recovered = ContextSession(leased,repo,'w',lambda:None,Settings(_env_file=None))
    messages = recovered.assemble(RuntimeContext(system='SYSTEM'))
    assert 'TARGET RESULT' in str(messages) and 'LATER TARGET RESULT' not in str(messages)
    with repo.engine.begin() as conn:
        conn.execute(delete(runs).where(runs.c.run_id==target['run_id']))
    with pytest.raises(ValueError,match='停止续接'):
        recovered.assemble(RuntimeContext(system='SYSTEM'))
