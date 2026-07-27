# Resolver federation install

Run `sudo ./install.sh`, set the real `SKELETON_RESOLVER_NODE_ID`, peer targets and the dedicated SSH identity in `/etc/skeleton/resolver-sync.env`, then start `skeleton-resolver-sync.timer`.

Generate one dedicated Ed25519 key per node at `/var/lib/skeleton-resolver/.ssh/id_ed25519`, owned by `skeleton-resolver` with mode `0600`. On the peer, install the public key for user `skeleton-resolver` with:

```text
restrict,command="/usr/local/bin/skeleton-resolver-receive-from-ssh" ssh-ed25519 AAAA...
```

The forced command permits only checksummed resolver bundles. It does not grant a shell.

Example event:

```json
{
  "domain": "example.invalid",
  "resolver": "browser-player-v2",
  "event_type": "extract_success",
  "confidence": 90,
  "payload": {
    "player_adapter": "cinemar",
    "script_fingerprint": "sha256:...",
    "qualities": ["720p", "480p"],
    "latency_ms": 820
  }
}
```

Record with `skeleton-resolver-sync record --file event.json`. Complete URLs, cookies, tokens and private history are rejected.
