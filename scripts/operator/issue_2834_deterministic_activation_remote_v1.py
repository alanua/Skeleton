#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
BASE_SHA = 'a08a3922ac7e01c32226bb193a6f072c4662a81f'
BRANCH = 'runner/universal-secretstore-production-activation-v1'
ISSUE = '2834'
REPO_FULL = 'alanua/Skeleton'
WORKTREE = Path('/home/agent/agent-dev/worktrees/issue-2834-deterministic-v1')

EXPECTED_FILES = {
    'core/runner_child_environment.py',
    'integrations/credential_runtime.py',
    'adapters/credential_mcp.py',
    'adapters/chatgpt/CREDENTIAL_CONTROL_REGISTRATION.json',
    'tests/test_registered_credential_runtime.py',
    'tests/test_credential_mcp.py',
    'tests/test_runner_credential_binding.py',
    'docs/CREDENTIAL_CONTROL_MCP.md',
}

SECRET_ENV_NAMES = {
    'BWS_ACCESS_TOKEN',
    'CREDENTIALS_DIRECTORY',
    'OPENROUTER_API_KEY',
    'LLM_API_KEY',
    'SKELETON_OPENROUTER_FALLBACK_API_KEY',
}

class BootstrapError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: int = 900, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and p.returncode != 0:
        raise BootstrapError('command_failed:' + Path(argv[0]).name)
    return p


def git(*args: str, cwd: Path = REPO, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return run(['git', *args], cwd=cwd, check=check, timeout=timeout)


def clean_test_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in SECRET_ENV_NAMES:
        env.pop(name, None)
    return env


def preflight() -> None:
    if not (REPO / '.git').is_dir():
        raise BootstrapError('canonical_repo_missing')
    if git('status', '--porcelain', '--untracked-files=all').stdout.strip():
        raise BootstrapError('canonical_repo_dirty')
    origin = git('remote', 'get-url', 'origin').stdout.strip()
    if not (origin.endswith('/alanua/Skeleton.git') or origin.endswith('/alanua/Skeleton')):
        raise BootstrapError('origin_mismatch')
    git('fetch', '--quiet', 'origin', 'main')
    current_main = git('rev-parse', 'origin/main').stdout.strip()
    if current_main != BASE_SHA:
        raise BootstrapError('main_head_changed:' + current_main)
    remote = git('ls-remote', '--heads', 'origin', f'refs/heads/{BRANCH}').stdout.strip()
    if remote:
        remote_sha = remote.split()[0]
        if remote_sha != BASE_SHA:
            raise BootstrapError('target_branch_moved:' + remote_sha)


def prepare_worktree() -> None:
    if WORKTREE.exists():
        git('worktree', 'remove', '--force', str(WORKTREE), check=False)
        shutil.rmtree(WORKTREE, ignore_errors=True)
    git('worktree', 'prune')
    git('branch', '-D', BRANCH, check=False)
    git('worktree', 'add', '-B', BRANCH, str(WORKTREE), BASE_SHA)


def write(path: str, content: str) -> None:
    target = WORKTREE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip(), encoding='utf-8')


def patch_runner_child_environment() -> None:
    path = WORKTREE / 'core/runner_child_environment.py'
    text = path.read_text(encoding='utf-8')

    old_imports = '''from core.secret_store import (\n    SecretAccessPolicy,\n    SecretResolutionContext,\n    SecretResolutionError,\n    SecretStoreGate,\n)\nfrom integrations.bitwarden_secret_store import (\n    BwsCliSecretsManagerStore,\n    bitwarden_reference_from_systemd_credential,\n)\n'''
    new_imports = '''from integrations.credential_runtime import (\n    RegisteredCredentialRuntimeError,\n    bind_registered_environment_credential,\n)\n'''
    if old_imports not in text:
        raise BootstrapError('runner_import_preimage_mismatch')
    text = text.replace(old_imports, new_imports, 1)

    old_constants = '''_OPENROUTER_SECRET_REF_CREDENTIAL = "openrouter-secret-ref"\n_OPENROUTER_FREE_MODEL = "openrouter/z-ai/glm-4.5-air:free"\n_RUNNER_MACHINE_IDENTITY = "hetzner-agent-runner-1"\n_OPENROUTER_AUDIENCE = "openhands-openrouter"\n_OPENROUTER_TASK_KIND = "code_generation"\n'''
    new_constants = '''_OPENROUTER_FREE_MODEL = "openrouter/z-ai/glm-4.5-air:free"\n_RUNNER_CREDENTIAL_SERVICE = "runner-openhands"\n_RUNNER_OPENROUTER_ALIAS = "openrouter-api"\n_RUNNER_OPENROUTER_ACTION = "bind-openrouter-fallback"\n'''
    if old_constants not in text:
        raise BootstrapError('runner_constant_preimage_mismatch')
    text = text.replace(old_constants, new_constants, 1)

    old_function = '''def _bind_trusted_openrouter(\n    environment: dict[str, str],\n    authority_environment: Mapping[str, str],\n) -> bool:\n    for name in _PROVIDER_OVERRIDE_ENV:\n        environment.pop(name, None)\n    try:\n        reference = bitwarden_reference_from_systemd_credential(\n            authority_environment,\n            _OPENROUTER_SECRET_REF_CREDENTIAL,\n        )\n        store = BwsCliSecretsManagerStore.from_systemd_credentials(authority_environment)\n        context = SecretResolutionContext(\n            machine_identity=_RUNNER_MACHINE_IDENTITY,\n            audience=_OPENROUTER_AUDIENCE,\n            task_kind=_OPENROUTER_TASK_KIND,\n        )\n        policy = SecretAccessPolicy(\n            allowed_machine_identities=frozenset({_RUNNER_MACHINE_IDENTITY}),\n            allowed_audiences=frozenset({_OPENROUTER_AUDIENCE}),\n            allowed_task_kinds=frozenset({_OPENROUTER_TASK_KIND}),\n        )\n        gate = SecretStoreGate(\n            stores={"bitwarden": store},\n            policies={(reference.provider, reference.reference_id): policy},\n        )\n        material = gate.resolve(reference, context)\n        bound = material.inject(environment, _OPENROUTER_FALLBACK_KEY_ENV)\n    except SecretResolutionError:\n        return False\n    environment.clear()\n    environment.update(bound)\n    environment[_OPENROUTER_FALLBACK_MODEL_ENV] = _OPENROUTER_FREE_MODEL\n    return True\n'''
    new_function = '''def _bind_trusted_openrouter(\n    environment: dict[str, str],\n    authority_environment: Mapping[str, str],\n) -> bool:\n    for name in _PROVIDER_OVERRIDE_ENV:\n        environment.pop(name, None)\n    try:\n        receipt = bind_registered_environment_credential(\n            service_id=_RUNNER_CREDENTIAL_SERVICE,\n            alias=_RUNNER_OPENROUTER_ALIAS,\n            action_id=_RUNNER_OPENROUTER_ACTION,\n            environment=environment,\n            authority_environment=authority_environment,\n        )\n    except RegisteredCredentialRuntimeError:\n        return False\n    result = receipt.get("result")\n    if not isinstance(result, Mapping) or result.get("status") != "USED":\n        return False\n    environment[_OPENROUTER_FALLBACK_MODEL_ENV] = _OPENROUTER_FREE_MODEL\n    return True\n'''
    if old_function not in text:
        raise BootstrapError('runner_binding_preimage_mismatch')
    text = text.replace(old_function, new_function, 1)
    path.write_text(text, encoding='utf-8')


def create_files() -> None:
    write('integrations/credential_runtime.py', r'''
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
    ''')

    write('adapters/credential_mcp.py', r'''
        from __future__ import annotations

        from collections.abc import Mapping

        from adapters.credential_control import CredentialControlAdapter


        MCP_SCHEMA = "skeleton.credential_mcp.v1"


        def credential_mcp_tool_specs() -> tuple[dict[str, object], ...]:
            alias_input = {
                "type": "object",
                "properties": {"alias": {"type": "string"}},
                "required": ["alias"],
                "additionalProperties": False,
            }
            use_input = {
                "type": "object",
                "properties": {
                    "alias": {"type": "string"},
                    "action_id": {"type": "string"},
                },
                "required": ["alias", "action_id"],
                "additionalProperties": False,
            }
            return (
                {
                    "name": "credential_probe",
                    "description": "Probe one logical credential alias; returns status only.",
                    "inputSchema": alias_input,
                },
                {
                    "name": "credential_find",
                    "description": "Find one registered logical credential alias; returns status only.",
                    "inputSchema": alias_input,
                },
                {
                    "name": "credential_use",
                    "description": "Execute one pre-registered credential action; never returns the secret.",
                    "inputSchema": use_input,
                },
            )


        class CredentialMcpAdapter:
            """Transport-neutral MCP surface around one already service-bound control adapter."""

            def __init__(self, control: CredentialControlAdapter) -> None:
                if not isinstance(control, CredentialControlAdapter):
                    raise TypeError("typed_credential_control_required")
                self._control = control

            @property
            def service_id(self) -> str:
                return self._control.service_id

            def list_tools(self) -> tuple[dict[str, object], ...]:
                return credential_mcp_tool_specs()

            def call_tool(
                self,
                name: str,
                arguments: Mapping[str, object],
            ) -> dict[str, object]:
                if name not in {"credential_probe", "credential_find", "credential_use"}:
                    return {
                        "schema": MCP_SCHEMA,
                        "service_id": self.service_id,
                        "result": {
                            "status": "BLOCKED",
                            "reason_class": "UNSUPPORTED_TOOL",
                        },
                    }
                if not isinstance(arguments, Mapping):
                    return {
                        "schema": MCP_SCHEMA,
                        "service_id": self.service_id,
                        "result": {
                            "status": "BLOCKED",
                            "reason_class": "INVALID_ARGUMENTS",
                        },
                    }
                return self._control.invoke(name, arguments)
    ''')

    write('adapters/chatgpt/CREDENTIAL_CONTROL_REGISTRATION.json', r'''
        {
          "schema": "skeleton.credential_control.registration.v1",
          "adapter_module": "adapters.credential_mcp",
          "runtime_binding": "service_bound",
          "operations": [
            "credential_probe",
            "credential_find",
            "credential_use"
          ],
          "caller_can_select_service_id": false,
          "caller_can_select_command": false,
          "caller_can_select_environment": false,
          "caller_can_read_secret_value": false,
          "secret_values_in_receipts": false,
          "external_connector_registration": "required"
        }
    ''')

    write('tests/test_registered_credential_runtime.py', r'''
        from __future__ import annotations

        import json

        import pytest

        from core.secret_store import ResolvedSecret, SecretReference, SecretResolutionContext
        from integrations import bitwarden_credential_runtime as bitwarden_runtime
        from integrations import credential_runtime


        SYNTHETIC_SECRET = "synthetic-openrouter-value"


        class FakeStore:
            provider = "bitwarden"

            def __init__(self) -> None:
                self.calls: list[str] = []

            def resolve(
                self,
                reference: SecretReference,
                context: SecretResolutionContext,
            ) -> ResolvedSecret:
                assert context.machine_identity == "hetzner-agent-runner-1"
                assert context.audience == "openhands-openrouter"
                assert context.task_kind == "code_generation"
                self.calls.append(reference.reference_id)
                return ResolvedSecret(SYNTHETIC_SECRET)


        def _install_fake_provider(monkeypatch):
            store = FakeStore()
            reference_calls: list[str] = []

            def fake_reference(_authority, credential_name: str) -> SecretReference:
                reference_calls.append(credential_name)
                return SecretReference(provider="bitwarden", reference_id="synthetic-ref")

            monkeypatch.setattr(
                credential_runtime,
                "bitwarden_reference_from_systemd_credential",
                fake_reference,
            )
            monkeypatch.setattr(
                bitwarden_runtime.BwsCliSecretsManagerStore,
                "from_systemd_credentials",
                classmethod(lambda cls, authority: store),
            )
            return store, reference_calls


        def test_runner_openhands_uses_registered_broker_binding(monkeypatch) -> None:
            store, reference_calls = _install_fake_provider(monkeypatch)
            environment = {"PATH": "/synthetic/bin", "UNRELATED": "keep"}

            receipt = credential_runtime.bind_registered_environment_credential(
                service_id="runner-openhands",
                alias="openrouter-api",
                action_id="bind-openrouter-fallback",
                environment=environment,
                authority_environment={},
            )

            assert receipt["result"]["status"] == "USED"
            assert environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == SYNTHETIC_SECRET
            assert environment["UNRELATED"] == "keep"
            assert reference_calls == ["openrouter-secret-ref"]
            assert store.calls == ["synthetic-ref"]
            assert SYNTHETIC_SECRET not in json.dumps(receipt, sort_keys=True)


        def test_unregistered_action_rejected_before_provider_resolution(monkeypatch) -> None:
            provider_calls: list[bool] = []
            monkeypatch.setattr(
                credential_runtime,
                "bitwarden_reference_from_systemd_credential",
                lambda *_args, **_kwargs: provider_calls.append(True),
            )

            with pytest.raises(
                credential_runtime.RegisteredCredentialRuntimeError,
                match="registered_credential_action_mismatch",
            ):
                credential_runtime.bind_registered_environment_credential(
                    service_id="runner-openhands",
                    alias="openrouter-api",
                    action_id="arbitrary-shell",
                    environment={},
                    authority_environment={},
                )

            assert provider_calls == []


        def test_unregistered_service_rejected_before_provider_resolution(monkeypatch) -> None:
            provider_calls: list[bool] = []
            monkeypatch.setattr(
                credential_runtime,
                "bitwarden_reference_from_systemd_credential",
                lambda *_args, **_kwargs: provider_calls.append(True),
            )

            with pytest.raises(
                credential_runtime.RegisteredCredentialRuntimeError,
                match="registered_credential_unavailable",
            ):
                credential_runtime.bind_registered_environment_credential(
                    service_id="other-service",
                    alias="openrouter-api",
                    action_id="bind-openrouter-fallback",
                    environment={},
                    authority_environment={},
                )

            assert provider_calls == []


        def test_registration_metadata_is_public_safe() -> None:
            capabilities = credential_runtime.registered_credential_capabilities()
            serialized = json.dumps(capabilities, sort_keys=True)
            assert "runner-openhands" in serialized
            assert "openrouter-api" in serialized
            assert "openrouter-secret-ref" not in serialized
            assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in serialized
    ''')

    write('tests/test_credential_mcp.py', r'''
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
    ''')

    write('tests/test_runner_credential_binding.py', r'''
        from __future__ import annotations

        from pathlib import Path

        import core.runner_child_environment as child_env


        def test_runner_openrouter_binding_uses_registered_credential_runtime(monkeypatch) -> None:
            observed: dict[str, object] = {}

            def fake_bind(**kwargs):
                observed.update(kwargs)
                kwargs["environment"]["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "synthetic"
                return {"result": {"status": "USED"}}

            monkeypatch.setattr(
                child_env,
                "bind_registered_environment_credential",
                fake_bind,
            )
            environment = {
                "BWS_ACCESS_TOKEN": "caller-must-not-win",
                "OPENROUTER_API_KEY": "caller-must-not-win",
                "UNRELATED": "keep",
            }
            authority = {"HOME": "/trusted", "PATH": "/trusted/bin"}

            assert child_env._bind_trusted_openrouter(environment, authority) is True
            assert observed["service_id"] == "runner-openhands"
            assert observed["alias"] == "openrouter-api"
            assert observed["action_id"] == "bind-openrouter-fallback"
            assert observed["authority_environment"] is authority
            assert "BWS_ACCESS_TOKEN" not in observed["environment"]
            assert "OPENROUTER_API_KEY" not in observed["environment"]
            assert environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == "synthetic"
            assert environment["SKELETON_OPENROUTER_FALLBACK_MODEL"].startswith("openrouter/")
            assert environment["UNRELATED"] == "keep"


        def test_runner_consumer_has_no_direct_bitwarden_or_secretstore_resolution_imports() -> None:
            source = Path(child_env.__file__).read_text(encoding="utf-8")
            assert "BwsCliSecretsManagerStore" not in source
            assert "bitwarden_reference_from_systemd_credential" not in source
            assert "SecretStoreGate" not in source
            assert "SecretAccessPolicy" not in source


        def test_registered_credential_failure_keeps_openhands_binding_fail_closed(monkeypatch) -> None:
            def fail(**_kwargs):
                raise child_env.RegisteredCredentialRuntimeError("synthetic-failure")

            monkeypatch.setattr(child_env, "bind_registered_environment_credential", fail)
            environment = {"UNRELATED": "keep"}

            assert child_env._bind_trusted_openrouter(environment, {}) is False
            assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in environment
            assert "SKELETON_OPENROUTER_FALLBACK_MODEL" not in environment
            assert environment["UNRELATED"] == "keep"
    ''')

    write('docs/CREDENTIAL_CONTROL_MCP.md', r'''
        # Skeleton credential control boundary

        The canonical runtime path is:

        `service -> registered alias/action -> CredentialControlAdapter -> CredentialBroker -> SecretStoreGate -> provider -> registered target`

        `adapters.credential_mcp.CredentialMcpAdapter` is the transport-neutral server-side MCP boundary. It receives an already service-bound `CredentialControlAdapter`; therefore the caller cannot select or spoof the service identity.

        Exposed operations are exactly `credential_probe`, `credential_find`, and `credential_use`. Their input schemas do not contain provider commands, executable paths, environment-variable names, hosts, output destinations, or a secret-value field. `credential_use` can only execute the action already registered in the underlying `ServiceCredentialBinding`.

        The current Runner/OpenHands OpenRouter credential is registered in `integrations.credential_runtime` and the Runner consumer imports that provider-neutral registered-credential surface. Bitwarden-specific token/reference handling remains behind the integration layer and the shared `CredentialBroker` path.

        Repository-side callable contract is complete after this change. The ChatGPT registration descriptor is `adapters/chatgpt/CREDENTIAL_CONTROL_REGISTRATION.json`. The repository does not own registration of a new external ChatGPT connector in the product runtime, so the remaining external step after merge/runtime validation is exactly:

        `CONNECTOR_REGISTRATION_REQUIRED`

        Secret values must never appear in connector responses, GitHub receipts, logs, repr output, or caller-selected destinations.
    ''')


def validate() -> tuple[int, int]:
    changed = set(git('diff', '--name-only', BASE_SHA, cwd=WORKTREE).stdout.splitlines())
    if changed != EXPECTED_FILES:
        raise BootstrapError('changed_file_scope_mismatch:' + ','.join(sorted(changed)))

    env = clean_test_env()
    focused = [
        sys.executable,
        '-m',
        'pytest',
        '-q',
        'tests/test_registered_credential_runtime.py',
        'tests/test_credential_mcp.py',
        'tests/test_runner_credential_binding.py',
        'tests/test_runner_child_environment.py',
        'tests/test_credential_broker.py',
        'tests/test_bitwarden_credential_runtime.py',
    ]
    p = run(focused, cwd=WORKTREE, env=env, timeout=900, check=False)
    if p.returncode != 0:
        failed = ';'.join(
            line.split(' - ', 1)[0].replace('FAILED ', '').strip()
            for line in (p.stdout + '\n' + p.stderr).splitlines()
            if line.startswith('FAILED ')
        )[:1200]
        raise BootstrapError('focused_tests_failed:' + (failed or 'collection_or_unknown'))

    full = run([sys.executable, '-m', 'pytest', '-q'], cwd=WORKTREE, env=env, timeout=1800, check=False)
    if full.returncode != 0:
        failed = ';'.join(
            line.split(' - ', 1)[0].replace('FAILED ', '').strip()
            for line in (full.stdout + '\n' + full.stderr).splitlines()
            if line.startswith('FAILED ')
        )[:1200]
        raise BootstrapError('full_tests_failed:' + (failed or 'collection_or_unknown'))

    py_files = sorted(str(WORKTREE / path) for path in EXPECTED_FILES if path.endswith('.py'))
    run([sys.executable, '-m', 'py_compile', *py_files], cwd=WORKTREE, env=env, timeout=120)
    json.loads((WORKTREE / 'adapters/chatgpt/CREDENTIAL_CONTROL_REGISTRATION.json').read_text(encoding='utf-8'))
    run(['git', 'diff', '--check', BASE_SHA], cwd=WORKTREE, env=env, timeout=120)

    def totals(text: str) -> tuple[int, int]:
        m_pass = re.search(r'(\d+) passed', text)
        m_skip = re.search(r'(\d+) skipped', text)
        return (int(m_pass.group(1)) if m_pass else 0, int(m_skip.group(1)) if m_skip else 0)

    return totals(full.stdout + '\n' + full.stderr)


def publish_blocked(reason: str) -> None:
    safe = reason.replace('`', '').replace('\n', ' ')[:1600]
    body = (
        '### #2834 deterministic production activation receipt\n\n'
        '```text\n'
        'STATUS=BLOCKED\n'
        f'BASE={BASE_SHA}\n'
        f'REASON={safe}\n'
        'SECRET_VALUES_PUBLISHED=NO\n'
        '```\n'
    )
    run(['gh', 'issue', 'comment', ISSUE, '--repo', REPO_FULL, '--body', body], check=False, timeout=60)


def publish_ready(head: str, pr_url: str, passed: int, skipped: int) -> None:
    body = (
        '### #2834 deterministic production activation receipt\n\n'
        '```text\n'
        'STATUS=PR_READY\n'
        f'BASE={BASE_SHA}\n'
        f'HEAD={head}\n'
        f'FULL_PYTEST={passed}_passed,{skipped}_skipped\n'
        'FOCUSED_TESTS=PASS\n'
        'PY_COMPILE=PASS\n'
        'JSON_DESCRIPTOR=PASS\n'
        'DIFF_CHECK=PASS\n'
        'PROTECTED_FILES=core/runner_child_environment.py\n'
        'RUNNER_OPENHANDS_SHARED_BROKER=IMPLEMENTED\n'
        'SERVER_SIDE_CREDENTIAL_MCP=IMPLEMENTED\n'
        'CONNECTOR_STATUS=CONNECTOR_REGISTRATION_REQUIRED\n'
        'SECRET_VALUES_PUBLISHED=NO\n'
        f'PR_URL={pr_url}\n'
        '```\n'
    )
    run(['gh', 'issue', 'comment', ISSUE, '--repo', REPO_FULL, '--body', body], check=False, timeout=60)


def main() -> int:
    result = 'BLOCKED'
    try:
        preflight()
        prepare_worktree()
        patch_runner_child_environment()
        create_files()
        passed, skipped = validate()
        git('add', *sorted(EXPECTED_FILES), cwd=WORKTREE)
        git('commit', '-m', 'P0 activate universal SecretStore production binding', cwd=WORKTREE)
        head = git('rev-parse', 'HEAD', cwd=WORKTREE).stdout.strip()
        git('push', 'origin', f'HEAD:refs/heads/{BRANCH}', cwd=WORKTREE, timeout=300)

        existing = run(
            ['gh', 'pr', 'list', '--repo', REPO_FULL, '--head', BRANCH, '--state', 'open', '--json', 'url', '--jq', '.[0].url // empty'],
            check=False,
            timeout=60,
        ).stdout.strip()
        if existing:
            pr_url = existing
        else:
            pr = run(
                [
                    'gh', 'pr', 'create', '--repo', REPO_FULL, '--draft', '--base', 'main', '--head', BRANCH,
                    '--title', 'P0 activate universal SecretStore production binding',
                    '--body',
                    'Implements #2834 from exact main `a08a3922ac7e01c32226bb193a6f072c4662a81f`.\n\n'
                    'Migrates Runner/OpenHands credential injection onto the shared ServiceCredentialBinding/CredentialBroker path and adds a service-bound transport-neutral credential MCP adapter plus ChatGPT registration descriptor. No secret values are returned or persisted.\n\n'
                    'Protected file touched: `core/runner_child_environment.py`; exact-head operator approval is required before merge.\n\n'
                    'Server-side callable boundary is implemented; external ChatGPT product connector registration remains `CONNECTOR_REGISTRATION_REQUIRED`.\n\n'
                    'No runtime/provider/credential mutation performed by this PR.',
                ],
                check=True,
                timeout=60,
            )
            pr_url = pr.stdout.strip().splitlines()[-1]
        publish_ready(head, pr_url, passed, skipped)
        print('RESULT=PR_READY')
        print('HEAD_SHA=' + head)
        print('PR_URL=' + pr_url)
        print('CONNECTOR_STATUS=CONNECTOR_REGISTRATION_REQUIRED')
        result = 'PR_READY'
    except BootstrapError as exc:
        publish_blocked(str(exc))
        print('RESULT=BLOCKED:' + str(exc).split(':', 1)[0])
    except Exception as exc:
        publish_blocked(type(exc).__name__)
        print('RESULT=BLOCKED:' + type(exc).__name__)
    finally:
        try:
            git('worktree', 'remove', '--force', str(WORKTREE), check=False)
            git('worktree', 'prune', check=False)
        except Exception:
            pass
    return 0 if result in {'PR_READY', 'BLOCKED'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
