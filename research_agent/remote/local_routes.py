"""Local-browser-only remote settings. All methods use the same guard."""
import ipaddress
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from research_agent.remote.local_store import LocalStore

class Strict(BaseModel): model_config=ConfigDict(extra='forbid',strict=True)
class Config(Strict):
    enabled:bool
    relay_url:str=Field(default='',max_length=2048)
    development:bool=False
class Decision(Strict):
    approved:bool
    spaces:list[str]=Field(default_factory=list,max_length=100)
class Grants(Strict): spaces:list[str]=Field(max_length=100)
class Empty(Strict): pass

def get_store():
    from research_agent.persistence.queue import get_repository
    return LocalStore(get_repository())
def guard(request:Request):
    port=os.environ.get('REMOTE_LOCAL_PORT','8000')
    origins={f'http://{host}:{p}' for host in ('localhost','127.0.0.1') for p in (port,'5173')}
    host=request.headers.get('host','')
    if host not in {f'{h}:{port}' for h in ('localhost','127.0.0.1')}:
        raise HTTPException(403,'Local browser required')
    try: loopback=ipaddress.ip_address(request.client.host).is_loopback
    except ValueError: loopback=request.client.host=='localhost'
    origin=request.headers.get('origin')
    valid_origin=origin in origins or (request.method=='GET' and origin is None and request.headers.get('sec-fetch-site')=='same-origin')
    if not loopback or not valid_origin or request.headers.get('x-remote-csrf')!='1':
        raise HTTPException(403,'Local browser required')
router=APIRouter(prefix='/remote/local',dependencies=[Depends(guard)])
def action(fn):
    try: return fn()
    except (ValueError,PermissionError): raise HTTPException(422,'Invalid remote settings') from None

def validate_spaces(spaces):
    from research_agent.api import get_workspaces
    allowed={w['id'] for w in get_workspaces().list()}|{''}
    if any(space not in allowed for space in spaces): raise ValueError('Unknown workspace')
    return spaces
@router.get('/status')
def status():
    store=get_store(); store.cleanup(); result=store.config()
    result.update(devices=store.list_devices(),controls=store.controls())
    # Invitation secrets are transient; do not return expired invite tokens.
    import time
    for control in result['controls']:
        control.pop('payload',None)
        if control.get('result') and control['method']=='pair.create' and control['result'].get('expires_at',0)<time.time(): control['result']=None
    return result
@router.put('/config')
def configure(body:Config): return action(lambda:get_store().configure(**body.model_dump()))
@router.post('/invites')
def invite(body:Empty):
    def create():
        store=get_store()
        if not store.config()['enabled']: raise ValueError('Disabled')
        return store.enqueue('pair.create',{})
    return action(create)
@router.post('/devices/{device_id}/decision')
def decide(device_id:str,body:Decision):
    return action(lambda:get_store().decide(device_id,body.approved,validate_spaces(body.spaces)))
@router.put('/devices/{device_id}')
def grants(device_id:str,body:Grants): return action(lambda:get_store().grants(device_id,validate_spaces(body.spaces)))
@router.delete('/devices/{device_id}')
def revoke(device_id:str): return action(lambda:get_store().revoke(device_id))
@router.post('/reset')
def reset(body:Empty):
    store=get_store(); store.set_state(enabled=False,connection_state='disabled',reset_requested=True)
    for device in store.list_devices(): store.revoke(device['device_id'])
    return {'status':'reset_pending'}

