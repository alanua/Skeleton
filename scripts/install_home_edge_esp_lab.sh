#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD_SCHEMA="skeleton.home_edge.esp_lab_stage1_payload.v1"
MANIFEST_SCHEMA="skeleton.home_edge.esp_lab_stage1_manifest.v1"
RESULT_SCHEMA="skeleton.home_edge.esp_lab_stage1_activation_result.v1"
EMPTY_SHA="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MAX_ENCODED=230000
MAX_DECODED=220000

fail() {
  printf 'BLOCKED: %s\n' "$1" >&2
  exit 2
}

if [[ $# -ne 0 ]]; then
  fail "installer accepts stdin only"
fi

TEST_MODE=0
TEST_ROOT=""
if [[ "${SKELETON_ESP_LAB_INSTALLER_TEST_MODE:-}" == "1" ]]; then
  if [[ -z "${PYTEST_CURRENT_TEST:-}" || -z "${SKELETON_ESP_LAB_TEST_ROOT:-}" ]]; then
    fail "test root requires pytest guard"
  fi
  TEST_MODE=1
  TEST_ROOT="$(realpath -e -- "${SKELETON_ESP_LAB_TEST_ROOT}")"
  [[ "$TEST_ROOT" == /tmp/* ]] || fail "test root must be under tmp"
  [[ -d "$TEST_ROOT" && ! -L "$TEST_ROOT" ]] || fail "test root is unsafe"
  root_mode="$(stat -c '%a' -- "$TEST_ROOT")"
  if (( (8#$root_mode & 8#022) != 0 )); then
    fail "test root mode is unsafe"
  fi
elif env | grep -q '^SKELETON_ESP_LAB_TEST_'; then
  fail "test environment requires guard"
fi

if [[ $TEST_MODE -eq 1 ]]; then
  RUNTIME_BASE="$TEST_ROOT/opt/skeleton/esp-lab"
  WRAPPER_PATH="$TEST_ROOT/usr/local/bin/skeleton-esp-lab"
  OS_RELEASE_PATH="$TEST_ROOT/etc/os-release"
  SYSFS_ROOT="$TEST_ROOT/sys/class/tty"
  APT_GET="$TEST_ROOT/usr/bin/apt-get"
else
  RUNTIME_BASE="/opt/skeleton/esp-lab"
  WRAPPER_PATH="/usr/local/bin/skeleton-esp-lab"
  OS_RELEASE_PATH="/etc/os-release"
  SYSFS_ROOT="/sys/class/tty"
  APT_GET="/usr/bin/apt-get"
fi
PYTHON_BIN="/usr/bin/python3"

WORK_DIR="$(mktemp -d /tmp/skeleton-esp-lab-install.XXXXXX)"
PAYLOAD_JSON="$WORK_DIR/payload.json"
DECODED_DIR="$WORK_DIR/decoded"
STAGING_PARENT=""
TARGET_CREATED=0
BASE_CREATED=0
BASE_WIDENED=0
BASE_MODE=""
WRAPPER_BACKUP=""
WRAPPER_REPLACED=0
DEPENDENCY_INSTALLED=0
COMMITTED=0

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ $COMMITTED -ne 1 ]]; then
    if [[ $WRAPPER_REPLACED -eq 1 && -n "$WRAPPER_BACKUP" && -f "$WRAPPER_BACKUP" ]]; then
      cp -p -- "$WRAPPER_BACKUP" "$WRAPPER_PATH" 2>/dev/null || true
    fi
    if [[ $TARGET_CREATED -eq 1 ]]; then
      if [[ $TEST_MODE -eq 1 && -d "$RUNTIME_BASE" && ! -L "$RUNTIME_BASE" ]]; then
        chmod u+w "$RUNTIME_BASE" 2>/dev/null || true
        BASE_WIDENED=1
        if [[ -d "$TARGET_ROOT" && ! -L "$TARGET_ROOT" ]]; then
          chmod -R u+w "$TARGET_ROOT" 2>/dev/null || true
        fi
      fi
      rm -rf -- "$TARGET_ROOT"
    fi
    if [[ $DEPENDENCY_INSTALLED -eq 1 && $TEST_MODE -eq 1 ]]; then
      rm -f -- "$TEST_ROOT/usr/bin/esptool"
    fi
  fi
  if [[ $BASE_WIDENED -eq 1 && -n "$BASE_MODE" && -d "$RUNTIME_BASE" && ! -L "$RUNTIME_BASE" ]]; then
    chmod "$BASE_MODE" "$RUNTIME_BASE" 2>/dev/null || true
  fi
  if [[ $BASE_CREATED -eq 1 && -d "$RUNTIME_BASE" && -z "$(find "$RUNTIME_BASE" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    rmdir "$RUNTIME_BASE" 2>/dev/null || true
  fi
  rm -rf -- "$WORK_DIR"
  if [[ -n "$STAGING_PARENT" ]]; then rm -rf -- "$STAGING_PARENT"; fi
  exit "$rc"
}
trap cleanup EXIT

dd bs=$((MAX_ENCODED + 1)) count=1 of="$PAYLOAD_JSON" status=none
payload_size="$(stat -c '%s' -- "$PAYLOAD_JSON")"
if (( payload_size == 0 || payload_size > MAX_ENCODED )); then
  fail "payload size is invalid"
fi

mkdir -p "$DECODED_DIR"
export PAYLOAD_JSON DECODED_DIR PAYLOAD_SCHEMA MANIFEST_SCHEMA EMPTY_SHA MAX_DECODED
SOURCE_SHA="$("$PYTHON_BIN" <<'PY'
import base64
import binascii
import hashlib
import json
import os
import re
from pathlib import Path

payload_path = Path(os.environ["PAYLOAD_JSON"])
decoded_dir = Path(os.environ["DECODED_DIR"])
payload_schema = os.environ["PAYLOAD_SCHEMA"]
manifest_schema = os.environ["MANIFEST_SCHEMA"]
empty_sha = os.environ["EMPTY_SHA"]
max_decoded = int(os.environ["MAX_DECODED"])

def blocked(message: str) -> None:
    raise SystemExit(f"BLOCKED: {message}")

try:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
except Exception:
    blocked("payload is not valid json")
if not isinstance(payload, dict) or list(payload.keys()) != ["schema", "source_sha", "files"]:
    blocked("payload keys are invalid")
if payload["schema"] != payload_schema:
    blocked("payload schema is invalid")
source_sha = payload["source_sha"]
if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
    blocked("source sha is invalid")
files = payload["files"]
if not isinstance(files, list) or len(files) != 2:
    blocked("payload file list is invalid")
expected_paths = ["core/__init__.py", "core/home_edge/esp_lab.py"]
manifest_files = {}
decoded_total = 0
for index, item in enumerate(files):
    if not isinstance(item, dict) or list(item.keys()) != ["path", "sha256", "base64"]:
        blocked("payload file keys are invalid")
    if item["path"] != expected_paths[index]:
        blocked("payload file order is invalid")
    sha = item["sha256"]
    encoded = item["base64"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        blocked("payload file hash is invalid")
    if not isinstance(encoded, str):
        blocked("payload file body is invalid")
    try:
        body = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        blocked("payload base64 is invalid")
    decoded_total += len(body)
    if decoded_total > max_decoded:
        blocked("payload decoded size is invalid")
    actual = hashlib.sha256(body).hexdigest()
    if actual != sha:
        blocked("payload hash mismatch")
    if index == 0 and (len(body) != 0 or sha != empty_sha):
        blocked("isolated package marker is invalid")
    destination = decoded_dir / item["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    manifest_files[item["path"]] = {"sha256": sha, "size": len(body)}
manifest = {
    "files": manifest_files,
    "schema": manifest_schema,
    "source_sha": source_sha,
}
(decoded_dir / "manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
print(source_sha)
PY
)" || exit 2

TARGET_ROOT="$RUNTIME_BASE/$SOURCE_SHA"

validate_os_release() {
  [[ -f "$OS_RELEASE_PATH" && ! -L "$OS_RELEASE_PATH" ]] || fail "os release is unavailable"
  local id="" version=""
  while IFS='=' read -r key value; do
    value="${value%\"}"
    value="${value#\"}"
    case "$key" in
      ID) id="$value" ;;
      VERSION_ID) version="$value" ;;
    esac
  done < "$OS_RELEASE_PATH"
  [[ "$id" == "debian" && "$version" == "13" ]] || fail "host os is unsupported"
}

if [[ $TEST_MODE -eq 0 ]]; then
  [[ ${EUID:-$(id -u)} -eq 0 ]] || fail "installer must run as root"
  [[ "$(hostname)" == "home-edge-01" ]] || fail "host is unsupported"
fi
validate_os_release
[[ -x "$PYTHON_BIN" ]] || fail "python runtime is unavailable"

if [[ -e "$RUNTIME_BASE" || -L "$RUNTIME_BASE" ]]; then
  [[ -d "$RUNTIME_BASE" && ! -L "$RUNTIME_BASE" ]] || fail "runtime base is unsafe"
  base_mode="$(stat -c '%a' -- "$RUNTIME_BASE")"
  if [[ $TEST_MODE -eq 0 ]]; then
    [[ "$(stat -c '%u:%g' -- "$RUNTIME_BASE")" == "0:0" ]] || fail "runtime base ownership is unsafe"
    if (( (8#$base_mode & 8#022) != 0 )); then fail "runtime base mode is unsafe"; fi
  fi
else
  mkdir -p -- "$RUNTIME_BASE"
  BASE_CREATED=1
  chmod 0555 "$RUNTIME_BASE"
fi

validate_tree() {
  local root="$1"
  ROOT_TO_CHECK="$root" SOURCE_TO_CHECK="$SOURCE_SHA" "$PYTHON_BIN" <<'PY'
import hashlib
import json
import os
import stat
from pathlib import Path

root = Path(os.environ["ROOT_TO_CHECK"])
source = os.environ["SOURCE_TO_CHECK"]
expected_manifest_path = Path(os.environ["DECODED_DIR"]) / "manifest.json"
expected_manifest = json.loads(expected_manifest_path.read_text(encoding="utf-8"))
expected_manifest = {
    "files": expected_manifest["files"],
    "schema": "skeleton.home_edge.esp_lab_stage1_manifest.v1",
    "source_sha": source,
}
expected_manifest_bytes = json.dumps(expected_manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
expected = {
    ".": ("dir", 0o555),
    "core": ("dir", 0o555),
    "core/home_edge": ("dir", 0o555),
    "core/__init__.py": ("file", 0o444),
    "core/home_edge/esp_lab.py": ("file", 0o444),
    "manifest.json": ("file", 0o444),
}
seen = set()
for path in [root, *root.rglob("*")]:
    rel = "." if path == root else path.relative_to(root).as_posix()
    seen.add(rel)
    if rel not in expected:
        raise SystemExit(1)
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise SystemExit(1)
    kind, mode = expected[rel]
    if kind == "dir" and not stat.S_ISDIR(st.st_mode):
        raise SystemExit(1)
    if kind == "file" and not stat.S_ISREG(st.st_mode):
        raise SystemExit(1)
    if stat.S_IMODE(st.st_mode) != mode:
        raise SystemExit(1)
    if os.environ.get("CHECK_OWNERSHIP") == "1" and (st.st_uid != 0 or st.st_gid != 0):
        raise SystemExit(1)
if seen != set(expected):
    raise SystemExit(1)
if (root / "core/__init__.py").read_bytes() != b"":
    raise SystemExit(1)
if hashlib.sha256((root / "core/__init__.py").read_bytes()).hexdigest() != expected_manifest["files"]["core/__init__.py"]["sha256"]:
    raise SystemExit(1)
if hashlib.sha256((root / "core/home_edge/esp_lab.py").read_bytes()).hexdigest() != expected_manifest["files"]["core/home_edge/esp_lab.py"]["sha256"]:
    raise SystemExit(1)
if (root / "manifest.json").read_bytes() != expected_manifest_bytes:
    raise SystemExit(1)
PY
}

TARGET_VALID_START=0
if [[ -e "$TARGET_ROOT" || -L "$TARGET_ROOT" ]]; then
  if [[ $TEST_MODE -eq 0 ]]; then export CHECK_OWNERSHIP=1; else export CHECK_OWNERSHIP=0; fi
  validate_tree "$TARGET_ROOT" || fail "existing runtime target is unsafe"
  TARGET_VALID_START=1
fi

WRAPPER_VALID_START=0
if [[ -e "$WRAPPER_PATH" || -L "$WRAPPER_PATH" ]]; then
  [[ -f "$WRAPPER_PATH" && ! -L "$WRAPPER_PATH" ]] || fail "existing wrapper is unsafe"
  expected_wrapper="$(mktemp "$WORK_DIR/wrapper.expected.XXXXXX")"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -Eeuo pipefail\n'
    printf 'cd /tmp\n'
    printf 'PYTHONPATH=%q exec /usr/bin/python3 -m core.home_edge.esp_lab "$@"\n' "$TARGET_ROOT"
  } > "$expected_wrapper"
  if cmp -s "$WRAPPER_PATH" "$expected_wrapper"; then
    wrapper_mode="$(stat -c '%a' -- "$WRAPPER_PATH")"
    if [[ "$wrapper_mode" == "755" ]]; then
      if [[ $TEST_MODE -eq 0 ]]; then
        [[ "$(stat -c '%u:%g' -- "$WRAPPER_PATH")" == "0:0" ]] || fail "existing wrapper ownership is unsafe"
      fi
      WRAPPER_VALID_START=1
    fi
  fi
  if [[ $WRAPPER_VALID_START -eq 0 ]]; then
    WRAPPER_BACKUP="$WORK_DIR/wrapper.backup"
    cp -p -- "$WRAPPER_PATH" "$WRAPPER_BACKUP"
  fi
fi

if [[ $TEST_MODE -eq 1 ]]; then
  [[ -x "$APT_GET" && ! -L "$APT_GET" ]] || fail "test apt is unavailable"
fi
if [[ $TEST_MODE -eq 0 ]]; then
  if ! command -v esptool >/dev/null 2>&1; then
    "$APT_GET" update
    DEBIAN_FRONTEND=noninteractive "$APT_GET" install -y --no-install-recommends esptool
    DEPENDENCY_INSTALLED=1
  fi
else
  if [[ ! -x "$TEST_ROOT/usr/bin/esptool" ]]; then
    "$APT_GET" update
    DEBIAN_FRONTEND=noninteractive "$APT_GET" install -y --no-install-recommends esptool
    DEPENDENCY_INSTALLED=1
  fi
fi

if [[ $TARGET_VALID_START -eq 0 ]]; then
  BASE_MODE="$(stat -c '%a' -- "$RUNTIME_BASE")"
  if [[ $TEST_MODE -eq 1 ]]; then
    chmod u+w "$RUNTIME_BASE"
    BASE_WIDENED=1
  fi
  STAGING_PARENT="$(mktemp -d "$RUNTIME_BASE/../.esp-lab-stage.XXXXXX")"
  mkdir -p "$STAGING_PARENT/core/home_edge"
  install -m 0444 "$DECODED_DIR/core/__init__.py" "$STAGING_PARENT/core/__init__.py"
  install -m 0444 "$DECODED_DIR/core/home_edge/esp_lab.py" "$STAGING_PARENT/core/home_edge/esp_lab.py"
  install -m 0444 "$DECODED_DIR/manifest.json" "$STAGING_PARENT/manifest.json"
  chmod 0755 "$STAGING_PARENT"
  chmod 0555 "$STAGING_PARENT/core" "$STAGING_PARENT/core/home_edge"
  if [[ $TEST_MODE -eq 0 ]]; then
    chown -R root:root "$STAGING_PARENT"
  fi
  mv -- "$STAGING_PARENT" "$TARGET_ROOT"
  STAGING_PARENT=""
  TARGET_CREATED=1
  chmod 0555 "$TARGET_ROOT"
  if [[ $BASE_WIDENED -eq 1 ]]; then
    chmod "$BASE_MODE" "$RUNTIME_BASE"
    BASE_WIDENED=0
  fi
fi

if [[ $TEST_MODE -eq 0 ]]; then export CHECK_OWNERSHIP=1; else export CHECK_OWNERSHIP=0; fi
validate_tree "$TARGET_ROOT" || fail "runtime target validation failed"

if [[ $WRAPPER_VALID_START -eq 0 ]]; then
  mkdir -p "$(dirname -- "$WRAPPER_PATH")"
  wrapper_tmp="$(mktemp "$(dirname -- "$WRAPPER_PATH")/.skeleton-esp-lab.XXXXXX")"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -Eeuo pipefail\n'
    printf 'cd /tmp\n'
    printf 'PYTHONPATH=%q exec /usr/bin/python3 -m core.home_edge.esp_lab "$@"\n' "$TARGET_ROOT"
  } > "$wrapper_tmp"
  chmod 0755 "$wrapper_tmp"
  if [[ $TEST_MODE -eq 0 ]]; then chown root:root "$wrapper_tmp"; fi
  mv -f -- "$wrapper_tmp" "$WRAPPER_PATH"
  WRAPPER_REPLACED=1
fi

CANARY_STDOUT="$WORK_DIR/canary.stdout"
if ! "$WRAPPER_PATH" discover --sysfs-root "$SYSFS_ROOT" >"$CANARY_STDOUT" 2>"$WORK_DIR/canary.stderr"; then
  fail "wrapper canary failed"
fi
export CANARY_STDOUT
CANDIDATE_COUNT="$("$PYTHON_BIN" <<'PY'
import json
import os
from pathlib import Path

data = json.loads(Path(os.environ["CANARY_STDOUT"]).read_text(encoding="utf-8"))
if not isinstance(data, list):
    raise SystemExit("BLOCKED: canary did not return a list")
print(len(data))
PY
)" || exit 2

if [[ "$CANDIDATE_COUNT" == "0" ]]; then
  DEVICE_CANARY="awaiting_physical_device"
else
  DEVICE_CANARY="serial_candidates_present"
fi
if [[ $TARGET_VALID_START -eq 1 && $WRAPPER_VALID_START -eq 1 ]]; then
  IDEMPOTENT_REUSE=true
else
  IDEMPOTENT_REUSE=false
fi
if [[ $DEPENDENCY_INSTALLED -eq 1 ]]; then
  DEPENDENCY_INSTALLED_PY=True
else
  DEPENDENCY_INSTALLED_PY=False
fi
if [[ "$IDEMPOTENT_REUSE" == "true" ]]; then
  IDEMPOTENT_REUSE_PY=True
else
  IDEMPOTENT_REUSE_PY=False
fi

COMMITTED=1
"$PYTHON_BIN" <<PY
import json
print(json.dumps({
    "schema": "$RESULT_SCHEMA",
    "runtime_state": "READY",
    "source_sha": "$SOURCE_SHA",
    "candidate_count": int("$CANDIDATE_COUNT"),
    "device_canary": "$DEVICE_CANARY",
    "dependency_installed_by_operation": $DEPENDENCY_INSTALLED_PY,
    "idempotent_reuse": $IDEMPOTENT_REUSE_PY,
}, separators=(",", ":")))
PY
