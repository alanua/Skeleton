from __future__ import annotations

import pytest

from core.credential_broker import InProcessCredentialAdapter
from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext
from core.service_credentials import ServiceCredentialBinding, ServiceCredentialCatalog
from integrations import bitwarden_credential_runtime as runtime


class FakeBitwardenStore:
    provider = "bitwarden"

    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.calls: list[str] = []

    def resolve(
        self,
        reference: SecretReference,
        context: SecretResolutionContext,
    ) -> ResolvedSecret:
        self.calls.append(reference.reference_id)
        return ResolvedSecret(self.values[reference.reference_id])


def _context(service_id: str) -> SecretResolutionContext:
    return SecretResolutionContext(
        machine_identity="skeleton-host-01",
        audience=f"service:{service_id}",
        task_kind="service_runtime",
    )


def _binding(service_id: str, reference_id: str) -> ServiceCredentialBinding:
    return ServiceCredentialBinding(
        service_id=service_id,
        alias="api",
        reference=SecretReference(provider="bitwarden", reference_id=reference_id),
        context=_context(service_id),
        action_id="use_api",
        adapter_id="in_process",
        target_id=service_id,
        required=True,
        reload_mode="per_use",
    )


def test_shared_runtime_builds_one_provider_and_service_bound_controls(monkeypatch) -> None:
    store = FakeBitwardenStore({"ref-a": "synthetic-a", "ref-b": "synthetic-b"})
    observed: dict[str, list[str]] = {"service-a": [], "service-b": []}

    monkeypatch.setattr(
        runtime.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(lambda cls, authority: store),
    )

    def consumer(service_id: str):
        def consume(material: ResolvedSecret, _binding: ServiceCredentialBinding) -> None:
            observed[service_id].append(material.inject({}, "TOKEN")["TOKEN"])
        return consume

    built = runtime.build_bitwarden_credential_runtime(
        catalog=ServiceCredentialCatalog(
            [_binding("service-a", "ref-a"), _binding("service-b", "ref-b")]
        ),
        registrations=(
            runtime.CredentialRuntimeRegistration("service-a", _context("service-a")),
            runtime.CredentialRuntimeRegistration("service-b", _context("service-b")),
        ),
        adapters={
            "in_process": InProcessCredentialAdapter(
                {
                    "service-a": consumer("service-a"),
                    "service-b": consumer("service-b"),
                }
            )
        },
        authority_environment={"CREDENTIALS_DIRECTORY": "/trusted/not-read-by-fake"},
    )

    assert built.registered_service_ids == ("service-a", "service-b")
    assert built.control_for("service-a").invoke(
        "credential_use", {"alias": "api", "action_id": "use_api"}
    )["result"]["status"] == "USED"
    assert observed == {"service-a": ["synthetic-a"], "service-b": []}
    assert store.calls == ["ref-a"]


def test_unregistered_service_has_no_control_surface(monkeypatch) -> None:
    store = FakeBitwardenStore({"ref-a": "synthetic-a"})
    monkeypatch.setattr(
        runtime.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(lambda cls, authority: store),
    )
    built = runtime.build_bitwarden_credential_runtime(
        catalog=ServiceCredentialCatalog([_binding("service-a", "ref-a")]),
        registrations=(runtime.CredentialRuntimeRegistration("service-a", _context("service-a")),),
        adapters={
            "in_process": InProcessCredentialAdapter(
                {"service-a": lambda _material, _binding: None}
            )
        },
        authority_environment={},
    )

    with pytest.raises(
        runtime.CredentialRuntimeRegistrationError,
        match="credential_service_not_registered",
    ):
        built.control_for("service-b")
    assert store.calls == []


def test_wrong_trusted_runtime_context_fails_before_delivery(monkeypatch) -> None:
    store = FakeBitwardenStore({"ref-a": "synthetic-a"})
    delivered: list[bool] = []
    monkeypatch.setattr(
        runtime.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(lambda cls, authority: store),
    )
    wrong_context = SecretResolutionContext(
        machine_identity="skeleton-host-01",
        audience="service:other",
        task_kind="service_runtime",
    )
    built = runtime.build_bitwarden_credential_runtime(
        catalog=ServiceCredentialCatalog([_binding("service-a", "ref-a")]),
        registrations=(runtime.CredentialRuntimeRegistration("service-a", wrong_context),),
        adapters={
            "in_process": InProcessCredentialAdapter(
                {"service-a": lambda _material, _binding: delivered.append(True)}
            )
        },
        authority_environment={},
    )

    result = built.control_for("service-a").invoke("credential_probe", {"alias": "api"})

    assert result["result"]["status"] == "BLOCKED"
    assert result["result"]["reason_class"] == "SECRET_OUT_OF_SCOPE"
    assert delivered == []
    assert store.calls == []


def test_duplicate_service_registration_is_rejected_before_provider_build(monkeypatch) -> None:
    provider_calls: list[bool] = []

    def provider_factory(cls, authority):
        provider_calls.append(True)
        return FakeBitwardenStore({"ref-a": "synthetic-a"})

    monkeypatch.setattr(
        runtime.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(provider_factory),
    )
    registration = runtime.CredentialRuntimeRegistration("service-a", _context("service-a"))

    with pytest.raises(
        runtime.CredentialRuntimeRegistrationError,
        match="duplicate_runtime_service_registration",
    ):
        runtime.build_bitwarden_credential_runtime(
            catalog=ServiceCredentialCatalog([_binding("service-a", "ref-a")]),
            registrations=(registration, registration),
            adapters={"in_process": InProcessCredentialAdapter({})},
            authority_environment={},
        )

    assert provider_calls == []
