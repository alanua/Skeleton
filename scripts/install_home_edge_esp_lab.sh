#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf '%s\n' \
    'Usage: install_home_edge_esp_lab.sh [--apply] [--root DIR] < install-request.json' \
    '' \
    'Reads one bounded JSON install request from stdin. Without --apply it emits a' \
    'public-safe plan only. With --apply it installs the Stage 1 ESP Lab helper' \
    'and Windows connector files under the selected filesystem root.'
}

APPLY=0
ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --root)
      ROOT="${2:?--root requires a directory}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unsupported argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SOURCE_TREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PREFIX="${ROOT%/}"
LIB_DIR="${PREFIX}/usr/local/lib/skeleton-esp-lab"
BIN_DIR="${PREFIX}/usr/local/bin"
STATE_DIR="${PREFIX}/var/lib/skeleton/esp-lab"
CONF_DIR="${PREFIX}/etc/skeleton/esp-lab"
MANIFEST_PATH="${CONF_DIR}/install-manifest.json"
SECRET_PATH="${CONF_DIR}/shared-secret"
REQUEST_TMP=""
PLAN_TMP=""
COMMITTED=0

cleanup() {
  local rc=$?
  if [[ -n "$REQUEST_TMP" && -e "$REQUEST_TMP" ]]; then
    rm -f "$REQUEST_TMP"
  fi
  if [[ -n "$PLAN_TMP" && -e "$PLAN_TMP" ]]; then
    rm -f "$PLAN_TMP"
  fi
  exit "$rc"
}
trap cleanup EXIT

REQUEST_TMP="$(mktemp "${TMPDIR:-/tmp}/skeleton-esp-lab-request.XXXXXX")"
PLAN_TMP="$(mktemp "${TMPDIR:-/tmp}/skeleton-esp-lab-plan.XXXXXX")"
chmod 0600 "$REQUEST_TMP" "$PLAN_TMP"

dd bs=32768 count=1 of="$REQUEST_TMP" status=none
if [[ ! -s "$REQUEST_TMP" ]]; then
  printf 'missing stdin JSON install request\n' >&2
  exit 2
fi
if [[ "$(wc -c < "$REQUEST_TMP")" -ge 32768 ]]; then
  printf 'stdin JSON install request exceeds limit\n' >&2
  exit 2
fi

"$PYTHON_BIN" - "$REQUEST_TMP" "$PLAN_TMP" "$MANIFEST_PATH" "$SECRET_PATH" "$LIB_DIR" "$BIN_DIR" "$STATE_DIR" "$APPLY" <<'PY'
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

request_path, plan_path, manifest_path, secret_path, lib_dir, bin_dir, state_dir, apply_value = sys.argv[1:]
allowed_fields = {
    "schema",
    "node_id",
    "bind_host",
    "port",
    "allow_lan",
    "enable_read_only_execution",
    "shared_secret",
    "allowed_node_ids",
    "esptool_command",
}
public_id = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

try:
    data = json.loads(Path(request_path).read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    raise SystemExit(f"invalid stdin JSON install request: {exc.msg}")

if not isinstance(data, dict):
    raise SystemExit("stdin JSON install request must be an object")
unknown = sorted(set(data) - allowed_fields)
if unknown:
    raise SystemExit(f"unsupported install request field: {unknown[0]}")
if data.get("schema") != "skeleton.home_edge.esp_lab.stdin_installer.v1":
    raise SystemExit("invalid install request schema")

node_id = data.get("node_id")
if not isinstance(node_id, str) or not public_id.fullmatch(node_id):
    raise SystemExit("invalid node_id")
bind_host = data.get("bind_host", "127.0.0.1")
if bind_host not in {"127.0.0.1", "::1", "localhost"}:
    if data.get("allow_lan") is not True:
        raise SystemExit("non-loopback bind requires allow_lan")
    if not data.get("shared_secret"):
        raise SystemExit("lan bind requires stdin shared_secret")
port = data.get("port", 9443)
if not isinstance(port, int) or not 0 <= port <= 65535:
    raise SystemExit("invalid port")
if not isinstance(data.get("enable_read_only_execution", False), bool):
    raise SystemExit("enable_read_only_execution must be boolean")
secret = data.get("shared_secret")
if secret is not None and (not isinstance(secret, str) or len(secret.encode("utf-8")) < 16):
    raise SystemExit("shared_secret must be at least 16 bytes")
allowed_node_ids = data.get("allowed_node_ids", [node_id])
if not isinstance(allowed_node_ids, list) or not allowed_node_ids:
    raise SystemExit("allowed_node_ids must be a non-empty list")
if any(not isinstance(item, str) or not public_id.fullmatch(item) for item in allowed_node_ids):
    raise SystemExit("invalid allowed_node_ids entry")
esptool_command = data.get("esptool_command")
if esptool_command is not None:
    if not isinstance(esptool_command, list) or not esptool_command:
        raise SystemExit("esptool_command must be a non-empty argv array")
    normalized = [str(item).strip().lower().replace("_", "-") for item in esptool_command]
    if any(("flash" in item and item != "flash-id") or item.startswith("erase-") or item.endswith("-mem") for item in normalized):
        raise SystemExit("esptool_command contains unsupported token")

manifest = {
    "schema": "skeleton.home_edge.esp_lab.stdin_install_manifest.v1",
    "adapter_kind": "linux_tty_and_windows_com",
    "bind_host": bind_host,
    "default_execution": "read_only" if data.get("enable_read_only_execution", False) else "plan",
    "installed_files": [
        f"{bin_dir}/skeleton-esp-lab",
        f"{bin_dir}/skeleton-esp-lab-windows",
        f"{lib_dir}/scripts/home_edge_esp_lab.py",
        f"{lib_dir}/scripts/home_edge_esp_lab_windows_connector.py",
        f"{lib_dir}/core/home_edge/esp_lab.py",
        f"{lib_dir}/core/home_edge/esp_lab_connector.py",
        manifest_path,
    ],
    "node_id_hash": hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16],
    "port": port,
    "public_safe": True,
    "secret_configured": secret is not None,
    "service_installed": False,
    "state_dir": state_dir,
    "supported_operations": [
        "discover_serial_candidates",
        "identify_chip",
        "inspect_flash_identity",
        "observe_serial_bounded",
    ],
}
if apply_value == "0":
    manifest["action"] = "plan_only"
Path(plan_path).write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY

if [[ "$APPLY" -eq 0 ]]; then
  cat "$PLAN_TMP"
  exit 0
fi

for path in \
  "$SOURCE_TREE/scripts/home_edge_esp_lab.py" \
  "$SOURCE_TREE/scripts/home_edge_esp_lab_windows_connector.py" \
  "$SOURCE_TREE/core/home_edge/esp_lab.py" \
  "$SOURCE_TREE/core/home_edge/esp_lab_connector.py"
do
  if [[ ! -r "$path" ]]; then
    printf 'required ESP Lab source file is unavailable\n' >&2
    exit 2
  fi
done

STAGING="${LIB_DIR}.staging.$$"
PREVIOUS="${LIB_DIR}.previous"
rm -rf "$STAGING"
mkdir -p \
  "$STAGING/scripts" \
  "$STAGING/core/home_edge" \
  "$BIN_DIR" \
  "$CONF_DIR" \
  "$STATE_DIR/private" \
  "$STATE_DIR/public"

printf '\n' > "$STAGING/core/__init__.py"
printf '\n' > "$STAGING/core/home_edge/__init__.py"
install -m 0644 "$SOURCE_TREE/core/home_edge/esp_lab.py" "$STAGING/core/home_edge/esp_lab.py"
install -m 0644 "$SOURCE_TREE/core/home_edge/esp_lab_connector.py" "$STAGING/core/home_edge/esp_lab_connector.py"
install -m 0755 "$SOURCE_TREE/scripts/home_edge_esp_lab.py" "$STAGING/scripts/home_edge_esp_lab.py"
install -m 0755 "$SOURCE_TREE/scripts/home_edge_esp_lab_windows_connector.py" "$STAGING/scripts/home_edge_esp_lab_windows_connector.py"

if [[ -d "$LIB_DIR" ]]; then
  rm -rf "$PREVIOUS"
  mv "$LIB_DIR" "$PREVIOUS"
fi
mv "$STAGING" "$LIB_DIR"

{
  printf '#!/usr/bin/env bash\n'
  printf 'set -Eeuo pipefail\n'
  printf 'export PYTHONPATH=%q\n' "$LIB_DIR"
  printf 'exec %q %q "$@"\n' "$PYTHON_BIN" "$LIB_DIR/scripts/home_edge_esp_lab.py"
} > "$BIN_DIR/skeleton-esp-lab"
{
  printf '#!/usr/bin/env bash\n'
  printf 'set -Eeuo pipefail\n'
  printf 'export PYTHONPATH=%q\n' "$LIB_DIR"
  printf 'exec %q %q "$@"\n' "$PYTHON_BIN" "$LIB_DIR/scripts/home_edge_esp_lab_windows_connector.py"
} > "$BIN_DIR/skeleton-esp-lab-windows"
chmod 0755 "$BIN_DIR/skeleton-esp-lab" "$BIN_DIR/skeleton-esp-lab-windows"

install -m 0600 "$PLAN_TMP" "$MANIFEST_PATH"
"$PYTHON_BIN" - "$REQUEST_TMP" "$SECRET_PATH" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
secret = request.get("shared_secret")
if secret is not None:
    target = Path(sys.argv[2])
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret)
        handle.write("\n")
PY

COMMITTED=1
printf '{"status":"installed","manifest":%s,"committed":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$MANIFEST_PATH")" "$COMMITTED"
