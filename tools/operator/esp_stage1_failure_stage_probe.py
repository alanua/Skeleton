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
                    key, value = kb.decode('utf-8'), vb.decode('utf-8')
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
        p = Path(profile_path)
        st = p.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            stop('PROFILE_UNSAFE')
        decoded = json.loads(p.read_text(encoding='utf-8'))
        if isinstance(decoded, dict):
            profile = decoded
    except (OSError, json.JSONDecodeError):
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

remote = r'''from __future__ import annotations
import json, os, stat, subprocess
from pathlib import Path

MAX_LOG_BYTES = 1024 * 1024

def rc(argv, timeout=15):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return p.returncode
    except Exception:
        return None

def safe_fixed_file(path_text):
    p = Path(path_text)
    try:
        st = p.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode) and st.st_uid == 0 and st.st_gid == 0 and (stat.S_IMODE(st.st_mode) & 0o022) == 0

def tail_text(path_text):
    p = Path(path_text)
    try:
        st = p.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return ''
        with p.open('rb') as fh:
            if st.st_size > MAX_LOG_BYTES:
                fh.seek(st.st_size - MAX_LOG_BYTES)
            return fh.read(MAX_LOG_BYTES).decode('utf-8', errors='replace')
    except OSError:
        return ''

apt_history = tail_text('/var/log/apt/history.log')
dpkg_log = tail_text('/var/log/dpkg.log')
canonical_sudo = rc(['/usr/bin/sudo','-n','-l','--','/usr/local/sbin/home_edge_exec_root','--server']) == 0
pkg_installed = rc(['/usr/bin/dpkg-query','-W','-f=${Status}','esptool']) == 0

apt_install_count = apt_history.count('apt-get install -y --no-install-recommends esptool')
apt_remove_count = apt_history.count('apt-get remove -y esptool')
# Debian apt history may record Commandline with or without an explicit apt-get prefix.
apt_install_count += apt_history.count('Commandline: /usr/bin/apt-get install -y --no-install-recommends esptool')
apt_remove_count += apt_history.count('Commandline: /usr/bin/apt-get remove -y esptool')
# Avoid double counting by converting to booleans below; dpkg log is a separate corroborating source.
dpkg_install = (' install esptool:' in dpkg_log) or (' status installed esptool:' in dpkg_log)
dpkg_remove = (' remove esptool:' in dpkg_log) or (' status not-installed esptool:' in dpkg_log)

result = {
    'canonical_sudo_allowed': canonical_sudo,
    'home_edge_exec_safe': safe_fixed_file('/usr/local/bin/home_edge_exec'),
    'root_wrapper_safe': safe_fixed_file('/usr/local/sbin/home_edge_exec_root'),
    'apt_history_install_seen': apt_install_count > 0,
    'apt_history_remove_seen': apt_remove_count > 0,
    'dpkg_install_seen': dpkg_install,
    'dpkg_remove_seen': dpkg_remove,
    'esptool_package_installed_current': pkg_installed,
}
print(json.dumps(result, sort_keys=True, separators=(',',':')))
'''

cmd = [
    'ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'ConnectTimeout=10',
    '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
    '-i', identity, f'{target_user}@{target_ip}', 'python3', '-',
]
try:
    cp = subprocess.run(cmd, input=remote, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45, check=False)
except Exception:
    stop('TRANSPORT_FAILED')
if cp.returncode != 0:
    stop('TRANSPORT_FAILED')
try:
    data = json.loads(cp.stdout)
except json.JSONDecodeError:
    stop('PROBE_INVALID')
if not isinstance(data, dict):
    stop('PROBE_INVALID')

install_seen = bool(data.get('apt_history_install_seen') or data.get('dpkg_install_seen'))
remove_seen = bool(data.get('apt_history_remove_seen') or data.get('dpkg_remove_seen'))
if not data.get('home_edge_exec_safe') or not data.get('root_wrapper_safe'):
    reason = 'EXECUTOR_INSTALL_INCOMPLETE'
elif not data.get('canonical_sudo_allowed'):
    reason = 'CANONICAL_ROOT_ROUTE_UNAVAILABLE'
elif not install_seen:
    reason = 'APT_UPDATE_OR_INSTALL_FAILED_BEFORE_PACKAGE_COMMIT'
elif install_seen and remove_seen and not data.get('esptool_package_installed_current'):
    reason = 'POST_APT_FAILURE_ROLLED_BACK'
elif install_seen and data.get('esptool_package_installed_current'):
    reason = 'PACKAGE_PRESENT_BUT_ACTIVATION_FAILED_LATER'
else:
    reason = 'PACKAGE_HISTORY_INCONSISTENT'

print('STATUS=OK')
for key in (
    'canonical_sudo_allowed', 'home_edge_exec_safe', 'root_wrapper_safe',
    'apt_history_install_seen', 'apt_history_remove_seen', 'dpkg_install_seen',
    'dpkg_remove_seen', 'esptool_package_installed_current',
):
    print(f'{key.upper()}={data.get(key)}')
print(f'REASON={reason}')
