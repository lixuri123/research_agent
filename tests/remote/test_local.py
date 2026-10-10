import asyncio
import pytest
from research_agent.persistence.repository import Repository
from research_agent.remote.local_store import LocalStore
from research_agent.remote.adapter import BusinessAdapter

@pytest.fixture
def store(tmp_path):
    return LocalStore(Repository('sqlite:///'+str(tmp_path/'local.db')))

def authorize(store, device='phone', spaces=None):
    store.configure(True, 'https://relay.example', False)
    store.pending({'device_id':device,'name':'Phone','code':'123456'})
    store.decide(device, True, spaces or [''])

@pytest.mark.asyncio
async def test_revocation_and_strict_parameters(store):
    authorize(store)
    adapter=BusinessAdapter(store, None)
    for payload in ({'task':'x','authorized_mcp_tools':['bad']},{'task':'x','skill_ids':['bad']}):
        with pytest.raises(ValueError):
            await adapter.dispatch('phone',{'method':'task.create','payload':payload,'idempotency_key':'key'})
    with pytest.raises(ValueError):
        await adapter.dispatch('phone',{'method':'mcp.tools.call','payload':{}})
    store.revoke('phone')
    with pytest.raises(PermissionError):
        await adapter.dispatch('phone',{'method':'host.status','payload':{}})

def test_durable_authorization_outbox_and_cursor(store):
    authorize(store)
    assert store.device('phone')['status']=='authorized'
    assert store.controls()[0]['method']=='pair.confirm'
    store.revoke('phone')
    assert store.device('phone')['permission_version']==2
    assert store.controls()[-1]['method']=='device.revoke'
    first=store.event_batch('task',[{'event_id':'b','created_at':2}])
    late=store.event_batch('task',[{'event_id':'a','created_at':1},{'event_id':'b','created_at':2}],1)
    assert [x['cursor'] for x in first+late]==[1,2]
    assert store.event_batch('task',[{'event_id':'a','created_at':1}],1)[0]['event_id']=='a'


@pytest.fixture
def business(store, monkeypatch,tmp_path):
    import research_agent.api as api
    from research_agent.workspace.service import WorkspaceService
    from research_agent.workspace.file_changes import FileChangeService
    work=WorkspaceService(store.repository.engine,[tmp_path])
    changes=FileChangeService(work)
    monkeypatch.setattr(api,'get_repository',lambda:store.repository)
    monkeypatch.setattr(api,'get_workspaces',lambda:work)
    monkeypatch.setattr(api,'get_file_changes',lambda:changes)
    return BusinessAdapter(store,api.app),work,changes

@pytest.mark.asyncio
async def test_sql_submission_retry_namespace_and_scope(store,business,tmp_path):
    adapter,work,changes=business
    authorize(store)
    authorize(store,'other')
    request={'method':'task.create','payload':{'task':'safe task'},'idempotency_key':'same'}
    one=await adapter.dispatch('phone',request)
    assert await adapter.dispatch('phone',request)==one
    two=await adapter.dispatch('other',request)
    assert two['task_id']!=one['task_id']
    assert store.repository.get(one['task_id'])['status']=='queued'
    with pytest.raises(RuntimeError):
        await adapter.dispatch('phone',dict(request,payload={'task':'changed'}))
    root=tmp_path/'space'; root.mkdir()
    workspace=work.bind('Private',str(root),True)
    private=store.repository.submit('private',workspace_id=workspace['id'],session_id='private-session')
    work.associate('private-session',workspace['id'])
    for method,payload in [('task.read',{'task_id':private['task_id']}),('session.read',{'session_id':'private-session'}),('task.subscribe',{'task_id':private['task_id'],'subscription_id':'sub'}),('file_change.list',{'workspace_id':workspace['id']})]:
        with pytest.raises(PermissionError): await adapter.dispatch('phone',{'method':method,'payload':payload})
    claimed=store.repository.claim('fake')
    while claimed['run_id']!=private['run_id']: claimed=store.repository.claim('fake')
    # Claim may be personal; save an immutable report on the private run without executing any model.
    report=store.repository.save_report(private['run_id'],{'text':'private'})
    with pytest.raises(PermissionError): await adapter.dispatch('phone',{'method':'report.read','payload':{'report_version_id':report['report_version_id']}})

@pytest.mark.asyncio
async def test_connector_commit_before_confirm_and_revoke_gate(store,tmp_path):
    from research_agent.remote.connector import Connector
    from research_agent.remote.protocol import BoundedQueue
    from research_agent.remote.credentials import CredentialFile
    connector=Connector(store,None,CredentialFile(tmp_path/'credential')); connector.queue=BoundedQueue()
    store.configure(True,'https://relay.example')
    await connector.handle({'version':1,'type':'event','method':'pair.pending','payload':{'device_id':'d','name':'Phone','code':'123'}})
    assert store.device('d')['status']=='awaiting_confirmation'
    store.decide('d',True,[''])
    control=store.controls(True)[0]
    assert store.authorize('d')['status']=='authorized'
    connector.inflight[control['id']]=1
    await connector.handle({'version':1,'type':'response','request_id':control['id'],'result':{'status':'authorized'}})
    assert not store.controls(True)
    store.revoke('d')
    await connector.handle({'version':1,'type':'request','request_id':'r','_device_id':'d','method':'host.status','payload':{}})
    import json
    assert json.loads(await connector.queue.get())['error']['code']=='forbidden'
    assert LocalStore(store.repository).controls(True)[0]['method']=='device.revoke'

def test_local_management_browser_guard(store,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from research_agent.remote import local_routes
    monkeypatch.setattr(local_routes,'get_store',lambda:store)
    app=FastAPI(); app.include_router(local_routes.router)
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',5000)) as client:
        assert client.get('/remote/local/status').status_code==403
        assert client.get('/remote/local/status',headers={'X-Remote-CSRF':'1','Sec-Fetch-Site':'same-origin'}).status_code==200
        assert client.get('/remote/local/status',headers={'X-Remote-CSRF':'1','Origin':'https://attacker.example'}).status_code==403
        assert client.put('/remote/local/config',headers={'X-Remote-CSRF':'1','Origin':'http://localhost:5173'},json={'enabled':True,'relay_url':'http://127.0.0.1:9999','development':True}).status_code==200
        assert client.put('/remote/local/config',headers={'X-Remote-CSRF':'1'},json={'enabled':False}).status_code==403



@pytest.mark.asyncio
async def test_report_versions_and_file_review_hash(store,business,tmp_path):
    adapter,work,changes=business
    authorize(store)
    request=store.repository.submit('research',budget={'require_approval':True})
    store.repository.claim('fake')
    old=store.repository.save_report(request['run_id'],{'text':'old'},'fake')
    new=store.repository.save_report(request['run_id'],{'text':'new'},'fake')
    store.repository.finish(request['run_id'],'fake','waiting_approval',{})
    def decision(version): return {'method':'report.decide','payload':{'report_version_id':version,'approved':True},'idempotency_key':'approval'}
    with pytest.raises(RuntimeError): await adapter.dispatch('phone',decision(old['report_version_id']))
    assert (await adapter.dispatch('phone',decision(new['report_version_id'])))['approved']
    assert (await adapter.dispatch('phone',decision(new['report_version_id'])))['approved']
    root=tmp_path/'files'; root.mkdir(); (root/'a.txt').write_text('old')
    workspace=work.bind('Files',str(root),True)
    store.grants('phone',['',workspace['id']])
    agent=store.repository.submit('edit',task_type='agent',workspace_id=workspace['id'])
    claimed=store.repository.claim('editor')
    while claimed['run_id']!=agent['run_id']: claimed=store.repository.claim('editor')
    proposal=changes.propose(workspace['id'],agent['run_id'],'editor','edit1','a.txt','new',work.read(workspace['id'],'a.txt')['version'],'change')
    authorize(store,'personal')
    with pytest.raises(PermissionError):
        await adapter.dispatch('personal',{'method':'file_change.read','payload':{'workspace_id':workspace['id'],'change_id':proposal['id']}})
    payload={'workspace_id':workspace['id'],'change_id':proposal['id'],'approved':True,'review_hash':'0'*64}
    with pytest.raises(RuntimeError): await adapter.dispatch('phone',{'method':'file_change.decide','payload':payload,'idempotency_key':'file'})
    assert (root/'a.txt').read_text()=='old'
    payload['review_hash']=proposal['review_hash']
    await adapter.dispatch('phone',{'method':'file_change.decide','payload':payload,'idempotency_key':'file'})
    assert (root/'a.txt').read_text()=='new'



@pytest.mark.asyncio
async def test_connector_failed_confirmation_and_resume_cursor(store,business,tmp_path):
    from research_agent.remote.connector import Connector
    from research_agent.remote.credentials import CredentialFile
    from research_agent.remote.protocol import BoundedQueue
    adapter,work,changes=business
    authorize(store)
    connector=Connector(store,adapter.app,CredentialFile(tmp_path/'credential')); connector.queue=BoundedQueue()
    confirm=store.controls(True)[0]; connector.inflight[confirm['id']]=1
    await connector.handle({'version':1,'type':'response','request_id':confirm['id'],'error':{'code':'offline','message':'offline'}})
    assert store.controls(True)[0]['id']==confirm['id']
    connector.inflight[confirm['id']]=1
    await connector.handle({'version':1,'type':'response','request_id':confirm['id'],'result':{'status':'authorized'}})
    task=store.repository.submit('safe')
    first=await adapter.dispatch('phone',{'method':'task.subscribe','payload':{'task_id':task['task_id'],'subscription_id':'sub'}})
    cursor=first['events'][-1]['cursor']
    from research_agent.persistence.repository import event_table
    from sqlalchemy import insert
    with store.repository._transaction() as c:
        c.execute(insert(event_table).values(event_id='late',run_id=task['run_id'],event='late',created_at=0,data={}))
    restarted=BusinessAdapter(LocalStore(store.repository),adapter.app)
    resumed=await restarted.dispatch('phone',{'method':'task.subscribe','payload':{'task_id':task['task_id'],'subscription_id':'sub','cursor':cursor}})
    assert [e['event_id'] for e in resumed['events']]==['late']
    with pytest.raises(ValueError,match='resync_required'):
        await restarted.dispatch('phone',{'method':'task.subscribe','payload':{'task_id':task['task_id'],'subscription_id':'sub','cursor':999}})
    authorize(store,'expire')
    control=store.controls(True)[0]; connector.inflight[control['id']]=1
    await connector.handle({'version':1,'type':'response','request_id':control['id'],'error':{'code':'expired','message':'expired'}})
    with pytest.raises(PermissionError): store.authorize('expire')

def test_credentials_roundtrip_and_outside_workspace(tmp_path):
    from research_agent.remote.credentials import CredentialFile
    secret=CredentialFile(tmp_path/'protected')
    value={'host_id':'fake','secret':'fake-secret','relay_url':'https://relay.example'}
    secret.write(value)
    assert secret.read()==value
    with pytest.raises(ValueError): CredentialFile(tmp_path/'bad',workspace_roots=[tmp_path])

@pytest.mark.asyncio
async def test_workspace_only_device_can_start_new_session(store,business,tmp_path):
    adapter,work,_=business
    root=tmp_path/'only'; root.mkdir(); workspace=work.bind('Only',str(root),True)
    authorize(store,spaces=[workspace['id']])
    value=await adapter.dispatch('phone',{'method':'task.create','payload':{'task':'safe','workspace_id':workspace['id'],'session_id':'brand-new'},'idempotency_key':'new'})
    assert store.repository.get(value['task_id'])['payload']['workspace_id']==workspace['id']
    with pytest.raises(PermissionError):
        await adapter.dispatch('phone',{'method':'task.create','payload':{'task':'personal'},'idempotency_key':'no'})
    store.grants('phone',[])
    with pytest.raises(PermissionError):
        await adapter.dispatch('phone',{'method':'task.subscribe','payload':{'task_id':value['task_id'],'subscription_id':'s'}})

def test_expired_invites_purged_and_pending_expired(store):
    import time
    control=store.enqueue('pair.create',{})
    store.complete(control['id'],{'invite_id':'fake','expires_at':time.time()-1,'token':'fake-token','url':'https://relay.example/#invite=fake'})
    store.pending({'device_id':'expired','name':'Expired','code':'123','expires':time.time()-1})
    store.cleanup()
    assert store.controls()[0]['result'] is None
    assert store.device('expired')['status']=='expired'
