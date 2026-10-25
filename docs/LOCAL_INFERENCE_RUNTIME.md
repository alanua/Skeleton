# Local inference runtime

Skeleton uses the installed loopback-only Ollama service as a reusable runtime capability. The model is not an autonomous shell agent and cannot mutate files, databases, calendars, Drive, GitHub, deployment state or secrets.

## Runtime topology

```text
private producer handoff
→ persistent atomic queue
→ single always-on worker
→ http://127.0.0.1:11434
→ strict JSON result validation
→ private result/review queue
→ domain-controlled Skeleton action
```

The worker is independent of GitHub, Runner, Codex and cloud model availability. It polls at 0.5 seconds by default, survives restarts, recovers stale claims, retries bounded transient failures and quarantines exhausted or unsafe tasks.

## MFP first consumer

The document conveyor writes one completed OCR handoff packet atomically into:

```text
$SKELETON_MFP_INFERENCE_HANDOFF_ROOT/pending/*.json
```

Packet contract:

```json
{
  "schema": "skeleton.family_document_inference_handoff.v1",
  "idempotency_key": "stable-private-document-key",
  "payload": {
    "ocr_text": "private OCR text",
    "languages": ["de"],
    "source_kind": "mfp",
    "page_count": 2,
    "mime_type": "application/pdf",
    "scan_provenance": {
      "session_id": "session-1",
      "scanner_id": "scanner-1",
      "profile_id": "profile-1",
      "pages": [
        {"pass_id": "pass-front", "sheet": 1, "side": "front", "page": 1},
        {"pass_id": "pass-back", "sheet": 1, "side": "back", "page": 2}
      ]
    }
  }
}
```

The three allowed family aliases are loaded from a separate root-owned private configuration file; the scanner packet cannot choose or override them. The worker claims the packet, writes a private receipt containing the opaque request ID, and immediately sends the bounded OCR payload to the configured local model. The result is a classification proposal only: subject aliases, fixed topic, issuing jurisdiction, date precision, document type, issuer, summary, evidence, confidence and allowed calendar-event candidates.

Invalid, low-confidence or incomplete model output is retried and then quarantined/reviewed. It never creates guessed archive paths. Deterministic archive verification, MemoryGateway mutation, canonical SQLite commit, calendar upsert and projection outbox remain separate controlled domain actions.

## Installation

The installer writes a private environment file and systemd unit but intentionally does not enable or start it:

```bash
sudo scripts/install_local_inference_worker.sh \
  /opt/Skeleton \
  /var/lib/skeleton/local-inference \
  /var/lib/skeleton/mfp-inference-handoff \
  /etc/skeleton/family-subject-aliases.json \
  agent \
  qwen2.5:1.5b
```

Expected receipt:

```text
INSTALLED_NOT_STARTED
```

After operator review, activation is explicit:

```bash
sudo systemctl enable --now skeleton-local-inference.service
```

Aggregate health contains queue counts only:

```bash
python3 -m scripts.local_inference_worker status
```

No private paths, OCR, document names, aliases, identifiers or result values are printed by the public status command.

## Future consumers

A new consumer registers an `AdapterSpec` with a request type, prompt builder and strict output validator. The queue, retries, Ollama client, service and security boundary remain unchanged.


MFP handoffs may include `payload.scan_provenance`: safe `session_id`, `scanner_id`,
`profile_id` tokens and up to 500 page entries containing `pass_id`, one-based `sheet`,
`side` (`front`/`back`), and one-based `page`. Tokens are restricted to ASCII letters,
digits, underscores and hyphens. Coverage must exactly match `page_count`; duplicate
pass/sheet/side entries are rejected. Unknown fields, paths, and OCR text cannot be
embedded in provenance. Validation establishes structural consistency; upstream
scanner verification remains responsible for the accuracy of supplied metadata.

Validated provenance survives alias binding and inference queue submission. The durable
handoff receipt retains only this safe provenance, aggregate page count, request ID,
and status. Private OCR remains in the private request packet, never the receipt.
Invalid packets receive the fixed `HANDOFF_INVALID` reason without exception text.
