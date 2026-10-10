import pytest
from fastapi.testclient import TestClient
from research_agent import api
from research_agent.persistence.repository import Repository


@pytest.fixture
def setup_workspace(tmp_path, monkeypatch):
    from research_agent.workspace.service import WorkspaceService
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'notes.md').write_text('hello', encoding='utf-8')
    (root / '.env').write_text('secret', encoding='utf-8')
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    service = WorkspaceService(repo.engine, [tmp_path])
    monkeypatch.setattr(api, 'get_repository', lambda: repo)
    monkeypatch.setattr(api, 'get_workspaces', lambda: service, raising=False)
    return root, repo, service


def test_workspace_persists_and_filters_sensitive_files(setup_workspace):
    from research_agent.workspace.service import WorkspaceService
    root, repo, service = setup_workspace
    workspace = service.bind('project', str(root), agent_read=True)
    restored = WorkspaceService(repo.engine, [root.parent])
    assert restored.get(workspace['id'])['root'] == str(root.resolve())
    assert [entry['name'] for entry in service.entries(workspace['id'])['entries']] == ['notes.md']


def test_paths_and_allowed_roots_are_enforced(setup_workspace):
    root, _, service = setup_workspace
    workspace = service.bind('project', str(root), agent_read=False)
    for path in ['../tasks.db', '.env', str(root / 'notes.md'), 'notes.md:stream']:
        with pytest.raises(ValueError):
            service.read(workspace['id'], path)
    with pytest.raises(ValueError):
        service.bind('outside', str(root.parent.parent), agent_read=True)


def test_save_refuses_external_changes(setup_workspace):
    root, _, service = setup_workspace
    workspace = service.bind('project', str(root), agent_read=False)
    original = service.read(workspace['id'], 'notes.md')
    saved = service.save(workspace['id'], 'notes.md', 'updated', original['version'])
    assert saved['content'] == 'updated'
    (root / 'notes.md').write_text('external', encoding='utf-8')
    with pytest.raises(ValueError, match='修改'):
        service.save(workspace['id'], 'notes.md', 'overwrite', saved['version'])
    assert (root / 'notes.md').read_text(encoding='utf-8') == 'external'


def test_api_keeps_workspace_in_task_and_rejects_session_rebinding(setup_workspace):
    _, repo, service = setup_workspace
    root = setup_workspace[0]
    workspace = service.bind('project', str(root), agent_read=True)
    with TestClient(api.app) as client:
        assert client.get('/workspaces').status_code == 200
        response = client.post('/tasks', json={'task': 'read notes', 'workspace_id': workspace['id'], 'session_id': 'one'})
        assert response.status_code == 200
        record = repo.get(response.json()['task_id'])
        assert record['payload']['workspace_id'] == workspace['id']
        assert record['payload']['project_id'] == workspace['id']
        assert client.get('/tasks').json()['tasks'][0]['workspace_id'] == workspace['id']
        assert client.post('/tasks', json={'task': 'other', 'session_id': 'one'}).status_code == 409
        client.post('/tasks', json={'task': 'personal', 'session_id': 'two'})
        scoped = client.get('/tasks', params={'workspace_id': workspace['id']}).json()['tasks']
        assert len(scoped) == 1
        assert scoped[0]['topic'] == 'read notes'
        assert client.put(f"/workspaces/{workspace['id']}/file", json={'path': 'notes.md', 'content': 'x', 'version': 'old'}).status_code == 409


def test_agent_tools_respect_workspace_permission(setup_workspace, monkeypatch):
    from research_agent.agent.capabilities import TaskCapabilities
    from research_agent.workspace import service as workspaces
    root, _, service = setup_workspace
    monkeypatch.setattr(workspaces, 'get_workspaces', lambda: service)
    denied = service.bind('project', str(root), agent_read=False)
    cap = TaskCapabilities({'workspace_id': denied['id']})
    monkeypatch.setattr(cap.mcp, 'discover', lambda: {'tools': {}, 'errors': []})
    assert 'workspace_read' not in cap.catalog()['tools']
    assert cap.call('workspace_read', {'path': 'notes.md'})['error']
    other = root.parent / 'readable'
    other.mkdir()
    (other / 'notes.md').write_text('real contents', encoding='utf-8')
    allowed = service.bind('readable', str(other), agent_read=True)
    cap = TaskCapabilities({'workspace_id': allowed['id']})
    monkeypatch.setattr(cap.mcp, 'discover', lambda: {'tools': {}, 'errors': []})
    assert 'workspace_write' not in cap.catalog()['tools']
    assert cap.call('workspace_read', {'path': 'notes.md'})['content'] == 'real contents'
    assert cap.call('workspace_read', {'path': '../project/notes.md'})['error']


def test_api_initializes_schema_before_serving_requests(setup_workspace, monkeypatch):
    _, repo, service = setup_workspace
    initialized = []
    monkeypatch.setattr(api, 'get_repository', lambda: initialized.append('repository') or repo)
    monkeypatch.setattr(api, 'get_workspaces', lambda: initialized.append('workspaces') or service)
    monkeypatch.setattr(api, 'get_file_changes', lambda: initialized.append('file_changes'))
    monkeypatch.setattr(api, 'get_executions', lambda: initialized.append('executions'))
    with TestClient(api.app):
        assert initialized == ['repository', 'workspaces', 'file_changes', 'executions']
