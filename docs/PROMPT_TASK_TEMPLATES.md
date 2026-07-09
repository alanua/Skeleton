# Prompt Task Templates

`PromptTaskTemplate` is a provider-neutral contract for describing bounded work before any agent or runtime is allowed to act. It turns useful prompt-writing patterns into typed data that can be validated, reviewed, approved, and later compiled for a specific execution environment.

This first slice is contract-only. It adds documentation, a JSON Schema, three synthetic fixtures, and tests. It does not implement a Task Compiler, dispatch an agent, call a model, mutate a runtime, deploy a service, or authorize any external action.

## Reusable principles

The contract preserves six practical principles:

1. **Outcome-first objective.** State the result to produce, not a long imitation of an implementation transcript.
2. **Exact evidence.** Reference logs, issues, files, fixtures, commits, or schemas by typed metadata rather than paraphrasing them or embedding sensitive content.
3. **Reference artifacts.** Name the existing pattern or artifact the work must match.
4. **Measurable completion.** Express acceptance criteria with stable identifiers and explicit expected results.
5. **Self-verification.** Require validation items with expected outcomes and record their status in an evidence receipt.
6. **Explicit output contract.** Declare the audience, format, required sections, and artifact paths before work begins.

These principles improve task quality, but prompt text never overrides repository policy, sandboxing, file allowlists, ActionGate, approval requirements, privacy boundaries, or rollback rules.

## Relationship to current Runner tasks

Current Runner issues already carry many of the same controls in prose or YAML-like task blocks: objective, base SHA, branch, risk, privacy boundary, allowlisted files, forbidden actions, validation, and expected output.

`PromptTaskTemplate` does not replace that execution contract. It normalizes the task intent before dispatch and adds:

- typed reference metadata;
- stable acceptance and validation IDs;
- bounded list and string sizes;
- explicit approval and rollback objects;
- a structured expected-output contract;
- a structured evidence receipt;
- cross-field gates for yellow/red risk;
- fail-closed rejection of unknown properties.

A future Task Compiler may translate a validated template into the exact Runner task format required by a selected provider or runtime. The canonical template itself contains no executable command field, unrestricted tool authority, provider-specific configuration path, or vendor-specific invocation syntax.

## Lifecycle

The intended lifecycle is:

1. **Draft.** A human or bounded planning component creates a template.
2. **Validated.** The Draft 2020-12 schema and repository tests accept it.
3. **Approved when required.** Yellow and red work must carry a non-`none` approval mode; red work must also declare an actionable rollback mode.
4. **Dispatched later.** A separate bounded compiler/runtime may convert an approved template into a concrete task.
5. **Receipted.** Exact base/head identifiers, changed files, validation results, blockers, and runtime-mutation status are recorded.

Validation is not execution. Approval is not execution. A template never grants merge, deployment, shell, network, secret, or service-mutation authority by itself.

## Contract overview

Required top-level fields:

- `template_version`
- `template_id`
- `task_kind`
- `objective`
- `context`
- `reference_artifacts`
- `allowed_files`
- `forbidden`
- `acceptance_criteria`
- `required_validation`
- `expected_output`
- `risk`
- `privacy_boundary`
- `approval_requirement`
- `rollback_requirement`
- `evidence_receipt`

All top-level and nested objects reject unknown properties. Strings and lists are bounded. File paths are repository-relative and reject absolute paths, parent traversal, and duplicate separators.

### Risk gates

- `green` may use approval mode `none`.
- `yellow` and `red` require `operator` or `two_person` approval.
- `red` requires `revert_commit`, `restore_backup`, or `documented_manual` rollback.

These are schema gates, not substitutes for ActionGate or repository governance.

### Evidence receipt

The receipt records:

- exact 40-character base and head identifiers;
- the changed-file list;
- per-validation status and evidence;
- unresolved blockers;
- whether any runtime mutation occurred.

The included fixtures keep `runtime_mutation_status` at `none`.

## Synthetic templates

The initial library contains only synthetic examples:

- `bug_investigation.json`: read-only diagnosis and reproduction artifact; source mutation is forbidden.
- `bounded_implementation.json`: exact file allowlist, measurable acceptance criteria, focused/full validation, approval, and rollback.
- `review_verification.json`: read-only findings with evidence, confidence, unresolved blockers, and no merge/deployment authority.

No fixture contains credentials, private paths, private data, external endpoints, or provider-specific execution commands.

## Future Council relationship

A future deliberation Council may propose a `PromptTaskTemplate` as a typed recommendation. It cannot execute that template directly. The proposal must still pass schema validation, risk and privacy checks, ActionGate, required operator approval, and the separate bounded dispatch path.
