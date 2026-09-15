# Machine Identity

Machine Identity is the public-safe contract for managed Skeleton nodes. Each
node owns its own private key material locally; this repository stores only
metadata that can be reviewed without exposing secrets.

## Public Fields

- `machine_id`: stable node identifier.
- `node_class`: host or constrained relay profile.
- `key_id`: public key identity label.
- `key_version`: positive integer version for deterministic rotation.
- `public_fingerprint`: public fingerprint or certificate fingerprint.
- `transport_profiles`: transports the identity can authenticate with.
- `capabilities`: capability names the identity may request.
- `issued_at`, `expires_at`, `last_verified_at`: public validity metadata.

The contract deliberately has no fields for private keys, passwords, tokens,
private certificates, secret-store paths or runtime handles.

## Validation

`MachineIdentity` rejects unstable identifiers, non-positive key versions,
malformed public fingerprints, duplicate transports or capabilities, naive
timestamps, verification before issue time and expiry before issue time.

Machine identity proves only that a public key fingerprint is known for a node.
It does not authorize actions. Transport authentication must still pass through
the Trust Registry capability decision.

## Privacy Boundary

Private keys never leave the node in this slice. Enrollment, rotation and
revocation are represented only as public metadata changes; live SSH, mTLS,
Tailscale, device and secret-store operations are intentionally out of scope.
