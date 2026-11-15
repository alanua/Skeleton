# Steel + Playwright disposable evaluation — 2026-11-15

**Execution status: BLOCKED. Verdict: unsuitable for production adoption on current
evidence.** This is an evidence/readiness verdict, not a claim that Steel cannot
work. Lab source is retained. No actual Chromium or human handoff success is claimed.
No PR/commit/publication was attempted under the Runner publication contract.

## Read-only preflight and explicit attempt

Sanctioned executor: Linux x86_64, Python 3.12.3, non-root UID 1000, 2 CPUs.
Initial available memory: 2,549,608,448 bytes; free disk: 1,708,445,696 bytes.
Explicit smoke observed 2,554,175,488 available memory bytes and 1,703,952,384 free
disk bytes. These are host/executor observations, not container resource peaks.
Cgroup root limit files were not available; no claim of complete nested limit
visibility. Thresholds: 3.5 GiB available RAM, 6 GiB free disk, 2 CPUs.

Docker and Podman executables are absent. Docker daemon permission therefore could
not be tested; no engine startup was attempted. Executor HTTPS requests to GitHub,
Docker Hub, GHCR, MCR and PyPI failed. Diagnostic on official GitHub request:
`URLError: [Errno -3] Temporary failure in name resolution`. No credentials,
environment dump, authentication file or private daemon endpoint was inspected.
An out-of-executor documentation browser could read some upstream files; that does
not establish executor package/image download capability.

`python3 tools/web_browser_probe/probe.py smoke` was explicitly executed and exited
**2**, before build/pull/start. Observed causes: DOCKER_EXECUTABLE_MISSING,
INSUFFICIENT_HEADROOM, OFFICIAL_DOWNLOAD_UNREACHABLE, STEEL_DIGEST_NOT_REVIEWED,
CLIENT_DIGEST_NOT_REVIEWED. Final source also explicitly blocks pending exact-image
stealth/default review and fixture-only process egress enforcement. Those source
gates cannot be satisfied by digest syntax alone. No sudo, daemon changes,
restriction bypass or alternate executor was attempted.

Minimum capability for completion: normal Runner execution with an authorized
Docker/Compose engine, official registry/PyPI/GitHub DNS+HTTPS access during
artifact preparation, sufficient headroom, and an already sanctioned mechanism
that enforces browser-process egress only to the synthetic fixture. Then resolve
and review immutable artifacts before removing the pending-review source gate.
Do not ask an operator to set up Docker based solely on this retained proposal.

## Identities, licenses and source evidence

| Component | Observed identity | Evidence and limitation |
| --- | --- | --- |
| Steel candidate | v0.5.5-beta, abbreviated commit 9fb9b92 | [Official releases](https://github.com/steel-dev/steel-browser/releases); full source commit and image digest UNRESOLVED |
| Steel API image | ghcr.io/steel-dev/steel-browser-api | [Official Compose](https://github.com/steel-dev/steel-browser/blob/main/docker-compose.yml); upstream moving tag is deliberately not copied |
| Playwright Python | candidate 1.56.0 | [Official tagged setup](https://github.com/microsoft/playwright-python/blob/v1.56.0/setup.py); driver metadata 1.56.1, installed package NOT_RUN |
| Client image | candidate v1.56.0-noble | MCR manifest/digest NOT_RESOLVED; compatibility NOT_RUN |
| Licenses | both Apache-2.0 | [Steel license](https://github.com/steel-dev/steel-browser/blob/main/LICENSE), [Playwright license](https://github.com/microsoft/playwright-python/blob/main/LICENSE); image-layer notices pending |

Reviewed official source before authoring: upstream README, Compose, session
service, route/schema files, and Playwright setup. Documentation source references
below are mutable inspection references, not immutable supply-chain attestation.

[Steel session service](https://github.com/steel-dev/steel-browser/blob/main/api/src/services/session.service.ts)
has one activeSession and pastSessions; IDs do not establish independent browsers.
[Session routes](https://github.com/steel-dev/steel-browser/blob/main/api/src/modules/sessions/sessions.routes.ts)
expose REST health/create/details/release, debug stream, recorded events and
live-details. [Session schema](https://github.com/steel-dev/steel-browser/blob/main/api/src/modules/sessions/sessions.schema.ts)
includes interactive/showControls stream parameters and fingerprint opt-out.
These establish an OSS surface to investigate, not accepted human control. Replay,
read-only live view and keyboard/mouse takeover have not been demonstrated.
Cloud-only feature parity is NOT_VERIFIED. Exact release source fetches and UI
component fetches failed in the documentation tool; no inference fills those gaps.

## Check ledger

| Check | Actual result |
| --- | --- |
| Focused pytest plus unchanged G1 contracts | PASS: 47 passed, 1.45 s on initial run |
| Python compilation | PASS |
| Explicit disposable smoke | BLOCKED: preflight exit 2, under 1 second; no build/pull |
| Real Chromium version/start/JS | NOT_RUN |
| Navigation/click/fill/extract/screenshot | NOT_RUN |
| Second automation connection, same identity/state | NOT_RUN |
| Second CDP context cookie/storage/draft isolation | NOT_RUN |
| Independent Steel session isolation/concurrency | NOT_VERIFIED; single-active source design concern |
| Typed challenge/resume/duplicate browser counter | NOT_RUN; source prepared, unit fixtures are not acceptance |
| HUMAN_VERIFIED handoff | NOT_VERIFIED; no operator, no protected route |
| G1 production auth/expiry binding to Steel | NOT_IMPLEMENTED; existing synthetic contract tests only |
| Runtime external-network denial proof | NOT_RUN; internal network and route allowlist alone insufficient |
| Owned session REST release | NOT_RUN |
| Container peak memory/CPU/PIDs | NOT_OBSERVABLE: zero containers started |
| Cleanup | No lab resources created; nothing retained or removed; finally path source present, runtime NOT_RUN |
| Full suite / baseline audit | NOT_RUN; no unrelated tests changed, skipped or weakened |

Final focused validation and diff check are recorded below after rerun. No limits
are described as measurements. No images, containers, volumes, profiles or bearer
URLs are retained. Synthetic screenshot is designed to remain in client memory.

## Production integration gaps

Resolve immutable release/full source/image identities and wheel hashes; inspect
notices and stealth defaults on those exact artifacts; prove fixture-only egress
at browser-process level; validate compatible CDP protocol; execute actual browser
and REST smoke with resource sampling; verify release and absence of owned
resources after cleanup; experimentally distinguish CDP-context isolation from
Steel-session replacement/concurrency; validate authenticated browser-only input
and view revocation; integrate exact G1 epoch/origin/expiry/idempotency binding;
verify human takeover separately. README stages a bounded #4976 proposal through
the existing attention/auth boundary; no tunnel, server or production change was
made. #4996/#4976 and the full Web program remain incomplete.

Final rerun: focused tests **47 passed in 1.32 s**; `py_compile` **PASS**;
explicit smoke **BLOCKED, exit 2** with the pending-review gate plus the five
capability/pin blockers above; `git diff --check` **PASS**. Final preflight:
2,558,681,088 available memory bytes, 1,702,305,792 free disk bytes, 2 CPUs.
