# AniTube Resolver Research

Status: RESEARCH / NOT_A_FIX
Date: 2026-07-29

This note records implementation constraints for the synthetic AniTube resolver task. It is not a production bypass plan and does not contain credentials, cookies, proxy guidance, CAPTCHA solving, residential IP routing, or access-control circumvention steps.

The resolver treats Cloudflare hard-block responses as an origin protection condition. Detection is limited to bounded response metadata and body indicators: HTTP status, `Server`/`cf-*` headers, and short body markers such as Cloudflare block pages. When detected, the resolver returns `origin_protected` and starts a 3600-second per-origin cooldown.

Cooldown keys are origin-scoped for AniTube and intentionally normalize `https://anitube.in.ua/example` and `https://anitube.in.ua/example.html` to the same source identity and cooldown key. During cooldown the resolver fails fast before invoking browser automation or public mirror attempts.

Inferred historical endpoints, embedded playlist field names, and URL forms are unverified. Synthetic fixtures in this repository model only public-safe shapes needed to validate parser behavior: voices, seasons, episodes, qualities, Cloudflare block pages, ordinary 403 pages, and trailer-only responses.

Operational boundaries:

- Preserve SSRF validation and HTTPS-only URL classification.
- Preserve redirect, output, and timeout bounds in existing download paths.
- Reject trailer-only inputs instead of returning `READY`.
- Do not modify TV mode, current MPV playback, or Home Edge remote control behavior.
