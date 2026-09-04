# Translation v2 — GOLD_LABELING_PLAN

Status: CANON resourcing/evaluation checkpoint. Runtime authority: none.

## Purpose

Prevent circular recall evaluation and prevent classes with too little evidence from being silently treated as passed. This plan governs construction of the human-adjudicated entity-recall gold set and later domain calibration sets.

## Adjudication owner and blindness

- Primary human adjudicator: Oleksii (operator), unless another human reviewer is explicitly designated later.
- Models/extractors may prepare document queues, hashes, provenance and neutral viewing packets, but they do not create gold labels.
- **Blind pass is mandatory for recall labeling:** the adjudicator sees the raw OCR/document text and class definition, but not extractor candidates, misses, confidence, regex spans, or model suggestions.
- Only after the blind labels are frozen may a reconciliation pass reveal extractor output and compare detected vs expected entities.
- Ambiguous legal/medical cases that require domain expertise remain `ADJUDICATION_UNRESOLVED` until a competent second human reviewer is designated; they do not count as gold in the meantime. **Current status: `SECOND_REVIEWER_UNAVAILABLE`** — no qualified second reviewer has been designated yet, so unresolved sensitive cases are excluded from gold rather than default-adjudicated.

## Initial entity-recall dataset gate

Each critical class starts with a target of at least 12 positive human-adjudicated examples drawn from real archive material, spanning real format variation. Negative regression fixtures grow from actual archive misses/corruption patterns and never satisfy the positive quota.

Classes: amount, date, case/reference number, IBAN/account identifier, medical code, name, address.

The current extractor-observation snapshot over 117 production documents is **not** a corpus truth estimate. It reports only what the extractor detected. Therefore `medical_code=2` and `name=0` do not prove the archive contains only two medical codes or zero names; blind annotation is required to determine true class availability.

## Class availability states

Every class has an explicit availability/readiness state:

- `UNASSESSED` — blind corpus annotation has not established availability.
- `ENOUGH_ARCHIVE_NATIVE_DATA` — at least the required number of positive archive-native gold examples exists.
- `INSUFFICIENT_ARCHIVE_NATIVE_DATA` — blind annotation shows the archive cannot supply the required count.
- `SUPPLEMENTAL_SOURCE_REQUIRED` — archive-native data is insufficient and an additional source is required.
- `GATE_INACTIVE_INSUFFICIENT_DATA` — no production recall claim may be made for the class; locking for that class cannot be production-trusted.

No class may disappear from reports because it lacks data. Insufficient data is an explicit fail-closed state, not a skip/pass.

## Supplemental examples

If archive-native material is genuinely insufficient, supplemental real documents may later be admitted only with explicit provenance and `source_scope=SUPPLEMENTAL`. They must remain distinguishable from `ARCHIVE_NATIVE` examples in every report. Synthetic examples may be useful for unit/regression testing but never satisfy the gold recall quota unless a later CANON amendment explicitly permits a narrowly defined use.

## Anti-anchoring protocol

1. Build a private randomized document packet from **all currently available source documents**, without extractor overlays and without filtering/ranking by extractor detections. Random order is generated from an independent cryptographic seed and source hashes, not filesystem order, document date, prior extractor order, or candidate density.
2. Human annotates all occurrences of the target class in the raw OCR/document text.
3. Freeze labels with source hash, spans/text, class, reviewer and timestamp.
4. Reveal extractor output only in a separate reconciliation step.
5. Record false negatives and false positives separately.
6. Promote newly observed real miss formats into negative regression fixtures after adjudication.
7. Prior human exposure to some documents cannot be erased; this is recorded as residual anchoring risk. Randomization and hiding extractor output reduce sequence/candidate anchoring but do not claim to eliminate memory effects.

## Immediate risk status

Current extractor observations suggest possible data scarcity for `medical_code` and `name`, but this is only a risk signal, not proof of corpus insufficiency. Their current availability state remains `UNASSESSED` until blind annotation. If blind annotation cannot produce the minimum positive count, those classes move to `INSUFFICIENT_ARCHIVE_NATIVE_DATA` and then either `SUPPLEMENTAL_SOURCE_REQUIRED` or `GATE_INACTIVE_INSUFFICIENT_DATA`; they never silently pass.

## Packet completeness rule

A blind packet is not called corpus-complete unless every eligible production metadata record has an available raw source-text artifact. Missing source text is reported explicitly and excluded from recall claims until recovered or adjudicated as unavailable. Packet construction never substitutes extractor output for missing source text.
