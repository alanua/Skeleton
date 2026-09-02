# Secret Store

Skeleton mail production uses Bitwarden Secrets Manager through a two-stage
boundary:

- Bootstrap discovers only the opaque Gmail OAuth secret UUID from Bitwarden
  metadata and encrypts the reference index directly with `systemd-creds`.
- Runtime rereads only the encrypted systemd credential and resolves the secret
  through the existing registered credential broker.

The bootstrap helper is installed at
`/opt/skeleton-mail-operations/bitwarden_gmail_reference_bootstrap.py` and runs
under `/opt/skeleton-mail-operations/bitwarden-sdk-runtime/bin/python`, an
isolated runtime containing `bitwarden-sdk==2.1.0`. Installers do not mutate
system Python and do not install live secrets.
