# Security Policy

Skeleton coordinates code, runtimes, infrastructure, and physical-device control paths. Security reports are therefore treated as maintainer-sensitive even when the affected component appears small.

For the repository's explicit trust boundaries, authority model, high-value attack surfaces, and fail-closed expectations, see [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Supported code

Security fixes target the current `main` branch and any explicitly documented supported release/runtime line. Historical prototypes, archived evidence, and stale deployment snapshots should not be assumed supported unless the repository says otherwise.

## Reporting a vulnerability

Please do **not** publish exploit details, credentials, private topology, tokens, keys, personal data, or a working proof-of-concept in a public issue.

Preferred route:

1. Use GitHub private vulnerability reporting / Security Advisories for this repository when available.
2. If that private route is unavailable, contact the primary maintainer `@alanua` through the GitHub profile and request a private reporting channel. A public issue may be used only to request contact, without vulnerability details.

Include the affected component, impact, prerequisites, reproduction outline, and any suggested mitigation. Do not test against systems or devices you do not own or have permission to assess.

## Scope notes

High-sensitivity areas include:

- execution and approval bypasses;
- credential or secret exposure;
- memory/privacy boundary failures;
- cross-project or cross-device authority confusion;
- network/router/firmware control paths;
- physical-device actions without the registered executor or approval gate;
- audit evidence tampering or state-verification gaps;
- unsafe fallback behavior between model/provider/runtime routes.

## Coordinated handling

The maintainer will validate the report, identify affected paths, prepare a bounded fix and regression coverage, and coordinate disclosure after a repair is available. No fixed response-time SLA is promised at the current project stage.
