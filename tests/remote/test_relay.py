import pytest
from fastapi.testclient import TestClient
from research_agent.remote.relay import create_app

ORIGIN = 'http://localhost'
HEADERS = {'origin': ORIGIN, 'X-Remote-CSRF': '1'}
def setup(tmp_path):
    app = create_app(f'sqlite:///{tmp_path}/relay.db', ORIGIN, development=True)
    return app, TestClient(app, base_url=ORIGIN)
def control(ws, method, payload=None, rid='control'):
    ws.send_json(dict(version=1,type='request',request_id=rid,method=method,payload=payload or {}))
    return ws.receive_json()
def test_session_and_tls(tmp_path):
    _, c = setup(tmp_path)
    assert c.get('/remote/session').status_code == 401
    with pytest.raises(ValueError): create_app('sqlite://', 'http://example.com')
def test_pairing_routing_revocation_and_durability(tmp_path):
    app,c = setup(tmp_path)
    c.__enter__()
    credentials = c.post('/remote/hosts/register',json={}).json()
    headers={'Authorization':'Bearer '+credentials['secret']}
    with c.websocket_connect('/remote/ws/host',headers=headers) as host:
        invite=control(host,'pair.create')['result']
        assert control(host,'pair.create')['result']==invite
        assert c.post('/remote/pair/redeem',json={'token':invite['token'],'name':'phone'},headers={'origin':'https://evil','X-Remote-CSRF':'1'}).status_code==403
        p=c.post('/remote/pair/redeem',json={'token':invite['token'],'name':'phone'},headers=HEADERS).json()
        assert host.receive_json()['method']=='pair.pending'
        assert c.post('/remote/pair/redeem',json={'token':invite['token'],'name':'other'},headers=HEADERS).status_code==409
        body={'device_id':p['device_id'],'pending_secret':p['pending_secret']}
        assert c.post('/remote/pair/finish',json=body,headers=HEADERS).status_code==409
        assert not c.cookies
        assert control(host,'pair.confirm',{'device_id':p['device_id'],'approved':True},'confirm')['result']['status']=='authorized'
        finish=c.post('/remote/pair/finish',json=body,headers=HEADERS)
        assert finish.status_code==200
        assert 'HttpOnly' in finish.headers['set-cookie']
        with c.websocket_connect('/remote/ws/device',headers={'origin':ORIGIN,'cookie':'remote_device='+c.cookies.get('remote_device')}) as device:
            device.send_json(dict(version=1,type='request',request_id='r1',host_id=credentials['host_id'],method='task.read',payload={'task_id':'t'},device_id='fake',_device_id='fake'))
            forwarded=host.receive_json()
            assert forwarded['_device_id']==p['device_id']
            assert 'device_id' not in forwarded
            host.send_json(dict(version=1,type='response',request_id='r1',_device_id=p['device_id'],result={'ok':True}))
            assert device.receive_json()['result']=={'ok':True}
            control(host,'device.revoke',{'device_id':p['device_id']},'revoke')
        assert c.get('/remote/session').status_code==401
        assert control(host,'pair.confirm',{'device_id':p['device_id'],'approved':True},'again')['error']['code']=='conflict'
    c.__exit__(None,None,None)
    _,restarted=setup(tmp_path)
    with restarted.websocket_connect('/remote/ws/host',headers=headers): pass

def test_expired_invite_and_bounded_queue(tmp_path):
    from research_agent.remote.protocol import BoundedQueue
    from research_agent.remote.relay_store import RelayStore
    q=BoundedQueue(max_messages=1,max_bytes=20)
    assert q.put({'a':1})
    assert not q.put({'a':2})
    store=RelayStore(f'sqlite:///{tmp_path}/expiry.db')
    h=store.register()
    invite=store.create_invite(h['host_id'],'i',ttl=-1)
    with pytest.raises(ValueError): store.redeem(invite['token'],'phone')

def test_invite_retry_survives_restart_without_secret_in_database(tmp_path):
    from research_agent.remote.relay_store import RelayStore
    url=f'sqlite:///{tmp_path}/durable.db'
    store=RelayStore(url); h=store.register()
    invite=store.create_invite(h['host_id'],'retry',host_secret=h['secret'])
    store.engine.dispose()
    restarted=RelayStore(url)
    assert restarted.create_invite(h['host_id'],'retry',host_secret=h['secret'])==invite
    raw=(tmp_path/'durable.db').read_bytes()
    assert h['secret'].encode() not in raw
    assert invite['token'].encode() not in raw

def test_offline_invite_not_consumed_and_pending_replayed(tmp_path):
    _,c=setup(tmp_path)
    with c:
        h=c.post('/remote/hosts/register',json={}).json(); auth={'authorization':'Bearer '+h['secret']}
        with c.websocket_connect('/remote/ws/host',headers=auth) as host:
            invite=control(host,'pair.create')['result']
        body={'token':invite['token'],'name':'phone'}
        assert c.post('/remote/pair/redeem',json=body,headers=HEADERS).json()['error']['code']=='offline'
        with c.websocket_connect('/remote/ws/host',headers=auth) as host:
            p=c.post('/remote/pair/redeem',json=body,headers=HEADERS).json()
            host.receive_json()
        with c.websocket_connect('/remote/ws/host',headers=auth) as host:
            assert host.receive_json()['payload']['device_id']==p['device_id']
            host.send_text('bad json')
            assert host.receive_json()['error']['code']=='invalid_request'

def test_finish_retry_cookie_stable_and_devices_isolated(tmp_path):
    _,c=setup(tmp_path)
    with c:
        h=c.post('/remote/hosts/register',json={}).json()
        with c.websocket_connect('/remote/ws/host',headers={'authorization':'Bearer '+h['secret']}) as host:
            cookies=[]; identities=[]
            for index in range(2):
                invite=control(host,'pair.create',rid='invite'+str(index))['result']
                p=c.post('/remote/pair/redeem',json={'token':invite['token'],'name':str(index)},headers=HEADERS).json(); host.receive_json()
                control(host,'pair.confirm',{'device_id':p['device_id'],'approved':True},'confirm'+str(index))
                body={'device_id':p['device_id'],'pending_secret':p['pending_secret']}
                assert c.post('/remote/pair/finish',json=body,headers=HEADERS).status_code==200
                token=c.cookies.get('remote_device')
                assert c.post('/remote/pair/finish',json=body,headers=HEADERS).status_code==200
                assert c.cookies.get('remote_device')==token
                cookies.append(token); identities.append(p['device_id'])
            with c.websocket_connect('/remote/ws/device',headers={'origin':ORIGIN,'cookie':'remote_device='+cookies[0]}) as a, c.websocket_connect('/remote/ws/device',headers={'origin':ORIGIN,'cookie':'remote_device='+cookies[1]}) as b:
                request=dict(version=1,type='request',request_id='same',host_id=h['host_id'],method='task.read',payload={})
                a.send_json(request); b.send_json(request)
                first=host.receive_json(); second=host.receive_json()
                for msg in (first,second): host.send_json(dict(version=1,type='response',request_id='same',_device_id=msg['_device_id'],result=msg['_device_id']))
                assert a.receive_json()['result']==identities[0]
                assert b.receive_json()['result']==identities[1]

def test_cleanup_expired_metadata_preserves_authorized_device(tmp_path):
    from research_agent.remote.relay_store import RelayStore
    from sqlalchemy import text
    store=RelayStore(f'sqlite:///{tmp_path}/cleanup.db'); h=store.register()
    invite=store.create_invite(h['host_id'],'expired',ttl=-1,host_secret=h['secret'])
    with store.engine.begin() as db:
        db.execute(text("INSERT INTO remote_devices VALUES ('pending',:h,'p','proof',NULL,NULL,'awaiting_confirmation',:expiry,'123456')"),{'h':h['host_id'],'expiry':__import__('time').time()-1})
        db.execute(text("INSERT INTO remote_devices VALUES ('authorized',:h,'a','proof','token','csrf','authorized',0,'123456')"),{'h':h['host_id']})
    store.cleanup()
    with store.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM remote_invites')).scalar()==0
        assert db.execute(text('SELECT count(*) FROM remote_controls')).scalar()==0
        assert db.execute(text("SELECT status FROM remote_devices WHERE id='pending'")).scalar()=='expired'
        assert db.execute(text("SELECT status FROM remote_devices WHERE id='authorized'")).scalar()=='authorized'

def test_large_pending_replay_does_not_leave_host_offline(tmp_path):
    app,c=setup(tmp_path)
    with c:
        h=c.post('/remote/hosts/register',json={}).json()
        for index in range(105):
            invite=app.state.store.create_invite(h['host_id'],str(index),host_secret=h['secret'])
            app.state.store.redeem(invite['token'],str(index))
        with c.websocket_connect('/remote/ws/host',headers={'authorization':'Bearer '+h['secret']}) as host:
            for index in range(105): assert host.receive_json()['method']=='pair.pending'
            assert control(host,'pair.create',rid='after')['result']['token']

def test_replacement_host_fails_inflight_and_allows_same_key_retry(tmp_path):
    _,c=setup(tmp_path)
    with c:
        h=c.post('/remote/hosts/register',json={}).json(); auth={'authorization':'Bearer '+h['secret']}
        with c.websocket_connect('/remote/ws/host',headers=auth) as old:
            invite=control(old,'pair.create')['result']
            p=c.post('/remote/pair/redeem',json={'token':invite['token'],'name':'p'},headers=HEADERS).json(); old.receive_json()
            control(old,'pair.confirm',{'device_id':p['device_id'],'approved':True})
            c.post('/remote/pair/finish',json={'device_id':p['device_id'],'pending_secret':p['pending_secret']},headers=HEADERS)
            with c.websocket_connect('/remote/ws/device',headers={'origin':ORIGIN,'cookie':'remote_device='+c.cookies.get('remote_device')}) as device:
                req=dict(version=1,type='request',request_id='retry',host_id=h['host_id'],method='task.create',payload={},idempotency_key='stable')
                device.send_json(req); old.receive_json()
                with c.websocket_connect('/remote/ws/host',headers=auth) as new:
                    assert device.receive_json()['error']['code']=='offline'
                    device.send_json(req)
                    routed=new.receive_json()
                    assert routed['idempotency_key']=='stable'
                    new.send_json(dict(version=1,type='response',request_id='retry',_device_id=p['device_id'],result='accepted'))
                    assert device.receive_json()['result']=='accepted'

def test_http_body_stream_limit_stops_reading_at_limit(tmp_path):
    import asyncio
    from starlette.requests import Request
    from research_agent.remote.protocol import MAX_MESSAGE_BYTES
    app,_=setup(tmp_path)
    endpoint=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/remote/hosts/register')
    chunks=[b'x'*(MAX_MESSAGE_BYTES+1),b'should-not-be-read']
    async def receive():
        assert chunks
        return {'type':'http.request','body':chunks.pop(0),'more_body':True}
    req=Request({'type':'http','method':'POST','path':'/remote/hosts/register','scheme':'http','query_string':b'','headers':[],'client':('127.0.0.1',12),'server':('localhost',80)},receive)
    result=asyncio.run(endpoint(req))
    assert result.status_code==400
    assert len(chunks)==1
