#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
PRIVATE_ROOT="${2:-$HOME/.local/share/skeleton-private}"
INSTALL_ROOT="${3:-$HOME/.local/lib/skeleton-local-ops}"
VENV="$INSTALL_ROOT/venv"
BIN="$INSTALL_ROOT/bin"
CLI="$BIN/skeleton-local"

[[ -f "$REPO_ROOT/scripts/skeleton_local.py" ]] || {
  echo 'BLOCKED: Skeleton repository not found' >&2
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
exec "$VENV/bin/python" "$REPO_ROOT/scripts/skeleton_local.py" "\$@"
WRAPPER
chmod 700 "$CLI"

SMOKE="$PRIVATE_ROOT/smoke"
mkdir -p "$SMOKE"
chmod 700 "$SMOKE"

"$CLI" --private-root "$PRIVATE_ROOT" memory init
"$CLI" --private-root "$PRIVATE_ROOT" memory put \
  --namespace systemcheck \
  --fact-id install \
  --value-json '{"installed":true}' \
  --actor operator \
  --reason install_check \
  --approval operator_approved \
  --transaction install-check-v1
"$CLI" --private-root "$PRIVATE_ROOT" memory get \
  --namespace systemcheck \
  --fact-id install
"$CLI" --private-root "$PRIVATE_ROOT" aufmass example \
  --output "$SMOKE/example.json"
"$CLI" --private-root "$PRIVATE_ROOT" aufmass calculate \
  --input "$SMOKE/example.json" \
  --output-dir "$SMOKE/result" \
  --write-memory \
  --actor operator \
  --reason install_check \
  --approval operator_approved \
  --transaction aufmass-install-check-v1
"$CLI" --private-root "$PRIVATE_ROOT" aufmass calculate \
  --input "$SMOKE/example.json" \
  --output-dir "$SMOKE/result" \
  --write-memory \
  --actor operator \
  --reason install_check \
  --approval operator_approved \
  --transaction aufmass-install-check-v1
BACKUP_JSON="$("$CLI" --private-root "$PRIVATE_ROOT" memory backup)"
SNAPSHOT_ID="$(printf '%s' "$BACKUP_JSON" | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["snapshot_id"])')"
"$CLI" --private-root "$PRIVATE_ROOT" memory verify-backup \
  --manifest "$PRIVATE_ROOT/memory/manifests/$SNAPSHOT_ID.json"
"$CLI" --private-root "$PRIVATE_ROOT" memory health

printf '%s\n' \
  'STATUS: DONE' \
  "COMMAND: $CLI" \
  "PRIVATE DATA: $PRIVATE_ROOT" \
  'MEMORY: working and backup verified' \
  'AUFMASS: repeated example calculation verified'
