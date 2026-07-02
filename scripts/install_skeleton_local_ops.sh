#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
PRIVATE_ROOT="${2:-${SKELETON_PRIVATE_MEMORY_ROOT:-$HOME/.local/share/skeleton-private-memory}}"
INSTALL_ROOT="${3:-$HOME/.local/lib/skeleton-local-ops}"
VENV="$INSTALL_ROOT/venv"
BIN="$INSTALL_ROOT/bin"
CLI="$BIN/skeleton-local"

[[ -f "$REPO_ROOT/scripts/skeleton_local.py" ]] || {
  echo 'BLOCKED: Skeleton repository not found' >&2
  exit 2
}
[[ -f "$REPO_ROOT/scripts/skeleton_local_ops.py" ]] || {
  echo 'BLOCKED: Skeleton local ops script not found' >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || {
  echo 'BLOCKED: python3 not found' >&2
  exit 2
}

mkdir -p "$PRIVATE_ROOT" "$INSTALL_ROOT" "$BIN"
chmod 700 "$PRIVATE_ROOT" "$INSTALL_ROOT" "$BIN"
python3 -m venv --system-site-packages "$VENV"

cat > "$CLI" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$REPO_ROOT"
export SKELETON_PRIVATE_MEMORY_ROOT="\${SKELETON_PRIVATE_MEMORY_ROOT:-$PRIVATE_ROOT}"
exec "$VENV/bin/python" "$REPO_ROOT/scripts/skeleton_local_ops.py" "\$@"
WRAPPER
chmod 700 "$CLI"

SMOKE="$PRIVATE_ROOT/smoke"
mkdir -p "$SMOKE"
chmod 700 "$SMOKE"

"$VENV/bin/python" "$REPO_ROOT/scripts/skeleton_private_memory.py" --root "$PRIVATE_ROOT" init
"$CLI" --private-root "$PRIVATE_ROOT" aufmass example \
  --output "$SMOKE/example.json"
"$CLI" --private-root "$PRIVATE_ROOT" aufmass calculate \
  --input "$SMOKE/example.json" \
  --output-dir "$SMOKE/result" \
  --use-memory \
  --write-memory \
  --actor operator \
  --reason install_check \
  --approval operator_approved \
  --transaction aufmass-install-check-v1
"$CLI" --private-root "$PRIVATE_ROOT" aufmass calculate \
  --input "$SMOKE/example.json" \
  --output-dir "$SMOKE/result" \
  --use-memory \
  --write-memory \
  --actor operator \
  --reason install_check \
  --approval operator_approved \
  --transaction aufmass-install-check-v1
"$CLI" --private-root "$PRIVATE_ROOT" aufmass memory-context --project-ref example-project
"$CLI" --private-root "$PRIVATE_ROOT" aufmass history --project-ref example-project
"$VENV/bin/python" "$REPO_ROOT/scripts/skeleton_private_memory.py" --root "$PRIVATE_ROOT" status

printf '%s\n' \
  'STATUS: DONE' \
  "COMMAND: $CLI" \
  "PRIVATE DATA: $PRIVATE_ROOT" \
  'MEMORY: working and backup verified' \
  'AUFMASS: repeated example calculation verified'
