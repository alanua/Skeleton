from __future__ import annotations

import json

import pytest

from core.action_gate import ActionGateRequest, validate_action_request
from core.memory_gateway_policy import MemoryGatewayPolicyError, validate_namespace
from core.runner_task import RUNNER_TASK_SCHEMA, RunnerTask, RunnerTaskValidationError
from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_pep import (
    AuthorityClaim,
    PEPError,
    PolicyEnforcementPoint,
    reservation_scope_fingerprint,
)
from core.secret_reference import (
    REFERENCE_BOOTSTRAP_REQUIRED,
    SecretReferenceRegistrationError,
    registered_bitwarden_reference_from_systemd_index,
)
from core.secret_store import SecretReference
from core.telegram_permissions import (
    TelegramAccessMode,
    TelegramGatewayError,
    TelegramSource,
)


class _Verifier:
    def __init__(self, result: bool = True) -> None:
        self.result = result

    def verify(self, claim: AuthorityClaim) -> bool:
        return self.result


def _task_mapping() -> dict[str, object]:
    return {
        "schema": RUNNER_TASK_SCHEMA,
        "repo": "alanua/Skeleton",
        "branch": "runner/issue-4791",
        "base_sha": "a" * 40,
        "task_kind": "code_edit",
        "payload": {"issue_number": 4791, "intent": "synthetic adversarial acceptance"},
        "requested_capabilities": [
            "repository_read",
            "repository_write_allowlisted",
            "test_execution",
        ],
        "allowed_files": ["tests/test_agent_independent_enforcement.py"],
        "forbidden_actions": ["no deployment", "no runtime mutation"],
        "validation_commands": [
            ["python3", "-m", "pytest", "-q", "tests/test_agent_independent_enforcement.py"]
        ],
        "validation_timeout_seconds": 900,
        "expected_output": ["synthetic test receipt"],
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": "synthetic-approval-reference",
        "idempotency_key": "agent-independent-enforcement-4791",
    }


def _task_reason(mapping: dict[str, object]) -> str:
    with pytest.raises(RunnerTaskValidationError) as excinfo:
        RunnerTask.from_mapping(mapping)
    return excinfo.value.reason_code


def _envelope(
    effect: EffectClass = EffectClass.GREEN,
    *,
    operation_id: str = "op:agent-independent",
    idem: str = "idem:agent-independent",
    target: str = "git:aaaaaaaa",
    fence: int = 7,
    resources: tuple[str, ...] = ("repo:tests/test_agent_independent_enforcement.py",),
) -> ExecutionEnvelope:
    reason = {
        EffectClass.GREEN: "GREEN_AUTONOMOUS_TYPED_EFFECT",
        EffectClass.YELLOW: "YELLOW_EFFECT_BOUNDARY",
        EffectClass.RED: "RED_EFFECT_BOUNDARY",
    }[effect]
    return ExecutionEnvelope(
        operation_id,
        "adapter:repository",
        target,
        fence,
        idem,
        resources,
        ("workspace_write",),
        effect,
        reason,
        ("repository_write_allowlisted",),
        PrivacyClass.PUBLIC_SAFE,
    )


def _ledger_for(
    envelope: ExecutionEnvelope,
    *,
    target: str | None = None,
    fence: int | None = None,
    scope_envelope: ExecutionEnvelope | None = None,
) -> OperationLedger:
    ledger = OperationLedger()
    identity = OperationIdentity(
        envelope.operation_id,
        envelope.idempotency_key,
        target or envelope.target_state_ref,
    )
    ledger.reserve(
        identity,
        fence_token=fence or envelope.fence_token,
        reservation_scope_hash=reservation_scope_fingerprint(scope_envelope or envelope),
    )
    return ledger


def _claim(
    effect: EffectClass = EffectClass.YELLOW,
    *,
    fresh: bool = True,
    operation_id: str = "op:agent-independent",
    target: str = "git:aaaaaaaa",
    resources: tuple[str, ...] = ("repo:tests/test_agent_independent_enforcement.py",),
) -> AuthorityClaim:
    return AuthorityClaim(
        evidence_refs=("github:approval-4791",),
        operation_id=operation_id,
        target_state_ref=target,
        effect_class=effect,
        resources=resources,
        fresh=fresh,
    )


def _credential_authority(tmp_path, registrations: list[dict[str, object]]) -> dict[str, str]:
    credentials = tmp_path / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "skeleton-secret-reference-index").write_text(
        json.dumps(
            {
                "schema": "skeleton.secret_reference_index.v1",
                "registrations": registrations,
            }
        ),
        encoding="utf-8",
    )
    return {"CREDENTIALS_DIRECTORY": str(credentials)}


def test_valid_bounded_task_and_green_operation_still_work() -> None:
    task = RunnerTask.from_mapping(_task_mapping())
    assert task.repo == "alanua/Skeleton"

    envelope = _envelope()
    receipt = PolicyEnforcementPoint().evaluate(envelope, _ledger_for(envelope))
    assert receipt.authorization_status == "AUTHORIZED_GREEN_BOUNDED"
    assert receipt.side_effects_executed is False


def test_untrusted_client_cannot_select_unallowlisted_merge_repository() -> None:
    decision = validate_action_request(
        ActionGateRequest(
            action_type="merge_pull_request",
            repo="attacker/NotSkeleton",
            pr_number=1,
            expected_head_sha="a" * 40,
            expected_files=("core/a.py",),
            user_approved=True,
        )
    )
    assert decision.status == "blocked"
    assert "repo is not allowlisted." in decision.reasons


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("base_sha", "stale-short-sha", "INVALID_BASE_SHA"),
        (
            "requested_capabilities",
            ["repository_read", "arbitrary_root_shell"],
            "INVALID_REQUESTED_CAPABILITY",
        ),
        ("allowed_files", ["core/../secrets.txt"], "INVALID_ALLOWED_FILE"),
    ],
)
def test_untrusted_task_cannot_widen_typed_scope(
    field: str,
    value: object,
    reason: str,
) -> None:
    mapping = _task_mapping()
    mapping[field] = value
    assert _task_reason(mapping) == reason


def test_plain_approval_reference_or_legacy_boolean_is_not_vnext_authority() -> None:
    task = RunnerTask.from_mapping(_task_mapping())
    assert task.approval_reference == "synthetic-approval-reference"

    legacy_decision = validate_action_request(
        ActionGateRequest(
            action_type="merge_pull_request",
            repo="alanua/Skeleton",
            pr_number=4791,
            expected_head_sha="a" * 40,
            expected_files=("tests/test_agent_independent_enforcement.py",),
            user_approved=True,
        )
    )
    assert legacy_decision.status == "allowed"

    envelope = _envelope(EffectClass.YELLOW)
    with pytest.raises(PEPError, match="PEP_OPERATOR_AUTHORITY_REQUIRED"):
        PolicyEnforcementPoint().evaluate(envelope, _ledger_for(envelope))


def test_green_cannot_smuggle_operator_authority() -> None:
    envelope = _envelope()
    with pytest.raises(PEPError, match="PEP_GREEN_AUTHORITY_CLAIM_UNEXPECTED"):
        PolicyEnforcementPoint().evaluate(
            envelope,
            _ledger_for(envelope),
            authority_claim=_claim(EffectClass.GREEN),
            authority_verifier=_Verifier(),
        )


@pytest.mark.parametrize("effect", [EffectClass.YELLOW, EffectClass.RED])
def test_protected_effect_without_authority_fails_closed(effect: EffectClass) -> None:
    envelope = _envelope(effect)
    with pytest.raises(PEPError, match="PEP_OPERATOR_AUTHORITY_REQUIRED"):
        PolicyEnforcementPoint().evaluate(envelope, _ledger_for(envelope))


def test_red_rejects_stale_authority() -> None:
    envelope = _envelope(EffectClass.RED)
    with pytest.raises(PEPError, match="PEP_FRESH_OPERATOR_AUTHORITY_REQUIRED"):
        PolicyEnforcementPoint().evaluate(
            envelope,
            _ledger_for(envelope),
            authority_claim=_claim(EffectClass.RED, fresh=False),
            authority_verifier=_Verifier(),
        )


@pytest.mark.parametrize(
    ("claim", "reason"),
    [
        (_claim(operation_id="op:other"), "PEP_AUTHORITY_OPERATION_MISMATCH"),
        (_claim(target="git:bbbbbbbb"), "PEP_AUTHORITY_TARGET_STATE_MISMATCH"),
        (
            _claim(resources=("repo:other.py",)),
            "PEP_AUTHORITY_SCOPE_MISMATCH",
        ),
    ],
)
def test_operator_authority_is_bound_to_exact_operation_target_and_scope(
    claim: AuthorityClaim,
    reason: str,
) -> None:
    envelope = _envelope(EffectClass.YELLOW)
    with pytest.raises(PEPError, match=reason):
        PolicyEnforcementPoint().evaluate(
            envelope,
            _ledger_for(envelope),
            authority_claim=claim,
            authority_verifier=_Verifier(),
        )


def test_unverified_claim_cannot_self_authorize() -> None:
    envelope = _envelope(EffectClass.YELLOW)
    with pytest.raises(PEPError, match="PEP_AUTHORITY_VERIFICATION_FAILED"):
        PolicyEnforcementPoint().evaluate(
            envelope,
            _ledger_for(envelope),
            authority_claim=_claim(),
            authority_verifier=_Verifier(False),
        )


def test_replay_fence_and_idempotency_mismatch_fail_closed() -> None:
    envelope = _envelope()
    with pytest.raises(PEPError, match="PEP_LEDGER_FENCE_MISMATCH"):
        PolicyEnforcementPoint().evaluate(
            envelope,
            _ledger_for(envelope, fence=envelope.fence_token + 1),
        )

    rebound = _envelope(idem="idem:forged")
    with pytest.raises(PEPError, match="PEP_LEDGER_RESERVATION_MISSING"):
        PolicyEnforcementPoint().evaluate(rebound, _ledger_for(envelope))


def test_terminal_operation_cannot_be_reauthorized() -> None:
    envelope = _envelope()
    ledger = _ledger_for(envelope)
    identity = OperationIdentity(
        envelope.operation_id,
        envelope.idempotency_key,
        envelope.target_state_ref,
    )
    ledger.finish(
        identity,
        fence_token=envelope.fence_token,
        success=True,
        reason_code="VALIDATION_PASS",
        touched_resource_refs=envelope.resources,
        before_state_ref="sha256:before",
        after_state_ref="sha256:after",
        validation_status="PASS",
    )

    with pytest.raises(PEPError, match="PEP_LEDGER_RESERVATION_REQUIRED"):
        PolicyEnforcementPoint().evaluate(envelope, ledger)


def test_memory_namespace_cannot_be_wildcard_or_unknown() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as wildcard:
        validate_namespace("*", allowed_namespaces=frozenset({"aufmass"}))
    assert wildcard.value.reason_code == "WILDCARD_NAMESPACE_FORBIDDEN"

    with pytest.raises(MemoryGatewayPolicyError) as unknown:
        validate_namespace("invented_namespace", allowed_namespaces=frozenset({"aufmass"}))
    assert unknown.value.reason_code == "UNKNOWN_NAMESPACE"


def test_telegram_read_only_source_cannot_be_promoted_to_write_by_caller() -> None:
    source = TelegramSource.from_mapping(
        {
            "source_id": "news",
            "handle": "@news",
            "access_modes": ["READ_PUBLIC"],
        }
    )
    with pytest.raises(TelegramGatewayError) as excinfo:
        source.require(TelegramAccessMode.WRITE_BOT)
    assert excinfo.value.reason_code == "ACCESS_MODE_NOT_ALLOWED"


def test_telegram_private_peer_outside_allowlist_fails_closed() -> None:
    source = TelegramSource.from_mapping(
        {
            "source_id": "private",
            "handle": "@private",
            "access_modes": ["READ_ALLOWED_PRIVATE"],
            "allowlisted_peer_ids": ["peer-allowed"],
        }
    )
    with pytest.raises(TelegramGatewayError) as excinfo:
        source.resolve_private_peer("peer-attacker")
    assert excinfo.value.reason_code == "PEER_NOT_ALLOWLISTED"


def test_secret_reference_missing_registration_fails_closed(tmp_path) -> None:
    authority = _credential_authority(tmp_path, [])
    with pytest.raises(SecretReferenceRegistrationError, match=REFERENCE_BOOTSTRAP_REQUIRED):
        registered_bitwarden_reference_from_systemd_index(
            authority,
            service_id="mail-gmail",
            alias="acct:gmail-primary",
        )


def test_secret_reference_unsupported_provider_fails_closed(tmp_path) -> None:
    authority = _credential_authority(
        tmp_path,
        [
            {
                "service_id": "mail-gmail",
                "alias": "acct:gmail-primary",
                "provider": "plaintext",
                "reference_id": "not-used",
            }
        ],
    )
    with pytest.raises(
        SecretReferenceRegistrationError,
        match="REFERENCE_PROVIDER_UNSUPPORTED",
    ):
        registered_bitwarden_reference_from_systemd_index(
            authority,
            service_id="mail-gmail",
            alias="acct:gmail-primary",
        )


def test_secret_reference_ambiguity_fails_closed(tmp_path) -> None:
    authority = _credential_authority(
        tmp_path,
        [
            {
                "service_id": "mail-gmail",
                "alias": "acct:gmail-primary",
                "provider": "bitwarden",
                "reference_id": "11111111-2222-3333-4444-555555555555",
            },
            {
                "service_id": "mail-gmail",
                "alias": "acct:gmail-primary",
                "provider": "bitwarden",
                "reference_id": "66666666-7777-8888-9999-000000000000",
            },
        ],
    )
    with pytest.raises(
        SecretReferenceRegistrationError,
        match="REFERENCE_REGISTRATION_AMBIGUOUS",
    ):
        registered_bitwarden_reference_from_systemd_index(
            authority,
            service_id="mail-gmail",
            alias="acct:gmail-primary",
        )


def test_valid_secret_registration_returns_only_opaque_reference(tmp_path) -> None:
    authority = _credential_authority(
        tmp_path,
        [
            {
                "service_id": "mail-gmail",
                "alias": "acct:gmail-primary",
                "provider": "bitwarden",
                "reference_id": "11111111-2222-3333-4444-555555555555",
            }
        ],
    )
    reference = registered_bitwarden_reference_from_systemd_index(
        authority,
        service_id="mail-gmail",
        alias="acct:gmail-primary",
    )
    assert reference == SecretReference(
        provider="bitwarden",
        reference_id="11111111-2222-3333-4444-555555555555",
    )
