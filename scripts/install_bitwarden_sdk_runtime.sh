#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SKELETON_BITWARDEN_SDK_RUNTIME_ROOT:-/opt/skeleton-bitwarden-sdk-runtime}"
PYTHON_BIN="${SKELETON_BITWARDEN_SDK_BOOTSTRAP_PYTHON:-/usr/bin/python3}"

install -d -m 0755 "$INSTALL_ROOT"
"$PYTHON_BIN" -m venv "$INSTALL_ROOT/venv"
"$INSTALL_ROOT/venv/bin/python" -m pip install --upgrade pip
"$INSTALL_ROOT/venv/bin/python" -m pip install 'bitwarden-sdk==2.1.0'
install -m 0755 scripts/bitwarden_gmail_reference_bootstrap.py "$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py"

"$INSTALL_ROOT/venv/bin/python" - <<'PY'
import bitwarden_sdk
if bitwarden_sdk.__version__ != "2.1.0":
    raise SystemExit("bitwarden-sdk version mismatch")
PY

printf '%s\n' "bitwarden_sdk_python=$INSTALL_ROOT/venv/bin/python"
printf '%s\n' "bitwarden_bootstrap_helper=$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py"
