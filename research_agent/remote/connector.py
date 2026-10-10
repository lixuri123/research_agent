"""Outbound-only connector. Run with python -m research_agent.remote.connector."""
import asyncio

import json
import random
import time
import httpx
from research_agent.remote.adapter import BusinessAdapter
from research_agent.remote.credentials import CredentialFile
from research_agent.remote.protocol import MAX_MESSAGE_BYTES, BoundedQueue, response

class Connector:
    def __init__(self,store,app,credentials=None):
        self.store=store; self.adapter=BusinessAdapter(store,app); self.credentials=credentials or CredentialFile()
        self.subscriptions={}; self.inflight={}; self.queue=None
        if credentials is None:
            from research_agent.workspace.service import workspace_table,metadata
            from sqlalchemy import select
            metadata.create_all(store.engine)
            with store.engine.connect() as c: roots=list(c.execute(select(workspace_table.c.root).where(workspace_table.c.owner_id=='local')).scalars())
            self.credentials=CredentialFile(workspace_roots=roots)
    async def bootstrap(self,config):
        credential=self.credentials.read()
        if credential:
            if credential['relay_url']!=config['relay_url']: raise ValueError('Credential relay mismatch')
            self.store.set_state(host_id=credential['host_id']); return credential
        async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
            r=await client.post(config['relay_url']+'/remote/hosts/register',json={}); r.raise_for_status(); value=r.json()
        credential={'relay_url':config['relay_url'],'host_id':value['host_id'],'secret':value['secret']}
        self.credentials.write(credential); self.store.set_state(host_id=value['host_id']); return credential
    def send(self,value):
        if not self.queue.put(value): raise ConnectionError('Outbound overflow')
    async def writer(self,ws):
        while True: await ws.send(await self.queue.get())
    async def controls(self,ws):
        while True:
            self.store.cleanup()
            config=self.store.config()
            if not config['enabled']: await ws.close(); return
            now=time.monotonic()
            for control in self.store.controls(True)[:40]:
                if now-self.inflight.get(control['id'],0)>3:
                    self.send({'version':1,'type':'request','request_id':control['id'],'method':control['method'],'payload':control['payload']})
                    self.inflight[control['id']]=now
            for (device_id,subscription_id),sub in list(self.subscriptions.items()):
                try:
                    self.store.authorize(device_id)
                    value=await self.adapter.dispatch(device_id,{'method':'task.subscribe','payload':sub})
                    for event in value['events']:
                        self.send({'version':1,'type':'event','_device_id':device_id,'subscription_id':subscription_id,'task_id':sub['task_id'],'cursor':event['cursor'],'payload':event})
                        sub['cursor']=event['cursor']
                except (ValueError,PermissionError): self.subscriptions.pop((device_id,subscription_id),None)
            await asyncio.sleep(.5)
    async def handle(self,message):
        if message.get('version')!=1: raise ValueError('Invalid version')
        if message['type']=='ping': self.send({'version':1,'type':'pong'}); return
        if message['type']=='pong': return
        if message['type']=='event' and message.get('method')=='pair.pending':
            self.store.pending(message['payload']); return
        if message['type']=='response':
            id=message['request_id']
            if id in self.inflight:
                control=next(c for c in self.store.controls(True) if c['id']==id)
                error=message.get('error')
                if not error or error.get('code') not in ('offline','rate_limited','internal_error'):
                    self.store.complete(id,message.get('result'),error)
                    if error and control['method']=='pair.confirm' and control['payload']['approved']:
                        self.store.revoke(control['payload']['device_id'])
                self.inflight.pop(id,None)
            return
        if message['type']!='request': raise ValueError('Invalid message')
        device=message.get('_device_id'); method=message.get('method'); request_id=message.get('request_id')
        # Revocations must settle with relay before accepting any business on reconnect.
        pending_revoke=any(c['method']=='device.revoke' for c in self.store.controls(True))
        try:
            if pending_revoke: raise PermissionError('Revocation synchronization pending')
            value=await self.adapter.dispatch(device,message)
            if method=='task.subscribe':
                if sum(1 for entry in self.subscriptions if entry[0]==device)>=100 and (device,message['payload']['subscription_id']) not in self.subscriptions: raise ValueError('Subscription limit')
                p=message['payload']; self.subscriptions[(device,p['subscription_id'])]=dict(p,cursor=value['events'][-1]['cursor'] if value['events'] else p.get('cursor',0))
            elif method=='task.unsubscribe': self.subscriptions.pop((device,message['payload']['subscription_id']),None)
            result=response(request_id,result=value)
        except PermissionError: result=response(request_id,code='forbidden')
        except ValueError as exc: result=response(request_id,code='resync_required' if str(exc)=='resync_required' else 'invalid_request')
        except RuntimeError: result=response(request_id,code='conflict')
        except Exception: result=response(request_id,code='internal_error')
        result['_device_id']=device; self.send(result)
    async def connection(self,credential):
        from websockets.asyncio.client import connect
        self.queue=BoundedQueue(); self.inflight={}; self.subscriptions={}
        url=credential['relay_url'].replace('https://','wss://',1).replace('http://','ws://',1)+'/remote/ws/host'
        async with connect(url,additional_headers={'Authorization':'Bearer '+credential['secret']},max_size=MAX_MESSAGE_BYTES,max_queue=100,open_timeout=15,ping_interval=20,ping_timeout=40) as ws:
            self.store.set_state(connection_state='online',error=None)
            tasks=[asyncio.create_task(self.writer(ws)),asyncio.create_task(self.controls(ws))]
            async def reader():
                async for raw in ws:
                    if len(raw.encode() if isinstance(raw,str) else raw)>MAX_MESSAGE_BYTES: raise ValueError('Oversize')
                    await self.handle(json.loads(raw))
            tasks.append(asyncio.create_task(reader()))
            try:
                done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                for task in done: task.result()
            finally:
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    async def run(self):
        delay=1
        while True:
            config=self.store.config()
            reset_error=None
            if config.get('reset_requested'):
                credential=self.credentials.read()
                if credential:
                    # Reset is explicit and destructive; ordinary disable never calls host.disable.
                    try:
                        async def detach():
                            from websockets.asyncio.client import connect
                            url=credential['relay_url'].replace('https://','wss://',1).replace('http://','ws://',1)+'/remote/ws/host'
                            async with connect(url,additional_headers={'Authorization':'Bearer '+credential['secret']},open_timeout=5) as ws:
                                await ws.send(json.dumps({'version':1,'type':'request','request_id':'reset','method':'host.disable','payload':{}}))
                                async for raw in ws:
                                    value=json.loads(raw)
                                    if value.get('type')=='ping': await ws.send(json.dumps({'version':1,'type':'pong'}))
                                    if value.get('request_id')=='reset': return
                        await asyncio.wait_for(detach(),8)
                        reset_error=None
                    except Exception: reset_error='old_relay_cleanup_unconfirmed'
                    self.credentials.path.unlink(missing_ok=True)
                self.store.set_state(host_id=None,relay_url='',reset_requested=False,error=reset_error)
                for control in self.store.controls(True): self.store.complete(control['id'],error={'code':'reset','message':'Host reset'})
                continue
            if not config['enabled']: await asyncio.sleep(.5); continue
            try:
                credential=await self.bootstrap(config); await self.connection(credential); delay=1
            except asyncio.CancelledError: raise
            except Exception:
                if self.store.config()['enabled']: self.store.set_state(connection_state='offline',error='connection_failed')
            await asyncio.sleep(min(30,delay+random.random()*.5)); delay=min(30,delay*2)

def main():
    from research_agent.persistence.queue import get_repository
    from research_agent.remote.local_store import LocalStore
    from research_agent.api import app,get_workspaces,get_file_changes
    get_workspaces(); get_file_changes()
    asyncio.run(Connector(LocalStore(get_repository()),app).run())
if __name__=='__main__': main()






