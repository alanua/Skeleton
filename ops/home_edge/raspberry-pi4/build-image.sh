#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 2; }
[[ $# -eq 1 ]] || { echo "Usage: sudo $0 OUTPUT_DIR" >&2; exit 2; }
for cmd in curl sha256sum xz losetup lsblk mount umount rsync; do command -v "$cmd" >/dev/null || { echo "missing $cmd" >&2; exit 2; }; done
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
out="$(mkdir -p "$1"; realpath "$1")"
build="$out/.build-pi4"; rm -rf "$build"; mkdir -p "$build/root" "$build/boot"
upstream=2026-06-18-raspios-trixie-arm64-lite.img.xz
url="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-06-19/$upstream"
sha256=acff736ca7945e3b305f07cda4abdb870910e12634991da69783611756e381b3
curl -fL --retry 4 -o "$build/$upstream" "$url"
echo "$sha256  $build/$upstream" | sha256sum -c -
raw="$build/skeleton-kyiv-media-rpi4-arm64-20260727.img"
xz -dc "$build/$upstream" > "$raw"
loop=$(losetup --find --partscan --show "$raw")
cleanup(){ set +e; mountpoint -q "$build/root" && umount "$build/root"; mountpoint -q "$build/boot" && umount "$build/boot"; [[ -n "$loop" ]] && losetup -d "$loop" >/dev/null 2>&1; }
trap cleanup EXIT
udevadm settle
rootpart=$(lsblk -bno PATH,SIZE,FSTYPE "$loop" | awk '$3=="ext4"{print $1,$2}' | sort -k2nr | head -n1 | awk '{print $1}')
bootpart=$(lsblk -bno PATH,SIZE,FSTYPE "$loop" | awk '$3=="vfat"{print $1,$2}' | sort -k2nr | head -n1 | awk '{print $1}')
[[ -b "$rootpart" && -b "$bootpart" ]] || { echo "Pi partitions not found" >&2; exit 2; }
mount "$rootpart" "$build/root"; mount "$bootpart" "$build/boot"
install -d -m 0755 "$build/root/opt/skeleton-image/resolver_federation" "$build/root/etc/systemd/system/multi-user.target.wants"
rsync -a "$root/ops/home_edge/resolver_federation/" "$build/root/opt/skeleton-image/resolver_federation/"
install -m 0755 "$root/ops/home_edge/raspberry-pi4/pi-firstboot.sh" "$build/root/opt/skeleton-image/pi-firstboot.sh"
install -m 0644 "$root/ops/home_edge/raspberry-pi4/skeleton-pi-firstboot.service" "$build/root/etc/systemd/system/skeleton-pi-firstboot.service"
ln -sfn ../skeleton-pi-firstboot.service "$build/root/etc/systemd/system/multi-user.target.wants/skeleton-pi-firstboot.service"
touch "$build/boot/ssh"
cat >"$build/boot/skeleton-node.env.example" <<'EOF'
SKELETON_NODE_ID=kyiv-media-pi4-01
SKELETON_ADMIN_USER=skeleton
SKELETON_INSTALL_BROWSER=0
EOF
cat >"$build/boot/README-SKELETON.txt" <<'EOF'
Before first boot, copy skeleton-node.env.example to skeleton-node.env and place one SSH public key line in authorized_keys. The image contains no passwords, private keys or media credentials.
EOF
install -d -m 0700 "$build/root/etc/skeleton"
cat >"$build/root/etc/skeleton/image-build.json" <<EOF
{"schema":"skeleton.node_image.v1","image":"kyiv-media-rpi4-arm64","upstream":"$url","upstream_sha256":"$sha256","built_at":"$(date -u +%FT%TZ)","contains_secrets":false}
EOF
sync; umount "$build/root"; umount "$build/boot"; losetup -d "$loop"; loop=""
final="$out/skeleton-kyiv-media-rpi4-arm64-20260727.img.xz"
xz -T0 -3 -c "$raw" > "$final"
(cd "$out"; sha256sum "$(basename "$final")" > "$(basename "$final").sha256")
(cd "$out"; sha512sum "$(basename "$final")" > "$(basename "$final").sha512")
cat > "$final.build.json" <<EOF
{"schema":"skeleton.image_build_receipt.v1","image":"$(basename "$final")","upstream":"$url","upstream_sha256":"$sha256","local_sha256":"$(sha256sum "$final" | awk '{print $1}')","local_sha512":"$(sha512sum "$final" | awk '{print $1}')","contains_secrets":false,"first_boot":"MPV/direct-play packages plus resolver federation after boot provisioning"}
EOF
trap - EXIT
rm -rf "$build"
echo "$final"
