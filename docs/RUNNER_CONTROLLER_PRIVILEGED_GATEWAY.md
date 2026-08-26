# Runner Controller Privileged Gateway

The Runner controller privileged gateway is the single reviewed bootstrap
boundary for rare Runner-owned root actions. It is not a shell API and does not
accept executable paths, argv, environment, sudo flags, service names, packages,
or destination paths from issue text.

Unprivileged Runner code submits a typed JSON request with schema
`skeleton.runner_controller_privileged_request.v1` to exactly:

```text
/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway
```

The optional SSH transport is only an alternate transport into the same JSON
authority for the dedicated `skeleton-runner-gateway` user. Its deterministic
restrictions prohibit PTY, forwarding, agent forwarding, interactive
keyboard/password auth, user rc, and interactive shell. It does not change
global root SSH policy. Authorized keys and the `Match User` `ForceCommand`
use only the same no-argument sudo gateway command:

```text
/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway
```

Both transports canonicalize the same request bytes before submission. The
request binds action id, operator approval, repository, target, expected main
SHA, registered clean main SHA, GitHub main SHA, checkout path, checkout HEAD,
checkout origin/main, issued time, expiry, request id, and idempotency key.
Extra fields and stale, future, expired, replayed, malformed, mismatched, or
unknown-action requests fail before the gateway action runner is called.

Installed production execution uses root-owned immutable trust anchors under
`/usr/local/lib/skeleton/runner-controller`: the gateway module bundle, fixed
action registry, protected capability registry copy, schemas, and canonical
checkout config. The canonical checkout config fixes the only accepted checkout
path to `/home/agent/agent-dev/repos/Skeleton`. The stdin gateway records every
accepted canonical request in an append-only local ledger at
`/var/lib/skeleton/runner-controller/privileged-gateway-ledger.jsonl` before
calling the action runner. A repeated request hash or reused idempotency key
fails closed before any privileged action is invoked again.

## Initial Production Action

The first and only registered production action is
`home_edge_01_esp_lab_stage1_signer_install_v1`. The action registry pins the
existing ESP Stage1 signer installer contract:

- source path: `scripts/install_home_edge_esp_lab_activation_signer.sh`
- source blob: `7ed95f5ba6d274451f62cfc31f88bc204eaaa386`
- source mode: `100755`
- protected destination:
  `/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh`
- installer argv:
  `/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh --repo-root {checkout_path}`
- post-audit artifacts: fixed signer executable, payload, Stage1 installer,
  and sudoers file with exact pinned content hashes and modes.

The gateway deliberately reuses the existing Runner maintenance signer
implementation, including clean-checkout, exact SHA, fresh remote-main,
reviewed blob, no-follow parent/destination, root ownership/mode, and post-audit
checks. It does not run ESP activation or any Home Edge device action.

Public receipts use schema
`skeleton.runner_controller_privileged_receipt.v1` and expose only stable
status, reason, hashes, fixed identifiers, booleans, and receipt hash. They do
not expose stderr, environment, secrets, private keys, SSH keys, private config
paths, or command output. `external_side_effects_executed` reflects protected
copy, installed-artifact verification, or activation flags from the protected
executor receipt, so a blocked second phase still reports that a partial root
mutation happened.

## Bootstrap Installer

`scripts/install_runner_controller_privileged_gateway.sh` requires explicit
`--repo-root` and `--expected-main-sha`. Before any root mutation in a live
install it canonicalizes the repo root, requires the fixed
`/home/agent/agent-dev/repos/Skeleton` checkout, exact `alanua/Skeleton`
origin, clean worktree, `HEAD` and `origin/main` equal to the 40-hex expected
SHA, and a fresh remote `main` lookup equal to the same SHA.

The installer verifies itself and every repo-sourced installed trust anchor
against the exact tree entry at the expected SHA. Installed bytes are copied
from Git blob objects at that SHA, never from mutable worktree contents. A
missing entry, mode/blob mismatch, dirty worktree, stale `origin/main`, fresh
remote mismatch, wrong origin, wrong SHA, or installer blob mismatch blocks
before target installation.

SSH is optional. With no `--ssh-public-key`, the installer creates no SSH
account plan, authorized key, sudoers rule for the SSH user, or sshd `Match`
fragment and reports `ssh=DISABLED_NOT_CONFIGURED`. With an approved operator
public key, it installs or verifies the locked non-root
`skeleton-runner-gateway` account, writes a root-controlled absolute
`AuthorizedKeysFile`, grants that user only the exact no-argument gateway
sudoers command, and validates the candidate sshd config with the fixed sshd
binary before accepting the fragment. Validation failure removes the candidate
and reports blocked. The installer does not reload sshd.

Codegen and tests must use `bash -n`, `--destdir` isolated-root validation,
synthetic Git fixtures, and installed-tree smoke checks only. They must not
execute live sudo, mutate host `/etc`, reload sshd, start services, or run a
privileged action.
