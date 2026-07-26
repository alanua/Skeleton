#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 ]]; then
  echo "usage: $0 REPO_ROOT PRIVATE_QUEUE_ROOT MFP_HANDOFF_ROOT SUBJECT_ALIASES_FILE RUN_AS_USER [MODEL]" >&2
  exit 2
fi

repo_root="$(cd "$1" && pwd -P)"
queue_root="$2"
mfp_handoff_root="$3"
subject_aliases_file="$4"
run_as_user="$5"
model="${6:-qwen2.5:1.5b}"
runtime_root="/usr/local/lib/skeleton/local-inference-runtime"

case "$repo_root" in
  /tmp/*|/var/tmp/*) echo "repository root must be durable" >&2; exit 2 ;;
esac
case "$queue_root" in
  /var/lib/skeleton/*) ;;
  *) echo "queue root must be below /var/lib/skeleton" >&2; exit 2 ;;
esac
case "$mfp_handoff_root" in
  /var/lib/skeleton/*) ;;
  *) echo "MFP handoff root must be below /var/lib/skeleton" >&2; exit 2 ;;
esac
case "$subject_aliases_file" in
  /etc/skeleton/*) ;;
  *) echo "subject aliases file must be below /etc/skeleton" >&2; exit 2 ;;
esac
[[ -f "$subject_aliases_file" ]] || { echo "subject aliases file missing" >&2; exit 2; }
python3 - "$subject_aliases_file" <<'PY_VALIDATE_ALIASES'
import json
import pathlib
import sys

try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(2)
if (
    not isinstance(value, list)
    or len(value) != 3
    or any(not isinstance(item, str) or not item.strip() for item in value)
    or len({item.strip() for item in value}) != 3
):
    raise SystemExit(2)
PY_VALIDATE_ALIASES
[[ "$run_as_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "invalid service user" >&2; exit 2; }
[[ "$model" =~ ^[A-Za-z0-9._:-]+$ ]] || { echo "invalid model name" >&2; exit 2; }

for required in \
  core/local_inference_adapters.py \
  core/local_inference_runtime.py \
  core/family_document_local_inference.py \
  scripts/local_inference_worker.py \
  scripts/local_inference_submit.py \
  ops/systemd/skeleton-local-inference.service; do
  [[ -f "$repo_root/$required" ]] || { echo "required source missing" >&2; exit 2; }
done

sudo install -d -m 0755 /etc/skeleton /usr/local/lib/skeleton "$runtime_root/core" "$runtime_root/scripts" /var/lib/skeleton
sudo install -d -o "$run_as_user" -g "$run_as_user" -m 0700 "$queue_root" "$mfp_handoff_root"
sudo install -m 0644 /dev/null "$runtime_root/core/__init__.py"
sudo install -m 0644 /dev/null "$runtime_root/scripts/__init__.py"
sudo install -m 0644 \
  "$repo_root/core/local_inference_adapters.py" \
  "$repo_root/core/local_inference_runtime.py" \
  "$repo_root/core/family_document_local_inference.py" \
  "$runtime_root/core/"
sudo install -m 0644 \
  "$repo_root/scripts/local_inference_worker.py" \
  "$repo_root/scripts/local_inference_submit.py" \
  "$runtime_root/scripts/"

wrapper_tmp="$(mktemp)"
env_tmp="$(mktemp)"
trap 'rm -f "$wrapper_tmp" "$env_tmp"' EXIT
printf -v runtime_quoted '%q' "$runtime_root"

cat >"$wrapper_tmp" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd $runtime_quoted
exec /usr/bin/python3 -m scripts.local_inference_worker run
EOF
cat >"$env_tmp" <<EOF
SKELETON_LOCAL_INFERENCE_ROOT=$queue_root
SKELETON_LOCAL_INFERENCE_MODELS=$model
SKELETON_LOCAL_INFERENCE_DEFAULT_MODEL=$model
SKELETON_MFP_INFERENCE_HANDOFF_ROOT=$mfp_handoff_root
SKELETON_FAMILY_SUBJECT_ALIASES_FILE=$subject_aliases_file
SKELETON_OLLAMA_ENDPOINT=http://127.0.0.1:11434
PYTHONDONTWRITEBYTECODE=1
EOF

sudo install -m 0755 "$wrapper_tmp" /usr/local/lib/skeleton/local-inference-worker
sudo install -m 0600 "$env_tmp" /etc/skeleton/local-inference.env
sudo install -m 0644 "$repo_root/ops/systemd/skeleton-local-inference.service" /etc/systemd/system/skeleton-local-inference.service
sudo sed -i "s/^User=.*/User=$run_as_user/; s/^Group=.*/Group=$run_as_user/" /etc/systemd/system/skeleton-local-inference.service
sudo systemctl daemon-reload

echo 'INSTALLED_NOT_STARTED'
