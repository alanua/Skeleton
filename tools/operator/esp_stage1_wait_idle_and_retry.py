#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

REPO = "alanua/Skeleton"
EXPECTED_MAIN = "8b04a008ddea5cde84c7c25923505e770646d399"
CHECKOUT = Path("/home/agent/agent-dev/repos/Skeleton")
ENV_FILE = Path("/etc/skeleton-runner.env")
WAIT_SECONDS = 1200
POLL_SECONDS = 10
RESULT_WAIT_SECONDS = 900
BODY = "\n".join(
    [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID: home_edge_01_esp_lab_stage1_activation_v1",
        f"Repository: {REPO}",
        f"Expected Main SHA: {EXPECTED_MAIN}",
        "Target: home-edge-01",
        "Operator Approval: EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_APPROVED",
    ]
)
ALLOWED_ENV = {
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


def run(argv: list[str], *, timeout: int = 60, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        stop("LOCAL_COMMAND_FAILED")


def one(argv: list[str], *, timeout: int = 60) -> str:
    cp = run(argv, timeout=timeout)
    if cp.returncode != 0:
        stop("LOCAL_COMMAND_FAILED")
    return cp.stdout.strip()


def verify_main() -> None:
    if not CHECKOUT.is_dir():
        stop("CHECKOUT_UNAVAILABLE")
    if one(["git", "-C", str(CHECKOUT), "rev-parse", "--abbrev-ref", "HEAD"]) != "main":
        stop("CHECKOUT_NOT_MAIN")
    if one(["git", "-C", str(CHECKOUT), "status", "--porcelain"]):
        stop("CHECKOUT_DIRTY")
    cp = run(["git", "-C", str(CHECKOUT), "fetch", "origin", "main", "--quiet"], timeout=120)
    if cp.returncode != 0:
        stop("FETCH_FAILED")
    head = one(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"])
    remote = one(["git", "-C", str(CHECKOUT), "rev-parse", "origin/main"])
    if head != EXPECTED_MAIN or remote != EXPECTED_MAIN:
        stop("EXACT_MAIN_MISMATCH")


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
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
        if key in ALLOWED_ENV:
            out[key] = value
    return out


def safe_file(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def runtime_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if safe_file(ENV_FILE):
        try:
            env.update(parse_env_text(ENV_FILE.read_text(encoding="utf-8")))
        except OSError:
            pass
    needed = {
        "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
        "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
    }
    if needed.issubset(env):
        return env
    uid = os.geteuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            if b"runner_poll_github_tasks.py" not in (entry / "cmdline").read_bytes():
                continue
            raw = (entry / "environ").read_bytes()
        except OSError:
            continue
        for item in raw.split(b"\0"):
            if b"=" not in item:
                continue
            kb, vb = item.split(b"=", 1)
            try:
                key, value = kb.decode(), vb.decode()
            except UnicodeDecodeError:
                continue
            if key in ALLOWED_ENV:
                env[key] = value
        if needed.issubset(env):
            return env
    stop("RUNNER_RUNTIME_ENV_UNAVAILABLE")
    return {}


def target(env: dict[str, str]) -> tuple[str, str, str, str]:
    profile: dict[str, object] = {}
    profile_path = env.get("SKELETON_HOME_EDGE_01_PROFILE", "")
    if profile_path:
        try:
            decoded = json.loads(Path(profile_path).read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                profile = decoded
        except Exception:
            stop("PROFILE_UNAVAILABLE")
    ssh_cfg = profile.get("ssh") if isinstance(profile.get("ssh"), dict) else {}
    identity = env.get(str(ssh_cfg.get("identity_env") or "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE"), "")
    known = env.get(str(ssh_cfg.get("known_hosts_env") or "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE"), "")
    user = str(env.get("SKELETON_HOME_EDGE_01_TARGET_USER") or ssh_cfg.get("target_user") or "")
    host = str(env.get("SKELETON_HOME_EDGE_01_TAILSCALE_IP") or profile.get("tailscale_ip") or "")
    if not identity or not known or not user or not host:
        stop("TARGET_UNAVAILABLE")
    if not safe_file(Path(identity)) or not safe_file(Path(known)):
        stop("SSH_MATERIAL_UNAVAILABLE")
    return identity, known, user, host


def package_manager_count(identity: str, known: str, user: str, host: str) -> int:
    remote = r'''import os
names=("apt","apt-get","dpkg","unattended-upgr","unattended-upgrade")
count=0
for item in os.listdir('/proc'):
    if not item.isdigit():
        continue
    try:
        state=open(f'/proc/{item}/stat',encoding='utf-8',errors='ignore').read().split()[2]
        if state=='Z':
            continue
        comm=open(f'/proc/{item}/comm',encoding='utf-8',errors='ignore').read().strip().lower()
    except Exception:
        continue
    if any(comm==n or comm.startswith(n) for n in names):
        count+=1
print(count)
'''
    cp = run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known}",
            "-o", "ConnectTimeout=10",
            "-i", identity,
            f"{user}@{host}",
            "python3", "-",
        ],
        timeout=35,
        input_text=remote,
    )
    if cp.returncode != 0:
        stop("HOME_EDGE_TRANSPORT_FAILED")
    try:
        return int(cp.stdout.strip())
    except ValueError:
        stop("HOME_EDGE_PROBE_INVALID")
    return -1


def wait_for_idle(identity: str, known: str, user: str, host: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    announced = False
    consecutive_idle = 0
    while time.monotonic() < deadline:
        count = package_manager_count(identity, known, user, host)
        if count == 0:
            consecutive_idle += 1
            if consecutive_idle >= 2:
                print("PACKAGE_MANAGER_IDLE=true")
                return
        else:
            consecutive_idle = 0
            if not announced:
                print("WAITING_FOR_PACKAGE_MANAGER=true")
                announced = True
        time.sleep(POLL_SECONDS)
    stop("PACKAGE_MANAGER_STILL_BUSY")


def create_retry_issue() -> int:
    verify_main()
    title = "P0 retry ESP Lab Stage 1 activation after package manager idle"
    cp = run(
        [
            "gh", "issue", "create",
            "--repo", REPO,
            "--title", title,
            "--body", BODY,
            "--label", "runner:ready",
            "--label", "runner:priority-1",
            "--label", "priority:P0",
            "--label", "risk:yellow",
            "--label", "agent:task",
        ],
        timeout=60,
    )
    if cp.returncode != 0:
        stop("CREATE_RUNTIME_ISSUE_FAILED")
    url = cp.stdout.strip().splitlines()[-1].strip()
    try:
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
    except Exception:
        stop("RUNTIME_ISSUE_NUMBER_INVALID")
    print(f"RUNTIME_ISSUE={number}")
    return number


def issue_snapshot(number: int) -> dict[str, object]:
    cp = run(
        ["gh", "issue", "view", str(number), "--repo", REPO, "--json", "labels,comments,state,body"],
        timeout=45,
    )
    if cp.returncode != 0:
        stop("READ_RUNTIME_ISSUE_FAILED")
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError:
        stop("RUNTIME_ISSUE_JSON_INVALID")
    if data.get("body") != BODY:
        stop("RUNTIME_ISSUE_BODY_MISMATCH")
    return data


def wait_for_runner(number: int) -> None:
    deadline = time.monotonic() + RESULT_WAIT_SECONDS
    while time.monotonic() < deadline:
        data = issue_snapshot(number)
        labels = {
            str(item.get("name"))
            for item in data.get("labels", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if "runner:done" in labels or "runner:blocked" in labels:
            status = "DONE" if "runner:done" in labels else "BLOCKED"
            print(f"STATUS={status}")
            comments = data.get("comments") if isinstance(data.get("comments"), list) else []
            body = ""
            if comments and isinstance(comments[-1], dict) and isinstance(comments[-1].get("body"), str):
                body = comments[-1]["body"]
            prefixes = (
                "runtime_state=",
                "source_sha=",
                "candidate_count=",
                "device_canary=",
                "dependency_installed_by_operation=",
                "idempotent_reuse=",
                "reason=",
                "success_criteria=",
            )
            for line in body.splitlines():
                if line.startswith(prefixes):
                    print(line)
            return
        time.sleep(POLL_SECONDS)
    stop("RUNNER_RESULT_TIMEOUT")


def main() -> None:
    verify_main()
    env = runtime_env()
    identity, known, user, host = target(env)
    wait_for_idle(identity, known, user, host)
    number = create_retry_issue()
    wait_for_runner(number)


if __name__ == "__main__":
    main()
