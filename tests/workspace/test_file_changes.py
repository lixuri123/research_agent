import pytest
from fastapi.testclient import TestClient
from research_agent import api
from research_agent.persistence.repository import Repository
from research_agent.workspace.service import WorkspaceService, WorkspaceConflict


@pytest.fixture
def setup_change(tmp_path, monkeypatch):
    from research_agent.workspace.file_changes import FileChangeService
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'notes.md').write_text('original\n', encoding='utf-8')
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    workspaces = WorkspaceService(repo.engine, [tmp_path])
    workspace = workspaces.bind('project', str(root), agent_read=True)
    repo.submit('edit notes', task_type='agent', workspace_id=workspace['id'])
    run = repo.claim('worker')
    service = FileChangeService(workspaces)
    monkeypatch.setattr(api, 'get_repository', lambda: repo)
    monkeypatch.setattr(api, 'get_workspaces', lambda: workspaces)
    monkeypatch.setattr(api, 'get_file_changes', lambda: service, raising=False)
    return root, repo, workspace, run, service


def propose(setup_change):
    _, _, workspace, run, service = setup_change
    base = service.workspaces.read(workspace['id'], 'notes.md')
    return service.propose(workspace['id'], run['run_id'], 'worker', 'step-1',
        'notes.md', 'updated\n', base['version'], 'clarify notes')


def test_proposal_is_durable_and_does_not_write_original(setup_change):
    from research_agent.workspace.file_changes import FileChangeService
    root, _, workspace, _, service = setup_change
    change = propose(setup_change)
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'original\n'
    assert propose(setup_change)['id'] == change['id']
    restored = FileChangeService(service.workspaces)
    assert restored.list(workspace['id'])[0]['status'] == 'pending'
    detail = restored.get(workspace['id'], change['id'])
    assert '-original' in detail['diff'] and '+updated' in detail['diff']


def test_apply_requires_exact_review_hash_and_is_idempotent(setup_change):
    root, _, workspace, _, service = setup_change
    change = propose(setup_change)
    with pytest.raises(WorkspaceConflict):
        service.decide(workspace['id'], change['id'], True, 'wrong')
    detail = service.get(workspace['id'], change['id'])
    assert service.decide(workspace['id'], change['id'], True, detail['review_hash'])['status'] == 'applied'
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'updated\n'
    (root / 'notes.md').write_text('later edit', encoding='utf-8')
    assert service.decide(workspace['id'], change['id'], True, detail['review_hash'])['status'] == 'applied'
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'later edit'


def test_conflict_and_rejection_never_overwrite(setup_change):
    root, _, workspace, _, service = setup_change
    change = propose(setup_change)
    detail = service.get(workspace['id'], change['id'])
    (root / 'notes.md').write_text('external edit', encoding='utf-8')
    with pytest.raises(WorkspaceConflict):
        service.decide(workspace['id'], change['id'], True, detail['review_hash'])
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'external edit'
    assert service.get(workspace['id'], change['id'])['status'] == 'conflict'


def test_reject_api_and_scope_guard(setup_change):
    root, _, workspace, _, service = setup_change
    change = propose(setup_change)
    with TestClient(api.app) as client:
        path = f"/workspaces/{workspace['id']}/changes/{change['id']}"
        detail = client.get(path).json()
        response = client.post(path + '/decision', json={'approved': False, 'review_hash': detail['review_hash']})
        assert response.status_code == 200
        assert response.json()['status'] == 'rejected'
        assert client.post(path + '/decision', json={'approved': True, 'review_hash': detail['review_hash']}).status_code == 409
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'original\n'
    with pytest.raises(ValueError):
        service.get('other-space', change['id'])


def test_proposal_requires_live_task_and_safe_path(setup_change):
    _, repo, workspace, run, service = setup_change
    for path in ['../notes.md', '.env', 'missing.md']:
        with pytest.raises(ValueError):
            service.propose(workspace['id'], run['run_id'], 'worker', path, path, 'x', 'old', 'change')
    repo.finish(run['run_id'], 'worker', 'failed', {'error': 'stopped'})
    with pytest.raises(ValueError):
        propose(setup_change)


def test_recover_approved_write_and_preserve_bom(setup_change):
    from sqlalchemy import update
    from research_agent.workspace.file_changes import changes
    root, _, workspace, _, service = setup_change
    (root / 'notes.md').write_bytes(b'\xef\xbb\xbforiginal\n')
    change = propose(setup_change)
    detail = service.get(workspace['id'], change['id'])
    with service.engine.begin() as conn:
        conn.execute(update(changes).where(changes.c.id == change['id']).values(status='applying'))
    (root / 'notes.md').write_bytes(b'\xef\xbb\xbfupdated\n')
    assert service.decide(workspace['id'], change['id'], True, detail['review_hash'])['status'] == 'applied'
    assert (root / 'notes.md').read_bytes() == b'\xef\xbb\xbfupdated\n'


def test_tool_proposes_without_writing_and_list_api(setup_change, monkeypatch):
    from types import SimpleNamespace
    from research_agent.agent.capabilities import TaskCapabilities
    root, _, workspace, run, service = setup_change
    cap = object.__new__(TaskCapabilities)
    cap.workspace_id, cap.run_id, cap.worker_id = workspace['id'], run['run_id'], 'worker'
    cap.allowed = {'workspace_propose_write'}
    cap.mcp = SimpleNamespace(redact=lambda value: value)
    monkeypatch.setattr(cap, 'workspaces', lambda: service.workspaces)
    args = dict(path='notes.md', content='updated\n', reason='clarify',
        base_version=service.workspaces.read(workspace['id'], 'notes.md')['version'])
    result = cap.call('workspace_propose_write', args, operation_id='step-1')
    assert result['error'] is None and result['proposal']['status'] == 'pending'
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'original\n'
    with TestClient(api.app) as client:
        listed = client.get(f"/workspaces/{workspace['id']}/changes", params={'run_id': run['run_id']})
        assert listed.status_code == 200 and len(listed.json()['changes']) == 1
        assert 'proposed_content' not in listed.json()['changes'][0]
        assert client.get(f"/workspaces/{workspace['id']}/changes", params={'run_id': 'other'}).json()['changes'] == []
