#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "Usage: sudo $0 MOUNTED_BACKUP_TARGET" >&2; exit 2; }
[[ $(id -u) -eq 0 ]] || { echo 'run as root' >&2; exit 2; }
target="$(realpath -e "$1")"
mountpoint -q "$target" || { echo 'target must be a separate mount point' >&2; exit 2; }
case "$target" in /|/home|/home/valertos08) exit 2;; esac
stamp="$(date -u +%Y%m%dT%H%M%SZ)"; out="$target/home-edge-01-$stamp"
umask 077; mkdir -p "$out/tree" "$out/inventory" "$out/reference"
registry=/home/valertos08/.local/state/skeleton/device-registry/registry.sqlite3
if [[ -f "$registry" ]]; then sqlite3 "$registry" 'PRAGMA wal_checkpoint(FULL); PRAGMA integrity_check;' | tee "$out/inventory/registry-integrity.txt" | grep -qx ok; fi
paths=(/home/valertos08/.config/skeleton /home/valertos08/.local/state/skeleton /home/valertos08/.local/state/skeleton-cast /home/valertos08/.local/lib/skeleton-cast /home/valertos08/.local/share/skeleton /home/valertos08/.local/bin /home/valertos08/.config/systemd/user /home/valertos08/.config/HyperHDR /home/valertos08/.local/bin/production /opt/skeleton-home-edge /etc/skeleton /var/lib/skeleton /var/lib/tailscale)
for p in "${paths[@]}"; do [[ -e "$p" ]] && rsync -aHAX --numeric-ids --relative "$p" "$out/tree/" || printf '%s
' "$p" >> "$out/inventory/missing.txt"; done
dpkg-query -W -f='${binary:Package}	${Version}
' > "$out/inventory/dpkg.tsv"
lsblk -e7 -o NAME,PATH,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS,MODEL,SERIAL > "$out/inventory/lsblk.txt"
systemctl list-unit-files --no-pager > "$out/inventory/system-units.txt"
/home/valertos08/.local/bin/skeleton-devices doctor > "$out/inventory/registry-doctor.txt" 2>&1 || true
/home/valertos08/.local/bin/skeleton-devices operations > "$out/inventory/registry-operations.txt" 2>&1 || true
(cd "$out"; find tree inventory reference -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS; sha256sum -c SHA256SUMS >/dev/null)
sync; echo "$out"
