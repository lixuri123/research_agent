"""A trusted helper owns target stdio and the restricted process Job."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from research_agent.workspace.service import linked


class WindowsACLProvider:
    def __init__(self, settings, workspace_root=None):
        self.settings = settings
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.helper = Path(settings.execution_helper_path or str(Path(__file__).resolve().parents[3] /
            'native/windows-sandbox/bin/research-sandbox.exe')).absolute()
        self.rg = Path(settings.execution_rg_path or shutil.which('rg') or 'missing-rg.exe').absolute()

    def _paths(self):
        if os.name != 'nt':
            raise RuntimeError('首版仅支持 Windows ACL 后端')
        for path in (self.helper, self.rg):
            if not path.is_file() or any(linked(part) for part in [path, *path.parents]):
                raise RuntimeError('缺少可信 Windows helper/rg，或程序路径含链接')
            if self.workspace_root and path.resolve().is_relative_to(self.workspace_root):
                raise RuntimeError('执行程序不能位于当前业务工作空间内，请配置独立受信安装路径')
            execution_root = Path(getattr(self.settings, 'execution_root', 'data/execution')).resolve()
            if path.resolve().is_relative_to(execution_root):
                raise RuntimeError('执行程序不能位于执行内部目录内，请配置独立受信安装路径')
        if self.rg.name.lower() != 'rg.exe':
            raise RuntimeError('仅允许配置固定 rg.exe 程序')

    def _environment(self):
        return {key: value for key, value in os.environ.items()
                if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC'}}

    def _invoke(self, argv, timeout, check_lease):
        # Parent EOF is cancellation; helper's noninherited Job closes on death.
        # Match dsh's console-preserving startup; hide initial windows without
        # depending on CREATE_NO_WINDOW initialization semantics.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen([str(self.helper), *argv], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._environment(),
            close_fds=True, startupinfo=startup)
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()
        max_envelope = self.settings.execution_output_bytes * 8 + 32768

        def drain(stream, target):
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                if len(target) + len(chunk) > max_envelope:
                    overflow.set()
                target.extend(chunk[:max(0, max_envelope - len(target))])

        readers = [threading.Thread(target=drain, args=(stream, target), daemon=True)
                   for stream, target in zip((process.stdout, process.stderr), buffers)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                check_lease()
                if overflow.is_set() or time.monotonic() >= deadline:
                    raise RuntimeError('执行器协议输出超限或未按时结束')
                time.sleep(0.05)
        finally:
            if process.stdin:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for reader in readers:
                reader.join(timeout=3)
            for stream in (process.stdout, process.stderr):
                stream.close()
        if overflow.is_set() or any(reader.is_alive() for reader in readers):
            raise RuntimeError('执行器协议输出无效')
        if process.returncode != 0:
            raise RuntimeError('Windows helper 失败；未降级为普通进程')
        value = json.loads(bytes(buffers[0]).decode('utf-8'))
        if not isinstance(value, dict) or value.get('protocol') != 1 or value.get('backend') != 'windows-acl' or value.get('mode') != 'read-only' or value.get('enforcement') != 'partial' or value.get('file_read') != 'not_isolated' or value.get('network') != 'not_isolated':
            raise RuntimeError('执行器未报告预期的真实权限边界')
        if value.get('status') not in {'succeeded', 'failed', 'timeout', 'unavailable'}:
            raise RuntimeError('执行器返回未知状态')
        for key in ('stdout', 'stderr'):
            if not isinstance(value.get(key), str):
                raise RuntimeError('执行器日志协议无效')
        if sum(len(value[key].encode('utf-8')) for key in ('stdout', 'stderr')) > self.settings.execution_output_bytes:
            raise RuntimeError('执行器未遵守日志上限')
        return value

    def status(self):
        result = {'available': False, 'backend': 'windows-acl', 'enforcement': 'partial',
                  'file_read': 'not_isolated', 'network': 'not_isolated', 'error': None}
        try:
            self._paths()
            probe = self._invoke(['--probe'], 5, lambda: None)
            if probe['status'] != 'succeeded':
                raise RuntimeError(probe.get('error') or 'Windows 受限令牌/Job 不可用')
            result['available'] = True
        except (OSError, ValueError, RuntimeError) as exc:
            result['error'] = str(exc)[:1000]
        return result

    def search(self, inputs, query, limit, check_lease):
        self._paths()
        check_lease()
        return self._invoke(['--search', str(self.rg), '--input', str(inputs), '--query', query,
            '--limit', str(limit), '--timeout-ms', str(int(self.settings.execution_timeout_seconds * 1000)),
            '--output-limit', str(self.settings.execution_output_bytes)],
            self.settings.execution_timeout_seconds + 5, check_lease)
