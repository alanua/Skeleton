#!/usr/bin/env bash
set -euo pipefail

runtime_root="/opt/skeleton-bitwarden-sdk"
python_bin="${runtime_root}/bin/python"
helper_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/bitwarden_secret_identifier_helper.py"
helper_target="${runtime_root}/bitwarden_secret_identifier_helper.py"

if [[ ! -f "${helper_source}" ]]; then
  echo "helper_source_missing" >&2
  exit 1
fi

python3 -m venv "${runtime_root}"
"${python_bin}" -m pip install --no-cache-dir --only-binary=:all: "bitwarden-sdk==2.1.0"
"${python_bin}" - <<'PY'
import importlib.metadata
import sys

if importlib.metadata.version("bitwarden-sdk") != "2.1.0":
    sys.exit("bitwarden_sdk_version_mismatch")
PY
install -m 0755 "${helper_source}" "${helper_target}"
