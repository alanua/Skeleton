from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from adapters.credential_control import CredentialControlAdapter
from core.credential_broker import (
    CredentialBroker,
    CredentialRequestError,
    InProcessCredentialAdapter,
    ProcessCredentialAdapter,
    ProcessCredentialTarget,
)
from core.secret_store import (
    ResolvedSecret,
    SecretMissing,
    SecretReference,
    SecretResolutionContext,
    SecretRevoked,
)
from core.service_credentials import (
    CATALOG_SCHEMA,
    ServiceCredentialBinding,
    ServiceCredentialBindingError,
    ServiceCredentialCatalog,
)


SYNTHETIC_SECRET_A = "synthetic-service-a-secret"
SYNTHETIC_SECRET_B = "synthetic-service-b-secret"


class FakeStore:
    provider = "bitwarden"

    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.failures: dict[str, Exception] = {}
        self.calls: list[str] = []

    def resolve(
        self,
        reference: SecretReference,
        context: SecretResolutionContext,
    ) -> ResolvedSecret:
        del context
        self.calls.append(reference.reference_id)
        failure = self.failures.get(reference.reference_id)
        if failure is not None:
            raise failure
        try:
            value = self.values[reference.reference_id]
        except KeyError as exc:
            raise SecretMissing("synthetic_missing") from exc
        return ResolvedSecret(value)


def _runtime_context(
    service_id: str,
    *,
    machine_identity: str = "skeleton-host-01",
    audience: str | None = None,
    task_kind: str = "service_runtime",
) -> SecretResolutionContext:
    return SecretResolutionContext(
        machine_identity=machine_identity,
        audience=audience or f"service:{service_id}",
        task_kind=task_kind,
    )


def _binding(
    service_id: str,
    reference_id: str,
    *,
    alias: str = "api",
    action_id: str = "use_api",
    adapter_id: str = "in_process",
    target_id: str | None = None,
    required: bool = True,
) -> ServiceCredentialBinding:
    return ServiceCredentialBinding(
        service_id=service_id,
        alias=alias,
        reference=SecretReference(provider="bitwarden", reference_id=reference_id),
        context=_runtime_context(service_id),
        action_id=action_id,
        adapter_id=adapter_id,
        target_id=target_id or service_id,
        required=required,
        reload_mode="per_use",
    )


def _broker(
    store: FakeStore,
    bindings: list[ServiceCredentialBinding],
    consumers: dict[str, object],
    *,
    runtime_contexts: dict[str, SecretResolutionContext] | None = None,
) -> CredentialBroker:
    return CredentialBroker(
        catalog=ServiceCredentialCatalog(bindings),
        stores={"bitwarden": store},
        adapters={
            "in_process": InProcessCredentialAdapter(consumers),
        },
        runtime_contexts=(
            runtime_contexts
            if runtime_contexts is not None
            else {binding.service_id: _runtime_context(binding.service_id) for binding in bindings}
        ),
    )


def test_generic_broker_delivers_service_secret_but_returns_only_safe_receipt() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    observed: list[str] = []

    def consume(material: ResolvedSecret, binding: ServiceCredentialBinding) -> None:
        assert binding.service_id == "service-a"
        observed.append(material.inject({}, "TOKEN")["TOKEN"])

    broker = _broker(store, [_binding("service-a", "ref-a")], {"service-a": consume})

    receipt = broker.use(service_id="service-a", alias="api", action_id="use_api")
    public = receipt.to_public_mapping()

    assert receipt.status == "USED"
    assert observed == [SYNTHETIC_SECRET_A]
    assert SYNTHETIC_SECRET_A not in repr(receipt)
    assert SYNTHETIC_SECRET_A not in json.dumps(public, sort_keys=True)
    assert public["reference_id"] == "ref-a"
    assert len(public["receipt_hash"]) == 64


def test_service_cannot_use_another_services_binding() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    broker = _broker(
        store,
        [_binding("service-a", "ref-a")],
        {"service-a": lambda _material, _binding: None},
    )

    with pytest.raises(CredentialRequestError, match="service_credential_binding_unavailable"):
        broker.use(service_id="service-b", alias="api", action_id="use_api")

    assert store.calls == []


def test_unregistered_action_is_rejected_before_secret_resolution() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    broker = _broker(
        store,
        [_binding("service-a", "ref-a")],
        {"service-a": lambda _material, _binding: None},
    )

    with pytest.raises(CredentialRequestError, match="credential_action_not_registered_for_binding"):
        broker.use(service_id="service-a", alias="api", action_id="arbitrary_shell")

    assert store.calls == []


def test_trusted_runtime_context_mismatch_is_blocked_before_provider() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    binding = _binding("service-a", "ref-a")
    broker = _broker(
        store,
        [binding],
        {"service-a": lambda _material, _binding: None},
        runtime_contexts={
            "service-a": _runtime_context(
                "service-a",
                machine_identity="wrong-host",
            )
        },
    )

    receipt = broker.probe(service_id="service-a", alias="api")

    assert receipt.status == "BLOCKED"
    assert receipt.reason_class == "SECRET_OUT_OF_SCOPE"
    assert store.calls == []


def test_missing_trusted_runtime_context_rejects_request_before_provider() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    broker = _broker(
        store,
        [_binding("service-a", "ref-a")],
        {"service-a": lambda _material, _binding: None},
        runtime_contexts={},
    )

    with pytest.raises(CredentialRequestError, match="trusted_runtime_context_unavailable"):
        broker.probe(service_id="service-a", alias="api")

    assert store.calls == []


def test_rotation_is_observed_on_next_use_without_binding_change() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    observed: list[str] = []

    def consume(material: ResolvedSecret, _binding: ServiceCredentialBinding) -> None:
        observed.append(material.inject({}, "TOKEN")["TOKEN"])

    binding = _binding("service-a", "ref-a")
    broker = _broker(store, [binding], {"service-a": consume})

    first = broker.use(service_id="service-a", alias="api", action_id="use_api")
    store.values["ref-a"] = SYNTHETIC_SECRET_B
    second = broker.use(service_id="service-a", alias="api", action_id="use_api")

    assert first.status == second.status == "USED"
    assert observed == [SYNTHETIC_SECRET_A, SYNTHETIC_SECRET_B]
    assert store.calls == ["ref-a", "ref-a"]


def test_optional_missing_secret_is_degraded_and_required_missing_is_blocked() -> None:
    store = FakeStore({})
    optional = _binding("optional-service", "optional-ref", required=False)
    required = _binding("required-service", "required-ref", required=True)
    broker = _broker(
        store,
        [optional, required],
        {
            "optional-service": lambda _material, _binding: None,
            "required-service": lambda _material, _binding: None,
        },
    )

    optional_receipt = broker.probe(service_id="optional-service", alias="api")
    required_receipt = broker.probe(service_id="required-service", alias="api")

    assert optional_receipt.status == "DEGRADED"
    assert optional_receipt.reason_class == "SECRET_MISSING"
    assert required_receipt.status == "BLOCKED"
    assert required_receipt.reason_class == "SECRET_MISSING"


def test_revoked_secret_fails_closed_with_bounded_reason() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    store.failures["ref-a"] = SecretRevoked("raw-provider-detail-must-not-leak")
    broker = _broker(
        store,
        [_binding("service-a", "ref-a")],
        {"service-a": lambda _material, _binding: None},
    )

    receipt = broker.probe(service_id="service-a", alias="api")

    assert receipt.status == "BLOCKED"
    assert receipt.reason_class == "SECRET_REVOKED"
    assert "raw-provider-detail" not in repr(receipt)


def test_malicious_consumer_return_value_is_blocked_and_not_exposed() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})

    def malicious(material: ResolvedSecret, _binding: ServiceCredentialBinding) -> object:
        return material.inject({}, "TOKEN")["TOKEN"]

    broker = _broker(store, [_binding("service-a", "ref-a")], {"service-a": malicious})

    receipt = broker.use(service_id="service-a", alias="api", action_id="use_api")

    assert receipt.status == "BLOCKED"
    assert receipt.reason_class == "DELIVERY_FAILED"
    assert SYNTHETIC_SECRET_A not in repr(receipt)


def test_process_adapter_uses_only_registered_target_and_discards_output() -> None:
    store = FakeStore({"ref-process": SYNTHETIC_SECRET_A})
    binding = _binding(
        "process-service",
        "ref-process",
        adapter_id="process",
        target_id="registered-process",
        action_id="start_with_api",
    )
    target = ProcessCredentialTarget(
        target_id="registered-process",
        argv=(
            sys.executable,
            "-c",
            "import os,sys; print(os.environ.get('SERVICE_TOKEN','')); sys.exit(0 if os.environ.get('SERVICE_TOKEN') else 3)",
        ),
        environment_variable="SERVICE_TOKEN",
        timeout_seconds=10,
    )
    broker = CredentialBroker(
        catalog=ServiceCredentialCatalog([binding]),
        stores={"bitwarden": store},
        adapters={
            "process": ProcessCredentialAdapter(
                {"registered-process": target},
                base_environment={},
            )
        },
        runtime_contexts={"process-service": _runtime_context("process-service")},
    )

    receipt = broker.use(
        service_id="process-service",
        alias="api",
        action_id="start_with_api",
    )

    assert receipt.status == "USED"
    assert SYNTHETIC_SECRET_A not in repr(receipt)
    with pytest.raises(CredentialRequestError):
        broker.use(
            service_id="process-service",
            alias="api",
            action_id="different-command",
        )


def test_catalog_round_trip_is_provider_neutral_and_rejects_duplicates() -> None:
    binding = _binding("service-a", "ref-a")
    catalog = ServiceCredentialCatalog([binding])
    mapping = catalog.to_public_mapping()

    assert mapping["schema"] == CATALOG_SCHEMA
    restored = ServiceCredentialCatalog.from_mapping(mapping)
    assert restored.get("service-a", "api") == binding
    assert SYNTHETIC_SECRET_A not in json.dumps(mapping, sort_keys=True)

    with pytest.raises(ServiceCredentialBindingError, match="duplicate_service_credential_binding"):
        ServiceCredentialCatalog([binding, binding])


def test_backend_can_change_without_consumer_or_binding_change() -> None:
    binding = _binding("service-a", "ref-a")
    observed: list[str] = []

    def consume(material: ResolvedSecret, _binding: ServiceCredentialBinding) -> None:
        observed.append(material.inject({}, "TOKEN")["TOKEN"])

    first_store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    second_store = FakeStore({"ref-a": SYNTHETIC_SECRET_B})

    first = _broker(first_store, [binding], {"service-a": consume})
    second = _broker(second_store, [binding], {"service-a": consume})

    assert first.use(service_id="service-a", alias="api", action_id="use_api").status == "USED"
    assert second.use(service_id="service-a", alias="api", action_id="use_api").status == "USED"
    assert observed == [SYNTHETIC_SECRET_A, SYNTHETIC_SECRET_B]


def test_control_adapter_is_bound_to_one_service_and_never_returns_secret() -> None:
    store = FakeStore({"ref-a": SYNTHETIC_SECRET_A})
    broker = _broker(
        store,
        [_binding("service-a", "ref-a")],
        {"service-a": lambda _material, _binding: None},
    )
    control = CredentialControlAdapter(broker, service_id="service-a")

    probe = control.invoke("credential_probe", {"alias": "api"})
    used = control.invoke(
        "credential_use",
        {"alias": "api", "action_id": "use_api"},
    )
    spoofed = control.invoke(
        "credential_use",
        {"service_id": "service-b", "alias": "api", "action_id": "use_api"},
    )
    rejected = control.invoke(
        "credential_use",
        {"alias": "api", "action_id": "use_api", "command": "/bin/sh"},
    )

    assert control.service_id == "service-a"
    assert probe["service_id"] == "service-a"
    assert probe["result"]["status"] == "AVAILABLE"
    assert used["result"]["status"] == "USED"
    assert spoofed == {
        "schema": "skeleton.credential_control.v1",
        "service_id": "service-a",
        "result": {"status": "BLOCKED", "reason_class": "UNKNOWN_FIELDS"},
    }
    assert rejected == spoofed
    combined = json.dumps([probe, used, spoofed, rejected], sort_keys=True)
    assert SYNTHETIC_SECRET_A not in combined
    assert "service-b" not in combined
    assert "/bin/sh" not in combined


def test_service_credential_schema_file_is_valid_json() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "service_credential_binding.schema.json"
    parsed = json.loads(schema_path.read_text(encoding="utf-8"))

    assert parsed["title"] == "Skeleton Service Credential Catalog"
    assert parsed["properties"]["schema"]["const"] == CATALOG_SCHEMA
