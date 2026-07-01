#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
TARGET="${BIN_DIR}/skeleton-memory"
PRIVATE_ROOT="${SKELETON_PRIVATE_MEMORY_ROOT:-${HOME}/.local/share/skeleton-private-memory}"
SMOKE_ROOT="$(mktemp -d)"

cleanup() {
  rm -rf "${SMOKE_ROOT}"
}
trap cleanup EXIT

mkdir -p "${BIN_DIR}"
cat > "${TARGET}" <<EOF
#!/usr/bin/env bash
exec python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" "\$@"
EOF
chmod 700 "${TARGET}"

python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" init >/dev/null
python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" put skeleton.smoke smoke_fact --json '{"summary":"synthetic smoke value","tags":["smoke"],"relationships":[{"kind":"checks","target":"installer"}]}' >/dev/null
python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" get skeleton.smoke smoke_fact >/dev/null
python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" search smoke >/dev/null
python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" relations installer >/dev/null
python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" delete skeleton.smoke smoke_fact --reason smoke-cleanup >/dev/null
python3 "${REPO_ROOT}/scripts/skeleton_private_memory.py" --root "${SMOKE_ROOT}" rebuild >/dev/null

mkdir -p "${PRIVATE_ROOT}"
chmod 700 "${PRIVATE_ROOT}"
echo "installed skeleton-memory"
