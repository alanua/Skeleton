# Remote Windows Audit Node

This is the public-safe one-click bootstrap for a non-technical Windows owner. It does not contain target identity, private keys, Tailscale auth keys, passwords, tokens, private hostnames, private addresses, or audit evidence.

## Owner Flow

1. The protected host-maintenance runtime prepares one private HTTPS enrollment link.
2. Send that HTTPS link to the owner over Viber or another private channel.
3. The owner opens the HTTPS link on the intended Windows target. The private endpoint serves the public-safe `Skeleton-Remote-Audit.cmd` wrapper with the enrollment URL bound as its first argument, or asks the owner to run the wrapper with `SKELETON_REMOTE_AUDIT_ENROLLMENT_URL` set.
4. Double-click the wrapper and approve the Windows UAC prompt.
5. Sign in to Tailscale only if the official Tailscale client asks for normal interactive sign-in.
6. Verify the generated machine identity fingerprint with the controller out of band before marking enrollment complete.

The supported path is Windows 10/11 x64. Older or non-x64 Windows exits before installing incompatible components.

## Bootstrap Behavior

The bootstrap installs or enables only:

- official Tailscale for Windows from `pkgs.tailscale.com`
- the Windows OpenSSH Server capability

It creates a dedicated local `skeleton-audit` account, configures public-key-only SSH for that account, disables password and keyboard-interactive SSH for that account, limits forwarding, and restricts the Windows OpenSSH firewall rule to the Private profile to avoid public-Internet SSH exposure. Machine identity is generated locally under `ProgramData\Skeleton\RemoteAudit\identity`; the private key stays on the target.

When `-EnrollmentUrl` is provided, the bootstrap fetches exactly one HTTPS owner enrollment payload matching `schemas/remote_windows_owner_enrollment.schema.json`. That payload supplies the controller `ssh-ed25519` public key. The enrollment token has no automatic expiry, TTL, or `EXPIRED` state; the controller must rotate or revoke it manually after successful enrollment or cancellation. If no enrollment URL is provided, the script falls back to an interactive controller public-key prompt for controlled manual testing.

Run the PowerShell bootstrap again with `-RotateMachineIdentity` to rotate the machine identity. Run it with `-Uninstall` to revoke Skeleton audit access and disable the audit account. Tailscale and OpenSSH are left installed because they may be owner-managed system components.

## Audit Operation

The downstream operation is `read_only_system_audit_v1` in `core.home_edge.remote_windows_audit`. It builds a fixed, signed `read_only` Home Edge executor request with no issue-supplied command, shell, user, host, or path. The remote collection is limited to OS, hardware, disk capacity, TPM/Secure Boot, Defender, OpenSSH, and Tailscale status needed for a deterministic `KEEP`, `UPGRADE`, `REINSTALL`, `REPAIR`, or `RETIRE` verdict.

`CurrentMainWorkstationNodeTransport` is the bounded current-main WorkstationNode transport for this operation. It accepts only the exact signed `read_only_system_audit_v1` request shape, the fixed idempotency key, the dedicated desktop user, the canonical node id, and the embedded PowerShell audit script. Any changed argv, timeout, stdin, cwd, environment, lane, user, idempotency key, or node binding is rejected before transport.

It never scans personal files, messages, browser history, photos, or document content.

## ESP Lab Stage B Host-Install Handoff

The fixed host-maintenance operation for the DE-PC ESPConnect install handoff is `esp_lab_stage_b_host_install`. It requires:

```yaml
command: esp_lab_stage_b_host_install
repository: alanua/Skeleton
apply: true
owner_approval: esp_lab_stage_b_host_install_v1
host_install_id: de-pc-workstation
```

The operation does not install software, enroll a target, launch PowerShell, open a listener, flash firmware, or create a service. It writes one private `0600` artifact under the protected host-maintenance runtime root with the fixed current-main install command for `scripts/espconnect_windows_stage_b_install.ps1 -Apply`, the pinned ESPConnect release `v1.1.18`, and tag commit `77c79a01786881206ad9b3ccbe3db2ddb08f2989`. The public receipt contains only the private artifact reference, hashes, pinned public metadata, and `target_installed: false`.

## Public/Private Boundary

Public GitHub-safe material:

- `Skeleton-Remote-Audit.cmd`
- `bootstrap.ps1`
- schemas
- public receipt hash and summarized non-identifying evidence
- deterministic verdict, confidence, and observed-evidence reasons
- Stage B host-install public metadata and fixed-command hash

Private controller or target material:

- controller private key
- target MachineIdentity private key
- Tailscale identity and login state
- hostnames, IP addresses, serial numbers, known-hosts entries
- full private evidence JSON
- Stage B private operator handoff artifact and exact install command

The public receipt includes only `machine_identity_hash` and `private_evidence_sha256`. Full evidence must be stored outside the repository.

## Next Private Operational Steps

1. Stage the private controller SSH key, public enrollment payload, and known-hosts store on the controller.
2. Generate one private HTTPS link with `windows_bootstrap_prepare_one_link`.
3. Have the owner open the link on the Windows target, launch the one-click CMD, and approve UAC.
4. Verify the displayed machine-identity fingerprint out of band.
5. Register the node with `schemas/remote_audit_enrollment.schema.json` in private controller storage.
6. Run the first `read_only_system_audit_v1` audit and store private evidence outside GitHub.
7. For ESP Lab Stage B, generate the private handoff with `esp_lab_stage_b_host_install`, then run the fixed installer manually on DE-PC only after owner/operator approval.
