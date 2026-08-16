from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from core.credential_broker import (
    CredentialBrokerError,
    InProcessCredentialAdapter,
)
from core.secret_store import (
    ResolvedSecret,
    SecretResolutionContext,
    SecretResolutionError,
)
from core.service_credentials import (
    ServiceCredentialBinding,
    ServiceCredentialBindingError,
    ServiceCredentialCatalog,
)
from integrations.bitwarden_credential_runtime import (
    CredentialRuntimeRegistration,
    CredentialRuntimeRegistrationError,
    build_bitwarden_credential_runtime,
)
from integrations.bitwarden_secret_store import (
    bitwarden_reference_from_systemd_credential,
)


class RegisteredCredentialRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredEnvironmentCredential:
    service_id: str
    alias: str
    reference_credential_name: str
    context: SecretResolutionContext
    action_id: str
    target_id: str
    environment_variable: str


_RUNNER_OPENHANDS = RegisteredEnvironmentCredential(
    service_id="runner-openhands",
    alias="openrouter-api",
    reference_credential_name="openrouter-secret-ref",
    context=SecretResolutionContext(
        machine_identity="hetzner-agent-runner-1",
        audience="openhands-openrouter",
        task_kind="code_generation",
    ),
    action_id="bind-openrouter-fallback",
    target_id="runner-openhands-environment",
    environment_variable="SKELETON_OPENROUTER_FALLBACK_API_KEY",
)

_REGISTERED_ENVIRONMENT_CREDENTIALS = {
    (_RUNNER_OPENHANDS.service_id, _RUNNER_OPENHANDS.alias): _RUNNER_OPENHANDS,
}


def _registered_environment_credential(
    service_id: str,
    alias: str,
    action_id: str,
) -> RegisteredEnvironmentCredential:
    spec = _REGISTERED_ENVIRONMENT_CREDENTIALS.get((service_id, alias))
    if spec is None:
        raise RegisteredCredentialRuntimeError("registered_credential_unavailable")
    if action_id != spec.action_id:
        raise RegisteredCredentialRuntimeError("registered_credential_action_mismatch")
    return spec


def bind_registered_environment_credential(
    *,
    service_id: str,
    alias: str,
    action_id: str,
    environment: MutableMapping[str, str],
    authority_environment: Mapping[str, str],
) -> dict[str, object]:
    """Resolve one code-registered credential and bind it to its fixed trusted target.

    Consumers select only service + logical alias + registered action. Provider details,
    reference credential name, target id, environment variable, and policy context are
    code-owned. The returned object is the public-safe CredentialControl receipt only.
    """

    spec = _registered_environment_credential(service_id, alias, action_id)
    staged_environment: dict[str, str] | None = None

    def consume(
        material: ResolvedSecret,
        _binding: ServiceCredentialBinding,
    ) -> None:
        nonlocal staged_environment
        staged_environment = material.inject(
            dict(environment),
            spec.environment_variable,
        )

    try:
        reference = bitwarden_reference_from_systemd_credential(
            authority_environment,
            spec.reference_credential_name,
        )
        binding = ServiceCredentialBinding(
            service_id=spec.service_id,
            alias=spec.alias,
            reference=reference,
            context=spec.context,
            action_id=spec.action_id,
            adapter_id="in_process",
            target_id=spec.target_id,
            required=True,
            reload_mode="per_use",
        )
        runtime = build_bitwarden_credential_runtime(
            catalog=ServiceCredentialCatalog([binding]),
            registrations=(
                CredentialRuntimeRegistration(spec.service_id, spec.context),
            ),
            adapters={
                "in_process": InProcessCredentialAdapter(
                    {spec.target_id: consume}
                )
            },
            authority_environment=authority_environment,
        )
        receipt = runtime.control_for(spec.service_id).invoke(
            "credential_use",
            {"alias": spec.alias, "action_id": spec.action_id},
        )
    except (
        CredentialBrokerError,
        CredentialRuntimeRegistrationError,
        SecretResolutionError,
        ServiceCredentialBindingError,
    ):
        raise RegisteredCredentialRuntimeError(
            "registered_credential_resolution_failed"
        ) from None

    public = receipt.get("result")
    if (
        isinstance(public, Mapping)
        and public.get("status") == "USED"
        and staged_environment is not None
    ):
        environment.clear()
        environment.update(staged_environment)
    return receipt


def registered_credential_capabilities() -> tuple[dict[str, str], ...]:
    """Return non-secret registration metadata for control-plane discovery."""

    return tuple(
        {
            "service_id": spec.service_id,
            "alias": spec.alias,
            "action_id": spec.action_id,
            "delivery": "registered_in_process",
        }
        for spec in sorted(
            _REGISTERED_ENVIRONMENT_CREDENTIALS.values(),
            key=lambda item: (item.service_id, item.alias),
        )
    )
