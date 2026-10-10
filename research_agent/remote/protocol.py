"""Version 1 JSON protocol and bounded transient buffers."""
import asyncio
import json
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_QUEUE_MESSAGES = 100
MAX_QUEUE_BYTES = 4 * MAX_MESSAGE_BYTES
HEARTBEAT_SECONDS = 20
STALE_SECONDS = 60
METHODS = frozenset('host.status workspace.list session.list session.read model.list task.create research.create task.list task.read task.subscribe task.unsubscribe report.read report.decide file_change.list file_change.read file_change.decide'.split())
MUTATIONS = frozenset({'task.create','research.create','report.decide','file_change.decide'})
def response(request_id, result=None, code=None):
    out={'version':1,'type':'response','request_id':request_id}
    out['error' if code else 'result']={'code':code,'message':code.replace('_',' ')} if code else result
    return out
class BoundedQueue:
    def __init__(self,max_messages=MAX_QUEUE_MESSAGES,max_bytes=MAX_QUEUE_BYTES):
        self.queue=asyncio.Queue(maxsize=max_messages); self.max_bytes=max_bytes; self.bytes=0
    def put(self,message):
        encoded=json.dumps(message,separators=(',',':')); size=len(encoded.encode())
        if size>MAX_MESSAGE_BYTES or self.bytes+size>self.max_bytes or self.queue.full(): return False
        self.queue.put_nowait((encoded,size)); self.bytes+=size; return True
    async def get(self):
        encoded,size=await self.queue.get(); self.bytes-=size; return encoded
