# Translation pipeline v2 — canonical implementation sequence

Status: CANON architecture and implementation order, explicitly approved by Oleksii on 2026-09-04. Runtime activation remains separately gated by evaluation and operator approval.

Parent review: #3719.

## Work packages

1. `skeleton.translation.ocr_gate`
   - classify OCR as CLEAN / UNCERTAIN / CORRUPT;
   - preserve OCR confidence separately from translation confidence;
   - tests: corrupt-glyph ratio, low-confidence tokens, layout sanity, terminal OCR_CORRUPT state.

2. `skeleton.translation.entity_ledger` extraction + `ENTITY_RECALL_GATE`
   - extract names, dates, amounts, case/reference numbers, IBAN/account identifiers, medical codes, addresses;
   - measure recall separately per critical class before locking is trusted;
   - missed entity is its own failure mode;
   - tests include nonstandard German case numbers, OCR-damaged medical codes, unicode names, dates/currency, overlapping entities and class-level recall fixtures.

3. `skeleton.translation.entity_ledger` locking
   - placeholder lock/reinsert exact source strings only after extraction recall gate is met for the intended scope;
   - deterministic no-new-entity and exact-round-trip audit.

4. `skeleton.translation.gateway.v2`
   - paragraph candidate generation with whole-document context;
   - dual-MT divergence only as escalation signal;
   - explicit state machine: OCR_PENDING -> OCR_CORRUPT/OCR_OK -> TRANSLATION_DRAFT -> REVIEW_REQUIRED/TRUSTED_FOR_DISPLAY;
   - sensitive domains fail closed until domain calibration passes;
   - shadow-only initially; tests: all state transitions and hard-fail precedence.

5. `skeleton.translation.verifier.terminology` + deterministic entity audit
   - domain glossary + NER consistency checks;
   - deterministic suspicious-substitution hard fails where configured;
   - regression tests for known bad examples including abbreviation/body-part confusion.

6. `skeleton.translation.verifier.llm_judge`
   - strong-model verifier with full-document context;
   - categorical verdict with cited source/translation evidence spans;
   - bounded retries/cost; failure => REVIEW_REQUIRED;
   - tests: malformed judge output, missing evidence, timeout, contradictory evidence.

7. `skeleton.translation.verifier.backtranslation`
   - auxiliary semantic-drift signal only;
   - can never independently grant TRUSTED_FOR_DISPLAY;
   - tests: shared-error round-trip regression and gross-drift detection.

8. `GOLD_LABELING_PLAN`
   - explicit per-domain owner/reviewer, target document count, estimated labeling time, adjudication rules and disagreement resolution;
   - model-assisted pre-labeling is allowed only as draft assistance; unreviewed model labels never enter the gold set;
   - checkpoint must be complete before production holdout work starts.

9. `skeleton.translation.calibration`
   - dev/engineering set (~50–100 documents/segments) is for debugging only;
   - separate independent production holdout for legal, medical, financial and government;
   - planning target ~299–300 zero-error examples per domain for an approximately 1% one-sided 95% upper error bound; exact bound reported from actual n/errors;
   - acceptance measured at whole-document level: one semantic/entity error fails the document;
   - gate checks confidence bound, not only point estimate or calibrated score;
   - no sensitive domain auto-trust until its held-out acceptance target is met.

10. `skeleton.translation.migration_queue`
   - separate idempotent queue for 94 blocked legacy translations;
   - OCR/entity pass first, sensitive domains direct to strong tier;
   - bounded batches and cost budget; migration never blocks new-document flow;
   - relevant domain acceptance gate must pass before legacy documents in that domain can become trusted-for-display.

## Cross-cutting tests

- golden document fixtures for legal, medical, financial, government, general;
- immutable OCR-source hash/provenance tests;
- exact entity preservation property tests;
- hard-fail aggregation tests;
- document-level acceptance tests where 1 bad segment out of N fails the whole document;
- fail-closed tests for unavailable verifier, exhausted budget, model/prompt version drift;
- regression fixtures for the production failures that triggered #3719.

## Runtime safety during implementation

The current v1.5 emergency publication gate stays active unchanged. No v2 component becomes authoritative and no blocked translation is re-enabled until the v2 acceptance gate has measured domain-specific precision and Oleksii explicitly approves production runtime promotion.
