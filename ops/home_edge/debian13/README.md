# Debian 13 rebuild bundle

Order:

1. Run `backup-current.sh` with a separate mounted backup disk.
2. Create and boot-test a full image of the current system disk.
3. Install minimal Debian 13 locally with Ethernet, OpenSSH, hostname `home-edge-01`, and user `valertos08` UID 1000.
4. Clone this branch and run `sudo ./bootstrap.sh --apply`.
5. Restore private state with `sudo ./restore-private.sh --apply BACKUP_DIR`.
6. Reinstall private/vendor artifacts from the backup inventory.
7. Run `sudo ./acceptance.sh`, then physically verify picture, audio, WLED, MFP and phone controls.

Scripts are plan-only unless `--apply` is provided. No credentials are stored here.
