# Document translation pipeline v2 — CANON

Status: CANON architecture. Approved by Oleksii on 2026-09-04.
Review provenance: issue #3719 and `docs/reviews/TRANSLATION_PIPELINE_V2_REVIEW.md`.

## Canonical decision

Skeleton will replace the v1.5 document-translation trust logic as a whole with v2. Dual-MT agreement is only an escalation/anomaly signal and never evidence sufficient for trust. Back-translation is auxiliary and can never independently grant `TRUSTED_FOR_DISPLAY`. Trust is decided at whole-document level: one semantic error or protected-entity error fails the document. Legal, medical, financial and government documents fail closed until domain-specific calibration proves the required document-level precision.

## Canonical implementation order

1. `skeleton.translation.ocr_gate`
2. `skeleton.translation.entity_ledger` and entity locking
3. `skeleton.translation.gateway.v2` and explicit state machine, initially shadow-only
4. `skeleton.translation.verifier.terminology` plus deterministic entity audit
5. `skeleton.translation.verifier.llm_judge` with whole-document context
6. `skeleton.translation.verifier.backtranslation` as auxiliary evidence only
7. `skeleton.translation.calibration` with versioned labeled eval sets and whole-document acceptance
8. `skeleton.translation.migration_queue` for the blocked historical corpus after acceptance gates pass

## Runtime boundary

The current v1.5 publication gate remains active only as temporary fail-closed protection. It is not a development target and its threshold/consensus logic must not be tuned toward v2. V2 components are introduced in shadow/test mode first. Production authority, re-enabling blocked translations, and migration require successful domain-specific document-level evaluation and a separate explicit operator approval.
