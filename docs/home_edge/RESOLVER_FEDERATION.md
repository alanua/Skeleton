# Resolver federation between Home Edge nodes

Status: **target implementation included in the node images**  
Updated: 2026-07-27

The German and Kyiv Home Edge nodes exchange resolver observations so that a successful extraction strategy, a failure signature or a changed player fingerprint learned at one location becomes evidence at the other location.

## Safety boundary

Federation shares reusable resolver knowledge, not private playback data. Bundles may contain domains, adapter names, selector and script fingerprints, parser versions, response-shape fingerprints, success/failure classes, quality metadata and bounded diagnostics.

Bundles must not contain cookies, authorization headers, passwords, tokens, signed stream URLs, complete page URLs, user identifiers, watch history or personal documents. The sync agent rejects sensitive keys and URL values before recording or importing an event.

## Convergence model

Each node has an append-only SQLite event store. Every event has a globally stable content-derived ID. Nodes periodically export all federated events as a compressed bundle and push it to configured peers over a dedicated SSH identity on the approved private network. Imports are idempotent, so nodes converge after either side returns online.

Remote evidence is never activated automatically. An imported event enters `remote_evidence`; the receiving resolver must reproduce and validate it locally before promotion to an active rule. This prevents one site-specific response, block page or compromised observation from changing the other node's production behavior.

## Nodes

- `home-edge-01`: German node and current production media/resolver environment.
- `kyiv-home-edge-01`: Debian VM on the Kyiv Proxmox host.
- `kyiv-media-pi4-01`: Raspberry Pi 4 direct-play endpoint. It may report playback outcomes but does not promote resolver rules.

## Transport

The service account is `skeleton-resolver`. Each node receives a dedicated peer key. The peer public key is installed with the forced command `/usr/local/bin/skeleton-resolver-receive-from-ssh`, so it can only deliver a bounded, checksummed bundle into the resolver inbox. General shell access is not required.

The initial timer runs every ten minutes and also processes the local inbox. Offline nodes catch up on the next successful exchange.
