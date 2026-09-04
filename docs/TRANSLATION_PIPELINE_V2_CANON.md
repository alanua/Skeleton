# Document translation pipeline v2 — CANON

Status: CANON architecture. Approved by Oleksii on 2026-09-04.
Review provenance: issue #3719 and `docs/reviews/TRANSLATION_PIPELINE_V2_REVIEW.md`.

## Canonical decision

Skeleton will replace the v1.5 document-translation trust logic as a whole with v2. Dual-MT agreement is only an escalation/anomaly signal and never evidence sufficient for trust. Back-translation is auxiliary and can never independently grant `TRUSTED_FOR_DISPLAY`. Trust is decided at whole-document level: one semantic error or protected-entity error fails the document. Legal, medical, financial and government documents fail closed until domain-specific calibration proves the required document-level precision.

## Canonical implementation order

1. `skeleton.translation.ocr_gate`
2. `skeleton.translation.entity_ledger` extraction, then mandatory `ENTITY_RECALL_GATE` measured separately for amounts, dates, case/reference numbers, IBAN/account identifiers, medical codes, names and addresses; only after that gate passes may entity locking be considered production-ready
3. `skeleton.translation.entity_ledger` entity locking and exact reinsertion
4. `skeleton.translation.gateway.v2` and explicit state machine, initially shadow-only
5. `skeleton.translation.verifier.terminology` plus deterministic entity audit
6. `skeleton.translation.verifier.llm_judge` with whole-document context
7. `skeleton.translation.verifier.backtranslation` as auxiliary evidence only
8. `GOLD_LABELING_PLAN` resourcing checkpoint per domain before production holdout construction
9. `skeleton.translation.calibration`: dev/engineering set first, then independent production holdout per domain; acceptance uses whole-document outcomes and a confidence bound on error rate, not a pooled point estimate
10. `skeleton.translation.migration_queue` for the blocked historical corpus after the relevant domain acceptance gates pass

## Statistical acceptance amendment

The engineering/dev set is for debugging only and cannot justify production trust. Legal, medical, financial and government domains each require their own independent human-adjudicated production holdout. As a planning target, approximately 299–300 independent zero-error examples per domain are needed to bound the one-sided 95% upper error rate to about 1%; the exact acceptance calculation must be reported from the actual sample size and observed errors. A model-generated label is never gold until a human reviewer adjudicates it. Production acceptance is whole-document: one semantic or protected-entity error fails that document.

## Entity extraction acceptance amendment

Entity locking is not trusted merely because exact reinsertion works for detected entities. Extraction recall is a separate first-class safety property. Missed critical entities are tracked as their own failure mode, and recall must be measured per critical entity class before locking can be promoted beyond shadow/test use.

## Gold labeling and insufficient-data amendment

Entity-recall gold labeling must include a blind human pass over raw OCR/document text before extractor candidates are revealed. The primary human adjudicator is the operator unless another human reviewer is explicitly designated; model/extractor suggestions never constitute gold. Ambiguous legal/medical cases requiring domain expertise remain unresolved and do not count until a competent human reviewer adjudicates them.

A class with too little evidence is never silently skipped or passed. It must carry an explicit insufficient-data state, and production trust for locking that class remains inactive. Current extractor observations (`medical_code=2`, `name=0` over 117 documents) are only detector-output scarcity signals and are not evidence that the archive truly contains only those counts; blind annotation determines archive-native availability. If archive-native material is insufficient, supplemental real examples require explicit provenance and must remain distinguishable from archive-native examples.

## Runtime boundary

The current v1.5 publication gate remains active only as temporary fail-closed protection. It is not a development target and its threshold/consensus logic must not be tuned toward v2. V2 components are introduced in shadow/test mode first. Production authority, re-enabling blocked translations, and migration require successful domain-specific document-level evaluation and a separate explicit operator approval.
