from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

from adapters.credential_control import CredentialControlAdapter
from core.credential_broker import CredentialBroker, CredentialDeliveryAdapter
from core.secret_store import SecretResolutionContext
from core.service_credentials import ServiceCredentialCatalog
from integrations.bitwarden_secret_store import BwsCliSecretsManagerStore


_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")


class CredentialRuntimeRegistrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CredentialRuntimeRegistration:
    """Trusted code-owned service identity used to authorize broker resolution."""

    service_id: str
    context: SecretResolutionContext

    def __post_init__(self) -> None:
        service_id = self.service_id.strip() if isinstance(self.service_id, str) else ""
        if not _SERVICE_ID_RE.fullmatch(service_id):
            raise CredentialRuntimeRegistrationError("invalid_registered_service_id")
        if not isinstance(self.context, SecretResolutionContext):
            raise CredentialRuntimeRegistrationError("typed_registered_runtime_context_required")
        object.__setattr__(self, "service_id", service_id)


class CredentialRuntime:
    """Central runtime owner. Services receive only their bound control adapter."""

    def __init__(
        self,
        *,
        broker: CredentialBroker,
        registrations: Sequence[CredentialRuntimeRegistration],
    ) -> None:
        if not isinstance(broker, CredentialBroker):
            raise CredentialRuntimeRegistrationError("typed_credential_broker_required")
        controls: dict[str, CredentialControlAdapter] = {}
        for registration in registrations:
            if not isinstance(registration, CredentialRuntimeRegistration):
                raise CredentialRuntimeRegistrationError("typed_runtime_registration_required")
            if registration.service_id in controls:
                raise CredentialRuntimeRegistrationError("duplicate_runtime_service_registration")
            controls[registration.service_id] = CredentialControlAdapter(
                broker,
                service_id=registration.service_id,
            )
        self._broker = broker
        self._controls = controls

    @property
    def registered_service_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._controls))

    def control_for(self, service_id: str) -> CredentialControlAdapter:
        service = service_id.strip() if isinstance(service_id, str) else ""
        control = self._controls.get(service)
        if control is None:
            raise CredentialRuntimeRegistrationError("credential_service_not_registered")
        return control


def build_bitwarden_credential_runtime(
    *,
    catalog: ServiceCredentialCatalog,
    registrations: Sequence[CredentialRuntimeRegistration],
    adapters: Mapping[str, CredentialDeliveryAdapter],
    authority_environment: Mapping[str, str],
) -> CredentialRuntime:
    """Build the shared production runtime from the trusted systemd authority boundary.

    Bitwarden-specific token handling stays here. Registered Skeleton services only receive
    their service-bound control adapter and never the provider object or secret material.
    """

    typed_registrations = tuple(registrations)
    runtime_contexts: dict[str, SecretResolutionContext] = {}
    for registration in typed_registrations:
        if not isinstance(registration, CredentialRuntimeRegistration):
            raise CredentialRuntimeRegistrationError("typed_runtime_registration_required")
        if registration.service_id in runtime_contexts:
            raise CredentialRuntimeRegistrationError("duplicate_runtime_service_registration")
        runtime_contexts[registration.service_id] = registration.context

    store = BwsCliSecretsManagerStore.from_systemd_credentials(authority_environment)
    broker = CredentialBroker(
        catalog=catalog,
        stores={"bitwarden": store},
        adapters=adapters,
        runtime_contexts=runtime_contexts,
    )
    return CredentialRuntime(broker=broker, registrations=typed_registrations)
