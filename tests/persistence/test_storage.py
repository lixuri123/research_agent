import json
import time
import pytest
from research_agent.persistence.repository import Repository

@pytest.fixture
def repo(tmp_path):
    return Repository('sqlite:///' + str(tmp_path / 'tasks.db'))

def test_durable_idempotency_and_owner(repo):
    r = repo.submit('topic', budget={'calls': 2}, idempotency_key='k')
    assert repo.submit('topic', budget={'calls': 2}, idempotency_key='k') == r
    with pytest.raises(ValueError): repo.submit('other', idempotency_key='k')
    assert Repository(str(repo.engine.url)).get(r['task_id']) == r
    assert repo.get(r['task_id'], owner_id='other') is None
    json.dumps(r)

def test_lease_reclaim_and_fencing(repo, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    r = repo.submit('topic')
    assert repo.claim('a', lease_seconds=.01)['run_id'] == r['run_id']
    assert repo.claim('b') is None
    clock[0] += .03
    with pytest.raises(ValueError): repo.heartbeat(r['run_id'], 'a', 120)
    assert repo.claim('b')['worker_id'] == 'b'
    with pytest.raises(ValueError): repo.finish(r['run_id'], 'a', 'completed', {})
    repo.heartbeat(r['run_id'], 'b', 120)
    repo.finish(r['run_id'], 'b', 'failed', {'error': 'failure'})
    assert repo.get_run(r['run_id'])['result'] == {'error': 'failure'}
    assert repo.claim('c') is None

def waiting(repo):
    r = repo.submit('topic'); repo.claim('worker')
    v = repo.save_report(r['run_id'], {'text': 'draft', 'claims': []})
    repo.finish(r['run_id'], 'worker', 'waiting_approval', {'draft_report': v['report']})
    return r, v

def test_version_bound_approval(repo):
    r,v = waiting(repo)
    assert repo.claim('other') is None
    assert repo.get_report(v['report_version_id'], owner_id='other') is None
    with pytest.raises(ValueError): repo.approve(v['report_version_id'], True, owner_id='other')
    newer = repo.save_report(r['run_id'], {'text': 'new'})
    with pytest.raises(ValueError): repo.approve(v['report_version_id'], True)
    approved = repo.approve(newer['report_version_id'], True)
    assert approved['status'] == 'queued'
    assert approved['approval']['approved'] is True
    assert repo.approve(newer['report_version_id'], True) == approved
    with pytest.raises(ValueError): repo.approve(newer['report_version_id'], False)
    assert repo.claim('resume')['run_id'] == r['run_id']
    assert len(repo.reports_for_run(r['run_id'])) == 2

def test_rejection_excludes_publication(repo, monkeypatch):
    # This assertion tests chronological ordering, not UUID tie-breaking.
    from itertools import count
    from types import SimpleNamespace
    from research_agent.persistence import repository as storage
    clock = count(100.0, 0.01)
    monkeypatch.setattr(storage, 'time', SimpleNamespace(time=lambda: next(clock)))
    r,v = waiting(repo)
    result = repo.approve(v['report_version_id'], False)
    assert result['status'] == 'rejected'
    assert 'final_report' not in result['result']
    assert repo.claim('resume') is None
    assert repo.events(r['run_id'])[-1]['event'] == 'rejected'

def test_stage_json_upsert(repo):
    r = repo.submit('topic')
    repo.save_stage(r['run_id'], 'budget', 'v1', {'calls': 1})
    repo.save_stage(r['run_id'], 'budget', 'v1', {'calls': 2})
    assert Repository(str(repo.engine.url)).get_stage(r['run_id'], 'budget', 'v1') == {'calls': 2}
    assert repo.get_stage(r['run_id'], 'budget', 'v2') is None

def test_expired_worker_cannot_finish_without_reclaim(repo):
    r = repo.submit('expiry')
    repo.claim('expired', .01)
    time.sleep(.03)
    with pytest.raises(ValueError): repo.finish(r['run_id'], 'expired', 'completed', {})

def test_waiting_requires_saved_report(repo):
    r = repo.submit('report')
    repo.claim('worker')
    with pytest.raises(ValueError): repo.finish(r['run_id'], 'worker', 'waiting_approval', {})

def test_rejection_removes_final_report_atomically(repo):
    r = repo.submit('reject')
    repo.claim('worker')
    v = repo.save_report(r['run_id'], {'body': 'draft'})
    repo.finish(r['run_id'], 'worker', 'waiting_approval', {'final_report': 'forbidden'})
    rejected = repo.approve(v['report_version_id'], False)
    assert 'final_report' not in rejected['result']
    assert Repository(str(repo.engine.url)).get_run(r['run_id'])['approval']['approved'] is False

@pytest.mark.skipif(not __import__('os').environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL not configured')
def test_postgres_durable_smoke():
    import os
    import uuid
    repo = Repository(os.environ['TEST_DATABASE_URL'])
    key = str(uuid.uuid4())
    record = repo.submit('postgres durability', idempotency_key=key)
    assert Repository(os.environ['TEST_DATABASE_URL']).get(record['task_id']) == record

def test_lease_expiry_between_read_and_update_is_fenced(repo, monkeypatch):
    r = repo.submit('race')
    repo.claim('worker', .02)
    original = repo._cas
    def delayed(conn, row, **values):
        time.sleep(.04)
        return original(conn, row, **values)
    monkeypatch.setattr(repo, '_cas', delayed)
    with pytest.raises(ValueError): repo.finish(r['run_id'], 'worker', 'completed', {})

def test_artifact_mutations_can_require_lease(repo):
    r = repo.submit('artifacts')
    repo.claim('old', .01)
    time.sleep(.03)
    repo.claim('new')
    with pytest.raises(ValueError): repo.save_report(r['run_id'], {'body': 'stale'}, worker_id='old')
    with pytest.raises(ValueError): repo.save_stage(r['run_id'], 'read', 'hash', {'body': 'stale'}, worker_id='old')
    version = repo.save_report(r['run_id'], {'body': 'current'}, worker_id='new')
    assert version['report']['body'] == 'current'
    repo.save_stage(r['run_id'], 'read', 'hash', {'body': 'current'}, worker_id='new')
    assert repo.get_stage(r['run_id'], 'read', 'hash') == {'body': 'current'}

def test_concurrent_heartbeat_and_stage_do_not_fail_valid_worker(repo):
    from concurrent.futures import ThreadPoolExecutor
    r = repo.submit('parallel writes')
    repo.claim('worker')
    def write(index):
        if index % 2:
            return repo.heartbeat(r['run_id'], 'worker', 120)
        return repo.save_stage(r['run_id'], 'budget', 'v1', {'calls': index}, worker_id='worker')
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(list(pool.map(write, range(20)))) == 20


def test_concurrent_claim_has_one_owner(repo):
    from concurrent.futures import ThreadPoolExecutor
    repo.submit('one task')
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda n: repo.claim(str(n)), range(4)))
    assert len([claim for claim in claims if claim is not None]) == 1

def test_new_report_after_approval_cannot_publish_without_new_decision(repo):
    r, version = waiting(repo)
    repo.approve(version['report_version_id'], True)
    repo.claim('resume')
    repo.save_report(r['run_id'], {'body': 'changed'}, worker_id='resume')
    with pytest.raises(ValueError): repo.finish(r['run_id'], 'resume', 'completed', {'final_report': 'changed'})
