#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SKELETON_MAIL_INSTALL_ROOT:-/opt/skeleton-mail-operations}"
RUNTIME_DIR="$INSTALL_ROOT/bitwarden-sdk-runtime"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
TMP_RUNTIME="${RUNTIME_DIR}.tmp.$$"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python_unavailable" >&2
  exit 1
fi

rm -rf "$TMP_RUNTIME"
"$PYTHON_BIN" -m venv "$TMP_RUNTIME"
"$TMP_RUNTIME/bin/python" -m pip install --upgrade pip
"$TMP_RUNTIME/bin/python" -m pip install "bitwarden-sdk==2.1.0"
"$TMP_RUNTIME/bin/python" - <<'PY'
import bitwarden_sdk
print("bitwarden_sdk_import=ok")
PY

install -d -m 0755 "$INSTALL_ROOT"
rm -rf "$RUNTIME_DIR.old"
if [[ -e "$RUNTIME_DIR" ]]; then
  mv "$RUNTIME_DIR" "$RUNTIME_DIR.old"
fi
mv "$TMP_RUNTIME" "$RUNTIME_DIR"
rm -rf "$RUNTIME_DIR.old"
