# G2 bounded web execution

Base reviewed: `0f6b134d939c2059499f4010b881bb7b05720832` (G1 / #4978).
Canonical session/attention owners remain `core/web_session.py` and
`core/operator_attention.py`. Registry inspection found no generic acquisition or
browser executor. The device-specific firmware uploader's explicit redirect
handling is retained as a safety principle, not reused as a public-web client.
No registry, dependency, gate or G1 model changes are included.

## API and privacy

`Fetcher.fetch(https_url)` provides bounded static text plus PRIVATE source/final
URL, UTC check time, HTTP status, access reason and raw body. It makes GET requests
only, ignores proxy environment variables, requests identity encoding, validates
all DNS answers, and connects to a validated numeric address with hostname TLS
verification. Every redirect is separately checked; a rebinding answer fails
closed. Limits cap the whole fetch at 30 seconds, 16 requests, 4 MB aggregate and
200k extracted characters; defaults are smaller. DNS has a deadline and at most
four outstanding daemon lookups. The HTTP watchdog shuts down the wire socket,
including during slow streaming. Errors contain fixed reason codes, never URL,
response, cookie, path or underlying exception text.

`BrowserAdapter` owns up to four isolated contexts, reuses G1 state and Operator
Attention, and exposes create/open_session, status, navigate, read, click, type,
select, wait, screenshot, close. Every operation declares a `WebActionClass`.
External mutation/high impact are rejected. Click resolves a single anchor and
performs guarded acquisition; it never dispatches a DOM click. Type/select only
modify the private offline document. Form submission is unsupported. Acquisition
is limited per navigation and each session permits 64 operations. An adapter retains
at most 32 session records, including closed-session tombstones. Wait is at most
one second. Each session retains at most four 2 MB screenshots, returned as opaque
`private:` refs. These in-memory artifacts are scoped to their session and purged
on close. Downloads are disabled. No public serialization of page evidence is
provided; callers must not log the private values. SecretReference is accepted
and retained opaquely; no secret resolution or credential injection occurs.
Host-only transport/resolver/backend injection supports deterministic no-network
fixtures; tasks must never supply these dependencies.

A challenge uses `require_challenge`/`require_captcha`, then G1 attention delivery,
in-progress transitions and exact-token resolution. Waiting blocks page operations
but retains the identical context, profile, form data and session epoch. Status
and close remain available. Human delivery and handoff integration is not part of
this adapter. Untrusted page text has no authority to trigger additional actions.
Cross-origin top-level navigation is rejected because G1 binds one exact origin.

## Optional runtime and exact packaging child

**Runtime packaging blocker:** Playwright and Chromium are absent from the project
manifest. Nothing was installed. Production Chromium rendering and a live human
handoff have not been validated. `PlaywrightDOM` lazily imports Playwright and
fails with `BROWSER_RUNTIME_UNAVAILABLE` when deployment prerequisites are absent.
The in-process backend fixtures validate the contract independently.

The optional backend uses isolated persistent Chromium contexts under the fixed
`/var/lib/skeleton/private/web/<generated UUID>` root, never under a checkout.
Deployment must provision the root owned by the service UID with mode 0700;
symlink ancestors are rejected. No task profile/executable/flags/path parameters
exist. Chromium sandbox is explicitly enabled. JavaScript, native network,
service workers and downloads are disabled; all context routes abort, including
subresources. Only bounded HTTP-acquired markup is loaded. This is an offline
renderer; JavaScript application shells cannot yet be verified. Profiles remain
private for retention/cleanup by the runtime operator; close releases processes
but does not promise secure deletion of persistent profiles.

Prepared child: **WEB_MCP_G2_RUNTIME_PACKAGING — optional Playwright/Chromium
sandbox and private artifacts**. Bounded scope: dependency/lock and image/runtime
packaging with pinned compatible versions, provision fixed private root and
retention quotas, sandbox-compatible non-root UID, OS-enforced deny-by-default
browser egress (IPv4/IPv6, DNS, WebRTC, WebSocket, service workers, popups and
downloads), broker every permitted request through pinned bounded acquisition,
CPU/memory/process/deadline limits, validate browser shutdown and restart cleanup,
and connect existing same-context human handoff. Only then consider enabling
scripts. Acceptance: isolated synthetic contexts, hostile redirects/subresources,
DNS rebinding, streaming/header timeout, process/resource exhaustion, private
artifact permissions, exact-context pause/resume and no external submission.
No Selenium substitution, package install, live login or CAPTCHA bypass here.

## Separate provider listing verification child

Generic HTTP/DOM acquisition does **not** implement listing verification. Neither
HTTP 200 nor a screenshot establishes active status, seller stock or exact ad
identity. HTTP 404 cannot distinguish sold, deleted or expired. Access failures
remain reasons for CANNOT_VERIFY, never invented lifecycle states. Search results,
snippets and caches are discovery candidates only.

Prepared child: **WEB_LISTING_EXACT_ID_VERIFIER — provider adapters and immutable
private evidence history**. Scope: separate provider modules using this generic
lane and existing operational/evidence boundaries; typed provider + immutable
ad/auction ID + canonical URL identity; validate final redirect identity against
the exact source; HTTP-first then permitted browser rendering; distinguish page
access, supported ACTIVE/RESERVED/SOLD/REMOVED/ENDED lifecycle, and independently
confirmed seller availability (unknown by default). Do not identify by title.
Challenge/login/403/429/timeout/5xx/robots/empty-shell/parser failures must produce
CANNOT_VERIFY plus a precise reason; challenges route through G1 attention.

Store immutable private evidence refs, bounded relevant text/screenshots, checked_at
UTC, source URL, method, observed state and field provenance. Extract only stated
price/currency, model/variant, condition/defects, location, delivery/pickup and
auction end/current bid/fees. Preserve unknowns and title/body/spec contradictions.
Retain old verified values with their old timestamps separately; require explicit
freshness bounds and exact-candidate revalidation before actionable presentation
or collection advice. Deduplicate by verified provider/ID, preserve history and
show changes. Search absence never establishes removal. No unbounded retries.

Required child synthetic matrix: active/reserved/sold/removed/ended; active without
stock confirmation; 200 challenge and empty shell; 403/429/timeout/login; wrong-ID,
category/home/search/login redirects; stale cache versus fresh source; conflicting
price/variant; search miss with directly active page; exact-ID deduplication and
append-only evidence. No seller messages, booking, purchases, contacts, real user
searches or live network fixtures. These provider tests are deliberately pending
that child, not claimed by generic fetch tests. Completion of listing verification
also requires separately approved real-page read-only acceptance; synthetic results
alone do not establish provider compatibility.

## Source validation

Run `python3 -m pytest -q tests/test_web_acquisition.py tests/test_web_browser.py tests/test_web_mcp_g1_contracts.py`.
The tests use injected DNS, transports and in-process documents only. Runtime
constructor behavior is tested with synthetic objects; no browser is installed or
launched and no real accounts or external mutations are involved.
