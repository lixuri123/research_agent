"""Single-instance trusted TLS relay, with authenticated, targeted routing."""
import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from collections import defaultdict,deque
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import make_url
from .protocol import *
from .relay_store import RelayStore

COOKIE='remote_device'
class Peer:
    def __init__(self, ws):
        self.ws = ws
        self.queue = BoundedQueue()
        self.last = time.monotonic()
        self.active = True

    async def send(self, message):
        if not self.active:
            return False
        if not self.queue.put(message):
            self.active = False
            with contextlib.suppress(Exception):
                await self.ws.close(code=1013)
            return False
        return True

    async def writer(self):
        try:
            while True:
                await self.ws.send_text(await self.queue.get())
        except (WebSocketDisconnect, RuntimeError):
            self.active = False
            with contextlib.suppress(Exception):
                await self.ws.close(code=1013)

def create_app(database_url,public_url,development=False):
    url=urlsplit(public_url)
    if url.scheme!='https' and not (development and url.scheme=='http' and url.hostname in {'localhost','127.0.0.1','::1'}): raise ValueError('HTTPS required; development HTTP is loopback only')
    if url.username or url.password or url.query or url.fragment or url.path not in ('','/'): raise ValueError('public_url must be an origin')
    origin=public_url.rstrip('/'); store=RelayStore(database_url); app=FastAPI(); app.state.store=store
    hosts={}; devices=defaultdict(set); pending={}; subscriptions=set(); attempts=defaultdict(deque)
    def online(hid):
        peer=hosts.get(hid)
        return bool(peer and peer.active and store.enabled(hid))
    def error(code,status=400): return JSONResponse({'error':{'code':code,'message':code.replace('_',' ')}},status_code=status)
    def limited(request,bucket):
        now=time.monotonic()
        for stale_key, values in list(attempts.items())[:500]:
            if not values or values[-1]<now-60: attempts.pop(stale_key,None)
        if len(attempts)>=10000: return True
        key=(bucket,request.client.host if request.client else 'unknown'); q=attempts[key]; now=time.monotonic()
        while q and q[0]<now-60: q.popleft()
        if len(q)>=(180 if bucket=='finish' else 20): return True
        q.append(now); return False
    def safe(request,browser=True):
        if request.url.query: return False
        if not development and request.url.scheme!='https': return False
        o=request.headers.get('origin')
        return (o==origin and request.headers.get('x-remote-csrf')=='1') if browser else (o is None or o==origin)
    async def body(request,fields):
        content_length=request.headers.get('content-length')
        if content_length:
            try:
                if int(content_length)>MAX_MESSAGE_BYTES: raise ValueError('invalid_request')
            except ValueError: raise ValueError('invalid_request')
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>MAX_MESSAGE_BYTES: raise ValueError('invalid_request')
            raw.extend(chunk)
        try: data=json.loads(raw)
        except Exception: raise ValueError('invalid_request')
        if not isinstance(data,dict) or set(data)!=set(fields): raise ValueError('invalid_request')
        return data
    def session(d): return {'host_id':d['host_id'],'device_id':d['id'],'name':d['name'],'csrf_token':d['csrf'],'online':online(d['host_id'])}
    @app.post('/remote/hosts/register')
    async def register(request:Request):
        if not safe(request,False): return error('forbidden',403)
        if limited(request,'register'): return error('rate_limited',429)
        try: await body(request,[])
        except ValueError: return error('invalid_request')
        return store.register()
    @app.post('/remote/pair/redeem')
    async def redeem(request:Request):
        if not safe(request): return error('forbidden',403)
        if limited(request,'redeem'): return error('rate_limited',429)
        try:
            b=await body(request,['token','name'])
            if not isinstance(b['token'],str) or not isinstance(b['name'],str) or not 1<=len(b['name'])<=80: raise ValueError('invalid_request')
            invite_host=store.invite_host(b['token'])
            if invite_host and not online(invite_host): return error('offline',409)
            result=store.redeem(b['token'],b['name']); host=hosts.get(result['host_id'])
            if host: await host.send({'version':1,'type':'event','method':'pair.pending','payload':{k:v for k,v in result.items() if k!='pending_secret'}})
            return result
        except ValueError as e: return error(str(e),409 if str(e)=='conflict' else 400)
    @app.post('/remote/pair/finish')
    async def finish(request:Request):
        if not safe(request): return error('forbidden',403)
        if limited(request,'finish'): return error('rate_limited',429)
        try:
            b=await body(request,['device_id','pending_secret'])
            if not all(isinstance(v,str) for v in b.values()): raise ValueError('invalid_request')
            token,d=store.finish(b['device_id'],b['pending_secret'])
            if not online(d['host_id']): return error('offline',409)
            out=JSONResponse(session(d)); out.set_cookie(COOKIE,token,httponly=True,secure=not development,samesite='strict',path='/remote',max_age=31536000); return out
        except ValueError as e: return error(str(e),409 if str(e) in {'conflict','offline'} else 401)
    @app.get('/remote/session')
    async def get_session(request:Request):
        if not development and request.url.scheme!='https': return error('forbidden',403)
        d=store.device(request.cookies.get(COOKIE))
        return session(d) if d else error('unauthorized',401)
    async def close_devices(hid,did=None):
        for ident,peers in list(devices.items()):
            d=store.device(did=ident)
            if ident==did or (did is None and (not d or d['host_id']==hid)):
                for peer in list(peers):
                    with contextlib.suppress(Exception): await peer.ws.close(code=4401)
    async def lifecycle(peer,valid,handler,initialize=None):
        writer=asyncio.create_task(peer.writer())
        async def heartbeat():
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                store.cleanup()
                if not peer.active or not valid() or time.monotonic()-peer.last>STALE_SECONDS: await peer.ws.close(code=4401); return
                await peer.send({'version':1,'type':'ping'})
        beat=asyncio.create_task(heartbeat())
        try:
            if initialize: await initialize()
            while True:
                raw=await peer.ws.receive_text()
                if len(raw.encode())>MAX_MESSAGE_BYTES: await peer.ws.close(code=1009); break
                if not valid(): await peer.ws.close(code=4401); break
                msg={}
                try:
                    msg=json.loads(raw)
                    if not isinstance(msg,dict) or msg.get('version')!=1: raise ValueError()
                    peer.last=time.monotonic()
                    if msg.get('type')=='ping': await peer.send({'version':1,'type':'pong'}); continue
                    if msg.get('type')=='pong': continue
                    await handler(msg)
                except (ValueError,TypeError,KeyError): await peer.send(response(msg.get('request_id') if isinstance(msg,dict) else None,code='invalid_request'))
        except (WebSocketDisconnect,RuntimeError): pass
        finally:
            peer.active=False
            writer.cancel(); beat.cancel()
            await asyncio.gather(writer,beat,return_exceptions=True)
    @app.websocket('/remote/ws/host')
    async def host_socket(ws:WebSocket):
        auth=ws.headers.get('authorization',''); hid=store.host(auth[7:]) if auth.startswith('Bearer ') else None
        if not hid or ws.headers.get('origin') not in (None,origin) or ws.url.query or (not development and ws.url.scheme!='wss'): await ws.close(code=4401); return
        await ws.accept(); peer=Peer(ws); previous=hosts.get(hid); hosts[hid]=peer
        if previous:
            for key,target in list(pending.items()):
                if key[0]==hid:
                    pending.pop(key,None)
                    await target.send(response(key[2],code='offline'))
            subscriptions.difference_update({key for key in subscriptions if key[0]==hid})
            with contextlib.suppress(Exception): await previous.ws.close(code=1012)
        async def handler(msg):
            kind=msg.get('type'); rid=msg.get('request_id')
            if kind in {'response','event'} and (not isinstance(msg.get('_device_id'),str) or (kind=='response' and not isinstance(rid,str))): raise ValueError()
            if kind=='request':
                if not isinstance(rid,str) or not 1<=len(rid)<=128 or not isinstance(msg.get('payload'),dict): raise ValueError()
                method=msg.get('method'); b=msg['payload']
                try:
                    if method=='pair.create':
                        if b: raise ValueError('invalid_request')
                        result=store.create_invite(hid,rid,host_secret=auth[7:]); result=dict(result,url=origin+'/mobile/#invite='+result['token'])
                    elif method=='pair.confirm':
                        if set(b)!={'device_id','approved'} or type(b['approved']) is not bool: raise ValueError('invalid_request')
                        result=store.confirm(hid,b['device_id'],b['approved'])
                    elif method=='device.revoke':
                        if set(b)!={'device_id'}: raise ValueError('invalid_request')
                        store.revoke(hid,b['device_id']); await close_devices(hid,b['device_id']); result={'device_id':b['device_id'],'status':'revoked'}
                    elif method=='host.disable':
                        if b: raise ValueError('invalid_request')
                        store.revoke(hid); await close_devices(hid); result={'status':'disabled'}
                    else: raise ValueError('invalid_request')
                    await peer.send(response(rid,result))
                except ValueError as e: await peer.send(response(rid,code=str(e)))
            elif kind=='response':
                did=msg.get('_device_id'); target=pending.pop((hid,did,rid),None)
                if target and store.device(did=did): await target.send({k:v for k,v in msg.items() if k not in {'_device_id','device_id','host_id'}})
            elif kind=='event':
                did=msg.get('_device_id'); key=(hid,did,msg.get('subscription_id'),msg.get('task_id'))
                if key in subscriptions and store.device(did=did):
                    for target in list(devices.get(did,())): await target.send({k:v for k,v in msg.items() if k not in {'_device_id','device_id','host_id'}})
            else: raise ValueError()
        async def replay():
            for d in store.pending(hid):
                if not await peer.send({'version':1,'type':'event','method':'pair.pending','payload':dict(d,device_id=d['id'])}): return
                await asyncio.sleep(0)
        try: await lifecycle(peer,lambda:store.enabled(hid) and hosts.get(hid) is peer,handler,replay)
        finally:
            if hosts.get(hid) is peer:
                hosts.pop(hid,None)
                for key,target in list(pending.items()):
                    if key[0]==hid: pending.pop(key,None); await target.send(response(key[2],code='offline'))
    @app.websocket('/remote/ws/device')
    async def device_socket(ws:WebSocket):
        token=ws.cookies.get(COOKIE); d=store.device(token)
        if not d or ws.headers.get('origin')!=origin or ws.url.query or (not development and ws.url.scheme!='wss'): await ws.close(code=4401); return
        await ws.accept(); peer=Peer(ws); did=d['id']; hid=d['host_id']; devices[did].add(peer)
        async def handler(msg):
            rid=msg.get('request_id'); method=msg.get('method'); payload=msg.get('payload')
            if msg.get('type')!='request' or not isinstance(rid,str) or not 1<=len(rid)<=128 or method not in METHODS or not isinstance(payload,dict) or msg.get('host_id')!=hid: raise ValueError()
            if method in MUTATIONS and (not isinstance(msg.get('idempotency_key'),str) or not 1<=len(msg['idempotency_key'])<=128): raise ValueError()
            host=hosts.get(hid)
            if not host or not online(hid): await peer.send(response(rid,code='offline')); return
            if len(pending)>=10000 or sum(1 for k in pending if k[1]==did)>=100: await peer.send(response(rid,code='rate_limited')); return
            key=(hid,did,rid)
            if key in pending: await peer.send(response(rid,code='conflict')); return
            if method=='task.subscribe':
                sid=payload.get('subscription_id'); tid=payload.get('task_id')
                if not isinstance(sid,str) or not 1<=len(sid)<=128 or not isinstance(tid,str) or not 1<=len(tid)<=128: raise ValueError()
                if sum(1 for k in subscriptions if k[1]==did)>=100: await peer.send(response(rid,code='rate_limited')); return
                subscriptions.add((hid,did,sid,tid))
            if method=='task.unsubscribe': subscriptions.discard((hid,did,payload.get('subscription_id'),payload.get('task_id')))
            out={k:msg[k] for k in ('version','type','request_id','method','payload','idempotency_key') if k in msg}; out.update(host_id=hid,_device_id=did)
            # Identity fields cannot masquerade as local data ownership.
            out['payload']={k:v for k,v in payload.items() if k not in {'device_id','host_id','owner_id','_device_id'}}
            pending[key]=peer
            if not await host.send(out): pending.pop(key,None); await peer.send(response(rid,code='offline'))
        try: await lifecycle(peer,lambda:bool(store.device(token)),handler)
        finally:
            devices[did].discard(peer)
            if not devices[did]: devices.pop(did,None); subscriptions.difference_update({k for k in subscriptions if k[1]==did})
            for key,target in list(pending.items()):
                if target is peer: pending.pop(key,None)
    return app

def deployment_app():
    """Uvicorn --factory entry point; importing this module never creates files."""
    database_url = os.getenv('REMOTE_RELAY_DATABASE_URL', 'sqlite:///data/remote-relay.db')
    database = make_url(database_url)
    if database.get_backend_name() == 'sqlite' and database.database not in (None, '', ':memory:'):
        Path(database.database).resolve().parent.mkdir(parents=True, exist_ok=True)
    app = create_app(database_url, os.getenv('REMOTE_RELAY_PUBLIC_URL', 'https://localhost'), os.getenv('REMOTE_RELAY_DEVELOPMENT', '').lower() == 'true')
    dist = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    index = dist / 'index.html'
    if not index.is_file() or not (dist / 'assets').is_dir():
        raise RuntimeError('Build frontend/dist before starting the relay.')
    @app.get('/mobile/')
    @app.get('/mobile/{path:path}')
    async def mobile(path: str = ''):
        return FileResponse(index)
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='mobile-assets')
    return app
