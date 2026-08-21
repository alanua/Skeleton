#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${SKELETON_BITWARDEN_SDK_RUNTIME_DIR:-/opt/skeleton-bitwarden-sdk-runtime}"
python_bin="${PYTHON:-python3}"

"${python_bin}" -m venv "${runtime_dir}"
"${runtime_dir}/bin/python" -m pip install --upgrade pip
"${runtime_dir}/bin/python" -m pip install "bitwarden-sdk==2.1.0"
"${runtime_dir}/bin/python" - <<'PY'
import bitwarden_sdk
print("bitwarden-sdk-runtime=ready")
PY
