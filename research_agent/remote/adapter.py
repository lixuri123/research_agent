"""Named, strictly typed business adapter; the desktop remains authorization authority."""
import hashlib
import json
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select,func,union
from research_agent.models.provider_registry import ModelSelection
from research_agent.remote.protocol import METHODS, MUTATIONS, MAX_MESSAGE_BYTES

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
class Empty(Strict): pass
class Space(Strict): workspace_id: str | None = Field(default=None,max_length=128)
class Listing(Space):
    limit: int=Field(default=50,ge=1,le=100)
    offset: int=Field(default=0,ge=0)
class TaskId(Strict): task_id: str=Field(min_length=1,max_length=128)
class SessionId(Strict): session_id: str=Field(min_length=1,max_length=128)
class ReportId(Strict): report_version_id: str=Field(min_length=1,max_length=128)
class ReportDecision(ReportId): approved: bool
class Budget(Strict):
    max_calls:int=Field(default=100,ge=1,le=1000)
    max_tokens:int=Field(default=200000,ge=100,le=2000000)
    max_documents:int=Field(default=20,ge=1,le=100)
    deadline_seconds:int=Field(default=900,ge=1,le=86400)
    require_approval:bool|None=None
class Create(Space):
    requirements:str=Field(default='',max_length=10000)
    session_id:str|None=Field(default=None,max_length=128)
    budget:Budget|None=None
    model_selection:ModelSelection|None=None
class TaskCreate(Create): task:str=Field(min_length=1,max_length=4000)
class ResearchCreate(Create): topic:str=Field(min_length=1,max_length=4000)
class Changes(Strict):
    workspace_id:str=Field(min_length=1,max_length=128)
    run_id:str|None=Field(default=None,max_length=128)
class ChangeId(Strict):
    workspace_id:str=Field(min_length=1,max_length=128)
    change_id:str=Field(min_length=1,max_length=128)
class ChangeDecision(ChangeId):
    approved:bool
    review_hash:str=Field(min_length=64,max_length=64)
class Subscribe(TaskId):
    subscription_id:str=Field(min_length=1,max_length=128)
    cursor:int=Field(default=0,ge=0)
SCHEMAS={'host.status':Empty,'workspace.list':Empty,'model.list':Empty,'session.list':Listing,'session.read':SessionId,'task.create':TaskCreate,'research.create':ResearchCreate,'task.list':Listing,'task.read':TaskId,'task.subscribe':Subscribe,'task.unsubscribe':Subscribe,'report.read':ReportId,'report.decide':ReportDecision,'file_change.list':Changes,'file_change.read':ChangeId,'file_change.decide':ChangeDecision}

class BusinessAdapter:
    def __init__(self,store,app): self.store=store; self.repo=store.repository; self.app=app
    def scope(self,device,space):
        if (space or '') not in device['spaces']: raise PermissionError('Workspace forbidden')
    def task(self,device,id):
        row=self.repo.get(id)
        if not row: raise ValueError('Task not found')
        self.scope(device,row['payload'].get('workspace_id')); return row
    def session_space(self,id):
        from research_agent.workspace.service import session_table
        from research_agent.persistence.repository import runs
        with self.repo.engine.connect() as c:
            row=c.execute(select(session_table.c.workspace_id).where(session_table.c.owner_id=='local',session_table.c.session_id==id)).scalar_one_or_none()
            prior=list(c.execute(select(runs.c.payload).where(runs.c.owner_id=='local',runs.c.payload['session_id'].as_string()==id)).scalars())
        spaces={p.get('workspace_id') or '' for p in prior}
        if row is not None: spaces.add(row)
        if len(spaces)>1: raise PermissionError('Session scope ambiguous')
        return next(iter(spaces),'')
    async def call(self,verb,path,params=None,body=None,key=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://local') as client:
            response=await client.request(verb,path,params=params,json=body,headers={'Idempotency-Key':key} if key else {})
        if response.status_code>=400:
            if response.status_code==403: raise PermissionError('Business authorization denied')
            if response.status_code==409: raise RuntimeError('Business conflict')
            raise ValueError('Invalid business request')
        return response.json()
    async def dispatch(self,device_id,message):
        device=self.store.authorize(device_id)
        if message.get('host_id') and message['host_id']!=self.store.config()['host_id']: raise PermissionError('Wrong host')
        method=message.get('method')
        if method not in METHODS: raise ValueError('Unknown method')
        try: p=SCHEMAS[method].model_validate(message.get('payload',{})).model_dump(exclude_none=True)
        except Exception: raise ValueError('Invalid parameters') from None
        for field,value in p.items():
            if field.endswith('_id') and isinstance(value,str) and any(ch in value for ch in '/\\?#%'):
                raise ValueError('Invalid object identifier')
        key=None
        if method in MUTATIONS:
            raw=message.get('idempotency_key')
            if not isinstance(raw,str) or not 1<=len(raw)<=128: raise ValueError('Idempotency key required')
            key='remote:'+hashlib.sha256(json.dumps([self.store.config()['host_id'],device_id,method,raw]).encode()).hexdigest()
        if method=='host.status': return {'enabled':True,'host_id':self.store.config()['host_id'],'online':True}
        if method=='workspace.list':
            value=await self.call('GET','/workspaces')
            return {'personal_access':'' in device['spaces'],'workspaces':[{k:w[k] for k in ('id','name','agent_read') if k in w} for w in value['workspaces'] if w['id'] in device['spaces']]}
        if method=='model.list':
            value=await self.call('GET','/settings/providers')
            return {'providers':[{k:v[k] for k in ('id','name','models','model') if k in v} for v in value['providers']],'active_provider_id':value.get('active_provider_id')}
        if method in ('task.create','research.create'):
            self.scope(device,p.get('workspace_id'))
            if p.get('session_id'):
                from research_agent.workspace.service import session_table
                from research_agent.persistence.repository import runs
                with self.repo.engine.connect() as c:
                    exists=c.execute(select(session_table).where(session_table.c.session_id==p['session_id'],session_table.c.owner_id=='local')).first()
                    history=c.execute(select(runs.c.run_id).where(runs.c.payload['session_id'].as_string()==p['session_id'],runs.c.owner_id=='local').limit(1)).first()
                if exists or history:
                    space=self.session_space(p['session_id']); self.scope(device,space)
                    if space!=(p.get('workspace_id') or ''): raise PermissionError('Session belongs to another space')
            result=await self.call('POST','/tasks' if method=='task.create' else '/research',body=p,key=key)
        elif method in ('task.list','session.list'):
            if 'workspace_id' in p: self.scope(device,p['workspace_id'])
            # Filter authoritative rows before pagination so personal is explicit, never all spaces.
            from research_agent.persistence.repository import runs
            space_expression=func.coalesce(runs.c.payload['workspace_id'].as_string(),'')
            query=select(runs).where(runs.c.owner_id=='local',space_expression.in_(device['spaces'])).order_by(runs.c.created_at.desc())
            if 'workspace_id' in p: query=query.where(space_expression==p['workspace_id'])
            if method=='task.list': query=query.offset(p['offset']).limit(p['limit'])
            else: query=query.limit(1000)
            with self.repo.engine.connect() as c: rows=[dict(r) for r in c.execute(query).mappings()]
            if method=='task.list':
                result={'tasks':[{'task_id':r['task_id'],'run_id':r['run_id'],'status':r['status'],'topic':r['payload']['topic'],'session_id':r['payload'].get('session_id'),'workspace_id':r['payload'].get('workspace_id'),'current_report_version_id':r['current_report_version_id'],'created_at':r['created_at']} for r in rows]}
            else:
                from research_agent.workspace.service import session_table
                bindings=select(session_table.c.session_id,session_table.c.workspace_id).where(session_table.c.owner_id=='local',session_table.c.workspace_id.in_(device['spaces']))
                history=select(runs.c.payload['session_id'].as_string().label('session_id'),space_expression.label('workspace_id')).where(runs.c.owner_id=='local',space_expression.in_(device['spaces']),runs.c.payload['session_id'].as_string().is_not(None))
                if 'workspace_id' in p:
                    bindings=bindings.where(session_table.c.workspace_id==p['workspace_id']); history=history.where(space_expression==p['workspace_id'])
                sessions=union(bindings,history).subquery()
                with self.repo.engine.connect() as c:
                    result={'sessions':[dict(r) for r in c.execute(select(sessions).order_by(sessions.c.session_id).offset(p['offset']).limit(p['limit'])).mappings()]}
        elif method=='session.read':
            self.scope(device,self.session_space(p['session_id']))
            result=await self.call('GET',f"/sessions/{p['session_id']}/messages")
        elif method.startswith('task.'):
            row=self.task(device,p['task_id'])
            if method=='task.read': result=await self.call('GET',f"/tasks/{row['task_id']}")
            elif method=='task.subscribe': result={'subscription_id':p['subscription_id'],'task_id':p['task_id'],'events':self.store.event_batch(p['task_id'],self.repo.events(row['run_id']),p['cursor'])}
            else: result={'unsubscribed':True}
        elif method.startswith('report.'):
            report=self.repo.get_report(p['report_version_id'])
            if not report: raise ValueError('Report not found')
            row=self.repo.get_run(report['run_id']); self.task(device,row['task_id'])
            if method=='report.read': result=await self.call('GET',f"/reports/{p['report_version_id']}")
            else: result=await self.call('POST',f"/research/{row['task_id']}/approve",body=p)
        elif method.startswith('file_change.'):
            from research_agent.workspace.file_changes import changes
            self.scope(device,p['workspace_id'])
            if p.get('run_id'):
                run=self.repo.get_run(p['run_id'])
                if not run: raise ValueError('Run not found')
                self.task(device,run['task_id'])
                if (run['payload'].get('workspace_id') or '')!=p['workspace_id']: raise PermissionError('Run mismatch')
            if 'change_id' in p:
                with self.repo.engine.connect() as c: change=c.execute(select(changes).where(changes.c.id==p['change_id'],changes.c.owner_id=='local')).mappings().first()
                if not change or change['workspace_id']!=p['workspace_id']: raise PermissionError('Change mismatch')
                run=self.repo.get_run(change['run_id']); self.task(device,run['task_id'])
            path=f"/workspaces/{p['workspace_id']}/changes"
            if method=='file_change.list': result=await self.call('GET',path,params={'run_id':p['run_id']} if p.get('run_id') else {})
            elif method=='file_change.read': result=await self.call('GET',path+'/'+p['change_id'])
            else: result=await self.call('POST',path+'/'+p['change_id']+'/decision',body={'approved':p['approved'],'review_hash':p['review_hash']})
        self.store.authorize(device_id)
        if len(json.dumps(result).encode())>MAX_MESSAGE_BYTES-1024: raise ValueError('Content too large; review on desktop')
        return result



