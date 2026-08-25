#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

ENV_FILE = Path("/etc/skeleton-runner.env")
SOURCE_SHA = "725dfc3aedbce194c7afcc229eb44b1eec4f463a"
ALLOWED_KEYS = {
    "SKELETON_HOME_EDGE_01_PROFILE",
    "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
    "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
    "SKELETON_HOME_EDGE_01_TAILSCALE_IP",
    "SKELETON_HOME_EDGE_01_TARGET_USER",
}


def stop(reason: str) -> None:
    print("STATUS=BLOCKED")
    print(f"REASON={reason}")
    raise SystemExit(2)


def parse_env_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=False, posix=True)
        except ValueError:
            continue
        if len(parts) != 1 or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        if key in ALLOWED_KEYS:
            result[key] = value
    return result


def env_from_file(path: Path) -> dict[str, str]:
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return {}
        return parse_env_text(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def env_from_live_runner(timeout_seconds: float = 70.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    uid = os.geteuid() if hasattr(os, "geteuid") else None
    while time.monotonic() < deadline:
        try:
            proc_entries = sorted(Path("/proc").iterdir(), key=lambda p: p.name)
        except OSError:
            proc_entries = []
        for entry in proc_entries:
            if not entry.name.isdigit():
                continue
            try:
                if uid is not None and entry.stat().st_uid != uid:
                    continue
                cmdline = (entry / "cmdline").read_bytes()
                if b"runner_poll_github_tasks.py" not in cmdline:
                    continue
                raw = (entry / "environ").read_bytes()
            except OSError:
                continue
            result: dict[str, str] = {}
            for item in raw.split(b"\0"):
                if not item or b"=" not in item:
                    continue
                key_b, value_b = item.split(b"=", 1)
                try:
                    key = key_b.decode("utf-8")
                    value = value_b.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if key in ALLOWED_KEYS:
                    result[key] = value
            if result:
                return result
        time.sleep(0.5)
    return {}


def safe_regular(path_text: str, reason: str) -> str:
    if not path_text:
        stop(reason)
    path = Path(path_text)
    try:
        st = path.lstat()
    except OSError:
        stop(reason)
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        stop(reason)
    return str(path)


env = env_from_file(ENV_FILE)
required_direct = {
    "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
    "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
}
if not required_direct.issubset(env):
    env.update(env_from_live_runner())
if not required_direct.issubset(env):
    stop("RUNNER_RUNTIME_ENV_UNAVAILABLE")

profile: dict[str, object] = {}
profile_path = env.get("SKELETON_HOME_EDGE_01_PROFILE", "")
if profile_path:
    try:
        p = Path(profile_path)
        st = p.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            stop("PROFILE_UNSAFE")
        decoded = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            stop("PROFILE_INVALID")
        profile = decoded
    except (OSError, json.JSONDecodeError):
        stop("PROFILE_UNAVAILABLE")

ssh_cfg = profile.get("ssh") if isinstance(profile.get("ssh"), dict) else {}
identity_env_name = str(ssh_cfg.get("identity_env") or "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE")
known_env_name = str(ssh_cfg.get("known_hosts_env") or "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE")
identity = safe_regular(env.get(identity_env_name, ""), "IDENTITY_UNAVAILABLE")
known_hosts = safe_regular(env.get(known_env_name, ""), "KNOWN_HOSTS_UNAVAILABLE")
target_user = str(env.get("SKELETON_HOME_EDGE_01_TARGET_USER") or ssh_cfg.get("target_user") or "")
tailscale_ip = str(env.get("SKELETON_HOME_EDGE_01_TAILSCALE_IP") or profile.get("tailscale_ip") or "")
if not target_user or not tailscale_ip:
    stop("TARGET_UNAVAILABLE")

remote = f'''from __future__ import annotations
import json, shutil, stat, subprocess
from pathlib import Path
SOURCE_SHA={SOURCE_SHA!r}

def run(argv, timeout=20):
    try:
        cp=subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
        return cp.returncode, cp.stdout
    except Exception:
        return None, ""

def meta(path_text):
    p=Path(path_text)
    try:
        st=p.lstat()
    except OSError:
        return {{"exists":False,"safe":True}}
    kind="dir" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "other"
    safe=(kind in {{"dir","file"}} and not stat.S_ISLNK(st.st_mode) and st.st_uid==0 and st.st_gid==0 and (stat.S_IMODE(st.st_mode)&0o022)==0)
    return {{"exists":True,"safe":safe,"kind":kind}}

os_id=""; os_version=""
try:
    for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
        if '=' not in line: continue
        k,v=line.split('=',1); v=v.strip().strip('\\"')
        if k=='ID': os_id=v
        elif k=='VERSION_ID': os_version=v
except OSError:
    pass
host_rc, host_out=run(['hostname'])
apt_sim_rc,_=run(['/usr/bin/apt-get','-s','-o','Debug::NoLocking=1','install','esptool'], timeout=60) if Path('/usr/bin/apt-get').exists() else (None,'')
dpkg_rc,_=run(['/usr/bin/dpkg-query','-W','-f=${{Status}}','esptool']) if Path('/usr/bin/dpkg-query').exists() else (None,'')
wrapper='/usr/local/bin/skeleton-esp-lab'
wrapper_canary='not_present'; candidate_count=None
wm=meta(wrapper)
if wm.get('exists') and wm.get('safe') and wm.get('kind')=='file':
    rc,out=run([wrapper,'discover','--sysfs-root','/sys/class/tty'], timeout=20)
    if rc==0:
        try:
            data=json.loads(out)
            if isinstance(data,list):
                wrapper_canary='ok'; candidate_count=len(data)
            else:
                wrapper_canary='bad_output'
        except Exception:
            wrapper_canary='bad_output'
    else:
        wrapper_canary='failed'
result={{
  'hostname_ok': host_rc==0 and host_out.strip()=='home-edge-01',
  'os_ok': os_id=='debian' and os_version=='13',
  'python3_present': bool(shutil.which('python3')),
  'apt_get_present': Path('/usr/bin/apt-get').is_file(),
  'apt_esptool_simulation_ok': apt_sim_rc==0,
  'esptool_present': bool(shutil.which('esptool')),
  'esptool_py_present': bool(shutil.which('esptool.py')),
  'esptool_package_installed': dpkg_rc==0,
  'runtime_base': meta('/opt/skeleton/esp-lab'),
  'runtime_target': meta('/opt/skeleton/esp-lab/'+SOURCE_SHA),
  'wrapper': wm,
  'wrapper_canary': wrapper_canary,
  'candidate_count': candidate_count,
}}
print(json.dumps(result,sort_keys=True,separators=(',',':')))
'''

cmd = [
    "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
    "-i", identity, f"{target_user}@{tailscale_ip}", "python3", "-",
]
try:
    cp = subprocess.run(cmd, input=remote, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90, check=False)
except Exception:
    stop("TRANSPORT_FAILED")
if cp.returncode != 0:
    stop("TRANSPORT_FAILED")
try:
    data = json.loads(cp.stdout)
except json.JSONDecodeError:
    stop("PROBE_INVALID")
if not isinstance(data, dict):
    stop("PROBE_INVALID")

reason = "UNCLASSIFIED_INSTALLER_FAILURE"
if not data.get("hostname_ok") or not data.get("os_ok"):
    reason = "HOST_CONTRACT_MISMATCH"
elif not data.get("python3_present") or not data.get("apt_get_present"):
    reason = "INSTALLER_PREREQUISITE_MISSING"
elif not data.get("runtime_base", {}).get("safe", False) or not data.get("runtime_target", {}).get("safe", False) or not data.get("wrapper", {}).get("safe", False):
    reason = "EXISTING_RUNTIME_STATE_UNSAFE"
elif data.get("wrapper_canary") == "ok" and data.get("runtime_target", {}).get("exists"):
    reason = "RUNTIME_PRESENT_PRIOR_RECEIPT_FAILED"
elif not data.get("esptool_present") and not data.get("apt_esptool_simulation_ok"):
    reason = "ESPTOOL_DEPENDENCY_UNRESOLVABLE"
elif not data.get("esptool_present"):
    reason = "ESPTOOL_NOT_INSTALLED_AFTER_ROLLBACK"
elif data.get("wrapper_canary") == "failed":
    reason = "WRAPPER_CANARY_FAILED"
elif data.get("runtime_target", {}).get("exists") and not data.get("wrapper", {}).get("exists"):
    reason = "WRAPPER_INSTALL_INCOMPLETE"
elif not data.get("runtime_target", {}).get("exists"):
    reason = "RUNTIME_INSTALL_ROLLED_BACK"

print("STATUS=OK")
print(f"REASON={reason}")
for key in (
    "hostname_ok", "os_ok", "python3_present", "apt_get_present",
    "apt_esptool_simulation_ok", "esptool_present", "esptool_py_present",
    "esptool_package_installed", "wrapper_canary", "candidate_count",
):
    print(f"{key.upper()}={data.get(key)}")
print(f"RUNTIME_BASE_EXISTS={data.get('runtime_base', {}).get('exists')}")
print(f"RUNTIME_BASE_SAFE={data.get('runtime_base', {}).get('safe')}")
print(f"RUNTIME_TARGET_EXISTS={data.get('runtime_target', {}).get('exists')}")
print(f"RUNTIME_TARGET_SAFE={data.get('runtime_target', {}).get('safe')}")
print(f"WRAPPER_EXISTS={data.get('wrapper', {}).get('exists')}")
print(f"WRAPPER_SAFE={data.get('wrapper', {}).get('safe')}")
