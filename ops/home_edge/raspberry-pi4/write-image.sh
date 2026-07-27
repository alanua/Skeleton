#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 2; }
[[ $# -eq 5 && $4 == --confirm-device ]] || { echo "Usage: sudo $0 IMAGE.img.xz BLOCK_DEVICE SSH_PUBLIC_KEY --confirm-device BLOCK_DEVICE" >&2; exit 2; }
image="$1"; dev="$2"; key="$3"; confirm="$5"
[[ "$dev" == "$confirm" && -b "$dev" && -f "$image" && -f "$image.sha256" && -f "$key" ]] || exit 2
[[ $(findmnt -n -o SOURCE /) != "$dev"* ]] || { echo "refusing system disk" >&2; exit 2; }
lsblk -nrpo MOUNTPOINTS "$dev" | grep -q '[^[:space:]]' && { echo "unmount all target partitions first" >&2; exit 2; }
sha256sum -c "$image.sha256"
xz -dc "$image" | dd of="$dev" bs=16M conv=fsync status=progress
partprobe "$dev"; udevadm settle
boot=$(lsblk -rno PATH,FSTYPE "$dev" | awk '$2=="vfat"{print $1; exit}')
[[ -b "$boot" ]] || exit 2
mnt=$(mktemp -d); trap 'umount "$mnt" 2>/dev/null || true; rmdir "$mnt"' EXIT
mount "$boot" "$mnt"
cp "$mnt/skeleton-node.env.example" "$mnt/skeleton-node.env"
install -m 0600 "$key" "$mnt/authorized_keys"
sync; umount "$mnt"; rmdir "$mnt"; trap - EXIT
echo "Image written. Edit skeleton-node.env on the boot partition before first boot if needed."
