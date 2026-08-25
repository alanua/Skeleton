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


def env_from_live_runner(timeout_seconds: float = 30.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    uid = os.geteuid()
    while time.monotonic() < deadline:
        try:
            entries = list(Path('/proc').iterdir())
        except OSError:
            entries = []
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
        time.sleep(0.5)
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

remote = r'''import json, os, shutil, subprocess
from pathlib import Path

PKG_NAMES = {
    'apt', 'apt-get', 'dpkg', 'dpkg-deb', 'unattended-upgr',
    'unattended-upgrade', 'unattended-upgrades', 'packagekitd',
}
LOCK_PATHS = {
    '/var/lib/dpkg/lock',
    '/var/lib/dpkg/lock-frontend',
    '/var/cache/apt/archives/lock',
    '/var/lib/apt/lists/lock',
}
UNITS = ('apt-daily.service', 'apt-daily-upgrade.service', 'unattended-upgrades.service')

def run(argv, timeout=10):
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    except Exception:
        return None

names = set()
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        comm = (entry / 'comm').read_text(encoding='utf-8').strip()
    except OSError:
        continue
    if comm in PKG_NAMES or comm.startswith('unattended-upgr'):
        names.add(comm)

units = {}
for unit in UNITS:
    cp = run(['/usr/bin/systemctl', 'is-active', unit])
    units[unit] = bool(cp and cp.returncode == 0 and cp.stdout.strip() == 'active')

lock_commands = set()
lock_count = 0
lslocks = shutil.which('lslocks')
if lslocks:
    cp = run([lslocks, '--json', '--output', 'COMMAND,PID,PATH'])
    if cp and cp.returncode == 0:
        try:
            payload = json.loads(cp.stdout)
        except Exception:
            payload = {}
        rows = payload.get('locks') if isinstance(payload, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                path = row.get('path')
                if path in LOCK_PATHS:
                    lock_count += 1
                    command = row.get('command')
                    if isinstance(command, str) and command:
                        lock_commands.add(command)

dpkg = run(['/usr/bin/dpkg', '--audit'])
dpkg_audit_rc = None if dpkg is None else dpkg.returncode
dpkg_audit_nonempty = bool(dpkg and (dpkg.stdout.strip() or dpkg.stderr.strip()))

result = {
    'package_manager_names': sorted(names),
    'package_manager_process_count': len(names),
    'lock_count': lock_count,
    'lock_commands': sorted(lock_commands),
    'apt_daily_active': units['apt-daily.service'],
    'apt_daily_upgrade_active': units['apt-daily-upgrade.service'],
    'unattended_upgrades_active': units['unattended-upgrades.service'],
    'dpkg_audit_rc': dpkg_audit_rc,
    'dpkg_audit_nonempty': dpkg_audit_nonempty,
}
print(json.dumps(result, sort_keys=True, separators=(',', ':')))
'''

cmd = [
    'ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'ConnectTimeout=10',
    '-i', identity, f'{target_user}@{target_ip}', 'python3', '-'
]
try:
    cp = subprocess.run(cmd, input=remote, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45, check=False)
except Exception:
    stop('TRANSPORT_FAILED')
if cp.returncode != 0:
    stop('TRANSPORT_FAILED')
try:
    data = json.loads(cp.stdout)
except Exception:
    stop('PROBE_INVALID')

print('STATUS=OK')
print(f"PACKAGE_MANAGER_PROCESS_COUNT={data.get('package_manager_process_count')}")
print('PACKAGE_MANAGER_NAMES=' + ','.join(data.get('package_manager_names') or []))
print(f"APT_LOCK_COUNT={data.get('lock_count')}")
print('APT_LOCK_COMMANDS=' + ','.join(data.get('lock_commands') or []))
print(f"APT_DAILY_ACTIVE={data.get('apt_daily_active')}")
print(f"APT_DAILY_UPGRADE_ACTIVE={data.get('apt_daily_upgrade_active')}")
print(f"UNATTENDED_UPGRADES_ACTIVE={data.get('unattended_upgrades_active')}")
print(f"DPKG_AUDIT_RC={data.get('dpkg_audit_rc')}")
print(f"DPKG_AUDIT_NONEMPTY={data.get('dpkg_audit_nonempty')}")

busy = bool(
    data.get('package_manager_process_count')
    or data.get('lock_count')
    or data.get('apt_daily_active')
    or data.get('apt_daily_upgrade_active')
)
if busy:
    print('REASON=PACKAGE_MANAGER_BUSY')
elif data.get('dpkg_audit_rc') not in (0, None) or data.get('dpkg_audit_nonempty'):
    print('REASON=DPKG_STATE_NEEDS_ATTENTION')
else:
    print('REASON=APT_LOCK_CLEAR')
