"""First phase: bounded fixed search on screened snapshots, never original writes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from research_agent.execution.policy import digest, search_arguments
from research_agent.execution.storage import ExecutionStore
from research_agent.execution.providers.windows_acl import WindowsACLProvider
from research_agent.workspace.service import linked


class ExecutionService:
    def __init__(self, workspaces, settings, provider=None):
        self.workspaces, self.settings = workspaces, settings
        self.store = ExecutionStore(workspaces.engine, workspaces.owner_id)
        self.provider = provider
        self.root = Path(settings.execution_root).absolute()

    def _provider(self, workspace_id=None):
        root = self.workspaces.get(workspace_id)['root'] if workspace_id else None
        return self.provider or WindowsACLProvider(self.settings, root)

    def status(self, workspace_id=None):
        if not self.settings.execution_enabled:
            return {'available': False, 'backend': 'windows-acl', 'enforcement': 'partial',
                'file_read': 'not_isolated', 'network': 'not_isolated', 'error': '服务端 EXECUTION_ENABLED 未开启'}
        return self._provider(workspace_id).status()

    def permission(self, workspace_id):
        workspace = self.workspaces.get(workspace_id)
        return {'enabled': self.store.permission(workspace_id), 'agent_read': workspace['agent_read'],
                'backend': self.status(workspace_id)}

    def set_permission(self, workspace_id, enabled):
        workspace = self.workspaces.get(workspace_id)
        if type(enabled) is not bool:
            raise ValueError('enabled 必须为布尔值')
        if enabled and not workspace['agent_read']:
            raise PermissionError('工作空间须先授权 Agent 读取')
        self.store.set_permission(workspace_id, enabled)
        return self.permission(workspace_id)

    def _guard(self, workspace_id, run_id, worker_id, check_lease):
        if check_lease:
            check_lease()
        if not self.settings.execution_enabled:
            raise PermissionError('服务端未启用本地受限执行')
        if not self.workspaces.get(workspace_id)['agent_read']:
            raise PermissionError('工作空间未授权 Agent 读取')
        with self.store.transaction() as conn:
            self.store.guard(conn, workspace_id, run_id, worker_id)

    @contextmanager
    def _lock(self, identity):
        if any(linked(path) for path in [self.root, *self.root.parents]):
            raise ValueError('执行内部目录不能包含链接')
        self.root.mkdir(parents=True, exist_ok=True)
        locks = self.root / 'locks'
        locks.mkdir(exist_ok=True)
        path = locks / (identity + '.lock')
        if linked(locks) or linked(path):
            raise ValueError('执行锁路径不能包含链接')
        with path.open('a+b') as stream:
            os.set_inheritable(stream.fileno(), False)
            if path.stat().st_nlink > 1:
                raise ValueError('执行锁不能是硬链接')
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise ValueError('同一步骤仍在执行，不能并行重试') from exc
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _snapshot(self, workspace_id, inputs, guard):
        manifest, directories = [], ['']
        total, visited, skipped, truncated = 0, 0, 0, False
        deadline = time.monotonic() + 5
        workspace_root = Path(self.workspaces.get(workspace_id)['root'])
        while directories:
            guard()
            if time.monotonic() >= deadline or visited >= 1000 or len(manifest) >= 100:
                truncated = True
                break
            directory = directories.pop(0)
            listing = self.workspaces.entries(workspace_id, directory)
            truncated |= listing['truncated']
            for entry in listing['entries']:
                guard()
                if time.monotonic() >= deadline or visited >= 1000 or len(manifest) >= 100:
                    truncated = True
                    break
                visited += 1
                path = entry['path']
                if (workspace_root / path).resolve().is_relative_to(self.root.resolve()):
                    skipped += 1
                    continue
                if entry['kind'] == 'directory':
                    directories.append(path)
                    continue
                if not entry['text']:
                    skipped += 1
                    continue
                try:
                    item = self.workspaces.read(workspace_id, path)
                except (ValueError, OSError):
                    skipped += 1
                    continue
                data = item['content'].encode('utf-8')
                if total + len(data) > 4 * 1024 * 1024:
                    truncated = True
                    break
                name = f'{len(manifest):05d}.txt'
                (inputs / name).write_bytes(data)
                if self.workspaces.read(workspace_id, path)['version'] != item['version']:
                    raise ValueError('准备材料时原文件已改变，未执行检索')
                manifest.append({'input': name, 'path': path, 'version': item['version'], 'size': len(data)})
                total += len(data)
            if total >= 4 * 1024 * 1024:
                truncated = True
                break
        return manifest, {'truncated': truncated, 'skipped': skipped, 'visited': visited}

    def search(self, workspace_id, run_id, worker_id, operation_id, arguments, check_lease=None):
        arguments = search_arguments(arguments)
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 255:
            raise ValueError('执行必须绑定有效的 Agent 步骤身份')
        guard = lambda: self._guard(workspace_id, run_id, worker_id, check_lease)
        guard()
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, digest([self.workspaces.owner_id, run_id, operation_id])))
        request_hash = digest([workspace_id, arguments, 'search-read-only-v1'])
        with self._lock(identity):
            saved = self.store.prepare(identity, workspace_id, run_id, worker_id, operation_id, request_hash, arguments)
            if saved is not None:
                guard()
                return saved
            result = {'id': identity, 'operation': 'workspace_search_text', 'mode': 'read-only',
                'enforcement': 'partial', 'file_read': 'not_isolated', 'network': 'not_isolated',
                'matches': [], 'manifest': [], 'status': 'failed', 'error': None, 'truncated': False}
            directory = None
            try:
                provider = self._provider(workspace_id)
                status = self.status(workspace_id)
                if not status['available']:
                    result.update(status='unavailable', error=status.get('error') or 'SANDBOX_UNAVAILABLE')
                else:
                    directory = Path(tempfile.mkdtemp(prefix=identity + '-', dir=self.root))
                    inputs = directory / 'inputs'
                    inputs.mkdir()
                    manifest, preparation = self._snapshot(workspace_id, inputs, guard)
                    result.update(manifest=manifest, preparation=preparation, truncated=preparation['truncated'])
                    self.store.started(identity, workspace_id, run_id, worker_id, manifest)
                    guard()
                    if not manifest:
                        result['status'] = 'succeeded'
                        result['notice'] = '没有符合授权与文本大小限制的输入文件'
                    else:
                        outcome = provider.search(inputs, arguments['query'], arguments['max_results'], guard)
                        guard()
                        result.update(status=outcome['status'], exit_code=outcome.get('exit_code'),
                            error=outcome.get('error'), truncated=result['truncated'] or outcome.get('truncated', False))
                        by_input = {item['input']: item for item in manifest}
                        for line in outcome.get('stdout', '').splitlines():
                            try:
                                match = json.loads(line)
                            except ValueError:
                                result['truncated'] = True
                                continue
                            if match.get('type') != 'match':
                                continue
                            data = match['data']
                            name = data.get('path', {}).get('text', '').replace('\\', '/').removeprefix('./')
                            source = by_input.get(name)
                            if source is None:
                                raise ValueError('执行输出引用了输入清单之外的文件')
                            if len(result['matches']) >= arguments['max_results']:
                                result['truncated'] = True
                                break
                            result['matches'].append({'path': source['path'], 'version': source['version'],
                                'line': data.get('line_number'), 'text': data.get('lines', {}).get('text', '')})
                        # rg's max-count is per file; reaching the cap cannot
                        # establish that there were no more matches in that file.
                        if len(result['matches']) >= arguments['max_results']:
                            result['truncated'] = True
                        # Helper diagnostics may reference private temp paths; never send raw logs to model.
                        if outcome.get('stderr'):
                            result['diagnostic'] = '固定检索程序返回了诊断输出；未展开内部路径'
            except Exception as exc:
                result.update(status='failed', error=f'{type(exc).__name__}: {str(exc)[:1000]}')
            finally:
                if directory is not None:
                    target = directory.resolve()
                    if not target.is_relative_to(self.root.resolve()) or target == self.root.resolve() or linked(directory):
                        result['cleanup_error'] = '执行副本目录身份改变，未自动删除'
                    else:
                        try:
                            shutil.rmtree(target)
                        except OSError:
                            result['cleanup_error'] = '执行副本清理失败；未影响原文件'
                self.store.finish(identity, result)
            guard()  # Do not publish an observation after authorization/lease loss.
            return result

    def list(self, workspace_id, run_id):
        self.workspaces.get(workspace_id)
        return self.store.list(workspace_id, run_id)
