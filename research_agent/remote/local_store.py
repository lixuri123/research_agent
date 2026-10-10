"""SQL-backed desktop authorization and durable connector controls."""
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit
from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, insert, select, update

metadata=MetaData()
state=Table('remote_local_state',metadata,Column('id',Integer,primary_key=True),Column('value',JSON,nullable=False))
devices=Table('remote_local_devices',metadata,Column('id',String(128),primary_key=True),Column('value',JSON,nullable=False))
controls=Table('remote_local_controls',metadata,Column('id',String(36),primary_key=True),Column('value',JSON,nullable=False))
cursors=Table('remote_local_cursors',metadata,Column('task_id',String(128),primary_key=True),Column('value',JSON,nullable=False))

def validate_relay(url, development=False):
    p=urlsplit(url)
    if p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise ValueError('Relay must be an origin')
    if p.scheme!='https' and not (development and p.scheme=='http' and p.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('HTTPS relay required; HTTP development is loopback only')
    if not p.hostname: raise ValueError('Relay hostname required')
    return url.rstrip('/')

class LocalStore:
    def __init__(self,repository):
        self.repository=repository; self.engine=repository.engine
        metadata.create_all(self.engine)
        with self.transaction() as c:
            if c.execute(select(state)).first() is None:
                c.execute(insert(state).values(id=1,value={'enabled':False,'relay_url':'','development':False,'host_id':None,'connection_state':'disabled','error':None}))
    @contextmanager
    def transaction(self):
        with self.repository._transaction() as c: yield c
    def config(self):
        with self.engine.connect() as c: return dict(c.execute(select(state.c.value)).scalar_one())
    def set_state(self,**values):
        with self.transaction() as c:
            value=dict(c.execute(select(state.c.value)).scalar_one()); value.update(values)
            c.execute(update(state).values(value=value))
        return value
    def configure(self,enabled,relay_url,development=False):
        old=self.config()
        if old.get('reset_requested'): raise ValueError('Reset pending')
        url=validate_relay(relay_url,development) if relay_url else ''
        if enabled and not url: raise ValueError('Relay required')
        if old['host_id'] and url!=old['relay_url']: raise ValueError('Paired relay cannot be changed; revoke devices and reset host separately')
        return self.set_state(enabled=enabled,relay_url=url,development=development,connection_state='connecting' if enabled else 'disabled',error=None)
    def device(self,id):
        with self.engine.connect() as c:
            row=c.execute(select(devices.c.value).where(devices.c.id==id)).scalar_one_or_none()
            return dict(row) if row else None
    def list_devices(self):
        with self.engine.connect() as c: return [dict(x) for x in c.execute(select(devices.c.value)).scalars()]
    def pending(self,payload):
        id=payload['device_id']; now=time.time()
        expires=payload.get('expires_at',payload.get('expires',now+300))
        if payload.get('invite_id'):
            for control in self.controls():
                result=control.get('result') or {}
                if result.get('invite_id')==payload['invite_id']: expires=result['expires_at']
        with self.transaction() as c:
            old=c.execute(select(devices.c.value).where(devices.c.id==id)).scalar_one_or_none()
            if old: return old
            value={'device_id':id,'name':payload['name'],'code':payload.get('code',''),'status':'awaiting_confirmation','permission_version':0,'spaces':[],'created_at':now,'last_seen':None,'expires_at':expires}
            c.execute(insert(devices).values(id=id,value=value)); return value
    def _enqueue(self,c,method,payload):
        value={'id':str(uuid.uuid4()),'method':method,'payload':payload,'status':'pending','result':None,'error':None,'created_at':time.time()}
        c.execute(insert(controls).values(id=value['id'],value=value)); return value
    def enqueue(self,method,payload):
        with self.transaction() as c: return self._enqueue(c,method,payload)
    def decide(self,id,approved,spaces):
        with self.transaction() as c:
            value=c.execute(select(devices.c.value).where(devices.c.id==id)).scalar_one_or_none()
            if not value or value['status']=='revoked': raise ValueError('Unknown or revoked device')
            value=dict(value)
            if value['status']=='awaiting_confirmation' and value.get('expires_at',time.time()+1)<time.time(): raise ValueError('Pairing expired')
            target='authorized' if approved else 'rejected'
            if value['status'] not in ('awaiting_confirmation',target): raise ValueError('Decision conflict')
            if value['status']=='awaiting_confirmation':
                value.update(status=target,spaces=list(dict.fromkeys(spaces)) if approved else [],permission_version=1)
                c.execute(update(devices).where(devices.c.id==id).values(value=value))
                self._enqueue(c,'pair.confirm',{'device_id':id,'approved':approved})
            return value
    def grants(self,id,spaces):
        with self.transaction() as c:
            value=c.execute(select(devices.c.value).where(devices.c.id==id)).scalar_one_or_none()
            if not value or value['status']!='authorized': raise ValueError('Unauthorized device')
            value=dict(value); value.update(spaces=list(dict.fromkeys(spaces)),permission_version=value['permission_version']+1)
            c.execute(update(devices).where(devices.c.id==id).values(value=value)); return value
    def revoke(self,id):
        with self.transaction() as c:
            value=c.execute(select(devices.c.value).where(devices.c.id==id)).scalar_one_or_none()
            if not value: raise ValueError('Unknown device')
            value=dict(value)
            if value['status']!='revoked':
                value.update(status='revoked',spaces=[],permission_version=value['permission_version']+1)
                c.execute(update(devices).where(devices.c.id==id).values(value=value))
                self._enqueue(c,'device.revoke',{'device_id':id})
            return value
    def authorize(self,id):
        if not self.config()['enabled']: raise PermissionError('Device unauthorized')
        with self.transaction() as c:
            value=c.execute(select(devices.c.value).where(devices.c.id==id)).scalar_one_or_none()
            if not value or value['status']!='authorized': raise PermissionError('Device unauthorized')
            value=dict(value)
            if time.time()-(value.get('last_seen') or 0)>30:
                value['last_seen']=time.time()
                c.execute(update(devices).where(devices.c.id==id).values(value=value))
            return value
    def cleanup(self):
        now=time.time()
        with self.transaction() as c:
            rows=c.execute(select(controls).where(controls.c.value['method'].as_string()=='pair.create',controls.c.value['result']['expires_at'].as_float()<now).limit(500)).mappings()
            for row in rows:
                value=dict(row['value']); result=value.get('result') or {}
                if value['method']=='pair.create' and result.get('expires_at',now+1)<now:
                    value['result']=None; c.execute(update(controls).where(controls.c.id==row['id']).values(value=value))
            for row in c.execute(select(devices).where(devices.c.value['status'].as_string()=='awaiting_confirmation',devices.c.value['expires_at'].as_float()<now).limit(500)).mappings():
                value=dict(row['value'])
                if value['status']=='awaiting_confirmation' and value.get('expires_at',now+1)<now:
                    value['status']='expired'; c.execute(update(devices).where(devices.c.id==row['id']).values(value=value))
    def controls(self,pending=False):
        with self.engine.connect() as c: rows=[dict(v) for v in c.execute(select(controls.c.value)).scalars()]
        return sorted([v for v in rows if not pending or v['status']=='pending'],key=lambda x:x['created_at'])
    def complete(self,id,result=None,error=None):
        with self.transaction() as c:
            value=dict(c.execute(select(controls.c.value).where(controls.c.id==id)).scalar_one())
            value.update(status='done' if error is None else 'failed',result=result,error=error)
            c.execute(update(controls).where(controls.c.id==id).values(value=value))
    def event_batch(self,task_id,events,after=0):
        with self.transaction() as c:
            old=c.execute(select(cursors.c.value).where(cursors.c.task_id==task_id)).scalar_one_or_none()
            value=dict(old or {'next':1,'events':[]}); mapped=list(value['events']); seen={x['event_id'] for x in mapped}
            for event in events:
                if event['event_id'] not in seen:
                    mapped.append(dict(event,cursor=value['next'])); value['next']+=1; seen.add(event['event_id'])
            value['events']=mapped
            if old is None: c.execute(insert(cursors).values(task_id=task_id,value=value))
            else: c.execute(update(cursors).where(cursors.c.task_id==task_id).values(value=value))
        if after>=value['next']: raise ValueError('resync_required')
        return [x for x in mapped if x['cursor']>after][:100]



