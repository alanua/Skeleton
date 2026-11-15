"""Source/config checks only. No test here claims Chromium integration success."""
from pathlib import Path
import importlib.util
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'tools/web_browser_probe'
spec = importlib.util.spec_from_file_location('web_browser_probe', LAB / 'probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_optional_runtime_import_safe():
    result = subprocess.run([sys.executable, '-c',
        "import runpy; runpy.run_path('tools/web_browser_probe/probe.py')"],
        cwd=ROOT, capture_output=True)
    assert result.returncode == 0
    assert not result.stdout


def test_headroom_fails_closed():
    assert probe.headroom(4 * 1024**3, 8 * 1024**3, 2)
    assert not probe.headroom(3 * 1024**3, 8 * 1024**3, 2)
    assert not probe.headroom(4 * 1024**3, 2 * 1024**3, 2)
    assert not probe.headroom(4 * 1024**3, 8 * 1024**3, 1)


def test_compose_boundaries():
    config = yaml.safe_load((LAB / 'compose.yaml').read_text())
    services = config['services']
    assert config['networks']['synthetic']['internal'] is True
    assert sum(float(s['cpus']) for s in services.values()) <= 2
    def mib(s):
        return int(s[:-1]) * (1024 if s.endswith('g') else 1)
    assert sum(mib(s['mem_limit']) for s in services.values()) <= 3072
    for service in services.values():
        assert not set(service) & {'ports', 'privileged', 'network_mode', 'volumes'}
        assert service['networks'] == ['synthetic']
        assert service['pids_limit'] <= 256
        assert service['cap_drop'] == ['ALL']
    assert '@${STEEL_DIGEST:?' in services['steel']['image']
    assert ':latest' not in (LAB / 'compose.yaml').read_text()


def test_fixture_has_typed_gate_and_duplicate_guard():
    body = (LAB / 'fixtures/challenge.html').read_text()
    assert 'data-reason="manual_review"' in body
    assert 'if(continued ||' in body
    assert 'JS_READY' in body
    assert 'localStorage' in body and 'document.cookie' in body


def test_missing_engine_and_downloads_are_not_success(monkeypatch):
    monkeypatch.setattr(probe.shutil, 'which', lambda _: None)
    def offline(*args, **kwargs):
        raise OSError('offline')
    monkeypatch.setattr(probe.urllib.request, 'urlopen', offline)
    monkeypatch.delenv('STEEL_DIGEST', raising=False)
    monkeypatch.delenv('CLIENT_DIGEST', raising=False)
    report = probe.preflight()
    assert 'DOCKER_EXECUTABLE_MISSING' in report['blockers']
    assert 'OFFICIAL_DOWNLOAD_UNREACHABLE' in report['blockers']
    assert 'STEEL_DIGEST_NOT_REVIEWED' in report['blockers']
    assert report['daemon_access'] is False
