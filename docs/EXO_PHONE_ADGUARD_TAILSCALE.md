# EXO Phone AdGuard Tailscale Contract

This contract defines a non-mutating diagnostic and future rollout gate for one
registered Android phone using AdGuard DNS over the existing tailnet. It does
not deploy DNS, change Tailscale, change LAN DHCP, change the secondary AdGuard
node, or alter any Home Edge service.

## Diagnostic Boundary

- Phone identity comes from the registered device node id supplied at runtime.
- DNS endpoints are runtime-injected and may appear only as public labels in
  reports. Private node identifiers, hostnames and addresses must not be written
  into source, docs, tests or public report output.
- `scripts/redmi_dns_probe.py` performs read-only DNS queries only. It does not
  call Android settings, Tailscale admin APIs, AdGuard admin APIs, DHCP, router
  controls or the Home Edge executor.
- DNS success proves only resolver behavior for the queried domains. It is not
  physical application verification and does not prove that apps stopped
  rendering ads.

The report separates:

- DNS reply correctness: an allowed domain returns a usable answer.
- Filter correctness: a blocked fixture domain receives a filter response.
- Tailnet reachability: the phone route can reach the injected resolver label.
- Android active resolver: independent evidence that Android is using the
  intended resolver path.
- AdGuard coverage: DNS filter behavior consistent with the declared AdGuard
  route.
- App ad-rendering limitations: separately verified app behavior, outside DNS.

## Phone-Specific Candidates

1. Android Private DNS using encrypted DNS-over-TLS to an AdGuard hostname
   reachable only over the tailnet.

   This is the preferred phone-only candidate if a stable private hostname,
   certificate, and Android verified-state evidence can be supplied. It can
   coexist with MagicDNS as long as the Private DNS hostname resolution and the
   Tailscale VPN route are independently verified. Android VPN behavior must be
   tested because Tailscale is normally the active VPN and may affect which
   resolver path Android can actually use.

2. Tailscale MagicDNS plus tailnet resolver settings.

   This may be valid for name resolution, but it risks becoming tailnet-wide if
   applied at the tailnet admin layer. This contract forbids such mutation from
   the phone-only probe. A tailnet-wide DNS option must be a separate operator
   approval packet and must preserve the secondary AdGuard, primary failover,
   LAN DHCP, DNS enforcement, Android media and all other Home Edge services.

3. Termux-local DNS testing.

   Termux can probe DNS reachability and filter behavior, but it cannot be
   treated as proof that Android system Private DNS settings were applied. A
   valid Android setting must not be assumed applyable through Termux.

If the phone-only candidate cannot satisfy registered identity, tailnet
reachability, DNS correctness, filter correctness, independent Android resolver
verification, and no tailnet-wide mutation, the rollout outcome is fail-closed
`blocked`. The separately approvable alternative is a tailnet-wide DNS plan,
not an implicit change to everyone else's DNS.

## Future Staged Operation Contract

Any future deployment must be a separate operator-approved runtime task through
the registered Skeleton Home Edge executor and audit path. This repository task
does not execute it.

Stage 0: prepare

- Read private runtime config references without printing values.
- Verify the registered phone node id and the Home Edge executor identity.
- Snapshot current phone resolver state, Tailscale state and AdGuard route
  labels into private audit.
- Confirm rollback instructions and idempotency key.

Stage 1: read-only diagnostics

- Run `scripts/redmi_dns_probe.py` with injected endpoint labels and private
  coordinates.
- Record public-safe report plus private audit receipt.
- Stop if any postcondition is blocked or unverified.

Stage 2: operator-approved phone-only change

- Apply only the approved phone-specific Android DNS setting through the
  supported operator path for that device.
- Do not mutate tailnet DNS, LAN DHCP, router settings, secondary AdGuard,
  primary failover or unrelated Home Edge services.

Stage 3: verify and rollback

- Re-run diagnostics.
- Verify Android active resolver independently from the DNS query result.
- Verify AdGuard coverage and preserve app ad-rendering as a separate manual or
  app-level check.
- If verification fails, revert the phone setting to the Stage 0 snapshot and
  write a private audit receipt.

Required postconditions:

- The registered phone identity matches.
- Tailscale reachability is present.
- Allowed DNS replies are correct.
- Blocked DNS fixtures are filtered.
- Android active resolver is independently verified.
- AdGuard coverage is observed.
- No tailnet-wide mutation occurred.
- Any app ad-rendering claim is backed by separate physical/app evidence.
