# Android Termux → Home Edge bridge

This bridge is a controller transport only. It never replaces the canonical Home Edge executor.

Canonical target:

- node: `home-edge-01`
- remote command: `/usr/local/bin/home_edge_exec --server`
- execution contract: signed `home_edge_exec` request/receipt

## Safety invariants

1. The phone may transport a request, but it must not execute arbitrary Home Edge commands outside `/usr/local/bin/home_edge_exec --server`.
2. No private key, HMAC value, token, hostname/IP, or secret value is stored in this repository.
3. Phone identity must be resolved from stable device evidence (hardware/protocol identifiers where available), never from IP alone.
4. Missing, stale, mismatched, or unapproved private configuration fails closed. There is no ad-hoc SSH fallback.
5. `operator_approval_ref`, `request_id`, `idempotency_key`, lane, timeout, run-as identity and receipt evidence are preserved end-to-end.
6. Home Edge actions remain subject to the lane/approval rules documented in `docs/HOME_EDGE_EXECUTOR.md`.

## Private runtime references

The live Termux bootstrap must use private runtime configuration references for:

- phone node identity and trust record;
- Home Edge controller connection profile;
- SSH identity and known-hosts material;
- Home Edge executor HMAC secret/reference;
- audit receipt destination.

The values are intentionally not specified here. Existing Skeleton private configuration or opaque SecretReference/PrivateDataReference entries must be used.

## First live verification

After the phone bridge is installed through an approved runtime path, the first probe is read-only and must terminate at the canonical executor. The expected flow is:

```text
phone bridge
  → signed read_only request
  → /usr/local/bin/home_edge_exec --server on home-edge-01
  → bounded receipt
  → independent identity/state check
```

Use a harmless identity probe such as `whoami` through the registered `read_only` operation. A successful SSH connection alone is not sufficient evidence; the returned Home Edge executor receipt and postcondition must be recorded.

## Bootstrap/repair packet

On the phone, repair only the registered controller transport prerequisites (Termux/OpenSSH/runtime config) and do not install a second executor. The runtime packet must:

1. verify the stable phone node identity;
2. verify private config references exist without printing their values;
3. verify the pinned Home Edge host identity/known-hosts entry;
4. verify the controller can invoke only `/usr/local/bin/home_edge_exec --server` for this route;
5. submit the read-only health probe through the existing Skeleton controller implementation;
6. retain the resulting audit receipt;
7. stop on any mismatch rather than falling back to a raw interactive SSH shell.

This repository document is code/config guidance only and does not authorize live deployment by itself.
