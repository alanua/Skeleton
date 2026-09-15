# Secret Store

Skeleton production credential resolution uses systemd encrypted credentials as
the host authority boundary and Bitwarden Secrets Manager as the value store.
Runner and mail services load `bitwarden-access-token`; no runtime path requires
a separate plaintext organization-id credential.

Registered service discovery is metadata-only. The Gmail bootstrap helper may
derive organization identity from the existing machine token through the fixed
Bitwarden identity endpoint, then select exactly one allowed Gmail OAuth secret
metadata record. The selected opaque UUID is persisted only through the existing
systemd encrypted credential path. The helper must not call `bws secret list`,
`secret get`, `get_by_ids`, sync-with-values, run, or export during discovery.
Public receipts expose only bounded status and reason fields.
