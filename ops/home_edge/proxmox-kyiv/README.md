# Kyiv Proxmox Debian node image

The release image is a secret-free Debian 13 generic-cloud qcow2 with a first-boot Skeleton base installer and resolver-federation agent. It does not contain Home Edge HMAC material, Tailscale identity, SSH private keys or media credentials.

Build with `sudo ./build-image.sh OUTPUT_DIR`. On the Kyiv Proxmox host, run:

```bash
sudo ./create-vm.sh --vmid 240 --image skeleton-kyiv-home-edge-debian13-amd64-20260727.qcow2 --ssh-public-key /root/operator.pub --storage local-lvm --bridge vmbr0 --start
```

The default VM is 2 vCPU, 4 GiB RAM and 32 GiB disk. First boot installs the base packages, QEMU guest agent and resolver federation. Skeleton executor enrollment, peer keys, Tailscale and private restore remain separate approval-gated steps.
