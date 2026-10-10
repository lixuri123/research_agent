"""The launcher must never leave its own sibling service running."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock, call

import pytest


def launcher():
    path = Path(__file__).resolve().parents[2] / 'scripts/start.py'
    spec = importlib.util.spec_from_file_location('project_launcher', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process(code=None):
    child = Mock()
    child.poll.return_value = code
    return child


def test_service_failure_stops_sibling(tmp_path, monkeypatch):
    module = launcher()
    api, worker = process(), process(7)
    monkeypatch.setattr(module.subprocess, 'Popen', Mock(side_effect=[api, worker]))
    monkeypatch.setattr(module, 'stop_process', stop := Mock())
    ready = Mock()
    assert module.supervise(['api'], ['worker'], tmp_path, ready) == 7
    ready.assert_called_once_with(api)
    assert stop.call_args_list == [call(worker), call(api)]


def test_api_startup_failure_does_not_launch_worker(tmp_path, monkeypatch):
    module = launcher()
    api = process()
    spawn = Mock(return_value=api)
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    monkeypatch.setattr(module, 'stop_process', stop := Mock())
    ready = Mock(side_effect=RuntimeError('API did not start'))
    with pytest.raises(RuntimeError, match='API did not start'):
        module.supervise(['api'], ['worker'], tmp_path, ready)
    assert spawn.call_count == 1
    stop.assert_called_once_with(api)


def test_ctrl_c_during_worker_launch_stops_api(tmp_path, monkeypatch):
    module = launcher()
    api = process()
    monkeypatch.setattr(module.subprocess, 'Popen', Mock(side_effect=[api, KeyboardInterrupt]))
    monkeypatch.setattr(module, 'stop_process', stop := Mock())
    assert module.supervise(['api'], ['worker'], tmp_path, Mock()) == 0
    stop.assert_called_once_with(api)


def test_real_service_failure_reaps_sibling(tmp_path, monkeypatch):
    module = launcher()
    children = []
    original = module.subprocess.Popen

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    assert module.supervise(
        [sys.executable, '-c', 'import time; time.sleep(60)'],
        [sys.executable, '-c', 'raise SystemExit(7)'],
        tmp_path, Mock(),
    ) == 7
    assert len(children) == 2
    assert all(child.poll() is not None for child in children)
