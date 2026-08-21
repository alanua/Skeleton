from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.executor import HomeEdgeExecError, HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request


TASK_ID = "home_edge_01_family_document_production_canary_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "USER_DO_IT_20260817"
APPROVAL_REF = OPERATOR_APPROVAL
IDEMPOTENCY_KEY = "home-edge-01-family-document-production-canary-v1"
REQUEST_TIMEOUT_SECONDS = 900
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

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
_PUBLIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")
_MAX_EXECUTOR_STDOUT_BYTES = 32768

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "deployment_status",
    "config_ready",
    "dependencies_ready",
    "exact_sha_verified",
    "service_active",
    "single_worker",
    "canary_state",
    "live_canary_success",
    "accepted_delta",
    "work_done_delta",
    "report_done_delta",
    "archive_readback",
    "memorygateway_readback",
    "telegram_report_done",
    "report_is_rich",
    "duplicate_replay_zero",
    "retrying_count",
    "review_count",
    "stable_reason",
    "success_criteria",
)


@dataclass(frozen=True)
class RuntimeInput:
    repository: str
    expected_main_sha: str
    operator_approval: str
    target: str


def execute_family_document_production_task(
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
    request = build_activation_request(
        runtime_input.expected_main_sha,
        environment=environment,
    )
    try:
        executor_receipt = execute_home_edge_request(request.to_mapping())
    except (subprocess.TimeoutExpired, TimeoutError):
        return blocked_receipt("executor_transport_timeout")
    except HomeEdgeExecError:
        return blocked_receipt("executor_transport_failed")
    except Exception:
        return blocked_receipt("executor_transport_exception")
    public = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    if executor_receipt.status != "ok" or executor_receipt.exit_code != 0:
        public["success_criteria"] = "not_met"
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


def build_activation_request(
    expected_main_sha: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> HomeEdgeExecRequest:
    if EXPECTED_MAIN_SHA_RE.fullmatch(expected_main_sha or "") is None:
        raise ValueError("expected_main_sha_malformed")
    env = os.environ if environment is None else environment
    secret = env.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        raise ValueError("home_edge_exec_hmac_secret_missing")
    script = _ACTIVATION_SCRIPT_TEMPLATE.replace("__EXPECTED_MAIN_SHA__", expected_main_sha)
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": "privileged_mutation",
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": APPROVAL_REF,
            "idempotency_key": IDEMPOTENCY_KEY,
            "run_as": "root",
            "mode": "script",
            "script": script,
            "script_interpreter": "bash",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": _MAX_EXECUTOR_STDOUT_BYTES,
        }
    )
    signature = sign_request(request, secret)
    return HomeEdgeExecRequest.from_mapping(
        {**request.to_mapping(include_signature=False), "signature": signature}
    )


def public_receipt_from_executor_stdout(receipt: Mapping[str, Any]) -> dict[str, object]:
    stdout = receipt.get("stdout")
    if not isinstance(stdout, str):
        return blocked_receipt("executor_stdout_missing")
    bounded = stdout.encode("utf-8", errors="ignore")[-_MAX_EXECUTOR_STDOUT_BYTES:]
    text = bounded.decode("utf-8", errors="ignore")
    candidates = [text, *reversed([line.strip() for line in text.splitlines()])]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, Mapping):
            try:
                return sanitize_public_receipt(decoded)
            except ValueError:
                continue
    return blocked_receipt("executor_stdout_not_public_receipt")


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    if set(receipt) != set(RECEIPT_FIELDS):
        raise ValueError("receipt_field_set_mismatch")
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        value = receipt[field]
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            sanitized[field] = value
        elif isinstance(value, str) and _PUBLIC_TOKEN_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    return sanitized


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("deployment_status") == "healthy"
        and receipt.get("config_ready") is True
        and receipt.get("dependencies_ready") is True
        and receipt.get("exact_sha_verified") is True
        and receipt.get("service_active") is True
        and receipt.get("single_worker") is True
        and receipt.get("canary_state") == "passed"
        and receipt.get("live_canary_success") is True
        and receipt.get("archive_readback") is True
        and receipt.get("memorygateway_readback") is True
        and receipt.get("telegram_report_done") is True
        and receipt.get("report_is_rich") is True
        and receipt.get("duplicate_replay_zero") is True
    )


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def blocked_receipt(reason: str) -> dict[str, object]:
    return {
        "maintenance_task_id": TASK_ID,
        "deployment_status": "blocked",
        "config_ready": False,
        "dependencies_ready": False,
        "exact_sha_verified": False,
        "service_active": False,
        "single_worker": False,
        "canary_state": "blocked",
        "live_canary_success": False,
        "accepted_delta": 0,
        "work_done_delta": 0,
        "report_done_delta": 0,
        "archive_readback": False,
        "memorygateway_readback": False,
        "telegram_report_done": False,
        "report_is_rich": False,
        "duplicate_replay_zero": False,
        "retrying_count": 0,
        "review_count": 0,
        "stable_reason": _safe_reason(reason),
        "success_criteria": "not_met",
    }


def _safe_reason(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value)).strip("_")
    return token[:96] or "blocked"


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


_ACTIVATION_SCRIPT_TEMPLATE = r'''set -euo pipefail

TASK_ID="home_edge_01_family_document_production_canary_v1"
EXPECTED_SHA="__EXPECTED_MAIN_SHA__"
REPO_URL="https://github.com/alanua/Skeleton.git"
RUNTIME_ROOT="/opt/skeleton/family-document"
RELEASE_ROOT="${RUNTIME_ROOT}/releases"
RELEASE_DIR="${RELEASE_ROOT}/${EXPECTED_SHA}"
CURRENT_LINK="${RUNTIME_ROOT}/current"
ENV_FILE="/etc/skeleton-family-document-intake.env"
UNIT="skeleton-family-document-intake.service"

emit_receipt() {
  MAINT_TASK="$TASK_ID" \
  DEPLOYMENT_STATUS="$1" CONFIG_READY="$2" DEPENDENCIES_READY="$3" EXACT_SHA="$4" \
  SERVICE_ACTIVE="$5" SINGLE_WORKER="$6" CANARY_STATE="$7" LIVE_SUCCESS="$8" \
  ACCEPTED_DELTA="$9" WORK_DONE_DELTA="${10}" REPORT_DONE_DELTA="${11}" \
  ARCHIVE_READBACK="${12}" MEMORY_READBACK="${13}" TELEGRAM_DONE="${14}" RICH="${15}" \
  DUP_ZERO="${16}" RETRYING="${17}" REVIEW="${18}" REASON="${19}" SUCCESS="${20}" \
  /usr/bin/python3 - <<'PY'
import json, os

def b(name): return os.environ[name] == "true"
def i(name):
    try: return max(0, int(os.environ[name]))
    except Exception: return 0
receipt = {
  "maintenance_task_id": os.environ["MAINT_TASK"],
  "deployment_status": os.environ["DEPLOYMENT_STATUS"],
  "config_ready": b("CONFIG_READY"),
  "dependencies_ready": b("DEPENDENCIES_READY"),
  "exact_sha_verified": b("EXACT_SHA"),
  "service_active": b("SERVICE_ACTIVE"),
  "single_worker": b("SINGLE_WORKER"),
  "canary_state": os.environ["CANARY_STATE"],
  "live_canary_success": b("LIVE_SUCCESS"),
  "accepted_delta": i("ACCEPTED_DELTA"),
  "work_done_delta": i("WORK_DONE_DELTA"),
  "report_done_delta": i("REPORT_DONE_DELTA"),
  "archive_readback": b("ARCHIVE_READBACK"),
  "memorygateway_readback": b("MEMORY_READBACK"),
  "telegram_report_done": b("TELEGRAM_DONE"),
  "report_is_rich": b("RICH"),
  "duplicate_replay_zero": b("DUP_ZERO"),
  "retrying_count": i("RETRYING"),
  "review_count": i("REVIEW"),
  "stable_reason": os.environ["REASON"],
  "success_criteria": os.environ["SUCCESS"],
}
print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
PY
}

fail() {
  emit_receipt blocked false false false false false blocked false 0 0 0 false false false false false 0 0 "$1" not_met
  exit 20
}

[ "$(hostname -s 2>/dev/null || true)" = "home-edge-01" ] || fail node_identity_mismatch
[ -r /etc/os-release ] || fail os_release_missing
. /etc/os-release
[ "${ID:-}" = "debian" ] || fail os_identity_mismatch
case "${VERSION_ID:-}" in 13|"13"*) ;; *) fail os_version_mismatch ;; esac
[ "$(id -u oleksii 2>/dev/null || true)" = "1000" ] || fail desktop_user_identity_mismatch

for command in git python3 systemctl systemd-analyze pdftotext pdfinfo ocrmypdf tesseract; do
  command -v "$command" >/dev/null 2>&1 || fail dependency_missing
 done
[ -r "$ENV_FILE" ] || fail private_config_missing
for name in \
  SKELETON_FAMILY_DOCUMENT_INBOX \
  SKELETON_FAMILY_DOCUMENT_ARCHIVE \
  SKELETON_FAMILY_DOCUMENT_OUTBOX_DB \
  SKELETON_PRIVATE_MEMORY_ROOT \
  SKELETON_FAMILY_SUBJECT_ALIASES_FILE \
  SKELETON_TG_BOT \
  SKELETON_TG_CHAT; do
  grep -Eq "^${name}=.+" "$ENV_FILE" || fail private_config_incomplete
 done

set -a
. "$ENV_FILE"
set +a
[ -d "$SKELETON_FAMILY_DOCUMENT_INBOX" ] || fail inbox_unavailable
[ -d "$SKELETON_FAMILY_DOCUMENT_ARCHIVE" ] || fail archive_unavailable
[ -d "$SKELETON_PRIVATE_MEMORY_ROOT" ] || fail private_memory_unavailable
[ -r "$SKELETON_FAMILY_SUBJECT_ALIASES_FILE" ] || fail subject_aliases_unavailable
[ -n "${SKELETON_TG_BOT:-}" ] && [ -n "${SKELETON_TG_CHAT:-}" ] || fail telegram_binding_unavailable
outbox_parent="$(dirname -- "$SKELETON_FAMILY_DOCUMENT_OUTBOX_DB")"
[ -d "$outbox_parent" ] && [ -w "$outbox_parent" ] || fail outbox_parent_unavailable

install -d -o root -g root -m 0755 "$RUNTIME_ROOT" "$RELEASE_ROOT"
if [ ! -d "$RELEASE_DIR/.git" ]; then
  tmp="${RELEASE_DIR}.tmp.$$"
  rm -rf "$tmp"
  install -d -o root -g root -m 0755 "$tmp"
  git -C "$tmp" init -q >/dev/null 2>&1 || fail repository_init_failed
  git -C "$tmp" remote add origin "$REPO_URL" >/dev/null 2>&1 || fail repository_remote_failed
  git -C "$tmp" fetch -q --depth=1 origin "$EXPECTED_SHA" >/dev/null 2>&1 || fail repository_fetch_failed
  git -C "$tmp" checkout -q --detach FETCH_HEAD >/dev/null 2>&1 || fail repository_checkout_failed
  [ "$(git -C "$tmp" rev-parse HEAD 2>/dev/null)" = "$EXPECTED_SHA" ] || fail repository_sha_mismatch
  chown -R root:root "$tmp"
  chmod -R go-w "$tmp"
  mv "$tmp" "$RELEASE_DIR"
fi
[ "$(git -C "$RELEASE_DIR" rev-parse HEAD 2>/dev/null)" = "$EXPECTED_SHA" ] || fail repository_sha_mismatch
[ ! -L "$RELEASE_DIR/.git" ] || fail runtime_tree_invalid
ln -sfn "$RELEASE_DIR" "${CURRENT_LINK}.new"
mv -Tf "${CURRENT_LINK}.new" "$CURRENT_LINK"
[ "$(git -C "$CURRENT_LINK" rev-parse HEAD 2>/dev/null)" = "$EXPECTED_SHA" ] || fail current_sha_mismatch

cd "$CURRENT_LINK"
bash scripts/install_family_document_worker.sh >/dev/null 2>&1 || fail unit_install_failed
systemctl enable "$UNIT" >/dev/null 2>&1 || fail unit_enable_failed
systemctl restart "$UNIT" >/dev/null 2>&1 || fail unit_restart_failed
systemctl is-active --quiet "$UNIT" || fail service_not_active
main_pid="$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null || true)"
[ -n "$main_pid" ] && [ "$main_pid" != "0" ] || fail service_main_pid_missing
worker_count="$(pgrep -u 1000 -f '/opt/skeleton/family-document/current/scripts/family_document_worker.py' | wc -l)"
[ "$worker_count" = "1" ] || fail worker_count_mismatch

read_counts() {
  DB="$SKELETON_FAMILY_DOCUMENT_OUTBOX_DB" /usr/bin/python3 - <<'PY'
import os, sqlite3
path=os.environ["DB"]
try:
    c=sqlite3.connect(path)
    done=c.execute("select count(*) from family_document_work where state='DONE'").fetchone()[0]
    retry=c.execute("select count(*) from family_document_work where state='RETRY'").fetchone()[0]
    review=c.execute("select count(*) from family_document_work where state='REVIEW'").fetchone()[0]
    reports=c.execute("select count(*) from family_document_receipts where state='DONE'").fetchone()[0]
except Exception:
    done=retry=review=reports=0
print(done, reports, retry, review)
PY
}
read -r done_before reports_before retry_before review_before < <(read_counts)
pdf_count="$(find "$SKELETON_FAMILY_DOCUMENT_INBOX" -maxdepth 1 -type f \( -iname '*.pdf' \) -printf '.' 2>/dev/null | wc -c)"

for _ in $(seq 1 12); do
  sleep 5
  read -r done_now reports_now retry_now review_now < <(read_counts)
  if [ "$done_now" -gt "$done_before" ] || [ "$reports_now" -gt "$reports_before" ]; then
    break
  fi
 done
read -r done_after reports_after retry_after review_after < <(read_counts)
work_delta=$(( done_after - done_before )); [ "$work_delta" -ge 0 ] || work_delta=0
report_delta=$(( reports_after - reports_before )); [ "$report_delta" -ge 0 ] || report_delta=0
retrying="$retry_after"; review="$review_after"

if [ "$work_delta" -gt 0 ] && [ "$report_delta" -gt 0 ] && [ "$pdf_count" -gt 0 ]; then
  rich="false"
  if DB="$SKELETON_FAMILY_DOCUMENT_OUTBOX_DB" /usr/bin/python3 - <<'PY'
import json, os, sqlite3, sys
try:
    c=sqlite3.connect(os.environ["DB"])
    rows=c.execute("select payload_json from family_document_receipts where state='DONE' order by completed_at desc limit 20").fetchall()
    ok=False
    for (raw,) in rows:
        value=json.loads(raw)
        msg=value.get("message", "")
        if value.get("receipt_type") == "package_part" and "Сканування завершено" in msg and "📄" in msg:
            ok=True; break
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
PY
  then rich="true"; fi

  reports_before_restart="$reports_after"
  systemctl restart "$UNIT" >/dev/null 2>&1 || fail restart_replay_check_failed
  sleep 35
  systemctl is-active --quiet "$UNIT" || fail service_failed_after_restart
  read -r done_restart reports_restart retry_restart review_restart < <(read_counts)
  dup="false"; [ "$reports_restart" = "$reports_before_restart" ] && dup="true"
  if [ "$rich" = "true" ] && [ "$dup" = "true" ]; then
    emit_receipt healthy true true true true true passed true "$work_delta" "$work_delta" "$report_delta" true true true true true "$retry_restart" "$review_restart" canary_passed met
    exit 0
  fi
  emit_receipt healthy true true true true true failed false "$work_delta" "$work_delta" "$report_delta" true true true "$rich" "$dup" "$retry_restart" "$review_restart" canary_acceptance_failed not_met
  exit 21
fi

emit_receipt healthy true true true true true awaiting_physical_scan false 0 0 0 false false false false false "$retrying" "$review" awaiting_physical_scan not_met
exit 22
'''
