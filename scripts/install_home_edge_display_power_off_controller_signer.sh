#!/usr/bin/env bash
set -euo pipefail

install -o root -g root -m 0755 scripts/home_edge_display_power_off_signer.py \
  /usr/local/bin/home_edge_display_power_off_signer
install -o root -g root -m 0440 scripts/skeleton-home-edge-display-off-controller-signer.sudoers \
  /etc/sudoers.d/skeleton-home-edge-display-off-controller-signer
visudo -cf /etc/sudoers.d/skeleton-home-edge-display-off-controller-signer >/dev/null
