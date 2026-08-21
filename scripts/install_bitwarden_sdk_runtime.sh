#!/usr/bin/env bash
set -euo pipefail

SDK_VERSION="2.1.0"
INSTALL_ROOT="${SKELETON_BITWARDEN_SDK_ROOT:-/opt/skeleton-bitwarden-sdk}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
WHEEL_DIR="${BITWARDEN_SDK_WHEEL_DIR:-}"

REQ_FILE="$(mktemp)"
cleanup() {
  rm -f "$REQ_FILE"
}
trap cleanup EXIT

cat >"$REQ_FILE" <<'REQ'
bitwarden-sdk==2.1.0 \
    --hash=sha256:2c5de50af003b7b2d16a9a690d3111480d1b1962eb25f39e416812d6d9d4e4d6 \
    --hash=sha256:b6f6b2624e340307891edc20a2860ec0b7c2140683eec2ce2e20d1076ffe9268 \
    --hash=sha256:cdf85d309aecf21c563c9245322d49a344135761d03cffa43b8703f509bc0df7 \
    --hash=sha256:8374ef97e481c17a0b65e1b8d700df492cb384ff5a4888bd2c697c37457fc332 \
    --hash=sha256:659b7e3faaab067f38556ffaaa7edf217e581047234f9a748770fa2926d2664c
REQ

"$PYTHON_BIN" -m venv "$INSTALL_ROOT/venv"
PIP="$INSTALL_ROOT/venv/bin/python3 -m pip"

if [[ -n "$WHEEL_DIR" ]]; then
  $PIP install --no-deps --no-index --find-links "$WHEEL_DIR" --require-hashes -r "$REQ_FILE"
else
  $PIP install --no-deps --require-hashes -r "$REQ_FILE"
fi

"$INSTALL_ROOT/venv/bin/python3" - <<'PY'
import importlib.metadata
import sys

version = importlib.metadata.version("bitwarden-sdk")
if version != "2.1.0":
    raise SystemExit(f"unexpected bitwarden-sdk version: {version}")
from bitwarden_sdk import BitwardenClient

if BitwardenClient is None:
    raise SystemExit("bitwarden sdk import failed")
PY

printf 'bitwarden-sdk-runtime=ready version=%s python=%s\n' "$SDK_VERSION" "$INSTALL_ROOT/venv/bin/python3"
