#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SKELETON_BITWARDEN_SDK_INSTALL_ROOT:-/opt/skeleton-bitwarden-sdk-runtime}"
VENV_DIR="$INSTALL_ROOT/venv"
BIN_DIR="$INSTALL_ROOT/bin"
HELPER_PATH="/opt/skeleton-bitwarden-sdk-runtime/bin/bitwarden-gmail-primary-reference-helper"

install -d -m 0755 "$INSTALL_ROOT"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install --require-hashes \
  'bitwarden-sdk==2.1.0' \
  --hash=sha256:b6f6b2624e340307891edc20a2860ec0b7c2140683eec2ce2e20d1076ffe9268

install -d -m 0755 "$BIN_DIR"
install -m 0755 scripts/bitwarden_gmail_primary_reference_helper.py "$HELPER_PATH"
"$VENV_DIR/bin/python" -m py_compile "$HELPER_PATH"
"$VENV_DIR/bin/python" "$HELPER_PATH" preflight >/dev/null

printf '%s\n' "$HELPER_PATH"
