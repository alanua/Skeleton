#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT="/opt/skeleton-bitwarden-sdk-runtime"
VENV_DIR="${RUNTIME_ROOT}/venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
HELPER_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bitwarden_gmail_reference_helper.py"
HELPER_DST="/opt/skeleton-mail-operations/bitwarden_gmail_reference_helper.py"

python3 -m venv "${VENV_DIR}"
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install "bitwarden-sdk==2.1.0"
"${PYTHON_BIN}" - <<'PY'
import importlib.metadata
version = importlib.metadata.version("bitwarden-sdk")
if version != "2.1.0":
    raise SystemExit(f"bitwarden-sdk version mismatch: {version}")
PY

install -m 0755 -D "${HELPER_SRC}" "${HELPER_DST}"
printf '%s\n' "${PYTHON_BIN} ${HELPER_DST} bootstrap-gmail-primary-index"
