# Disposable Steel + Playwright evaluation

This lab is retained for review, **blocked before runtime**. It is not production
browser integration and does not complete #4996, #4976 or the Web program.
Read RESULTS.md before execution. No dependency on #4994.

Run from the repository root:

```sh
python3 tools/web_browser_probe/probe.py preflight
python3 tools/web_browser_probe/probe.py smoke
```

The explicit smoke exits 2 when optional execution capabilities are absent.
Default pytest only checks configuration and G1 contracts. No global Python
installation is needed or permitted. Build dependencies go into the client image.
The smoke accepts no shell, URL, image, or session arguments. It never prints
unfiltered container logs. It holds a local directory lock for one experiment.

## Pinning and provenance gate

Candidate Steel release: **v0.5.5-beta**, release commit **9fb9b92** (abbreviated
identity from official release page, not an immutable resolved source pin).
Registry: `ghcr.io/steel-dev/steel-browser-api`. Reviewed immutable digest:
**UNRESOLVED**. Do not substitute a guessed digest or use a moving tag.

Candidate client: official `mcr.microsoft.com/playwright/python:v1.56.0-noble`,
Playwright Python **1.56.0**; registry digest **UNRESOLVED**. requirements.lock pins
all proposed Python dependencies, but wheel hashes and installation compatibility
have not been verified. This is a version lock, not a verified artifact lock.

On an authorized Runner with registry access, resolve both manifests for linux/amd64,
record the full digests, source commit, release relation, OCI labels and available
attestations in RESULTS.md; inspect that exact Steel source for fingerprint/plugin
defaults and network behavior. Then provide `STEEL_DIGEST` and `CLIENT_DIGEST` as
`sha256:` plus 64 hex digits. Current Compose intentionally refuses absent pins.
The digest syntax check is not a provenance verifier. Review must precede execution.
No floating upstream image is present in runnable configuration. The local client
image is built from the pinned base and version-locked requirements.

Both upstream projects declare Apache-2.0. Preserve their LICENSE/NOTICE material
in distributed images; this lab neither vendors nor relicenses upstream code.
Image layer licenses and notices remain part of the pending exact-artifact review.

## Scope and safety

Compose has no published ports, host networking, profile mounts, docker.sock,
privileged mode or persistent volumes. It creates an internal network; runtime
services have aggregate limits of 3 GiB and 2 CPUs, bounded PIDs, and dropped
capabilities. Pull/build occurs first; startup does not build. Preflight requires
3.5 GiB available RAM, 6 GiB free disk and 2 CPUs. Build resource limits are not
claimed as runtime limits. The 570-second runtime budget reserves 30 seconds for
owned-project cleanup; preflight itself is separately bounded by request timeouts.
Only the unique lab Compose project is removed in finally. No prune or image removal.

The browser tests a fixed TEST-NET-1 address, then applies a Playwright request
allowlist to the fixture endpoint. The internal network blocks normal external
routing; it is **not** a per-destination host firewall. Application interception
is defense in depth, not proof of browser-process or DNS egress containment.
Before execution, a sanctioned executor must enforce fixture-only browser egress
(including DNS, UDP, IPv6, redirects and host bridge access) without changing the
host daemon. This is an unresolved execution prerequisite; do not equate a failed
TEST-NET request with a complete network-policy proof. The lab must stay blocked
until that policy and exact-image stealth defaults are reviewed. No proxy,
CAPTCHA solver, paid API, LLM, cloud account or real external site is used.

The script uses actual Steel REST plus Chromium CDP. It checks delayed JS, browser
operations, an in-memory PNG, same-page reconnection, cookie/localStorage/draft
continuity, a second CDP context, typed synthetic challenge and duplicate no-op.
A second context is not a second independent Steel session. The OSS source has
one activeSession; concurrency remains unsupported/unverified until an actual
backend experiment proves otherwise. Release response is checked separately from
Compose cleanup. No raw profile or screenshot is retained.

## Protected handoff proposal for #4976 (not deployed)

Existing G1 `WebSessionBinding` carries provider, exact session ID, epoch and HTTPS
origin. `WebInteractiveChallenge` creates a typed attention request; public output
must use `public_receipt()` and keep runtime binding/resume token private. The
synthetic HTTP fixture cannot be passed off as satisfying G1's HTTPS origin rule.
No adapter, auth proxy or attention delivery integration is implemented here.

The OSS session stream schema exposes `interactive` and `showControls`; routes
include debugger HTML and live-details. This supports investigating browser-only
control, but a schema flag alone does not prove keyboard/mouse delivery. Recorded
events/replay are distinct from live view, and live pixels are distinct from input
control. Do not infer cloud feature parity or human acceptance from these sources.

For a protected follow-up: through Skeleton's authenticated operator boundary,
authorize one actor for one exact session/epoch/origin, issue a short-lived private
route, and proxy only the browser view/input protocol. Never expose Steel/CDP or
pass connection URLs into notifications. Pause automation; let that actor type in
the existing tab, verify draft/cookie/storage continuity, then resolve the typed
attention using its private resume token before expiry. Validate origin and epoch
again on resume; reject stale, wrong-actor, expired and duplicate resolutions.
Revoke input/view access and release the owned session on completion or timeout.
Test replay, read-only viewing and actual key/mouse input separately. No desktop,
terminal, arbitrary CDP access or shell control belongs in that route. With no
protected route or operator interaction here, HUMAN_VERIFIED is NOT_VERIFIED.
