from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TypeVar

from core.credential_broker import (
    CredentialBrokerError,
    InProcessCredentialAdapter,
)
from core.secret_store import (
    ResolvedSecret,
    SecretResolutionContext,
    SecretResolutionError,
    SecretProviderUnavailable,
)
from core.secret_reference import (
    SecretReferenceRegistrationError,
    registered_bitwarden_reference_from_systemd_index,
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
    bootstrap_required: bool = False


@dataclass(frozen=True, slots=True)
class RegisteredMaterialCredential:
    service_id: str
    alias: str
    reference_credential_name: str
    context: SecretResolutionContext
    action_id: str
    target_id: str
    bootstrap_required: bool = False


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

_GMAIL_PRIMARY = RegisteredMaterialCredential(
    service_id="mail-gmail",
    alias="acct:gmail-primary",
    reference_credential_name="gmail-primary-oauth-secret-ref",
    context=SecretResolutionContext(
        machine_identity="hetzner-agent-runner-1",
        audience="mail-gmail-readonly",
        task_kind="mail_poll",
    ),
    action_id="use-gmail-readonly-oauth",
    target_id="mail-gmail-primary-oauth-consumer",
    bootstrap_required=True,
)

_GMAIL_SECONDARY = RegisteredMaterialCredential(
    service_id="mail-gmail",
    alias="acct:gmail-secondary",
    reference_credential_name="gmail-secondary-oauth-secret-ref",
    context=SecretResolutionContext(
        machine_identity="hetzner-agent-runner-1",
        audience="mail-gmail-readonly",
        task_kind="mail_poll",
    ),
    action_id="use-gmail-readonly-oauth",
    target_id="mail-gmail-secondary-oauth-consumer",
)

_REGISTERED_ENVIRONMENT_CREDENTIALS = {
    (_RUNNER_OPENHANDS.service_id, _RUNNER_OPENHANDS.alias): _RUNNER_OPENHANDS,
}
_REGISTERED_MATERIAL_CREDENTIALS = {
    (_GMAIL_PRIMARY.service_id, _GMAIL_PRIMARY.alias): _GMAIL_PRIMARY,
    (_GMAIL_SECONDARY.service_id, _GMAIL_SECONDARY.alias): _GMAIL_SECONDARY,
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


def _registered_material_credential(
    service_id: str,
    alias: str,
    action_id: str,
) -> RegisteredMaterialCredential:
    spec = _REGISTERED_MATERIAL_CREDENTIALS.get((service_id, alias))
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
    """Resolve one code-registered credential and bind it to its fixed trusted target."""

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

    receipt = _invoke_registered_binding(
        service_id=spec.service_id,
        alias=spec.alias,
        reference_credential_name=spec.reference_credential_name,
        context=spec.context,
        action_id=spec.action_id,
        target_id=spec.target_id,
        consumer=consume,
        authority_environment=authority_environment,
        bootstrap_required=spec.bootstrap_required,
    )
    public = receipt.get("result")
    if (
        isinstance(public, Mapping)
        and public.get("status") == "USED"
        and staged_environment is not None
    ):
        environment.clear()
        environment.update(staged_environment)
    return receipt


T = TypeVar("T")


def consume_registered_material_credential(
    *,
    service_id: str,
    alias: str,
    action_id: str,
    consumer: Callable[[str], None],
    authority_environment: Mapping[str, str],
) -> dict[str, object]:
    """Deliver a code-registered secret only to an in-process bounded consumer.

    The broker never returns plaintext. Provider/reference/context/target are code-owned;
    the caller may select only a registered service + logical alias + action.
    """

    spec = _registered_material_credential(service_id, alias, action_id)

    def consume_material(
        material: ResolvedSecret,
        _binding: ServiceCredentialBinding,
    ) -> None:
        ephemeral = material.inject({}, "SKELETON_EPHEMERAL_REGISTERED_MATERIAL")
        value = ephemeral.pop("SKELETON_EPHEMERAL_REGISTERED_MATERIAL")
        consumer(value)

    return _invoke_registered_binding(
        service_id=spec.service_id,
        alias=spec.alias,
        reference_credential_name=spec.reference_credential_name,
        context=spec.context,
        action_id=spec.action_id,
        target_id=spec.target_id,
        consumer=consume_material,
        authority_environment=authority_environment,
        bootstrap_required=spec.bootstrap_required,
    )


def _invoke_registered_binding(
    *,
    service_id: str,
    alias: str,
    reference_credential_name: str,
    context: SecretResolutionContext,
    action_id: str,
    target_id: str,
    consumer: Callable[[ResolvedSecret, ServiceCredentialBinding], None],
    authority_environment: Mapping[str, str],
    bootstrap_required: bool,
) -> dict[str, object]:
    try:
        try:
            reference = registered_bitwarden_reference_from_systemd_index(
                authority_environment,
                service_id=service_id,
                alias=alias,
                bootstrap_required=bootstrap_required,
                fallback_credential_name=reference_credential_name,
            )
        except SecretProviderUnavailable:
            reference = bitwarden_reference_from_systemd_credential(
                authority_environment,
                reference_credential_name,
            )
        binding = ServiceCredentialBinding(
            service_id=service_id,
            alias=alias,
            reference=reference,
            context=context,
            action_id=action_id,
            adapter_id="in_process",
            target_id=target_id,
            required=True,
            reload_mode="per_use",
        )
        runtime = build_bitwarden_credential_runtime(
            catalog=ServiceCredentialCatalog([binding]),
            registrations=(CredentialRuntimeRegistration(service_id, context),),
            adapters={
                "in_process": InProcessCredentialAdapter({target_id: consumer})
            },
            authority_environment=authority_environment,
        )
        return runtime.control_for(service_id).invoke(
            "credential_use",
            {"alias": alias, "action_id": action_id},
        )
    except SecretReferenceRegistrationError as exc:
        raise RegisteredCredentialRuntimeError(str(exc)) from None
    except (
        CredentialBrokerError,
        CredentialRuntimeRegistrationError,
        SecretResolutionError,
        ServiceCredentialBindingError,
    ):
        raise RegisteredCredentialRuntimeError(
            "registered_credential_resolution_failed"
        ) from None


def registered_credential_capabilities() -> tuple[dict[str, str], ...]:
    """Return non-secret registration metadata for control-plane discovery."""

    values: list[dict[str, str]] = []
    for spec in _REGISTERED_ENVIRONMENT_CREDENTIALS.values():
        values.append(
            {
                "service_id": spec.service_id,
                "alias": spec.alias,
                "action_id": spec.action_id,
                "delivery": "registered_environment",
            }
        )
    for spec in _REGISTERED_MATERIAL_CREDENTIALS.values():
        values.append(
            {
                "service_id": spec.service_id,
                "alias": spec.alias,
                "action_id": spec.action_id,
                "delivery": "registered_in_process",
            }
        )
    return tuple(sorted(values, key=lambda item: (item["service_id"], item["alias"])))
