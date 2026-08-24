#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=""
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  install_home_edge_esp_lab.sh [--root DIR] < activation-config.json

Installs the Home Edge ESP Lab Stage 1 controller files and public-safe config.
The installer accepts its contract only as JSON on stdin. Secrets are never
accepted as argv values.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="${2:?--root requires a directory}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unsupported argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

contract="$(cat)"
if [[ -z "${contract//[[:space:]]/}" ]]; then
  printf 'BLOCKED: activation installer requires JSON on stdin\n' >&2
  exit 2
fi

prefix="${ROOT%/}"
lib_dir="${prefix}/usr/local/lib/skeleton/home-edge/esp-lab"
bin_dir="${prefix}/usr/local/bin"
config_dir="${prefix}/etc/skeleton"
config_file="${config_dir}/home-edge-esp-lab-stage1.json"
wrapper="${bin_dir}/skeleton-home-edge-esp-lab"

tmp_contract="$(mktemp)"
trap 'rm -f "$tmp_contract"' EXIT
printf '%s' "$contract" > "$tmp_contract"

python3 - "$tmp_contract" "$config_file" <<'PY'
import json
import re
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
data = json.loads(source.read_text(encoding="utf-8"))
allowed = {
    "schema",
    "operation_id",
    "node_id",
    "endpoint_kind",
    "adapter_kind",
    "default_execution_mode",
    "connector_url",
    "connector_ca_cert",
    "connector_pinned_cert_sha256",
    "connector_secret_file",
}
unknown = set(data) - allowed
if unknown:
    raise SystemExit(f"BLOCKED: unknown installer field {sorted(unknown)[0]}")
if data.get("schema") != "skeleton.home_edge.esp_lab.activation_installer.v1":
    raise SystemExit("BLOCKED: invalid installer schema")
if data.get("operation_id") != "home_edge_esp_lab_stage1_activation_v2":
    raise SystemExit("BLOCKED: invalid operation id")
token = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
if not isinstance(data.get("node_id"), str) or not token.fullmatch(data["node_id"]):
    raise SystemExit("BLOCKED: invalid node id")
if data.get("default_execution_mode", "plan") not in {"plan", "read_only"}:
    raise SystemExit("BLOCKED: invalid execution mode")
public_config = {
    key: data[key]
    for key in sorted(data)
    if key != "connector_secret_file"
}
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(public_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

mkdir -p "$lib_dir/core/home_edge" "$bin_dir"
install -m 0644 "$REPO_ROOT/core/home_edge/esp_lab.py" "$lib_dir/core/home_edge/esp_lab.py"
install -m 0644 "$REPO_ROOT/core/home_edge/esp_lab_connector.py" "$lib_dir/core/home_edge/esp_lab_connector.py"
install -m 0644 "$REPO_ROOT/core/home_edge/esp_lab_activation.py" "$lib_dir/core/home_edge/esp_lab_activation.py"
printf '%s\n' '"""Installed Home Edge ESP Lab package."""' > "$lib_dir/core/__init__.py"
printf '%s\n' '"""Installed Home Edge package."""' > "$lib_dir/core/home_edge/__init__.py"
cat > "$wrapper" <<WRAPPER
#!/usr/bin/env bash
set -Eeuo pipefail
exec env PYTHONPATH="$lib_dir" python3 -m core.home_edge.esp_lab_activation "\$@"
WRAPPER
chmod 0755 "$wrapper"
python3 -m py_compile \
  "$lib_dir/core/home_edge/esp_lab.py" \
  "$lib_dir/core/home_edge/esp_lab_connector.py" \
  "$lib_dir/core/home_edge/esp_lab_activation.py"

printf 'DONE: Home Edge ESP Lab Stage 1 controller installed\n'
printf 'operation_id=home_edge_esp_lab_stage1_activation_v2\n'
printf 'config=%s\n' "$config_file"
