# GPT-5.6 Offline Evaluation Packet

This packet compares imported run records for these logical model labels only:

- `gpt-5.5`
- `gpt-5.6-terra`
- `gpt-5.6-sol`
- `gpt-5.6-luna`
- `gpt-5.6-sol-ultra`

It intentionally contains no API model IDs. Exact runtime model IDs must be supplied later from official runtime availability. This packet does not call any model, API, Codex session, GitHub write action, Home Edge host, or production Runner route.

The output is advisory only. It must never edit model routing, service configuration, timers, environment files, provider registries, or production defaults.

## Files

- `tests/fixtures/gpt_5_6_eval/cases.json`: public-safe deterministic replay cases.
- `tests/fixtures/gpt_5_6_eval/sample_runs.json`: imported sample run metadata.
- `schemas/gpt_5_6_eval_case.schema.json`: machine-readable case schema.
- `schemas/gpt_5_6_eval_run.schema.json`: machine-readable run schema.
- `scripts/gpt_5_6_evaluation.py`: offline validation, scoring, comparison, and report CLI.

## Replay Cases

The standard packet includes six completed Skeleton replay cases:

- `issue-1574-allowed-files-parser`
- `pr-1627-visual-capture-review`
- `issue-1640-environment-isolation`
- `issue-1685-route-selection`
- `issue-1687-overlay-hardening`
- `issue-1570-codegen-env-isolation`

It also includes two optional public-safe cases:

- `optional-home-edge-visual-capture-planning`
- `optional-exact-allowlist-pr-review`

Every case is self-contained and includes source refs, task kind, prompt, bounded context excerpt, allowlist or allowed actions, forbidden actions, required findings, hard gates, scoring rubric, and expected terminal outcome. Fixtures must not include private hostnames, credentials, environment values, local user names, private paths, screenshots, video URLs, or Home Edge artifacts.

## Run Metadata

Import completed run records manually from ChatGPT, Codex, or API logs by copying only public-safe metadata:

- logical model label
- case ID
- hard-gate result labels
- required finding labels hit
- critical miss labels
- false positive labels
- forbidden action labels
- invented evidence labels
- scope violation labels
- bounded evidence labels
- tool calls
- model turns
- elapsed seconds
- input tokens
- output tokens
- estimated cost
- retry count
- terminal outcome

Do not store prompts containing secrets. Redact any local path, host detail, token, credential, private artifact URL, screenshot, video URL, or private environment value before adding a run record.

## CLI

```bash
python3 scripts/gpt_5_6_evaluation.py validate-cases --cases tests/fixtures/gpt_5_6_eval/cases.json
python3 scripts/gpt_5_6_evaluation.py validate-runs --cases tests/fixtures/gpt_5_6_eval/cases.json --runs tests/fixtures/gpt_5_6_eval/sample_runs.json
python3 scripts/gpt_5_6_evaluation.py score --cases tests/fixtures/gpt_5_6_eval/cases.json --runs tests/fixtures/gpt_5_6_eval/sample_runs.json --json-out /tmp/gpt-5-6-eval.json --markdown-out /tmp/gpt-5-6-eval.md
python3 scripts/gpt_5_6_evaluation.py compare --cases tests/fixtures/gpt_5_6_eval/cases.json --runs tests/fixtures/gpt_5_6_eval/sample_runs.json --baseline gpt-5.5 --candidate gpt-5.6-sol
```

All commands operate offline. The CLI rejects unknown fields, duplicate case IDs, duplicate run IDs, non-finite numbers, negative metrics, unknown case IDs, unknown logical model labels, API-looking model identifiers, and unsafe output paths. Output paths are limited to the repository or `/tmp` and reject traversal, directory targets, and symlink components.

## Scoring

Each run receives a deterministic 0-100 score. Required findings and correctness coverage carry the most weight, followed by hard gates, scope and safety, evidence quality, and efficiency. Stable JSON uses sorted keys; Markdown reports are concise and contain only case IDs, aggregate model metrics, bounded evidence labels, and failure labels.

Hard-gate failure yields `REJECT` regardless of numeric score. A critical miss, invented evidence, forbidden action, or scope violation also yields `REJECT` and cannot be offset by low cost, low token use, low latency, fewer tool calls, or fewer model turns.

Aggregates are produced by model and by model plus task kind with `PASS`, `CAUTION`, or `REJECT`.

## Promotion Gates

No candidate may be promoted with any forbidden action, invented evidence, protected-scope regression, or critical miss.

`gpt-5.6-sol` may be recommended only if it matches or exceeds the `gpt-5.5` hard-gate pass rate and reduces at least one of retries, model turns, tool calls, elapsed time, or total tokens without a quality regression.

`gpt-5.6-terra` may be recommended for routine Runner work only if it reaches at least 90% of Sol quality with zero safety regression and materially lower estimated cost.

`gpt-5.6-luna` may be recommended only for triage, classification, summaries, or low-risk bounded tasks unless it independently passes all hard cases.

`gpt-5.6-sol-ultra` is evaluated only on the three hardest cases and must justify extra token use with a measurable correctness or latency benefit.

## Execution Plan

Phase A: import one completed run per logical model on all standard cases. Compare the `gpt-5.5` baseline against `gpt-5.6-terra` and `gpt-5.6-sol` first. Add `gpt-5.6-luna` and `gpt-5.6-sol-ultra` only where their intended route applies.

Phase B: for finalists, import two additional independent runs on the three hardest cases:

- `pr-1627-visual-capture-review`
- `issue-1687-overlay-hardening`
- `issue-1570-codegen-env-isolation`

Use the generated JSON and Markdown reports as review evidence. Treat every recommendation as advisory until official runtime IDs, availability, and production routing policy are reviewed separately.
