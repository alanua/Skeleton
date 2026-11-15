"""Explicit disposable smoke; importing this module never imports Playwright or starts I/O."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import urllib.request
import uuid

LAB = Path(__file__).resolve().parent
FIXTURE = 'http://fixture:8080/challenge'
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def headroom(memory_bytes, disk_bytes, cpus):
    return memory_bytes >= 3584 * 1024**2 and disk_bytes >= 6 * 1024**3 and cpus >= 2


def preflight():
    """Read-only, sanitized observations. No environment or daemon output is emitted."""
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    cpus = len(os.sched_getaffinity(0))
    # Account for cgroup limits when available, including nested executor paths.
    for base in [Path('/sys/fs/cgroup')]:
        try:
            maximum = (base / 'memory.max').read_text().strip()
            if maximum != 'max':
                available = min(available, int(maximum) - int((base / 'memory.current').read_text()))
        except (OSError, ValueError):
            pass
        try:
            quota, period = (base / 'cpu.max').read_text().split()
            if quota != 'max':
                cpus = min(cpus, int(quota) / int(period))
        except (OSError, ValueError):
            pass
    disk = shutil.disk_usage(LAB).free
    # Fail closed until exact-artifact stealth defaults and executor endpoint policy
    # are reviewed and this source gate is intentionally updated. Pins alone are insufficient.
    causes = ['EXACT_ARTIFACT_AND_FIXTURE_ONLY_EGRESS_REVIEW_PENDING']
    engine = bool(shutil.which('docker'))
    permission = False
    if engine:
        try:
            permission = subprocess.run(['docker', 'info'], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not permission:
        causes.append('DOCKER_EXECUTABLE_MISSING' if not engine else 'DOCKER_DAEMON_UNAVAILABLE')
    if not headroom(available, disk, cpus):
        causes.append('INSUFFICIENT_HEADROOM')
    reachability = {}
    for name, url in {
        'github': 'https://api.github.com/repos/steel-dev/steel-browser/releases',
        'ghcr': 'https://ghcr.io/v2/', 'mcr': 'https://mcr.microsoft.com/v2/',
        'pypi': 'https://pypi.org/pypi/playwright/1.56.0/json',
    }.items():
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                reachability[name] = str(response.status)
        except urllib.error.HTTPError as exc:
            reachability[name] = str(exc.code)  # Registry auth challenge proves reachability.
        except OSError:
            reachability[name] = 'UNREACHABLE'
    if 'UNREACHABLE' in reachability.values():
        causes.append('OFFICIAL_DOWNLOAD_UNREACHABLE')
    for key in ('STEEL_DIGEST', 'CLIENT_DIGEST'):
        if not DIGEST.fullmatch(os.environ.get(key, '')):
            causes.append(key + '_NOT_REVIEWED')
    return dict(executor=platform.system(), architecture=platform.machine(),
                python=platform.python_version(), docker_present=engine, daemon_access=permission,
                memory_available_bytes=available, disk_free_bytes=disk, cpus=cpus,
                downloads=reachability, blockers=causes)


def api(method, path, body=None):
    # Paths are generated only by this program; never accept arbitrary URLs.
    request = urllib.request.Request('http://steel:3000/v1/' + path, method=method,
                                    data=None if body is None else json.dumps(body).encode(),
                                    headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = response.read()
        return json.loads(data) if data else None


def client_smoke():
    from playwright.sync_api import sync_playwright
    started = time.monotonic()
    checks = {}
    owned = None
    released = False
    try:
        for _ in range(60):
            try:
                api('GET', 'health')
                break
            except OSError:
                time.sleep(1)
        # Explicitly disable optional fingerprint/solver behavior; review against pinned schema.
        session = api('POST', 'sessions', {'skipFingerprintInjection': True,
                                          'blockAds': False,
                                          'timezone': 'UTC'})
        assert session.get('solveCaptcha', False) is False
        owned = session['id']
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', owned):
            raise ValueError('UNEXPECTED_SESSION_ID')
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp('http://steel:9223', timeout=15000)
            assert len(browser.contexts) == 1
            context = browser.contexts[0]
            page = context.new_page()
            page.set_default_timeout(10000)
            page.goto(FIXTURE)
            page.wait_for_function("document.querySelector('#rendered').textContent === 'JS_READY'")
            checks['chromium_js'] = 'PASS'
            checks['chromium_version'] = browser.version
            # No real external site: TEST-NET-1 address, never routed on the public Internet.
            try:
                page.goto('http://192.0.2.1:80/', timeout=3000)
            except Exception as exc:
                if 'net::ERR_' not in str(exc) and 'Timeout' not in type(exc).__name__:
                    raise
                checks['external_test_net_denied'] = 'PASS'
            else:
                raise AssertionError('EXTERNAL_NETWORK_REACHABLE')
            # Allow only fixture requests after the explicit network attempt.
            context.route('**/*', lambda route: route.continue_()
                          if route.request.url == FIXTURE else route.abort())
            page.goto(FIXTURE)
            page.fill('#draft', 'synthetic-draft')
            page.click('#save')
            assert page.locator('h1').inner_text() == 'Disposable synthetic challenge'
            assert page.screenshot().startswith(b'\x89PNG')  # In-memory synthetic evidence only.
            checks['navigation_click_fill_extraction_screenshot'] = 'PASS'
            other = pw.chromium.connect_over_cdp('http://steel:9223', timeout=15000)
            same = next(p for c in other.contexts for p in c.pages if p.url == FIXTURE)
            assert same.locator('#draft').input_value() == 'synthetic-draft'
            assert same.evaluate("localStorage.getItem('marker')") == 'synthetic-only'
            assert 'marker=synthetic-only' in same.evaluate('document.cookie')
            assert api('GET', 'sessions/' + owned)['id'] == owned
            checks['second_connection_same_session'] = 'PASS'
            isolated = browser.new_context()
            try:
                clean = isolated.new_page()
                clean.goto(FIXTURE)
                assert clean.evaluate("localStorage.getItem('marker')") is None
                assert clean.evaluate('document.cookie') == ''
                assert clean.locator('#draft').input_value() == ''
                checks['cdp_context_isolation'] = 'PASS'
            finally:
                isolated.close()
            # This is automation exercising a synthetic gate, never human acceptance.
            assert same.locator('section').get_attribute('data-reason') == 'manual_review'
            same.fill('#answer', 'synthetic-approved')
            same.click('#resume')
            same.click('#resume')
            assert same.locator('#counter').inner_text() == '1'
            assert api('GET', 'sessions/' + owned)['id'] == owned
            checks['synthetic_duplicate_continuation_noop'] = 'PASS'
            checks['independent_steel_sessions'] = 'NOT_VERIFIED_SINGLE_ACTIVE_BACKEND'
            checks['human_handoff'] = 'NOT_VERIFIED'
    finally:
        if owned:
            try:
                receipt = api('POST', 'sessions/' + owned + '/release', {})
                released = receipt.get('success') is True and receipt.get('status') == 'released'
                if not released:
                    raise AssertionError('RELEASE_NOT_CONFIRMED')
            finally:
                checks['release_request'] = 'PASS' if released else 'FAIL'
        print(json.dumps(dict(checks=checks, duration_seconds=round(time.monotonic()-started, 3))))


def smoke():
    # Lock the existing directory inode; no lock-file or shared host daemon mutation.
    with directory_lock():
        return _smoke()


class directory_lock:
    def __enter__(self):
        self.fd = os.open(LAB, os.O_RDONLY)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise RuntimeError('ONE_ACTIVE_EXPERIMENT_ONLY') from None

    def __exit__(self, *_):
        os.close(self.fd)


def _smoke():
    report = preflight()
    print(json.dumps(report, sort_keys=True))
    if report['blockers']:
        return 2
    # Unique project identity; never stop or remove preexisting projects.
    project = 'steel-probe-' + uuid.uuid4().hex[:12]
    command = ['docker', 'compose', '--project-name', project, '-f', str(LAB / 'compose.yaml')]
    deadline = time.monotonic() + 570  # Reserve 30 seconds for cleanup within 10 minutes.
    def run(args, timeout):
        remaining = min(timeout, max(0.01, deadline - time.monotonic()))
        return subprocess.run(command + args, cwd=LAB, capture_output=True, timeout=remaining)
    try:
        # Build/pull precede isolated runtime; no --build on up.
        for args in (['pull', 'steel'], ['build', 'fixture']):
            if run(args, 180).returncode:
                print('BLOCKED: OFFICIAL_PULL_OR_BUILD_FAILED (raw logs withheld)')
                return 2
        result = run(['up', '--no-build', '--abort-on-container-exit', '--exit-code-from', 'client'], 600)
        print('smoke_exit_code', result.returncode)
        # Never echo unfiltered Steel/CDP logs or bearer connection URLs.
        return result.returncode
    except subprocess.TimeoutExpired:
        print('BLOCKED: BOUNDED_TIMEOUT')
        return 2
    finally:
        try:
            cleanup = subprocess.run(command + ['down', '--volumes', '--remove-orphans', '--timeout', '10'],
                                     cwd=LAB, capture_output=True, timeout=30)
            print('owned_project_cleanup', 'PASS' if cleanup.returncode == 0 else 'FAIL')
        except (OSError, subprocess.TimeoutExpired):
            print('owned_project_cleanup FAIL')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['preflight', 'smoke', 'client'])
    mode = parser.parse_args().mode
    if mode == 'preflight':
        print(json.dumps(preflight(), sort_keys=True))
    elif mode == 'smoke':
        raise SystemExit(smoke())
    else:
        client_smoke()
