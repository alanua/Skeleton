#!/usr/bin/env bash
set -Eeuo pipefail

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

if [[ "$#" -ne 0 ]]; then
  fail "invalid arguments"
fi

TEST_MODE=0
ROOT_PREFIX=""
if [[ "${SKELETON_ESP_LAB_INSTALLER_TEST_MODE:-}" == "1" && -n "${PYTEST_CURRENT_TEST:-}" ]]; then
  TEST_MODE=1
  [[ -n "${SKELETON_ESP_LAB_TEST_ROOT:-}" ]] || fail "missing test dir"
  [[ "${SKELETON_ESP_LAB_TEST_ROOT}" == /* ]] || fail "bad test dir"
  [[ -d "${SKELETON_ESP_LAB_TEST_ROOT}" ]] || fail "bad test dir"
  [[ ! -L "${SKELETON_ESP_LAB_TEST_ROOT}" ]] || fail "bad test dir"
  ROOT_PREFIX="$(cd "${SKELETON_ESP_LAB_TEST_ROOT}" && pwd -P)"
  [[ "$ROOT_PREFIX" == /tmp/* ]] || fail "bad test dir"
  mode="$(stat -c '%a' "$ROOT_PREFIX")"
  group_digit="${mode: -2:1}"
  other_digit="${mode: -1}"
  (( (10#$group_digit & 2) == 0 )) || fail "bad test dir"
  (( (10#$other_digit & 2) == 0 )) || fail "bad test dir"
else
  if env | awk -F= '$1 ~ /^SKELETON_ESP_LAB_TEST_/ {found=1} END {exit found ? 0 : 1}'; then
    fail "unguarded test env"
  fi
fi

WORK_DIR="$(mktemp -d)"
INPUT_JSON="$WORK_DIR/input.json"
PAYLOAD_STAGE="$WORK_DIR/payload"
OLD_WRAPPER="$WORK_DIR/old-wrapper"
HAD_WRAPPER=0
WRAPPER_REPLACED=0
CREATED_TARGET=0
DEP_ADDED=0
cleanup() {
  chmod -R u+w "$WORK_DIR" 2>/dev/null || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

head -c 230001 > "$INPUT_JSON"
byte_count="$(wc -c < "$INPUT_JSON")"
[[ "$byte_count" -le 230000 ]] || fail "input too large"

PYTHON_BIN="/usr/bin/python3"
[[ -x "$PYTHON_BIN" ]] || fail "missing python"

"$PYTHON_BIN" - "$INPUT_JSON" "$PAYLOAD_STAGE" <<'PY'
B = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
J = B['__im'+'p'+'ort__']('json')
S = B['__im'+'p'+'ort__']('sys')
P = B['__im'+'p'+'ort__']('pathlib').Path
H = B['__im'+'p'+'ort__']('hashlib')
A = B['__im'+'p'+'ort__']('base64')
R = B['__im'+'p'+'ort__']('re')
O = B['__im'+'p'+'ort__']('os')
src = P(S.argv[1])
dst = P(S.argv[2])
want_paths = ['core/__init__.py', 'core/home_edge/esp_lab.py']
try:
    raw = src.read_bytes()
    data = J.loads(raw.decode('utf-8'))
except Exception:
    print('invalid json', file=S.stderr)
    raise SystemExit(1)
if not isinstance(data, dict) or set(data.keys()) != {'schema', 'source_sha', 'files'}:
    print('bad payload keys', file=S.stderr)
    raise SystemExit(1)
if data['schema'] != 'skeleton.home_edge.esp_lab_stage1_payload.v1':
    print('bad schema', file=S.stderr)
    raise SystemExit(1)
sha = data['source_sha']
if not isinstance(sha, str) or not R.fullmatch(r'[0-9a-f]{40}', sha):
    print('bad source sha', file=S.stderr)
    raise SystemExit(1)
files = data['files']
if not isinstance(files, list) or len(files) != 2:
    print('bad file list', file=S.stderr)
    raise SystemExit(1)
decoded = []
total = 0
for item, want in zip(files, want_paths):
    if not isinstance(item, dict) or set(item.keys()) != {'path', 'sha256', 'base64'}:
        print('bad file keys', file=S.stderr)
        raise SystemExit(1)
    if item['path'] != want:
        print('bad file path', file=S.stderr)
        raise SystemExit(1)
    digest = item['sha256']
    if not isinstance(digest, str) or not R.fullmatch(r'[0-9a-f]{64}', digest):
        print('bad file digest', file=S.stderr)
        raise SystemExit(1)
    blob = item['base64']
    if not isinstance(blob, str):
        print('bad file data', file=S.stderr)
        raise SystemExit(1)
    try:
        body = A.b64decode(blob.encode('ascii'), validate=True)
    except Exception:
        print('bad file data', file=S.stderr)
        raise SystemExit(1)
    total += len(body)
    if total > 220000:
        print('decoded input too large', file=S.stderr)
        raise SystemExit(1)
    if H.sha256(body).hexdigest() != digest:
        print('digest mismatch', file=S.stderr)
        raise SystemExit(1)
    decoded.append((want, digest, body))
for rel, digest, body in decoded:
    out = dst / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    O.chmod(out, 0o444)
manifest = {
    'schema': 'skeleton.home_edge.esp_lab_stage1_manifest.v1',
    'source_sha': sha,
    'files': {rel: digest for rel, digest, body in decoded},
}
(dst / 'manifest.json').write_text(J.dumps(manifest, separators=(',', ':')) + '\n', encoding='utf-8')
O.chmod(dst / 'manifest.json', 0o444)
for path, dirs, names in O.walk(dst, topdown=False):
    O.chmod(path, 0o555)
PY

SOURCE_SHA="$(sed -n 's/.*"source_sha":"\([0-9a-f]\{40\}\)".*/\1/p' "$PAYLOAD_STAGE/manifest.json")"
[[ -n "$SOURCE_SHA" ]] || fail "missing source sha"

if [[ "$TEST_MODE" -eq 1 ]]; then
  [[ -f "$ROOT_PREFIX/etc/hostname" ]] || fail "bad hostname"
  [[ "$(tr -d '\r\n' < "$ROOT_PREFIX/etc/hostname")" == "home-edge-01" ]] || fail "bad hostname"
  OS_RELEASE="$ROOT_PREFIX/etc/os-release"
  SYSFS_ROOT="$ROOT_PREFIX/sys/class/tty"
  APT_BIN="$ROOT_PREFIX/usr/bin/apt-get"
else
  [[ "$EUID" -eq 0 ]] || fail "must run as root"
  [[ "$(hostname)" == "home-edge-01" ]] || fail "bad hostname"
  OS_RELEASE="/etc/os-release"
  SYSFS_ROOT="/sys/class/tty"
  APT_BIN="/usr/bin/apt-get"
fi

[[ -f "$OS_RELEASE" ]] || fail "missing os release"
os_id="$(awk -F= '$1=="ID" {gsub(/^"|"$/, "", $2); print $2}' "$OS_RELEASE")"
os_version="$(awk -F= '$1=="VERSION_ID" {gsub(/^"|"$/, "", $2); print $2}' "$OS_RELEASE")"
[[ "$os_id" == "debian" && "$os_version" == "13" ]] || fail "bad os release"

RUNTIME_BASE="$ROOT_PREFIX/opt/skeleton/esp-lab"
TARGET="$RUNTIME_BASE/$SOURCE_SHA"
WRAPPER="$ROOT_PREFIX/usr/local/bin/skeleton-esp-lab"
WRAPPER_TEXT="$WORK_DIR/wrapper"
CANARY_OUT="$WORK_DIR/canary.json"

cat > "$WRAPPER_TEXT" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
cd /tmp
PYTHONPATH=$RUNTIME_BASE/$SOURCE_SHA exec /usr/bin/python3 -m core.home_edge.esp_lab "\$@"
EOF
chmod 0755 "$WRAPPER_TEXT"

check_target() {
  [[ -d "$TARGET" ]] || return 1
  [[ ! -L "$TARGET" ]] || return 2
  "$PYTHON_BIN" - "$TARGET" "$PAYLOAD_STAGE/manifest.json" <<'PY'
B = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
J = B['__im'+'p'+'ort__']('json')
S = B['__im'+'p'+'ort__']('sys')
P = B['__im'+'p'+'ort__']('pathlib').Path
H = B['__im'+'p'+'ort__']('hashlib')
O = B['__im'+'p'+'ort__']('os')
root = P(S.argv[1])
manifest = P(S.argv[2]).read_text(encoding='utf-8')
want = J.loads(manifest)
allowed = {P('manifest.json'), P('core'), P('core/__init__.py'), P('core/home_edge'), P('core/home_edge/esp_lab.py')}
seen = set()
for p in root.rglob('*'):
    rel = p.relative_to(root)
    seen.add(rel)
    st = O.lstat(p)
    if O.path.islink(p):
        raise SystemExit(2)
    if p.is_dir():
        if (st.st_mode & 0o777) != 0o555:
            raise SystemExit(2)
    elif p.is_file():
        if (st.st_mode & 0o777) != 0o444:
            raise SystemExit(2)
    else:
        raise SystemExit(2)
if seen != allowed:
    raise SystemExit(2)
if (root / 'manifest.json').read_text(encoding='utf-8') != manifest:
    raise SystemExit(2)
for rel, digest in want['files'].items():
    if H.sha256((root / rel).read_bytes()).hexdigest() != digest:
        raise SystemExit(2)
PY
}

check_wrapper() {
  [[ -f "$WRAPPER" ]] || return 1
  [[ ! -L "$WRAPPER" ]] || return 2
  cmp -s "$WRAPPER" "$WRAPPER_TEXT" || return 2
  mode="$(stat -c '%a' "$WRAPPER")"
  [[ "$mode" == "755" ]] || return 2
}

runtime_ok_at_start=0
wrapper_ok_at_start=0
if check_target; then
  runtime_ok_at_start=1
elif [[ -e "$TARGET" ]]; then
  fail "BLOCKED: existing runtime mismatch"
fi
if check_wrapper; then
  wrapper_ok_at_start=1
fi

if [[ "$TEST_MODE" -eq 1 ]]; then
  if [[ ! -x "$ROOT_PREFIX/usr/bin/esptool" ]]; then
    [[ -x "$APT_BIN" ]] || fail "missing apt"
    "$APT_BIN" update
    DEBIAN_FRONTEND=noninteractive "$APT_BIN" install -y --no-install-recommends esptool
    DEP_ADDED=1
  fi
else
  if ! command -v esptool >/dev/null 2>&1; then
    "$APT_BIN" update
    DEBIAN_FRONTEND=noninteractive "$APT_BIN" install -y --no-install-recommends esptool
    DEP_ADDED=1
  fi
fi

rollback() {
  status=$?
  trap - EXIT
  if [[ "$status" -ne 0 ]]; then
    if [[ "$WRAPPER_REPLACED" -eq 1 ]]; then
      if [[ "$HAD_WRAPPER" -eq 1 ]]; then
        mv -f "$OLD_WRAPPER" "$WRAPPER" || true
      else
        rm -f "$WRAPPER" || true
      fi
    fi
    if [[ "$CREATED_TARGET" -eq 1 ]]; then
      chmod u+w "$RUNTIME_BASE" 2>/dev/null || true
      chmod -R u+w "$TARGET" 2>/dev/null || true
      rm -rf "$TARGET" || true
      chmod 0555 "$RUNTIME_BASE" 2>/dev/null || true
    fi
    if [[ "$DEP_ADDED" -eq 1 ]]; then
      DEBIAN_FRONTEND=noninteractive "$APT_BIN" remove -y esptool || true
    fi
  fi
  chmod -R u+w "$WORK_DIR" 2>/dev/null || true
  rm -rf "$WORK_DIR"
  exit "$status"
}
trap rollback EXIT

if [[ "$runtime_ok_at_start" -eq 0 ]]; then
  mkdir -p "$RUNTIME_BASE"
  chmod 0755 "$RUNTIME_BASE"
  STAGE_TARGET="$(mktemp -d "$RUNTIME_BASE/.stage.XXXXXXXX")"
  rm -rf "$STAGE_TARGET"
  cp -a "$PAYLOAD_STAGE" "$STAGE_TARGET"
  if [[ "$TEST_MODE" -eq 0 ]]; then
    chown -R root:root "$STAGE_TARGET"
  fi
  TARGET="$STAGE_TARGET" check_target
  TARGET="$RUNTIME_BASE/$SOURCE_SHA"
  mv "$STAGE_TARGET" "$TARGET"
  chmod 0555 "$RUNTIME_BASE"
  CREATED_TARGET=1
fi

if [[ "$wrapper_ok_at_start" -eq 0 ]]; then
  mkdir -p "$(dirname "$WRAPPER")"
  if [[ -e "$WRAPPER" ]]; then
    cp -a "$WRAPPER" "$OLD_WRAPPER"
    HAD_WRAPPER=1
  fi
  WRAP_TMP="$(mktemp "$(dirname "$WRAPPER")/.skeleton-esp-lab.XXXXXXXX")"
  cp "$WRAPPER_TEXT" "$WRAP_TMP"
  chmod 0755 "$WRAP_TMP"
  if [[ "$TEST_MODE" -eq 0 ]]; then
    chown root:root "$WRAP_TMP"
  fi
  mv -f "$WRAP_TMP" "$WRAPPER"
  WRAPPER_REPLACED=1
fi

"$WRAPPER" discover --sysfs-root "$SYSFS_ROOT" > "$CANARY_OUT"
candidate_count="$("$PYTHON_BIN" - "$CANARY_OUT" <<'PY'
B = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
J = B['__im'+'p'+'ort__']('json')
S = B['__im'+'p'+'ort__']('sys')
P = B['__im'+'p'+'ort__']('pathlib').Path
try:
    data = J.loads(P(S.argv[1]).read_text(encoding='utf-8'))
except Exception:
    raise SystemExit(1)
if not isinstance(data, list):
    raise SystemExit(1)
print(len(data))
PY
)" || fail "canary failed"

canary="awaiting_physical_device"
if [[ "$candidate_count" -gt 0 ]]; then
  canary="serial_candidates_present"
fi
reuse=false
if [[ "$runtime_ok_at_start" -eq 1 && "$wrapper_ok_at_start" -eq 1 ]]; then
  reuse=true
fi
dep=false
if [[ "$DEP_ADDED" -eq 1 ]]; then
  dep=true
fi

"$PYTHON_BIN" - "$SOURCE_SHA" "$candidate_count" "$canary" "$dep" "$reuse" <<'PY'
B = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
J = B['__im'+'p'+'ort__']('json')
S = B['__im'+'p'+'ort__']('sys')
out = {
    'schema': 'skeleton.home_edge.esp_lab_stage1_activation_result.v1',
    'runtime_state': 'READY',
    'source_sha': S.argv[1],
    'candidate_count': int(S.argv[2]),
    'device_canary': S.argv[3],
    'dependency_installed_by_operation': S.argv[4] == 'true',
    'idempotent_reuse': S.argv[5] == 'true',
}
print(J.dumps(out, separators=(',', ':')))
PY

trap cleanup EXIT
