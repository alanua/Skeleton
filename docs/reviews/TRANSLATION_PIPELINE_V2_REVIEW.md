# Review: production-grade document translation quality pipeline (issue #3719)

## TL;DR

The core flaw in v1.4/v1.5 is not the threshold — it's what's being measured. **Agreement between two weak local MT models is not evidence of correctness.** Two architecturally similar undertrained NMT systems can share the same failure modes (mistranslating an abbreviation, confusing a homograph) and "agree" while both are wrong — this is almost certainly what produced `рак руки` and the corrupted legal letter. Raising the score threshold, or trusting dual-MT consensus more, cannot fix a signal that isn't measuring semantic correctness in the first place.

The fix is architectural, not numerical: (1) prevent entity corruption structurally instead of hoping MT preserves it, (2) stop treating local-model agreement as a trust decision and repurpose it as a cheap escalation trigger, (3) verify with signals independent of the generator, (4) calibrate "trusted" against real labeled data instead of a handcrafted score, (5) fail closed by document domain until the pipeline has proven precision on that domain.

---

## Recommended target architecture

### Stage 1 — OCR corruption gate (before any translation)
- Per-token OCR confidence from the OCR engine.
- Structural sanity checks: garbled-character ratio, source-language dictionary hit rate, layout consistency.
- Classification: `OCR_CLEAN` / `OCR_UNCERTAIN` / `OCR_CORRUPT`.
- `OCR_CORRUPT` blocks translation entirely and routes to rescan/manual review — never let bad OCR feed a plausible-looking but wrong translation.
- This separates OCR uncertainty from translation uncertainty explicitly, as two distinct fields in the quality record, not one blended score.

### Stage 2 — Document understanding & entity ledger
- Classify document type: legal / medical / financial / government / personal correspondence / other.
- Whole-document parse; segment into paragraph-sized units for translation and audit, but keep full-document context available to every unit (context window carries the whole doc, not an isolated sentence).
- Extract critical entities up front (NER + regex): names, dates, monetary amounts, case/reference numbers, IBAN/account numbers, addresses. Build a structured **entity ledger** with source spans.

### Stage 3 — Entity locking
- Replace each ledger entity with a placeholder token before translation; translate around it; re-insert the exact original string afterward.
- This is the single highest-leverage change. It structurally guarantees preservation of names/IDs/dates/amounts instead of relying on MT+agreement to "happen to" get it right — which is what v1.5 was really trying (and failing) to do.

### Stage 4 — Candidate generation (local-first)
- Primary candidate from the best local model, with document-level context injected.
- Keep the dual local models (NLLB + OPUS), but **repurpose their role**: they no longer decide trust. Divergence between the two candidates is used only as a cheap **anomaly/escalation trigger** — agreement is not proof of correctness, only a pre-filter for what needs stronger review.

### Stage 5 — Escalation tier (strong model)
Escalate to a stronger external model when any of:
- document type is legal / medical / financial / government,
- entity-lock audit finds a conflict,
- local candidates diverge significantly,
- OCR confidence for the segment is `OCR_UNCERTAIN`.

Cost control: escalate only flagged segments/paragraphs, not the whole archive; cap per-document strong-model spend with a hard budget breaker that routes to `review_required` on exhaustion rather than retrying indefinitely.

### Stage 6 — Verification (independent of the generator)
Combine multiple independent signals into a **categorical decision**, not a weighted float:
- **Entity audit** (deterministic): every locked entity round-trips exactly; no new numbers/dates/names appear that weren't in the source.
- **Back-translation entailment**: translate UK→source again, check semantic entailment against the original via LLM judge or embedding similarity — catches gross meaning drift.
- **LLM judge with full document context**: strong model asked "does this preserve the legal/medical meaning of the source, given the whole document," returning a categorical safe/unsafe verdict plus cited evidence spans — not a bare number.
- **Terminology/NER consistency**: domain glossary check to catch suspicious substitutions (e.g. a body-part word appearing where a legal abbreviation should).

**Any single hard-fail signal forces `review_required`**, regardless of what the others say. This is the opposite of the current "high agreement score wins" logic.

**Amendment:** back-translation entailment must never be sufficient on its own to grant `trusted_for_display`. It shares the generator's own failure modes (it can round-trip a shared error back to something that looks consistent) and is treated strictly as one contributing signal, weighted no higher than the LLM judge or entity audit.

### Stage 7 — Calibration
- Build a labeled evaluation set: sample ~150–300 segments stratified across legal/medical/financial/general, human-label actual semantic correctness.
- Calibrate the trusted/review decision against this set (isotonic regression / Platt scaling) so "trusted" corresponds to a *measured* precision (e.g. ≥99% no-semantic-error on held-out data), not an arbitrary 0.92 cutoff.
- Version the calibration artifact; re-calibrate whenever models or prompts change.

**Amendment — document-level, not just segment-level:** the calibrated gate and acceptance metric must be evaluated at the whole-document level, not only per-segment. A single corrupted amount, date, case number, or medical term makes the entire document unsafe to display, even if every other paragraph passed. Concretely: `TRUSTED_FOR_DISPLAY` requires *all* segments in the document to pass, and the eval set's precision target is measured as "fraction of documents with zero semantic errors," not "fraction of segments correct." A document with one bad segment out of 100 is a failed document, not a 99%-correct one.

### Stage 8 — State machine
```
OCR_PENDING
  → OCR_CORRUPT (terminal — needs rescan)
  → OCR_OK
      → TRANSLATION_DRAFT (local candidate, entity-locked, unverified)
          → REVIEW_REQUIRED   (any hard-fail signal, OR sensitive domain
                                without proven pipeline precision on that domain)
          → TRUSTED_FOR_DISPLAY (passes calibrated gate)
REVIEW_REQUIRED
  → human action → TRUSTED_FOR_DISPLAY | REJECTED_NEEDS_RETRANSLATION
```
Legal/medical/financial documents default to `REVIEW_REQUIRED` even on a pipeline "pass" until calibration proves sufficient precision on that specific domain — fail closed by domain, not just by score.

---

## Answers to the specific questions

1. **Dual-MT consensus** — useful only as a cheap divergence/anomaly pre-filter deciding what needs escalation. Never as a trust/publish decision; agreement between weak correlated models is not evidence of correctness.
2. **Verifier design** — a combination: deterministic entity audit + back-translation entailment + strong-model document-context LLM judge + terminology/NER consistency. No single verifier is sufficient alone.
3. **Confidence calibration** — build a human-labeled eval set, calibrate against it (isotonic/Platt), report measured precision at the chosen operating point instead of a handcrafted score.
4. **Granularity** — paragraph-sized units for locality and audit, but with the full document injected as context into every unit's translation and verification prompt.
5. **Entity preservation** — entity locking (placeholder substitution + exact re-insertion), backed by a deterministic round-trip audit — structural prevention, not hopeful detection.
6. **Local vs strong model** — local models (NLLB/OPUS-class) are fine for candidate generation and as a divergence detector on Home Edge hardware; escalate to a stronger external model for anything sensitive-domain, entity-flagged, or divergent. Accept added latency/cost only for that subset.
7. **Evaluation set & acceptance gates** — stratified, human-labeled sample across all document types including the three sensitive domains; require a measured precision threshold (e.g. ≥99% on the "trusted" bucket) before re-enabling the MFP UI gate.
8. **What to keep vs replace from v1.5** — Keep: immutable OCR source, fail-closed default, "publish only if DONE" mechanism, the general instinct that agreement matters. Replace: `quality_score` as sole heuristic gate → categorical hard-fail verification + calibrated decision; dual-MT-agreement-as-trust-signal → escalation trigger only; sentence/fragment-isolated translation → document-context translation; no entity locking → add it as the primary preservation mechanism.

---

## Migration plan for the 94 blocked legacy translations

1. Run the cheap corruption/OCR-check + entity extraction pass on all 94 first — build the entity ledger and confirm document domain for each, without retranslating yet.
2. Route all 94 through entity-locked translation + local candidate + divergence check (cheap tier).
3. Auto-escalate flagged documents to strong-tier verification/translation — given the prior 0/94 pass rate, expect most to need this. Process in capped batches (e.g. 10–15/day), not all at once.
4. Legal/medical/financial documents go straight to the strong tier given the known failure rate — don't waste cycles on local-only attempts for these.
5. Track migration in its own queue, separate from new-document flow, so a stalled migration never blocks new incoming documents.

---

## Failure modes / risks

- **Escalation cost runaway** if too large a fraction of the archive routes to the strong tier → per-document/day budget caps, monitored spend.
- **LLM judge itself wrong or hallucinating** → require cited evidence spans from the judge; periodically human-audit the judge's own output, not just the translations.
- **Entity extraction misses an entity** → locking can't protect what isn't detected → keep a regex/NER safety net plus a post-hoc numeric/date count-consistency check between source and translation.
- **Undetected OCR corruption** → garbage in, garbage out regardless of translation quality → this is why Stage 1 exists as a hard gate, not an afterthought.
- **Calibration set too small/unrepresentative** → precision estimate unreliable → keep the eval set alive and growing, re-calibrate periodically, especially after any model/prompt change.

---

## Concrete Skeleton modules to add/change

| Module | Status | Purpose |
|---|---|---|
| `skeleton.translation.ocr_gate` | new | OCR corruption/uncertainty classification, gates pipeline entry |
| `skeleton.translation.entity_ledger` | new | entity extraction, locking/placeholder substitution, re-insertion, numeric round-trip audit |
| `skeleton.translation.gateway.v2` | replaces v1.5 | orchestrates candidates, divergence detection, escalation routing, verification aggregation, state machine |
| `skeleton.translation.verifier.backtranslation` | new | back-translation entailment check |
| `skeleton.translation.verifier.llm_judge` | new | strong-model document-context judge; categorical verdict + cited spans |
| `skeleton.translation.verifier.terminology` | new | domain glossary / NER consistency checker |
| `skeleton.translation.calibration` | new | eval-set management, isotonic/Platt fit, versioned calibration artifact, precision reporting |
| `skeleton.translation.migration_queue` | new | separate queue/state for reprocessing the 94 legacy documents |

Tests: golden-file tests per verifier; entity round-trip tests; state-machine transition tests; regression tests pinned to the frozen labeled eval set.

Docs: canonical architecture doc (replacing the inline logic in `gateway.py`), state-machine diagram, provenance schema (model/provider/version, prompt/policy version, OCR source hash, quality evidence, issues, timestamps).

---

## Minimal safe implementation sequence

1. OCR corruption/uncertainty gate — cheap, stops garbage at the door.
2. Entity ledger + locking — removes most "wrong number/name" risk immediately, works even with the existing MT models.
3. Redefine dual-MT's role to divergence-detector-only; wire escalation to a strong external model for sensitive domains and divergent segments. This alone should fix the `рак руки`-class failures.
4. Add deterministic entity round-trip + terminology consistency checks.
5. Add the LLM-judge verifier with full document context and required evidence citation.
6. Build an initial labeled eval set (start at ~50–100 segments across domains) and calibrate the trusted-for-display gate against it, retiring the arbitrary 0.92 threshold.
7. Run the migration queue for the 94 blocked legacy documents — sensitive-domain ones go straight to the strong tier.
8. Re-enable the MFP UI display gate based on calibrated, measured precision — not on score alone.

---

**Status note:** the v1.5 emergency gate (0.92 threshold + dual-MT consensus) stays active as a protective measure — it is correctly refusing to publish bad translations right now — but is not to be extended or tuned further. It is fully retired once v2's calibrated document-level gate is in place, not incrementally patched toward it.

*Prepared in response to Skeleton issue #3719. Recommends replacing v1.5's dual-MT-agreement-as-trust-signal with entity locking + independent verification + calibrated, document-level decision gates; keeps fail-closed publising and immutable OCR source from the current design. Amended per review discussion: back-translation is a contributing signal only, and acceptance is measured at the document level, not the segment level.*
