#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT="${SKELETON_BITWARDEN_SDK_RUNTIME_ROOT:-/opt/skeleton-bitwarden-sdk}"
PYTHON_BIN="${SKELETON_BITWARDEN_SDK_PYTHON_BIN:-python3}"
PINNED_VERSION="2.1.0"

"$PYTHON_BIN" -m venv "$RUNTIME_ROOT"
"$RUNTIME_ROOT/bin/python" -m pip install --upgrade pip
"$RUNTIME_ROOT/bin/python" -m pip install --only-binary=:all: "bitwarden-sdk==${PINNED_VERSION}"
"$RUNTIME_ROOT/bin/python" - <<'PY'
from importlib import metadata

expected = "2.1.0"
actual = metadata.version("bitwarden-sdk")
if actual != expected:
    raise SystemExit("bitwarden_sdk_version_mismatch")
PY
