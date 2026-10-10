"""Protected connector credential file, outside user workspace by default."""
import ctypes
import json
import os
from pathlib import Path

class CredentialFile:
    def __init__(self,path=None,workspace_roots=()):
        self.path=Path(path) if path else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'ResearchAgent'/'remote-host.credential'
        candidate=self.path.resolve()
        roots=[Path(root).resolve() for root in workspace_roots]
        if path is None: roots.append(Path(__file__).resolve().parents[2])
        if any(candidate.is_relative_to(root) for root in roots): raise ValueError('Credential must be outside workspace roots')
    def _protect(self,data,decrypt=False):
        if os.name!='nt': return data
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_char))]
        buf=ctypes.create_string_buffer(data); source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_char))); out=Blob()
        fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
        if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)): raise OSError('Windows credential protection failed')
        try: return ctypes.string_at(out.data,out.size)
        finally: ctypes.windll.kernel32.LocalFree(out.data)
    def read(self):
        if not self.path.exists(): return None
        return json.loads(self._protect(self.path.read_bytes(),True))
    def write(self,value):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        target=self.path.with_suffix('.tmp')
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'wb') as f: f.write(self._protect(json.dumps(value).encode()))
        os.replace(target,self.path)
        if os.name!='nt': self.path.chmod(0o600)

