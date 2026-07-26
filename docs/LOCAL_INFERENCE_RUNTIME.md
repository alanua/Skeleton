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
    "allowed_subject_aliases": ["configured-alias-1", "configured-alias-2", "configured-alias-3"],
    "languages": ["de"],
    "source_kind": "mfp",
    "page_count": 2,
    "mime_type": "application/pdf"
  }
}
```

The worker claims the packet, writes a private receipt containing the opaque request ID, and immediately sends the bounded OCR payload to the configured local model. The result is a classification proposal only: subject aliases, fixed topic, issuing jurisdiction, date precision, document type, issuer, summary, evidence, confidence and allowed calendar-event candidates.

Invalid, low-confidence or incomplete model output is retried and then quarantined/reviewed. It never creates guessed archive paths. Deterministic archive verification, MemoryGateway mutation, canonical SQLite commit, calendar upsert and projection outbox remain separate controlled domain actions.

## Installation

The installer writes a private environment file and systemd unit but intentionally does not enable or start it:

```bash
sudo scripts/install_local_inference_worker.sh \
  /opt/Skeleton \
  /var/lib/skeleton/local-inference \
  /var/lib/skeleton/mfp-inference-handoff \
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
