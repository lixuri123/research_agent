"""Durable metadata only. No request bodies or business results are stored."""
import hashlib
import hmac
import secrets
import threading
import time
import json
from sqlalchemy import create_engine, text

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def secret(): return secrets.token_urlsafe(32)
class RelayStore:
    def __init__(self,database_url):
        kwargs={'connect_args':{'check_same_thread':False}} if database_url.startswith('sqlite') else {}
        if database_url in ('sqlite://','sqlite:///:memory:'):
            from sqlalchemy.pool import StaticPool
            kwargs['poolclass']=StaticPool
        self.engine=create_engine(database_url,**kwargs); self.lock=threading.RLock()
        with self.engine.begin() as c:
            c.execute(text('CREATE TABLE IF NOT EXISTS remote_hosts (id TEXT PRIMARY KEY, credential TEXT NOT NULL, enabled INTEGER NOT NULL)'))
            c.execute(text('CREATE TABLE IF NOT EXISTS remote_invites (id TEXT PRIMARY KEY, host_id TEXT NOT NULL, token TEXT UNIQUE NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL)'))
            c.execute(text('CREATE TABLE IF NOT EXISTS remote_devices (id TEXT PRIMARY KEY, host_id TEXT NOT NULL, name TEXT NOT NULL, pending TEXT NOT NULL, credential TEXT, csrf TEXT, status TEXT NOT NULL, expires REAL NOT NULL, code TEXT NOT NULL)'))
            c.execute(text('CREATE TABLE IF NOT EXISTS remote_controls (host_id TEXT NOT NULL, request_id TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(host_id,request_id))'))
    def cleanup(self, limit=500):
        """Bounded opportunistic expiry; authorized devices remain durable."""
        now = time.time()
        with self.lock, self.engine.begin() as c:
            expired = c.execute(text('SELECT id FROM remote_invites WHERE expires<:now LIMIT :limit'), {'now': now, 'limit': limit}).scalars().all()
            for iid in expired:
                c.execute(text('DELETE FROM remote_invites WHERE id=:id'), {'id': iid})
            controls = c.execute(text('SELECT host_id,request_id,result FROM remote_controls LIMIT :limit'), {'limit': limit}).mappings().all()
            for item in controls:
                if json.loads(item['result'])['expires_at'] < now:
                    c.execute(text('DELETE FROM remote_controls WHERE host_id=:h AND request_id=:r'), {'h': item['host_id'], 'r': item['request_id']})
            pending = c.execute(text("SELECT id FROM remote_devices WHERE status='awaiting_confirmation' AND expires<:now LIMIT :limit"), {'now': now, 'limit': limit}).scalars().all()
            for did in pending:
                c.execute(text("UPDATE remote_devices SET status='expired',pending='' WHERE id=:id"), {'id': did})
            stale = c.execute(text("SELECT id FROM remote_devices WHERE status IN ('expired','rejected','revoked') AND expires<:cutoff LIMIT :limit"), {'cutoff': now-86400, 'limit': limit}).scalars().all()
            for did in stale:
                c.execute(text('DELETE FROM remote_devices WHERE id=:id'), {'id': did})
    def register(self):
        hid=secret(); token=secret()
        with self.engine.begin() as c: c.execute(text('INSERT INTO remote_hosts VALUES (:id,:credential,1)'),{'id':hid,'credential':digest(token)})
        return {'host_id':hid,'secret':token}
    def host(self,token):
        with self.engine.connect() as c:
            row=c.execute(text('SELECT id FROM remote_hosts WHERE credential=:v AND enabled=1'),{'v':digest(token)}).first()
            return row[0] if row else None
    def enabled(self,hid):
        with self.engine.connect() as c: return bool(c.execute(text('SELECT enabled FROM remote_hosts WHERE id=:id'),{'id':hid}).scalar())
    def create_invite(self,hid,rid,ttl=300,host_secret=None):
        self.cleanup()
        with self.lock,self.engine.begin() as c:
            old=c.execute(text('SELECT result FROM remote_controls WHERE host_id=:h AND request_id=:r'),{'h':hid,'r':rid}).scalar()
            if old:
                result=json.loads(old)
                if host_secret: result["token"]=hmac.new(host_secret.encode(),result.pop("nonce").encode(),hashlib.sha256).hexdigest()
                return result
            nonce=secret(); token=hmac.new(host_secret.encode(),nonce.encode(),hashlib.sha256).hexdigest() if host_secret else secret(); iid=secret(); expires=time.time()+ttl
            c.execute(text('INSERT INTO remote_invites VALUES (:id,:h,:t,:e,0)'),{'id':iid,'h':hid,'t':digest(token),'e':expires})
            result={'invite_id':iid,'token':token,'expires_at':expires}
            # Invite retry secret must be recoverable briefly, never a persistent host credential.
            c.execute(text('INSERT INTO remote_controls VALUES (:h,:r,:v)'),{'h':hid,'r':rid,'v':json.dumps({'invite_id':iid,'expires_at':expires,'nonce':nonce} if host_secret else {'invite_id':iid,'expires_at':expires})})
            return result
    def invite_host(self,token):
        with self.engine.connect() as c: return c.execute(text("SELECT host_id FROM remote_invites WHERE token=:t AND used=0 AND expires>=:now"),{"t":digest(token),"now":time.time()}).scalar()
    def pending(self,hid):
        self.cleanup()
        with self.engine.connect() as c: return [dict(row) for row in c.execute(text("SELECT id,host_id,name,code,status,expires AS expires_at FROM remote_devices WHERE host_id=:h AND status='awaiting_confirmation' AND expires>=:now"),{"h":hid,"now":time.time()}).mappings()]
    def redeem(self,token,name):
        with self.lock,self.engine.begin() as c:
            row=c.execute(text('SELECT * FROM remote_invites WHERE token=:t'),{'t':digest(token)}).mappings().first()
            if not row or row['expires']<time.time(): raise ValueError('expired')
            if row['used']: raise ValueError('conflict')
            if not c.execute(text('SELECT enabled FROM remote_hosts WHERE id=:h'),{'h':row['host_id']}).scalar(): raise ValueError('unauthorized')
            changed=c.execute(text('UPDATE remote_invites SET used=1 WHERE id=:id AND used=0'),{'id':row['id']})
            if changed.rowcount!=1: raise ValueError('conflict')
            did=secret(); pending=secret(); code=f'{secrets.randbelow(1000000):06d}'
            c.execute(text('INSERT INTO remote_devices VALUES (:id,:h,:n,:p,NULL,NULL,:s,:e,:code)'),{'id':did,'h':row['host_id'],'n':name,'p':digest(pending),'s':'awaiting_confirmation','e':row['expires'],'code':code})
            return {'invite_id':row['id'],'host_id':row['host_id'],'device_id':did,'pending_secret':pending,'code':code,'name':name,'status':'awaiting_confirmation','expires_at':row['expires']}
    def confirm(self,hid,did,approved):
        with self.lock,self.engine.begin() as c:
            d=c.execute(text('SELECT * FROM remote_devices WHERE id=:id AND host_id=:h'),{'id':did,'h':hid}).mappings().first()
            if not d: raise ValueError('forbidden')
            target='authorized' if approved else 'rejected'
            if d['status']==target: return {'device_id':did,'status':target}
            if d['status']!='awaiting_confirmation': raise ValueError('conflict')
            if d['expires']<time.time(): raise ValueError('expired')
            c.execute(text('UPDATE remote_devices SET status=:s WHERE id=:id'),{'s':target,'id':did})
            return {'device_id':did,'status':target}
    def finish(self,did,pending):
        with self.lock,self.engine.begin() as c:
            d=c.execute(text('SELECT * FROM remote_devices WHERE id=:id AND pending=:p'),{'id':did,'p':digest(pending)}).mappings().first()
            if not d: raise ValueError('unauthorized')
            if d['status']=='awaiting_confirmation': raise ValueError('conflict' if d['expires']>=time.time() else 'expired')
            if d['status']!='authorized' or not self.enabled(d['host_id']): raise ValueError('unauthorized')
            if d['expires']<time.time(): raise ValueError('expired')
            token=hmac.new(pending.encode(),('device-cookie:'+did).encode(),hashlib.sha256).hexdigest(); csrf=d['csrf'] or secret()
            c.execute(text('UPDATE remote_devices SET credential=:t,csrf=:c WHERE id=:id'),{'t':digest(token),'c':csrf,'id':did})
            return token,dict(d,csrf=csrf)
    def device(self,token=None,did=None):
        with self.engine.connect() as c:
            query='id=:v' if did else 'credential=:v'
            d=c.execute(text('SELECT * FROM remote_devices WHERE '+query+" AND status='authorized'"),{'v':did or digest(token or '')}).mappings().first()
            return dict(d) if d and self.enabled(d['host_id']) else None
    def revoke(self,hid,did=None):
        with self.lock,self.engine.begin() as c:
            if did:
                if not c.execute(text('SELECT id FROM remote_devices WHERE id=:id AND host_id=:h'),{'id':did,'h':hid}).scalar(): raise ValueError('forbidden')
                c.execute(text("UPDATE remote_devices SET status='revoked',credential=NULL WHERE id=:id AND host_id=:h"),{'id':did,'h':hid})
            else:
                c.execute(text('UPDATE remote_hosts SET enabled=0 WHERE id=:h'),{'h':hid})
                c.execute(text("UPDATE remote_devices SET status='revoked',credential=NULL WHERE host_id=:h"),{'h':hid})
