# S20 private WiFi LAN transport (source only)

This opt-in adapter adds TCP to TravelSecureLink without activating any Home,
TV, tablet, remote UI, service, discovery, or device operation. Existing INTERNET
permission suffices; no manifest change is needed. Android integration and real
Samsung S20 testing remain separate work.

A foreground owner explicitly calls `TravelLanTransport.host(true, address)` on
a worker using the numeric RFC1918 IPv4 address of its private WiFi interface.
Wildcard, public, link-local and IPv6 addresses are rejected. Loopback is allowed
for JVM tests. The caller must verify that the selected interface belongs to its
chosen WiFi network, and close the host/client on foreground exit or network
change. An RFC1918 address alone does not establish that a network is trusted.
There is no auto-start, subnet scan, broadcast discovery, retry, or reconnect.

Render `host.invite.qrPayload()` locally as a QR using an existing trusted UI.
This module supplies the QR payload, not a camera or QR renderer. Transfer it
out of band to the intended peer, parse with `TravelLanPairing.parse`, and call
`TravelLanTransport.connect(true, invite, sessionId)` from a worker. The payload
contains version, numeric address, ephemeral port, random 128-bit link ID and
random 256-bit key. It is a bearer credential: anyone who sees it can pair.
Never log it, persist it, send it through analytics, or expose it via intents,
URLs, screenshots or backups. `toString` is redacted. Close discarded invites;
connection setup destroys its invite key copy. Strings/QR pixels cannot be
reliably erased by the JVM; the UI must dismiss them when pairing ends.

Call `host.serve(session, dispatch)` once on a worker. `dispatch` must run its
callback synchronously on the session/player owning thread (Android main), with
a bounded wait. It must propagate failures and must not wait on the network
worker from that thread. Locally enable remote access separately using
`session.allowRemoteControl(true)`. Authenticated commands still pass through
all existing session/revision/player policy. Key confirmation in both directions
precedes any command. The listener is consumed by the first connection, including
failed authentication; an attacker can deny pairing but cannot execute commands.
Create a fresh host/invite after any failure. Never retry an uncertain command.

Each frame is a four-byte big-endian length followed by a complete AES-GCM record
(28..65536 bytes). Length is checked before allocation. Existing directional keys,
AAD, counters and tags reject tampering, replay, reflection and wrong-session keys.
One serialized request is in flight. Connect/accept deadlines and a socket-closing
watchdog bound a whole handshake or request/response, including partial reads and
blocked writes. Timeout defaults to 10 seconds and is restricted to 100..30000 ms.
No unbounded worker pool or unsolicited state stream is created. Owner close
interrupts socket I/O; serve's finally block revokes remote control on the owning
thread, clears keys and closes resources. It leaves local playback intact.
The foreground owner must join its serve worker off the main thread to observe
completed revocation. A dispatch callback that never returns violates this API's
contract and cannot be safely interrupted by the transport.

Run source contracts:

```
python3 -m unittest discover -s tests/android -p 'test_travel*py'
```

Run `TravelLanPairingTest`, `TravelLanTransportTest`, and `TravelSecureLinkTest`
with the Android Gradle unit-test toolchain (`gradle -p android/home
:app:testDebugUnitTest`). Tests use synthetic keys and loopback only: round trips,
revision enforcement, single-use invites, malformed/oversize/truncated frames,
wrong keys, tampering, reflection, replay, stalled peers and owner cleanup. No
LAN/device pairing is performed. The source-only runner has no Java/Kotlin/Gradle
runtime; Python source contracts do not substitute for executing these JVM tests.
