# Translation pipeline v2 — implementation breakdown (REVIEW only)

Status: REVIEW. Not CANON. Do not merge or activate runtime behavior from this plan without explicit operator approval.

Parent review: #3719.

## Work packages

1. `skeleton.translation.ocr_gate`
   - classify OCR as CLEAN / UNCERTAIN / CORRUPT;
   - preserve OCR confidence separately from translation confidence;
   - tests: corrupt-glyph ratio, low-confidence tokens, layout sanity, terminal OCR_CORRUPT state.

2. `skeleton.translation.entity_ledger`
   - extract names, dates, amounts, case/reference numbers, IBAN/account identifiers, addresses;
   - placeholder lock/reinsert exact source strings;
   - deterministic no-new-entity and exact-round-trip audit;
   - tests: unicode names, German dates/currency, case numbers, overlapping entities, missed-entity safety net.

3. `skeleton.translation.gateway.v2`
   - paragraph candidate generation with whole-document context;
   - dual-MT divergence only as escalation signal;
   - explicit state machine: OCR_PENDING -> OCR_CORRUPT/OCR_OK -> TRANSLATION_DRAFT -> REVIEW_REQUIRED/TRUSTED_FOR_DISPLAY;
   - sensitive domains fail closed until domain calibration passes;
   - tests: all state transitions and hard-fail precedence.

4. `skeleton.translation.verifier.backtranslation`
   - auxiliary semantic-drift signal only;
   - can never independently grant TRUSTED_FOR_DISPLAY;
   - tests: shared-error round-trip regression and gross-drift detection.

5. `skeleton.translation.verifier.llm_judge`
   - strong-model verifier with full-document context;
   - categorical verdict with cited source/translation evidence spans;
   - bounded retries/cost; failure => REVIEW_REQUIRED;
   - tests: malformed judge output, missing evidence, timeout, contradictory evidence.

6. `skeleton.translation.verifier.terminology`
   - domain glossary + NER consistency checks;
   - deterministic suspicious-substitution hard fails where configured;
   - regression tests for known bad examples including abbreviation/body-part confusion.

7. `skeleton.translation.calibration`
   - versioned labeled evaluation set and calibration artifact;
   - acceptance measured at whole-document level: one semantic/entity error fails the document;
   - report measured zero-semantic-error document precision by domain;
   - no sensitive domain auto-trust until its held-out acceptance target is met.

8. `skeleton.translation.migration_queue`
   - separate idempotent queue for 94 blocked legacy translations;
   - OCR/entity pass first, sensitive domains direct to strong tier;
   - bounded batches and cost budget; migration never blocks new-document flow.

## Cross-cutting tests

- golden document fixtures for legal, medical, financial, government, general;
- immutable OCR-source hash/provenance tests;
- exact entity preservation property tests;
- hard-fail aggregation tests;
- document-level acceptance tests where 1 bad segment out of N fails the whole document;
- fail-closed tests for unavailable verifier, exhausted budget, model/prompt version drift;
- regression fixtures for the production failures that triggered #3719.

## Runtime safety during REVIEW

The current v1.5 emergency publication gate stays active unchanged. No v2 component becomes authoritative and no blocked translation is re-enabled until the v2 acceptance gate has measured domain-specific precision and Oleksii explicitly approves CANON/runtime promotion.
