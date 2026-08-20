# Skeleton Machine Identity and Trust Registry

This module implements the public-safe identity and trust foundation from #2213.

The runtime model is:

`MachineIdentity -> TrustRegistry -> transport/capability decision -> executor/adapter`

Each managed node owns its own private machine key. The repository, Trust Registry,
MemoryGateway and public receipts contain only public identity metadata such as a
fingerprint, key/version id, transport profile, capability scope and trust state.
Private keys and other secret values are not fields in these contracts.

## Trust states

- `PENDING`: enrolled metadata exists but the key cannot authorize actions.
- `TRUSTED`: the key may authorize only its registered transports and capabilities.
- `ROTATING`: a bounded overlap key remains usable while a replacement is verified.
- `REVOKED`: the key is rejected immediately without affecting unrelated identities.

The registry fails closed for unknown or mismatched fingerprints, pending/revoked
identities, expired identities, identities whose verification is older than the
configured freshness window, disallowed transports and capability mismatches.

Transport authentication is not authorization. A valid SSH, mTLS or network-level
identity still needs an allowed Skeleton capability before an action can proceed.

## Rotation

Rotation registers a second key id/version for the same `machine_id`. During the
verified overlap window the old binding is `ROTATING` and the new binding is
`TRUSTED`. After the new handshake is proven, the old binding becomes `REVOKED`.
No other node needs to rotate because one machine changes or loses its key.

## Secret-store boundary

Bitwarden/SecretStore may hold recovery or escrow material and service credentials,
but live private machine keys stay on their owning node or approved OS/hardware
keystore whenever possible. SecretStore references and private paths are separate
from the public Trust Registry contract.

This foundation deliberately performs no SSH, Bitwarden, network or runtime mutation.
Live enrollment is a later operator-gated step that verifies the target fingerprint
out of band, installs only the required public trust material, proves a read-only
handshake, then enables the minimum registered capability.
