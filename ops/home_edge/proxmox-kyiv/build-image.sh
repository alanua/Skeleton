#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 2; }
[[ $# -eq 1 ]] || { echo "Usage: sudo $0 OUTPUT_DIR" >&2; exit 2; }
for cmd in curl sha512sum qemu-img qemu-nbd lsblk mount umount rsync; do command -v "$cmd" >/dev/null || { echo "missing $cmd" >&2; exit 2; }; done
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
out="$(mkdir -p "$1"; realpath "$1")"
build="$out/.build-proxmox"; rm -rf "$build"; mkdir -p "$build"
upstream=debian-13-genericcloud-amd64-20260722-2547.qcow2
url="https://cloud.debian.org/images/cloud/trixie/20260722-2547/$upstream"
sha512=735d1b2d0ef265a0c2323fdaa7d46e7bd7a1b984f73e8a785e638034bf07876e26374a9d809d713501270c071b3464d2ada0c5589f07742b95ed853cc6d48f45
curl -fL --retry 4 -o "$build/$upstream" "$url"
echo "$sha512  $build/$upstream" | sha512sum -c -
image="$out/skeleton-kyiv-home-edge-debian13-amd64-20260727.qcow2"
cp --reflink=auto "$build/$upstream" "$image"
modprobe nbd max_part=16
nbd=""
for candidate in /dev/nbd{0..15}; do
  [[ -e "$candidate" ]] || continue
  if [[ ! -e /sys/block/${candidate##*/}/pid ]]; then nbd="$candidate"; break; fi
  [[ ! -s /sys/block/${candidate##*/}/pid ]] && { nbd="$candidate"; break; }
done
[[ -n "$nbd" ]] || { echo "no free nbd device" >&2; exit 2; }
cleanup(){ set +e; mountpoint -q "$build/root" && umount "$build/root"; [[ -n "$nbd" ]] && qemu-nbd --disconnect "$nbd" >/dev/null 2>&1; rm -rf "$build"; }
trap cleanup EXIT
qemu-nbd --connect="$nbd" "$image"
udevadm settle
mkdir -p "$build/root"
rootpart=$(lsblk -bno PATH,SIZE,FSTYPE "$nbd" | awk '$3=="ext4"{print $1,$2}' | sort -k2nr | head -n1 | awk '{print $1}')
[[ -b "$rootpart" ]] || { echo "root partition not found" >&2; exit 2; }
mount "$rootpart" "$build/root"
install -d -m 0755 "$build/root/opt/skeleton-image/resolver_federation" "$build/root/etc/systemd/system/multi-user.target.wants"
rsync -a "$root/ops/home_edge/resolver_federation/" "$build/root/opt/skeleton-image/resolver_federation/"
install -m 0755 "$root/ops/home_edge/proxmox-kyiv/proxmox-firstboot.sh" "$build/root/opt/skeleton-image/proxmox-firstboot.sh"
install -m 0644 "$root/ops/home_edge/proxmox-kyiv/skeleton-image-firstboot.service" "$build/root/etc/systemd/system/skeleton-image-firstboot.service"
ln -sfn ../skeleton-image-firstboot.service "$build/root/etc/systemd/system/multi-user.target.wants/skeleton-image-firstboot.service"
install -d -m 0700 "$build/root/etc/skeleton"
cat >"$build/root/etc/skeleton/image-build.json" <<EOF
{"schema":"skeleton.node_image.v1","image":"kyiv-home-edge-debian13-amd64","upstream":"$url","upstream_sha512":"$sha512","built_at":"$(date -u +%FT%TZ)","contains_secrets":false}
EOF
sync
umount "$build/root"
qemu-nbd --disconnect "$nbd"; nbd=""
qemu-img resize "$image" 8G
qemu-img check "$image"
(cd "$out"; sha256sum "$(basename "$image")" > "$(basename "$image").sha256")
(cd "$out"; sha512sum "$(basename "$image")" > "$(basename "$image").sha512")
cat > "$image.build.json" <<EOF
{"schema":"skeleton.image_build_receipt.v1","image":"$(basename "$image")","upstream":"$url","upstream_sha512":"$sha512","local_sha256":"$(sha256sum "$image" | awk '{print $1}')","local_sha512":"$(sha512sum "$image" | awk '{print $1}')","contains_secrets":false,"first_boot":"base packages plus resolver federation; node enrollment remains pending"}
EOF
trap - EXIT
rm -rf "$build"
echo "$image"
