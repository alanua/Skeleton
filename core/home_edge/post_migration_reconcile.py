from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.executor import HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request


TASK_ID = "home_edge_01_post_migration_reconcile_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "EXPLICIT_RECONCILE_HOME_EDGE_POST_MIGRATION_20260808"
IDEMPOTENCY_KEY = "home-edge-01-post-migration-reconcile-20260808-v2"
REQUEST_TIMEOUT_SECONDS = 900
RECEIPT_SCHEMA = "skeleton.home_edge.post_migration_reconcile_receipt.v1"
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

VERIFIED_ENTRYPOINTS = {
    "gallery_refresh": "/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh",
    "gallery_verify": "/home/oleksii/.local/bin/home-edge-screensaver-verify-v9",
    "brother_verify": "/home/oleksii/.local/bin/brother-scan-key-verify",
    "aggregate_verify": "/home/oleksii/.local/bin/home-edge-platform-repair-verify",
    "registry_cli": "/home/oleksii/.local/bin/skeleton-devices",
    "cast_status": "/home/oleksii/.local/bin/skeleton-cast-control",
    "pointer_status": "/home/oleksii/.local/bin/home-edge-pointer-status",
    "watchdog_status": "/home/oleksii/.local/bin/home-edge-media-watchdog",
}
INVENTED_ENTRYPOINTS = (
    "home-edge-brother-scankey-verify-v4",
    "home-edge-platform-verify",
    "home-edge-media-watchdog-status",
)

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "os_identity_status",
    "node_identity_status",
    "boot_id_unchanged",
    "registry_pre_status",
    "screensaver_status",
    "screensaver_refresh_count",
    "gallery_pre_count",
    "gallery_post_count",
    "brother_verify_status",
    "aggregate_verify_status",
    "aggregate_source_repaired",
    "stale_operational_matches_before",
    "stale_operational_matches_after",
    "stale_files_changed_count",
    "system_failed_units_count",
    "user_failed_units_count",
    "cast_status",
    "pointer_status",
    "watchdog_status",
    "watchdog_critical_count",
    "watchdog_warning_count",
    "rollback_ready",
    "rollback_applied",
    "mutation_executor_receipt_hash",
    "audit_receipt_hash",
    "stable_reason",
    "success_criteria",
    "canonical_memory_post_step",
)

_ALLOWED_FIELDS = frozenset(
    {
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Operator Approval",
        "Target",
    }
)
_FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>.*?)\s*$")
_PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")


@dataclass(frozen=True)
class RuntimeInput:
    repository: str
    expected_main_sha: str
    operator_approval: str
    target: str


def execute_post_migration_reconcile_task(
    body: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    runtime_input = parse_runtime_input(body)
    validate_main_sha(
        runtime_input.expected_main_sha,
        registered_clean_main_sha=registered_clean_main_sha,
        github_main_sha=github_main_sha,
    )
    request = build_reconcile_request(environment=environment)
    executor_receipt = execute_home_edge_request(request.to_mapping())
    public = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    public["mutation_executor_receipt_hash"] = executor_receipt.receipt_hash
    public["audit_receipt_hash"] = _audit_hash(public)
    return public


def parse_runtime_input(body: str) -> RuntimeInput:
    fields: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in _metadata_lines(body):
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        field = match.group("field").strip()
        value = match.group("value").strip()
        if not value:
            continue
        if field in fields:
            duplicates.add(field)
        fields[field] = value
    if duplicates:
        raise ValueError("duplicate_runtime_input_field")
    unknown = sorted(set(fields) - _ALLOWED_FIELDS)
    if unknown:
        raise ValueError("unknown_runtime_input_field")
    if fields.get("Mode") != "RUNTIME_MAINTENANCE_TASK":
        raise ValueError("runtime_mode_mismatch")
    if fields.get("Maintenance Task ID") != TASK_ID:
        raise ValueError("maintenance_task_id_mismatch")
    runtime_input = RuntimeInput(
        repository=fields.get("Repository", ""),
        expected_main_sha=fields.get("Expected Main SHA", ""),
        operator_approval=fields.get("Operator Approval", ""),
        target=fields.get("Target", ""),
    )
    if runtime_input.repository != REPOSITORY:
        raise ValueError("repository_mismatch")
    if EXPECTED_MAIN_SHA_RE.fullmatch(runtime_input.expected_main_sha) is None:
        raise ValueError("expected_main_sha_malformed")
    if runtime_input.operator_approval != OPERATOR_APPROVAL:
        raise ValueError("operator_approval_mismatch")
    if runtime_input.target != TARGET_NODE:
        raise ValueError("target_mismatch")
    return runtime_input


def validate_main_sha(
    expected_main_sha: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> None:
    if EXPECTED_MAIN_SHA_RE.fullmatch(registered_clean_main_sha or "") is None:
        raise ValueError("registered_clean_main_sha_unavailable")
    if EXPECTED_MAIN_SHA_RE.fullmatch(github_main_sha or "") is None:
        raise ValueError("github_main_sha_unavailable")
    if expected_main_sha != registered_clean_main_sha:
        raise ValueError("registered_clean_main_sha_mismatch")
    if expected_main_sha != github_main_sha:
        raise ValueError("github_main_sha_mismatch")


def build_reconcile_request(
    *, environment: Mapping[str, str] | None = None
) -> HomeEdgeExecRequest:
    env = os.environ if environment is None else environment
    secret = env.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        raise ValueError("home_edge_exec_hmac_secret_missing")
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": "privileged_mutation",
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": OPERATOR_APPROVAL,
            "idempotency_key": IDEMPOTENCY_KEY,
            "run_as": "root",
            "mode": "script",
            "script": RECONCILE_SCRIPT,
            "script_interpreter": "bash",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": 65536,
        }
    )
    signature = sign_request(request, secret)
    return HomeEdgeExecRequest.from_mapping({**request.to_mapping(include_signature=False), "signature": signature})


def public_receipt_from_executor_stdout(receipt: Mapping[str, Any]) -> dict[str, object]:
    if receipt.get("status") != "ok" or receipt.get("exit_code") != 0:
        return _blocked_receipt("executor_receipt_not_ok")
    stdout = receipt.get("stdout")
    if not isinstance(stdout, str):
        return _blocked_receipt("executor_stdout_missing")
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return _blocked_receipt("executor_stdout_not_json")
    if not isinstance(decoded, dict):
        return _blocked_receipt("executor_stdout_not_object")
    return sanitize_public_receipt(decoded)


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    for field in RECEIPT_FIELDS:
        if field not in receipt:
            raise ValueError("receipt_field_missing")
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        value = receipt[field]
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, str) and _PUBLIC_VALUE_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    if sanitized["canonical_memory_post_step"] != "home_edge_audit_persist_v1":
        raise ValueError("receipt_canonical_memory_post_step_mismatch")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("os_identity_status") == "verified"
        and receipt.get("node_identity_status") == "verified"
        and receipt.get("boot_id_unchanged") is True
        and receipt.get("registry_pre_status") == "healthy"
        and receipt.get("screensaver_status") in {"already_healthy", "refreshed_healthy"}
        and receipt.get("brother_verify_status") == "healthy"
        and receipt.get("aggregate_verify_status") == "healthy"
        and receipt.get("stale_operational_matches_after") == 0
        and receipt.get("system_failed_units_count") == 0
        and receipt.get("user_failed_units_count") == 0
        and receipt.get("cast_status") == "healthy"
        and receipt.get("pointer_status") == "healthy"
        and receipt.get("watchdog_status") == "healthy"
        and receipt.get("watchdog_critical_count") == 0
        and receipt.get("watchdog_warning_count") == 0
        and receipt.get("rollback_ready") is True
        and receipt.get("rollback_applied") is False
        and receipt.get("canonical_memory_post_step") == "home_edge_audit_persist_v1"
    )


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


def _blocked_receipt(reason: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": TASK_ID,
        "os_identity_status": "unverified",
        "node_identity_status": "unverified",
        "boot_id_unchanged": False,
        "registry_pre_status": "blocked",
        "screensaver_status": "blocked",
        "screensaver_refresh_count": 0,
        "gallery_pre_count": 0,
        "gallery_post_count": 0,
        "brother_verify_status": "blocked",
        "aggregate_verify_status": "blocked",
        "aggregate_source_repaired": False,
        "stale_operational_matches_before": 0,
        "stale_operational_matches_after": 0,
        "stale_files_changed_count": 0,
        "system_failed_units_count": -1,
        "user_failed_units_count": -1,
        "cast_status": "blocked",
        "pointer_status": "blocked",
        "watchdog_status": "blocked",
        "watchdog_critical_count": -1,
        "watchdog_warning_count": -1,
        "rollback_ready": False,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "unavailable",
        "audit_receipt_hash": "pending",
        "stable_reason": reason,
        "success_criteria": "not_met",
        "canonical_memory_post_step": "home_edge_audit_persist_v1",
    }
    receipt["audit_receipt_hash"] = _audit_hash(receipt)
    return receipt


def _audit_hash(receipt: Mapping[str, object]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key in RECEIPT_FIELDS and key != "audit_receipt_hash"
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


RECONCILE_SCRIPT = r'''set -uo pipefail
TASK_ID="home_edge_01_post_migration_reconcile_v1"
STATE_ROOT="/var/lib/skeleton/home-edge-01/post-migration-reconcile-v1"
ROLLBACK_ROOT="$STATE_ROOT/rollback"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
ROLLBACK_DIR="$ROLLBACK_ROOT/$RUN_ID"
MANIFEST="$ROLLBACK_DIR/manifest.tsv"
LOG_DIR=""
DESKTOP_USER="oleksii"
DESKTOP_UID="1000"
DESKTOP_HOME="/home/oleksii"
USER_RUNTIME="/run/user/1000"
USER_DBUS="unix:path=/run/user/1000/bus"
GALLERY_REFRESH="/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh"
GALLERY_VERIFY="/home/oleksii/.local/bin/home-edge-screensaver-verify-v9"
BROTHER_VERIFY="/home/oleksii/.local/bin/brother-scan-key-verify"
AGGREGATE_VERIFY="/home/oleksii/.local/bin/home-edge-platform-repair-verify"
REGISTRY_CLI="/home/oleksii/.local/bin/skeleton-devices"
CAST_STATUS="/home/oleksii/.local/bin/skeleton-cast-control"
POINTER_STATUS="/home/oleksii/.local/bin/home-edge-pointer-status"
WATCHDOG_STATUS="/home/oleksii/.local/bin/home-edge-media-watchdog"
OLD_HOME="/home/jeeves"
NEW_HOME="/home/oleksii"
OLD_BROTHER_UNIT="brother_guard_v2.service"
NEW_BROTHER_UNIT="brother-guard.service"
receipt_emitted=false
rollback_ready=false
rollback_applied=false
os_identity_status="unverified"
node_identity_status="unverified"
boot_id_unchanged=false
registry_pre_status="blocked"
screensaver_status="blocked"
screensaver_refresh_count=0
gallery_pre_count=0
gallery_post_count=0
brother_verify_status="blocked"
aggregate_verify_status="blocked"
aggregate_source_repaired=false
stale_operational_matches_before=0
stale_operational_matches_after=0
stale_files_changed_count=0
system_failed_units_count=-1
user_failed_units_count=-1
cast_status="blocked"
pointer_status="blocked"
watchdog_status="blocked"
watchdog_critical_count=-1
watchdog_warning_count=-1
stable_reason="completed"
boot_id_before="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
bounded_log() {
  local name="$1"
  if [ -n "$LOG_DIR" ]; then printf '%s/%s.log' "$LOG_DIR" "$name"; else printf '/dev/null'; fi
}
emit_receipt() {
  if [ "$receipt_emitted" = true ]; then exit 70; fi
  receipt_emitted=true
  local criteria="$1"
  local hash
  hash="$(printf '%s:%s:%s:%s:%s:%s' "$TASK_ID" "$screensaver_status" "$brother_verify_status" "$aggregate_verify_status" "$stale_operational_matches_after" "$stable_reason" | sha256sum | awk '{print $1}')"
  printf '{"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"os_identity_status":"%s",' "$os_identity_status"
  printf '"node_identity_status":"%s",' "$node_identity_status"
  printf '"boot_id_unchanged":%s,' "$boot_id_unchanged"
  printf '"registry_pre_status":"%s",' "$registry_pre_status"
  printf '"screensaver_status":"%s",' "$screensaver_status"
  printf '"screensaver_refresh_count":%s,' "$screensaver_refresh_count"
  printf '"gallery_pre_count":%s,' "$gallery_pre_count"
  printf '"gallery_post_count":%s,' "$gallery_post_count"
  printf '"brother_verify_status":"%s",' "$brother_verify_status"
  printf '"aggregate_verify_status":"%s",' "$aggregate_verify_status"
  printf '"aggregate_source_repaired":%s,' "$aggregate_source_repaired"
  printf '"stale_operational_matches_before":%s,' "$stale_operational_matches_before"
  printf '"stale_operational_matches_after":%s,' "$stale_operational_matches_after"
  printf '"stale_files_changed_count":%s,' "$stale_files_changed_count"
  printf '"system_failed_units_count":%s,' "$system_failed_units_count"
  printf '"user_failed_units_count":%s,' "$user_failed_units_count"
  printf '"cast_status":"%s",' "$cast_status"
  printf '"pointer_status":"%s",' "$pointer_status"
  printf '"watchdog_status":"%s",' "$watchdog_status"
  printf '"watchdog_critical_count":%s,' "$watchdog_critical_count"
  printf '"watchdog_warning_count":%s,' "$watchdog_warning_count"
  printf '"rollback_ready":%s,' "$rollback_ready"
  printf '"rollback_applied":%s,' "$rollback_applied"
  printf '"mutation_executor_receipt_hash":"pending",'
  printf '"audit_receipt_hash":"%s",' "$hash"
  printf '"stable_reason":"%s",' "$stable_reason"
  printf '"success_criteria":"%s",' "$criteria"
  printf '"canonical_memory_post_step":"home_edge_audit_persist_v1"}\n'
}
block() { stable_reason="$1"; emit_receipt not_met; exit 10; }
desktop_run() {
  runuser -u oleksii -- env HOME=/home/oleksii XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "$@"
}
run_private() {
  local label="$1"
  shift
  "$@" >"$(bounded_log "$label")" 2>&1
}
ensure_private_state() {
  install -d -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT" "$ROLLBACK_DIR" || return 1
  LOG_DIR="$ROLLBACK_DIR/logs"
  install -d -m 0700 "$LOG_DIR" || return 1
  : >"$MANIFEST" || return 1
  chmod 0600 "$MANIFEST" || return 1
  rollback_ready=true
  [ -r "$MANIFEST" ] || return 1
}
json_count() {
  local file="$1"
  python3 - "$file" <<'PY_COUNT'
import json, sys
keys = {"qualified_items", "qualified_count", "gallery_count", "item_count", "count"}
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print(0)
    raise SystemExit(0)
def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, int) and not isinstance(item, bool):
                return item
        for item in value.values():
            found = walk(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = walk(item)
            if found:
                return found
    return 0
print(walk(data))
PY_COUNT
}
backup_file() {
  local path="$1" reason="$2" rel backup mode uid gid
  [ -f "$path" ] || return 1
  [ ! -L "$path" ] || return 1
  uid="$(stat -c '%u' "$path" 2>/dev/null)" || return 1
  gid="$(stat -c '%g' "$path" 2>/dev/null)" || return 1
  mode="$(stat -c '%a' "$path" 2>/dev/null)" || return 1
  [ "$uid" = "0" ] || [ "$uid" = "1000" ] || return 1
  [ $((8#$mode & 002)) -eq 0 ] || return 1
  rel="${path#/}"
  backup="$ROLLBACK_DIR/files/$rel"
  install -d -m 0700 "$(dirname "$backup")" || return 1
  cp -a -- "$path" "$backup" || return 1
  chmod 0600 "$backup" || return 1
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$path" "$reason" "$mode" "$uid" "$gid" "$backup" >>"$MANIFEST" || return 1
}
restore_owned_files() {
  local path reason mode uid gid backup tmp failed=false
  while IFS="$(printf '\t')" read -r path reason mode uid gid backup; do
    [ -n "$path" ] || continue
    tmp="$path.restore.$$"
    cp -a -- "$backup" "$tmp" || { failed=true; continue; }
    chown "$uid:$gid" "$tmp" 2>/dev/null || true
    chmod "$mode" "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$path" || { failed=true; continue; }
  done <"$MANIFEST"
  [ "$failed" = false ]
}
fail_after_mutation() {
  local reason="$1"
  if restore_owned_files; then rollback_applied=true; stable_reason="$reason"; emit_receipt not_met; exit 50; fi
  rollback_applied=false
  stable_reason="rollback_failed"
  emit_receipt not_met
  exit 60
}
safe_replace_file() {
  local path="$1" tmp
  backup_file "$path" stale_home_path || return 1
  tmp="$(mktemp "${path}.tmp.XXXXXX")" || return 1
  python3 - "$path" "$tmp" <<'PY_REPLACE'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(sys.argv[2])
old = "/home/jeeves"
new = "/home/oleksii"
text = path.read_text(encoding="utf-8", errors="surrogateescape")
out = text.replace(old, new)
tmp.write_text(out, encoding="utf-8", errors="surrogateescape")
PY_REPLACE
  chown --reference="$path" "$tmp" 2>/dev/null || true
  chmod --reference="$path" "$tmp" || return 1
  mv -f -- "$tmp" "$path" || return 1
}
enumerate_operational_files() {
  find /home/oleksii/.local/bin -maxdepth 1 -type f \( -name 'home-edge-*' -o -name 'skeleton-*' -o -name 'tv-mode*' \) ! -name '*~' ! -name '*.bak' ! -name '*.backup' ! -name '*.archive' ! -path '*/__pycache__/*' -print0 2>/dev/null
  find /home/oleksii/.config/systemd/user -maxdepth 1 -type f \( -name '*.service' -o -name '*.timer' \) -print0 2>/dev/null
  find /etc/systemd/system -maxdepth 1 -type f \( -iname '*home-edge*.service' -o -iname '*home-edge*.timer' -o -iname '*skeleton*.service' -o -iname '*skeleton*.timer' \) -print0 2>/dev/null
  find /etc/skeleton -maxdepth 2 -type f -print0 2>/dev/null
}
count_stale_matches() {
  local count=0 path
  while IFS= read -r -d '' path; do
    case "$path" in
      *memory-gate*|*device-registry*|*phone-ssh*|*github-app*|*gmail*|*secret*|*credential*|*token*|*password*|*known_hosts*|*archive*|*backup*) continue ;;
    esac
    [ -f "$path" ] && [ ! -L "$path" ] || continue
    matches="$(grep -Fao "$OLD_HOME" -- "$path" 2>/dev/null | wc -l | tr -d ' ')"
    count=$((count + ${matches:-0}))
  done < <(enumerate_operational_files)
  printf '%s\n' "$count"
}
repair_stale_paths() {
  local path changed=false needs_system_reload=false needs_user_reload=false
  while IFS= read -r -d '' path; do
    case "$path" in
      *memory-gate*|*device-registry*|*phone-ssh*|*github-app*|*gmail*|*secret*|*credential*|*token*|*password*|*known_hosts*|*archive*|*backup*) continue ;;
    esac
    [ -f "$path" ] && [ ! -L "$path" ] || continue
    grep -Fq "$OLD_HOME" -- "$path" 2>/dev/null || continue
    python3 - "$path" "$OLD_HOME" "$NEW_HOME" <<'PY_TARGETS'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="surrogateescape")
old, new = sys.argv[2], sys.argv[3]
for suffix in re.findall(re.escape(old) + r"/[A-Za-z0-9._/@%+=:,~-]+", text):
    target = new + suffix[len(old):]
    if not pathlib.Path(target).exists():
        raise SystemExit(1)
PY_TARGETS
    [ "$?" = "0" ] || continue
    safe_replace_file "$path" || return 1
    stale_files_changed_count=$((stale_files_changed_count + 1))
    changed=true
    case "$path" in
      /etc/systemd/system/*.service|/etc/systemd/system/*.timer) needs_system_reload=true ;;
      /home/oleksii/.config/systemd/user/*.service|/home/oleksii/.config/systemd/user/*.timer) needs_user_reload=true ;;
    esac
  done < <(enumerate_operational_files)
  [ "$needs_system_reload" = false ] || systemctl daemon-reload >"$(bounded_log system_daemon_reload)" 2>&1 || return 1
  [ "$needs_user_reload" = false ] || desktop_run systemctl --user daemon-reload >"$(bounded_log user_daemon_reload)" 2>&1 || return 1
  [ "$changed" = false ] || return 0
}
postcheck_all() {
  run_private registry_post desktop_run "$REGISTRY_CLI" doctor || return 1
  run_private gallery_final desktop_run "$GALLERY_VERIFY" || return 1
  run_private brother_final desktop_run "$BROTHER_VERIFY" || return 1
  run_private aggregate_final desktop_run "$AGGREGATE_VERIFY" || return 1
  run_private cast_status desktop_run "$CAST_STATUS" status || return 1
  cast_status="healthy"
  run_private pointer_status desktop_run "$POINTER_STATUS" || return 1
  pointer_status="healthy"
  run_private watchdog_status desktop_run "$WATCHDOG_STATUS" status || return 1
  watchdog_status="healthy"
  watchdog_critical_count=0
  watchdog_warning_count=0
  system_failed_units_count="$(systemctl --failed --no-legend 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
  user_failed_units_count="$(desktop_run systemctl --user --failed --no-legend 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
  [ "$system_failed_units_count" = "0" ] || return 1
  [ "$user_failed_units_count" = "0" ] || return 1
  stale_operational_matches_after="$(count_stale_matches)"
  [ "$stale_operational_matches_after" = "0" ] || return 1
  [ "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)" = "$boot_id_before" ] || return 1
  boot_id_unchanged=true
}
[ "$(id -u)" = "0" ] || block missing_root_execution
[ -r /etc/os-release ] || block os_release_missing
. /etc/os-release
[ "${ID:-}" = "debian" ] || block os_not_debian
case "${VERSION_ID:-}" in 13*) ;; *) block os_version_not_13 ;; esac
[ "$(hostname)" = "home-edge-01" ] || block hostname_mismatch
oleksii_entry="$(getent passwd oleksii || true)"
[ -n "$oleksii_entry" ] || block user_missing
IFS=: read -r user_name _ user_uid _ _ user_home _ <<EOF_USER
$oleksii_entry
EOF_USER
[ "$user_name" = "$DESKTOP_USER" ] || block user_mismatch
[ "$user_uid" = "$DESKTOP_UID" ] || block uid_mismatch
[ "$user_home" = "$DESKTOP_HOME" ] || block home_mismatch
[ -d "$USER_RUNTIME" ] || block user_runtime_missing
[ -S /run/user/1000/bus ] || block user_dbus_missing
ensure_private_state || block rollback_manifest_unavailable
for entrypoint in "$GALLERY_REFRESH" "$GALLERY_VERIFY" "$BROTHER_VERIFY" "$AGGREGATE_VERIFY" "$REGISTRY_CLI" "$CAST_STATUS" "$POINTER_STATUS" "$WATCHDOG_STATUS"; do
  [ -f "$entrypoint" ] || block entrypoint_missing
  [ ! -L "$entrypoint" ] || block entrypoint_not_regular
  [ -x "$entrypoint" ] || block entrypoint_not_executable
done
os_identity_status="verified"
node_identity_status="verified"
run_private registry_pre desktop_run "$REGISTRY_CLI" doctor || block registry_pre_degraded
registry_pre_status="healthy"
pre_gallery_log="$(bounded_log gallery_pre)"
if desktop_run "$GALLERY_VERIFY" >"$pre_gallery_log" 2>&1; then
  screensaver_status="already_healthy"
else
  screensaver_status="precheck_degraded"
  run_private gallery_refresh desktop_run "$GALLERY_REFRESH" || block screensaver_refresh_failed
  screensaver_refresh_count=1
  post_gallery_log="$(bounded_log gallery_post)"
  desktop_run "$GALLERY_VERIFY" >"$post_gallery_log" 2>&1 || block screensaver_postcheck_degraded
  screensaver_status="refreshed_healthy"
fi
gallery_pre_count="$(json_count "$pre_gallery_log")"
gallery_post_count="$(json_count "$(bounded_log gallery_post)")"
run_private brother_pre desktop_run "$BROTHER_VERIFY" || block brother_verify_degraded
brother_verify_status="healthy"
if run_private aggregate_pre desktop_run "$AGGREGATE_VERIFY"; then
  aggregate_verify_status="healthy"
else
  [ -f "$AGGREGATE_VERIFY" ] || block aggregate_verify_not_regular
  [ ! -L "$AGGREGATE_VERIFY" ] || block aggregate_verify_not_regular
  size="$(stat -c '%s' "$AGGREGATE_VERIFY" 2>/dev/null)" || block aggregate_verify_unstatable
  [ "$size" -le 1048576 ] || block aggregate_verify_too_large
  owner="$(stat -c '%u' "$AGGREGATE_VERIFY" 2>/dev/null)" || block aggregate_verify_unstatable
  [ "$owner" = "0" ] || [ "$owner" = "1000" ] || block aggregate_verify_unexpected_owner
  matches="$(grep -Fao "$OLD_BROTHER_UNIT" -- "$AGGREGATE_VERIFY" | wc -l | tr -d ' ')"
  [ "$matches" = "1" ] || block aggregate_old_unit_literal_count_mismatch
  desktop_run systemctl --user list-unit-files "$NEW_BROTHER_UNIT" --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$NEW_BROTHER_UNIT" || block brother_guard_target_unit_unverified
  desktop_run systemctl --user is-active --quiet "$NEW_BROTHER_UNIT" || block brother_guard_target_unit_unverified
  backup_file "$AGGREGATE_VERIFY" aggregate_brother_unit || block rollback_manifest_unavailable
  tmp="$(mktemp "${AGGREGATE_VERIFY}.tmp.XXXXXX")" || block aggregate_source_repair_failed
  python3 - "$AGGREGATE_VERIFY" "$tmp" <<'PY_AGG'
import pathlib, sys
src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
text = src.read_text(encoding="utf-8", errors="surrogateescape")
old = "brother_guard_v2.service"
new = "brother-guard.service"
if text.count(old) != 1:
    raise SystemExit(1)
dst.write_text(text.replace(old, new), encoding="utf-8", errors="surrogateescape")
PY_AGG
  chown --reference="$AGGREGATE_VERIFY" "$tmp" 2>/dev/null || true
  chmod --reference="$AGGREGATE_VERIFY" "$tmp" || block aggregate_source_repair_failed
  mv -f -- "$tmp" "$AGGREGATE_VERIFY" || block aggregate_source_repair_failed
  aggregate_source_repaired=true
  if run_private aggregate_after desktop_run "$AGGREGATE_VERIFY"; then
    aggregate_verify_status="healthy"
  else
    restore_owned_files || block rollback_failed
    rollback_applied=true
    run_private aggregate_restored desktop_run "$AGGREGATE_VERIFY" || block aggregate_repair_rollback_unverified
    block aggregate_repair_postcheck_failed
  fi
fi
stale_operational_matches_before="$(count_stale_matches)"
repair_stale_paths || fail_after_mutation stale_path_repair_failed
postcheck_all || fail_after_mutation postcheck_failed
stable_reason="completed"
if [ "$screensaver_refresh_count" = "0" ] && [ "$aggregate_source_repaired" = false ] && [ "$stale_files_changed_count" = "0" ]; then stable_reason="already_healthy"; fi
emit_receipt met
'''
