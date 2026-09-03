# Architecture review request: near-zero-cost semantic Gmail pipeline

## Role

Please act as a critical architecture reviewer for Skeleton. Do not treat this document as canon and do not propose bypassing Skeleton approval/audit boundaries. Start from [`BOOT_MANIFEST.yaml`](../BOOT_MANIFEST.yaml) and the Claude adapter contract in [`adapters/claude/START_HERE.md`](../adapters/claude/START_HERE.md).

## Problem

Skeleton needs to semantically organize one Gmail account with roughly **10,777 historical messages**, then process only new changes incrementally. Gmail API access is already working with `gmail.modify` and `gmail.send` scopes. The first full message-ID inventory is complete. The mailbox includes Inbox, Sent, Spam, archived mail and other system labels.

We intentionally rejected regex/body-keyword classification because it produced systemic false positives. Examples: bank promotions classified as finance/important, ordinary GitHub discussion classified as a technical report, newsletters polluted by footer keywords, and automated `No jobs were run` messages over-promoted.

A semantic email adapter now separates two independent axes:

- `primary_category`: domain such as government, finance, work, security, technical, shopping, travel, education, personal, ads, spam, other;
- `message_kind`: automated_report, incident_alert, issue_discussion, proposal, action_request, transactional_notice, newsletter, promotion, personal_correspondence, other.

The relevant implementation is [`core/email_message_local_inference.py`](../core/email_message_local_inference.py) and its tests are in [`tests/test_email_message_local_inference.py`](../tests/test_email_message_local_inference.py).

The current inference topology is local-first: Ollama `qwen2.5:1.5b`, then Gemini API fallback. The local 1.5B model is not reliable enough for autonomous mailbox mutation. Gemini quality is adequate, but the current free-tier key is limited to only 20 `gemini-3.5-flash` generation requests and has already hit HTTP 429 quota exhaustion. We therefore need an architecture that does **not** require an expensive LLM call for every historical message.

## Required behavior

1. Perform a one-time historical baseline across all relevant mail, including **Sent and Spam**.
2. After baseline, process only Gmail history/checkpoint deltas; never repeatedly rescan all 10k+ messages.
3. Preserve Gmail originals as source evidence.
4. Store normalized/private correspondence history locally; do not put raw mail bodies into public Git or MemoryGate.
5. Promote to MemoryGate only durable facts that can affect future understanding or action: commitments, deadlines, legal/government matters, work, documents, status changes, important contacts, security events, payments/bills, orders, travel, insurance, tax, technical incidents, confirmations/refusals/risks, etc. Payments are only one example.
6. Advertising, ordinary newsletters, duplicates, inconsequential automated noise and obvious spam must not enter MemoryGate.
7. Mailbox mutation must be conservative, auditable and idempotent. Low-confidence semantic output should go to review rather than silently changing labels.
8. The architecture should remain model-neutral: Gmail API handles mail access/mutation; inference is replaceable.

## What we want reviewed

Please propose the strongest **free or near-zero-cost** architecture you would use under these constraints. In particular, answer these questions:

1. **How should we reduce LLM calls by one or two orders of magnitude without falling back to brittle regex classification?** Consider Gmail's native category/importance/spam signals, thread structure, sender history, deterministic MIME/header features, deduplication, embeddings, clustering, rules learned from already-confirmed examples, and confidence gating.
2. **Which decisions are safe without a large LLM, and which should always require stronger semantic inference?** Give explicit trust boundaries.
3. **Should Gmail's own `CATEGORY_PROMOTIONS`, `CATEGORY_UPDATES`, `CATEGORY_SOCIAL`, `CATEGORY_FORUMS`, `IMPORTANT`, Spam and other labels be treated as features, priors, routing signals, or authoritative classifications?** Explain how to avoid amplifying Gmail mistakes.
4. **What local model or embedding/reranker stack would actually be realistic on the current inference node (2 CPU, about 4 GiB RAM, no GPU)?** We currently have qwen2.5:1.5b and nomic-embed-text. Recommend only options that plausibly fit this machine.
5. **Would a retrieval/prototype approach work better than asking a generative model to classify every message?** For example: embeddings of confirmed exemplars + nearest-neighbor confidence + LLM only for boundary cases.
6. **How would you do thread-level reasoning?** We care about complete correspondence history, especially Sent ↔ received replies, and do not want to classify each email independently when the thread already gives context.
7. **How should we calibrate confidence and review?** Propose thresholds for auto-label, review-only, and no-action, and a method for measuring false-positive risk before enabling bulk Gmail mutation.
8. **What is the best one-time bootstrap strategy for 10,777 historical messages?** We can tolerate slow processing if it is reliable and free/cheap. Estimate what fraction should need strong LLM inference after prefiltering/clustering.
9. **What is the best incremental strategy after bootstrap?** New mail volume is small, so architecture may differ from the historical pass.
10. **Can the built-in Gemini features inside Gmail be meaningfully reused as part of an automated Skeleton backend, or should we treat Gmail's UI Gemini as user-facing only?** Be precise about API/automation boundaries rather than assuming the Gmail UI assistant is callable programmatically.
11. **What failure modes are we missing?** Especially label drift, sender-domain shortcuts, quoted-thread contamination, multilingual mail, phishing/spam, promotional mail from banks/insurers, forwarded messages, and mixed-purpose threads.
12. Provide a staged migration plan that lets us test quality on a stratified shadow sample before any mass relabeling.

## Desired answer format

Please return:

- a recommended architecture diagram in text;
- pipeline stages and state/checkpoints;
- what runs locally vs externally;
- exact confidence/review policy;
- expected LLM-call reduction for the 10,777-message bootstrap;
- model/backend options ranked by cost, quality and operational complexity;
- risks and counterarguments;
- a concrete first implementation slice that can be tested without touching Gmail labels.

Critique the current design rather than merely agreeing with it. If a simpler architecture is safer, say so.
