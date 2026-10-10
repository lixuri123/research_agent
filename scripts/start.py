"""Supervise local API/Worker and an independent idle remote connector."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def stop_process(child):
    """Stop only a process we launched, including its Windows descendants."""
    if child.poll() is not None:
        return
    try:
        if os.name == 'nt':
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.wait(timeout=5)


def supervise(api_command, worker_command, cwd, ready, connector_command=None):
    children = []
    options = {'cwd': cwd}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    try:
        api = subprocess.Popen(api_command, **options)
        children.append(api)
        ready(api)
        worker = subprocess.Popen(worker_command, **options)
        children.append(worker)
        connector = None
        next_connector_start = 0.0
        print('API and Worker started. Press Ctrl+C to stop all services.', flush=True)
        while True:
            for name, child in (('API', api), ('Worker', worker)):
                code = child.poll()
                if code is not None:
                    print(f'{name} exited (code {code}); stopping all services.', file=sys.stderr)
                    return code or 1
            if connector_command is not None:
                if connector is not None and connector.poll() is not None:
                    print('Remote connector exited; restarting in 5 seconds.', file=sys.stderr)
                    children.remove(connector)
                    connector = None
                    next_connector_start = time.monotonic() + 5
                if connector is None and time.monotonic() >= next_connector_start:
                    try:
                        connector = subprocess.Popen(connector_command, **options)
                        children.append(connector)
                    except OSError:
                        print('Remote connector unavailable; retrying in 5 seconds.', file=sys.stderr)
                        next_connector_start = time.monotonic() + 5
            time.sleep(0.2)
    except KeyboardInterrupt:
        print('\nStopping all services...', flush=True)
        return 0
    finally:
        # Ignore repeated Ctrl+C while cleaning up our child processes.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            for child in reversed(children):
                try:
                    stop_process(child)
                except (OSError, subprocess.SubprocessError) as exc:
                    print(f'Could not stop process {child.pid}: {exc}', file=sys.stderr)
        finally:
            signal.signal(signal.SIGINT, previous)


def wait_ready(api, url, timeout=30):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if api.poll() is not None:
            raise RuntimeError('API exited during startup; see its error output above.')
        try:
            with opener.open(url + '/health', timeout=1) as response:
                if response.status == 200 and api.poll() is None:
                    return
        except (URLError, OSError):
            pass
        time.sleep(0.2)
    raise RuntimeError('API did not become ready within 30 seconds.')


def main():
    parser = argparse.ArgumentParser(description='Start the local API and Worker together.')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    for path, instruction in [
        (ROOT / '.env', 'Create .env from .env.example and configure your model service.'),
        (ROOT / 'frontend/dist/index.html', 'Run npm run build in frontend first.'),
    ]:
        if not path.is_file():
            print(instruction, file=sys.stderr)
            return 1
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.port))
    except OSError:
        print(f'Port {args.port} is occupied or unavailable. Stop the existing API or use --port.', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{args.port}'
    print(f'Workbench: {url}\nStarting services from {ROOT}', flush=True)
    api_command = [sys.executable, '-m', 'uvicorn', 'research_agent.api:app',
                   '--host', '127.0.0.1', '--port', str(args.port)]
    worker_command = [sys.executable, '-m', 'research_agent.worker']
    connector_command = [sys.executable, '-m', 'research_agent.remote.connector']
    # Shared by API management checks and the idle connector; never a credential.
    os.environ['REMOTE_LOCAL_PORT'] = str(args.port)
    try:
        return supervise(api_command, worker_command, ROOT, lambda api: wait_ready(api, url), connector_command)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Startup failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
