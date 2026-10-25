# Brother MFC-J4540DW WBM status adapter

This module implements a bounded HTTPS session for the printer's web management status page. It does not expose print-head cleaning, test-page, scan, or other maintenance actions.

## TLS modes

- `ca` (default): validate the device certificate chain and hostname with the system trust store.
- `pin`: require a configured SHA-256 leaf certificate pin. The pin is checked on the active TLS connection before any HTTP request is sent.
- `encrypted_unverified`: keep HTTPS/TLS encryption but skip certificate identity verification. This is intended only for a private LAN with a locally managed device. It does not downgrade to HTTP and does not protect against an active on-path impersonator.

Do not supply a password until the client has established TLS using one of these explicit modes. The adapter uses TLS 1.2 or newer, fixed same-origin paths, bounded response sizes and timeouts, no automatic redirects, and generic redacted errors. The client closes the connection and clears in-memory cookies on success or failure.

## Broker binding

The only credential route is the code-owned `RegisteredMaterialCredential` for service `brother-mfc-j4540dw`, alias `wbm-admin`, and action `read-authenticated-status`. Credential material is delivered directly to the in-process client callback and is never returned in status metadata.

The login form is accepted only when it matches the observed contract: password input `Baf9`, hidden `loginurl=/general/print_head_cleaning.html`, and POST action `/home/status.html`. The adapter verifies an authenticated response only when it is HTTP 200, has the exact Brother model title, contains a Logout control, and has no login/password form. It performs no maintenance action.

## Activation

Keep this adapter inactive until the Home Edge runtime has an explicit registration and a locally configured TLS mode. For pin mode, configure the pin out of band; the adapter never enrolls a pin from its first connection. For a private self-signed printer, `encrypted_unverified` is available as an explicit operator choice when certificate provisioning is not practical.
