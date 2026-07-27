# Kyiv Raspberry Pi 4 media image

This is a secret-free Raspberry Pi OS Lite ARM64 image for the `display_direct_play` profile. On first boot it installs MPV, PipeWire, Sway, HDMI-CEC tools, the resolver-federation agent and optional Chromium.

Before boot, the FAT boot partition must contain:

- `skeleton-node.env`, copied from the included example;
- `authorized_keys`, containing one administrator SSH public key.

Use `write-image.sh` to flash and provision the boot partition. The script is destructive and requires the block device twice. The Raspberry Pi reports playback outcomes into resolver federation but does not automatically promote remote resolver evidence.
