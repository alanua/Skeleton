#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

ENV_FILE = Path('/etc/skeleton-runner.env')
ALLOWED_KEYS = {
    'SKELETON_HOME_EDGE_01_PROFILE',
    'SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE',
    'SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE',
    'SKELETON_HOME_EDGE_01_TAILSCALE_IP',
    'SKELETON_HOME_EDGE_01_TARGET_USER',
}


def stop(reason: str) -> None:
    print('STATUS=BLOCKED')
    print(f'REASON={reason}')
    raise SystemExit(2)


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            parts = shlex.split(line, comments=False, posix=True)
        except ValueError:
            continue
        if len(parts) != 1 or '=' not in parts[0]:
            continue
        key, value = parts[0].split('=', 1)
        if key in ALLOWED_KEYS:
            out[key] = value
    return out


def env_from_file(path: Path) -> dict[str, str]:
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return {}
        return parse_env_text(path.read_text(encoding='utf-8'))
    except OSError:
        return {}


def env_from_live_runner(timeout_seconds: float = 20.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    uid = os.geteuid()
    while time.monotonic() < deadline:
        try:
            entries = list(Path('/proc').iterdir())
        except OSError:
            break
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                if b'runner_poll_github_tasks.py' not in (entry / 'cmdline').read_bytes():
                    continue
                raw = (entry / 'environ').read_bytes()
            except OSError:
                continue
            out: dict[str, str] = {}
            for item in raw.split(b'\0'):
                if b'=' not in item:
                    continue
                kb, vb = item.split(b'=', 1)
                try:
                    key, value = kb.decode(), vb.decode()
                except UnicodeDecodeError:
                    continue
                if key in ALLOWED_KEYS:
                    out[key] = value
            if out:
                return out
        time.sleep(0.25)
    return {}


def safe_regular(value: str, reason: str) -> str:
    try:
        p = Path(value)
        st = p.lstat()
    except OSError:
        stop(reason)
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        stop(reason)
    return str(p)


env = env_from_file(ENV_FILE)
needed = {'SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE', 'SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE'}
if not needed.issubset(env):
    env.update(env_from_live_runner())
if not needed.issubset(env):
    stop('RUNNER_RUNTIME_ENV_UNAVAILABLE')

profile: dict[str, object] = {}
profile_path = env.get('SKELETON_HOME_EDGE_01_PROFILE', '')
if profile_path:
    try:
        decoded = json.loads(Path(profile_path).read_text(encoding='utf-8'))
        if isinstance(decoded, dict):
            profile = decoded
    except Exception:
        stop('PROFILE_UNAVAILABLE')
ssh_cfg = profile.get('ssh') if isinstance(profile.get('ssh'), dict) else {}
identity_name = str(ssh_cfg.get('identity_env') or 'SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE')
known_name = str(ssh_cfg.get('known_hosts_env') or 'SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE')
identity = safe_regular(env.get(identity_name, ''), 'IDENTITY_UNAVAILABLE')
known_hosts = safe_regular(env.get(known_name, ''), 'KNOWN_HOSTS_UNAVAILABLE')
target_user = str(env.get('SKELETON_HOME_EDGE_01_TARGET_USER') or ssh_cfg.get('target_user') or '')
target_ip = str(env.get('SKELETON_HOME_EDGE_01_TAILSCALE_IP') or profile.get('tailscale_ip') or '')
if not target_user or not target_ip:
    stop('TARGET_UNAVAILABLE')

remote = r'''import json, os, pwd, shutil, subprocess, tempfile, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix='skeleton-apt-probe-', dir='/tmp'))
try:
    root.chmod(0o755)
    lists = root / 'lists'
    archives = root / 'archives'
    (lists / 'partial').mkdir(parents=True)
    (archives / 'partial').mkdir(parents=True)
    for p in (lists, lists/'partial', archives, archives/'partial'):
        p.chmod(0o755)

    source_count = 0
    candidates = [Path('/etc/apt/sources.list')]
    source_dir = Path('/etc/apt/sources.list.d')
    if source_dir.is_dir():
        candidates.extend(sorted(source_dir.glob('*.list')))
        candidates.extend(sorted(source_dir.glob('*.sources')))
    for p in candidates:
        try:
            if p.is_file() and not p.is_symlink() and p.stat().st_size > 0:
                source_count += 1
        except OSError:
            pass

    pm_count = 0
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc/'cmdline').read_bytes().replace(b'\0', b' ').lower()
        except OSError:
            continue
        if any(token in raw for token in (b'apt-get', b'/apt ', b'dpkg', b'unattended-upgrade')):
            pm_count += 1

    user = pwd.getpwuid(os.geteuid()).pw_name
    cmd = [
        '/usr/bin/apt-get',
        '-o', f'Dir::State::lists={lists}',
        '-o', f'Dir::Cache::archives={archives}',
        '-o', 'Debug::NoLocking=1',
        '-o', 'APT::Get::List-Cleanup=0',
        '-o', 'Acquire::Retries=0',
        '-o', 'Acquire::http::Timeout=20',
        '-o', 'Acquire::https::Timeout=20',
        '-o', f'APT::Sandbox::User={user}',
        'update',
    ]
    started = time.monotonic()
    try:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, check=False)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        cp = None
        timed_out = True
        text = ((exc.stdout or '') if isinstance(exc.stdout, str) else '') + '\n' + ((exc.stderr or '') if isinstance(exc.stderr, str) else '')
    duration = round(time.monotonic() - started, 2)
    if cp is not None:
        text = (cp.stdout or '') + '\n' + (cp.stderr or '')
        rc = cp.returncode
    else:
        rc = None
    low = text.lower()
    if timed_out:
        cls = 'TIMEOUT'
    elif rc == 0:
        cls = 'OK'
    elif 'temporary failure resolving' in low or 'could not resolve' in low or 'name or service not known' in low:
        cls = 'DNS'
    elif 'certificate verification failed' in low or 'certificate' in low and 'failed' in low or 'tls' in low and 'failed' in low:
        cls = 'TLS'
    elif 'no_pubkey' in low or 'signatures couldn' in low or 'is not signed' in low:
        cls = 'SIGNATURE_OR_KEY'
    elif 'does not have a release file' in low or '404' in low and 'release' in low:
        cls = 'REPOSITORY_RELEASE'
    elif 'could not connect' in low or 'connection timed out' in low or 'network is unreachable' in low or 'connection refused' in low:
        cls = 'NETWORK'
    elif 'permission denied' in low:
        cls = 'PERMISSION'
    elif 'malformed' in low or 'type is not known' in low:
        cls = 'SOURCE_SYNTAX'
    else:
        cls = 'OTHER'
    errors = sum(1 for line in text.splitlines() if line.startswith('Err:') or line.startswith('E:'))
    warnings = sum(1 for line in text.splitlines() if line.startswith('W:'))
    free_mib = shutil.disk_usage('/var/lib/apt/lists').free // (1024*1024)
    print(json.dumps({
        'rc': rc,
        'class': cls,
        'duration': duration,
        'timed_out': timed_out,
        'source_count': source_count,
        'package_manager_process_count': pm_count,
        'error_count': errors,
        'warning_count': warnings,
        'free_mib': free_mib,
    }, sort_keys=True, separators=(',',':')))
finally:
    shutil.rmtree(root, ignore_errors=True)
'''

cmd = [
    'ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
    '-o',f'UserKnownHostsFile={known_hosts}','-o','ConnectTimeout=10',
    '-i',identity,f'{target_user}@{target_ip}','python3','-'
]
try:
    cp = subprocess.run(cmd, input=remote, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=150, check=False)
except Exception:
    stop('TRANSPORT_FAILED')
if cp.returncode != 0:
    stop('TRANSPORT_FAILED')
try:
    data = json.loads(cp.stdout)
except Exception:
    stop('PROBE_INVALID')

print('STATUS=OK')
print(f"APT_UPDATE_RC={data.get('rc')}")
print(f"APT_UPDATE_CLASS={data.get('class')}")
print(f"APT_UPDATE_DURATION_SECONDS={data.get('duration')}")
print(f"APT_UPDATE_TIMED_OUT={data.get('timed_out')}")
print(f"APT_SOURCE_FILE_COUNT={data.get('source_count')}")
print(f"PACKAGE_MANAGER_PROCESS_COUNT={data.get('package_manager_process_count')}")
print(f"APT_ERROR_COUNT={data.get('error_count')}")
print(f"APT_WARNING_COUNT={data.get('warning_count')}")
print(f"APT_LISTS_FREE_MIB={data.get('free_mib')}")
cls = data.get('class')
if cls == 'OK':
    print('REASON=ISOLATED_APT_UPDATE_OK')
elif cls == 'TIMEOUT':
    print('REASON=APT_UPDATE_TIMEOUT')
else:
    print(f'REASON=APT_UPDATE_{cls}_FAILURE')
