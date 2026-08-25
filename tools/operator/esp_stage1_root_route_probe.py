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


def env_from_live_runner(timeout_seconds: float = 60.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    uid = os.geteuid()
    while time.monotonic() < deadline:
        for entry in Path('/proc').iterdir():
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

remote = r'''import json, os, subprocess

def rc(argv):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        return p.returncode
    except Exception:
        return None

result = {
    'remote_uid_is_root': os.geteuid() == 0,
    'sudo_root_bash_ok': rc(['/usr/bin/sudo','--non-interactive','--','bash','-c','exit 0']) == 0,
    'sudo_root_installer_prereq_ok': rc(['/usr/bin/sudo','--non-interactive','--','bash','-c','test "$(hostname)" = home-edge-01 && test -x /usr/bin/python3 && test -x /usr/bin/apt-get && test -d /sys/class/tty']) == 0,
    'home_edge_exec_present': os.path.isfile('/usr/local/bin/home_edge_exec'),
    'opt_skeleton_present': os.path.isdir('/opt/skeleton'),
    'usr_local_bin_present': os.path.isdir('/usr/local/bin'),
}
print(json.dumps(result, sort_keys=True, separators=(',',':')))
'''
cmd = [
    'ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
    '-o',f'UserKnownHostsFile={known_hosts}','-o','ConnectTimeout=10',
    '-i',identity,f'{target_user}@{target_ip}','python3','-'
]
try:
    cp = subprocess.run(cmd, input=remote, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=40, check=False)
except Exception:
    stop('TRANSPORT_FAILED')
if cp.returncode != 0:
    stop('TRANSPORT_FAILED')
try:
    data = json.loads(cp.stdout)
except Exception:
    stop('PROBE_INVALID')

print('STATUS=OK')
for key in ('remote_uid_is_root','sudo_root_bash_ok','sudo_root_installer_prereq_ok','home_edge_exec_present','opt_skeleton_present','usr_local_bin_present'):
    print(f'{key.upper()}={data.get(key)}')
if data.get('remote_uid_is_root') or data.get('sudo_root_bash_ok'):
    print('REASON=ROOT_ROUTE_AVAILABLE')
else:
    print('REASON=ROOT_ROUTE_UNAVAILABLE')
