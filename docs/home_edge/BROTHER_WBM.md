# Brother MFC-J4540DW WBM status adapter

This module implements a bounded HTTPS session for the printer's web management status page. It does not expose print-head cleaning, test-page, scan, or other maintenance actions.

## TLS modes

- `ca` (default): validate the device certificate chain and hostname with the system trust store.
- `pin`: require a configured SHA-256 leaf certificate pin. The pin is checked on the active TLS connection before any HTTP request is sent.
- `encrypted_unverified`: keep HTTPS/TLS encryption but skip certificate identity verification. This is intended only for a private LAN with a locally managed device. It does not downgrade to HTTP and does not protect against an active on-path impersonator.

Do not supply a password until the client has established TLS using one of these explicit modes. The adapter uses TLS 1.2 or newer, fixed same-origin paths, bounded response sizes and timeouts, no automatic redirects or request retries, and generic redacted errors. Redirect locations must be literal approved paths without an origin, query, or fragment; server-provided URLs are never followed automatically. The client closes the connection and clears in-memory cookies on success or failure.

## Broker binding

The only credential route is the code-owned `RegisteredMaterialCredential` for service `brother-mfc-j4540dw`, alias `wbm-admin`, and action `read-authenticated-status`. Credential material is delivered directly to the in-process client callback and is never returned in status metadata.

The login form is accepted only when it matches the observed contract: password input `Baf9`, hidden `loginurl=/general/print_head_cleaning.html`, and POST action `/home/status.html`, with the login control and fields inside one complete form. Ambiguous, split, or duplicate forms are rejected. The adapter verifies an authenticated response only when it is HTTP 200, has the exact Brother model title, contains a Logout control, and has no login/password form. It performs no maintenance action.

## Activation

Keep this adapter inactive until the Home Edge runtime has an explicit registration and a locally configured TLS mode. For pin mode, configure the pin out of band; the adapter never enrolls a pin from its first connection. For a private self-signed printer, `encrypted_unverified` is available as an explicit operator choice when certificate provisioning is not practical.

## Offline validation

`python3 -m pytest -q tests/test_brother_wbm.py tests/test_registered_credential_runtime.py` uses synthetic HTML, certificates, credentials, and HTTPS transport doubles. It covers TLS policy and ordering, rejected pins before HTTP or credential consumption, login and authenticated-status parsing, redirect restrictions, response bounds, redacted failures, and session cleanup. These fixtures do not establish live device compatibility or authorize activation.
