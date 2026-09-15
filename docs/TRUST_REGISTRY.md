# Trust Registry

The Trust Registry is the provider-neutral authorization contract for public
machine identity metadata. It maps a node fingerprint and key version to a trust
state, allowed transport profile and allowed capability set.

## Trust States

- `PENDING`: enrolled metadata exists, but authorization fails closed.
- `TRUSTED`: fingerprint can authorize only registered transports and
  capabilities.
- `ROTATING`: old public identity remains valid only until
  `rotation_expires_at` while a higher trusted key version exists.
- `REVOKED`: fingerprint is rejected immediately.

Unknown machine ids, mismatched fingerprints, revoked identities, expired
identities and stale verification timestamps all fail closed.

## Rotation

Rotation is deterministic:

1. Register the replacement public identity with a higher `key_version`.
2. Keep the previous binding in `ROTATING` with a bounded
   `rotation_expires_at`.
3. At cutover, authorization for the old fingerprint fails with
   `ROTATION_OVERLAP_EXPIRED` or the old binding is moved to `REVOKED`.

The registry rejects duplicate key versions for one machine and rejects shared
fingerprints across machines. Revoking one node affects only that node's
bindings.

## Authorization Boundary

Transport authentication never implies capability authorization. A request must
match the machine id, public fingerprint, trust state, validity window,
freshness window, transport profile and explicit capability.

Constrained relay node classes may not receive host-control capabilities. Relay
profiles are therefore less authoritative than host profiles even when their
transport authentication succeeds.

## Receipts

Trust decisions serialize to public receipts containing status, machine id,
public fingerprint, key id, key version, trust state and reason codes only. They
must never include private keys, passwords, tokens, private certificate material
or runtime connection data.

This module performs no live enrollment, rotation, revocation, SSH, mTLS,
Tailscale or device calls. Runtime wiring belongs in a later operator-gated
transport slice.
