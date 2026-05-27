# Operator Rules Registry

`OPERATOR_RULES.yaml` is a stage 1 machine-readable registry for operator-facing rules.

It is a reference/index over existing Skeleton sources. It is not a second canon store, not a runtime enforcement layer, and not a replacement for the existing action gate.

## Source relationship

The registry points back to these existing sources:

- `COMMANDS.yaml` for command meanings and response behavior.
- `MEMORY_ROUTING.yaml` for canon, review, private, temporary, and rejected routing.
- `projects/skeleton/PROJECT_OPERATING_STANDARD.md` for Skeleton operating rules.
- `CAPABILITY_REGISTRY.yaml` for implemented and planned capabilities.
- `core/action_gate.py` for bounded action-gate behavior that already exists.

## Stage 1 boundary

This stage only adds a static registry.

It does not add `core/operator_rule_gate.py`, runtime hooks, Telegram callback changes, merge behavior changes, or deployment behavior changes.

Future enforcement must be proposed in a separate task with explicit approval, tests, and a small scope.

## Severity meanings

- `block`: unsafe without an approved route.
- `rewrite`: response should be rewritten before being sent.
- `warn`: allowed, but requires care or review.
- `log`: informational rule for tracking and audit.

## Practical use

Adapters and operators can read `OPERATOR_RULES.yaml` during boot/context loading to keep high-priority behavior rules visible in one place. The original sources remain authoritative when details conflict.
