# AI-Assisted Maintenance

Skeleton is built to make AI-assisted engineering reproducible rather than chat-dependent.

## Maintainer use of AI

The project uses AI tools such as Codex and other model providers for bounded engineering tasks including:

- code and architecture investigation;
- focused implementation patches;
- regression-test generation;
- pull-request review assistance;
- issue triage and handoff synthesis;
- runtime diagnostics and recovery planning;
- documentation and migration analysis.

AI output is treated as a proposal or execution step inside the project control model, not as independent authority.

## Bounded worker model

Skeleton treats models and automation workers as scoped capabilities, not as interchangeable superusers. A worker may be allowed to inspect, review, prepare a patch, run tests, or execute one registered maintenance operation without inheriting unrelated authority.

Typical boundaries include:

- read/audit roles that cannot mutate repositories or runtimes;
- issue-workspace coding roles that operate inside a bounded checkout;
- review roles that can analyze diffs and test evidence without merge authority;
- runtime-maintenance handlers that bypass normal coding agents and expose only fixed, allowlisted operations;
- Home Edge execution paths that carry explicit node, user, lane, timeout, idempotency, approval, and verification requirements.

Provider choice does not widen the role. Codex, another hosted model, or a local model receives only the authority declared for that route.

## Authority ladder

Skeleton resolves conflicts in this order:

    human decision / repository policy
            >
    canonical private runtime state
            >
    derived indexes, projections, and summaries
            >
    model inference

A model may identify a likely inconsistency, but it does not silently rewrite higher-authority state. Derived memory and graph/index layers are aids to retrieval, not independent sources of operational truth.

## Human control boundary

The primary maintainer owns final decisions about merge, deployment, credentials, network changes, firmware, destructive actions, and physical-device operations. Security-sensitive actions use explicit approval and registered execution paths.

## Why the project benefits from Codex/API automation

Skeleton has a large maintenance surface spanning Python services, Android, CI/runtime infrastructure, device-control adapters, registries, and long-running issue/PR chains. The recurring cost is not only writing code; it is preserving context, validating exact state, reviewing diffs, tracing failures, and keeping recovery paths current.

API-backed maintainer automation can reduce that load by helping with repetitive triage, exact-context task packaging, test/review loops, release-readiness checks, and safe handoff generation while the human maintainer remains in control.

## Verification expectations

For code, use relevant tests and diff review. For external runtimes or devices, distinguish command delivery from the observed postcondition. A successful tool call is not sufficient evidence that a requested real-world state was achieved.

## Data and secret handling

Do not place credentials, private keys, tokens, private user data, or sensitive device topology in public prompts, issues, logs, or repository files. Public artifacts should contain only safe metadata or redacted references.
