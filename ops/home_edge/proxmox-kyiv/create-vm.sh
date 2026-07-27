#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run on the Proxmox host as root" >&2; exit 2; }
vmid=""; image=""; sshkey=""; storage=local-lvm; bridge=vmbr0; name=kyiv-home-edge-01; memory=4096; cores=2; disk=32G; start=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --vmid) vmid="$2"; shift 2;; --image) image="$2"; shift 2;; --ssh-public-key) sshkey="$2"; shift 2;;
    --storage) storage="$2"; shift 2;; --bridge) bridge="$2"; shift 2;; --name) name="$2"; shift 2;;
    --memory) memory="$2"; shift 2;; --cores) cores="$2"; shift 2;; --disk) disk="$2"; shift 2;; --start) start=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[[ "$vmid" =~ ^[0-9]+$ && -f "$image" && -f "$image.sha256" && -f "$sshkey" ]] || { echo "required: --vmid N --image FILE --ssh-public-key FILE" >&2; exit 2; }
sha256sum -c "$image.sha256"
qm status "$vmid" >/dev/null 2>&1 && { echo "VMID already exists" >&2; exit 2; }
qm create "$vmid" --name "$name" --memory "$memory" --cores "$cores" --cpu host --machine q35 --ostype l26 --net0 "virtio,bridge=$bridge,firewall=1" --agent enabled=1 --serial0 socket --vga serial0
qm importdisk "$vmid" "$image" "$storage" --format qcow2
unused=$(qm config "$vmid" | awk '/^unused[0-9]+:/{print $1,$2; exit}')
slot=${unused%%:*}; volume=${unused#* }
[[ -n "$slot" && -n "$volume" ]] || { echo "imported disk not found" >&2; exit 2; }
qm set "$vmid" --delete "$slot"
qm set "$vmid" --scsihw virtio-scsi-single --scsi0 "$volume,discard=on,ssd=1,iothread=1" --ide2 "$storage:cloudinit" --boot order=scsi0 --ciuser skeleton --sshkeys "$sshkey" --ipconfig0 ip=dhcp --onboot 1
qm resize "$vmid" scsi0 "$disk"
qm cloudinit update "$vmid"
[[ $start -eq 1 ]] && qm start "$vmid"
echo "VM $vmid created as $name; resolver peers and private enrollment are intentionally not configured."
