# Quickstart: Safe Local Proof

This quickstart demonstrates three Skeleton properties without credentials, cloud access, private state, or external side effects:

1. canonical boot from a declared manifest;
2. approval-gate validation bound to exact metadata;
3. public-safe audit-event validation.

## Requirements

- Python 3.11+
- Git

## Clone and install minimal dependencies

    git clone https://github.com/alanua/Skeleton.git
    cd Skeleton

    python -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install PyYAML pytest

On Windows PowerShell, activate the environment with:

    .venv\\Scripts\\Activate.ps1

## 1. Read-only canonical boot

    python -m core.boot_loader

The JSON report is built from BOOT_MANIFEST.yaml. It reports the loaded sources and explicitly returns mode=boot and writes=none.

The command exits non-zero if the declared entrypoint is not among the loaded sources.

## 2. Exercise the approval gate

Run:

    python - <<'PY'
    from core.action_gate import ActionGateRequest, validate_action_request

    decision = validate_action_request(
        ActionGateRequest(
            action_type="merge_pull_request",
            repo="alanua/Skeleton",
            pr_number=1,
            expected_head_sha="0" * 40,
            expected_files=("README.md",),
            user_approved=False,
        )
    )
    print(decision)
    PY

The request is blocked because user_approved is false. The gate does not perform the merge; it only validates whether a future protected action request satisfies the declared contract.

## 3. Run focused contract tests

    python -m pytest -q \
      tests/test_boot_loader.py \
      tests/test_action_gate.py \
      tests/test_audit_ledger.py

These tests exercise declared boot behavior, approval gating, and rejection of unsafe public audit content.

## Next reading

- [Project Impact](PROJECT_IMPACT.md)
- [Threat Model](THREAT_MODEL.md)
- [Action Gate](ACTION_GATE.md)
- [Audit Ledger](AUDIT_LEDGER.md)
- [AI-Assisted Maintenance](AI_ASSISTED_MAINTENANCE.md)

This quickstart intentionally stops before any live repository action, deployment, private runtime access, or Home Edge operation.
