from __future__ import annotations

import json
from pathlib import Path

from adapters.credential_control import CredentialControlAdapter
from adapters.credential_mcp import CredentialMcpAdapter
from core.credential_broker import CredentialBroker, InProcessCredentialAdapter
from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext
from core.service_credentials import ServiceCredentialBinding, ServiceCredentialCatalog


SYNTHETIC_SECRET = "synthetic-mcp-secret"


class FakeStore:
    provider = "bitwarden"

    def resolve(self, reference, context):
        del reference, context
        return ResolvedSecret(SYNTHETIC_SECRET)


def _mcp() -> CredentialMcpAdapter:
    context = SecretResolutionContext(
        machine_identity="synthetic-host",
        audience="service:synthetic",
        task_kind="service_runtime",
    )
    binding = ServiceCredentialBinding(
        service_id="synthetic-service",
        alias="api",
        reference=SecretReference(provider="bitwarden", reference_id="ref-a"),
        context=context,
        action_id="use-api",
        adapter_id="in_process",
        target_id="synthetic-target",
    )
    broker = CredentialBroker(
        catalog=ServiceCredentialCatalog([binding]),
        stores={"bitwarden": FakeStore()},
        adapters={
            "in_process": InProcessCredentialAdapter(
                {"synthetic-target": lambda _material, _binding: None}
            )
        },
        runtime_contexts={"synthetic-service": context},
    )
    return CredentialMcpAdapter(
        CredentialControlAdapter(broker, service_id="synthetic-service")
    )


def test_mcp_surface_has_no_service_or_target_selection_fields() -> None:
    adapter = _mcp()
    specs = adapter.list_tools()
    combined = json.dumps(specs, sort_keys=True)
    assert adapter.service_id == "synthetic-service"
    assert "service_id" not in combined
    assert "command" not in combined
    assert "environment" not in combined
    assert "host" not in combined


def test_mcp_probe_and_use_return_only_public_receipts() -> None:
    adapter = _mcp()
    probe = adapter.call_tool("credential_probe", {"alias": "api"})
    use = adapter.call_tool(
        "credential_use",
        {"alias": "api", "action_id": "use-api"},
    )
    spoof = adapter.call_tool(
        "credential_use",
        {"service_id": "other", "alias": "api", "action_id": "use-api"},
    )
    serialized = json.dumps([probe, use, spoof], sort_keys=True)
    assert probe["result"]["status"] == "AVAILABLE"
    assert use["result"]["status"] == "USED"
    assert spoof["result"]["status"] == "BLOCKED"
    assert SYNTHETIC_SECRET not in serialized
    assert "other" not in serialized


def test_chatgpt_registration_descriptor_is_fail_closed() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "adapters"
        / "chatgpt"
        / "CREDENTIAL_CONTROL_REGISTRATION.json"
    )
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    assert descriptor["runtime_binding"] == "service_bound"
    assert descriptor["caller_can_select_service_id"] is False
    assert descriptor["caller_can_select_command"] is False
    assert descriptor["caller_can_select_environment"] is False
    assert descriptor["caller_can_read_secret_value"] is False
    assert descriptor["external_connector_registration"] == "required"
