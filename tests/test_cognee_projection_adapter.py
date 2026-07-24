from __future__ import annotations

import inspect
import json
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

from core.cognee_projection_adapter import (
    CogneePackageBackend,
    CogneeProjectionAdapter,
    DisposableInMemoryCogneeBackend,
)
from core.cognee_local_runtime import (
    CogneeLocalRuntimeError,
    CogneePackageFacade,
    validate_local_provider_config,
)
from core.semantic_memory_projection import (
    COGNEE_DEPENDENCY_UNAVAILABLE,
    COGNEE_RUNTIME_NOT_IMPLEMENTED,
    CROSS_PROJECT_RECALL_FORBIDDEN,
    PROJECTION_STALE,
    SEMANTIC_PROJECTION_EVENT_SCHEMA,
    SEMANTIC_RECALL_REQUEST_SCHEMA,
    SemanticProjectionError,
    SemanticProjectionHealth,
    SemanticProjectionProtocol,
    SemanticRecallResult,
    canonical_json_hash,
    projection_text_hash,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATHS = [
    ROOT / "schemas" / "semantic_memory_projection_event.schema.json",
    ROOT / "schemas" / "semantic_memory_recall_request.schema.json",
    ROOT / "schemas" / "semantic_memory_recall_response.schema.json",
    ROOT / "schemas" / "semantic_memory_projection_receipt.schema.json",
]
PROJECT_ID = "synthetic_project"
DATASET_ID = "dataset_phase_0"


def event(
    text: str = "Українська пам'ять und deutsche Wörter bleiben erhalten.",
    *,
    canonical_revision: int = 1,
    canonical_ref: str = "canonical.fact.001",
    canonical_content: object | None = None,
) -> dict[str, object]:
    if canonical_content is None:
        canonical_content = {"fact": "separate canonical value", "lang": "uk-de"}
    return {
        "schema": SEMANTIC_PROJECTION_EVENT_SCHEMA,
        "project_id": PROJECT_ID,
        "dataset_id": DATASET_ID,
        "canonical_revision": canonical_revision,
        "canonical_ref": canonical_ref,
        "content_hash": canonical_json_hash(canonical_content),
        "projection_text_hash": projection_text_hash(text),
        "bounded_text": text,
        "provenance": [{"kind": "synthetic_fixture", "source_ref": "fixture.001"}],
    }


def recall_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema": SEMANTIC_RECALL_REQUEST_SCHEMA,
        "project_id": PROJECT_ID,
        "dataset_id": DATASET_ID,
        "query": "deutsche Wörter",
        "current_canonical_revision": 1,
        "limit": 5,
    }
    request.update(overrides)
    return request


def test_unicode_projection_text_accepted_without_lexical_filtering() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    german = "Maßänderung für Türen, größere Öffnung, Straße und Übergang."
    ukrainian = "Пам'ять проекту зберігає українські слова без фільтрації."

    receipt_de = adapter.project(event(german))
    receipt_uk = adapter.project({**event(ukrainian), "canonical_ref": "canonical.fact.002", "canonical_revision": 2})

    assert receipt_de["status"] == "PROJECTED"
    assert receipt_uk["status"] == "PROJECTED"
    assert receipt_de["hashes"]["projection_text_hashes"] == [projection_text_hash(german)]
    assert receipt_uk["hashes"]["projection_text_hashes"] == [projection_text_hash(ukrainian)]


def test_control_and_oversize_projection_text_rejected() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())

    with pytest.raises(SemanticProjectionError) as control:
        adapter.project(event("valid prefix\u0007 invalid bell"))
    assert control.value.reason_code == "TEXT_CONTROL_CHARACTERS_REJECTED"

    with pytest.raises(SemanticProjectionError) as oversize:
        adapter.project(event("x" * 4097))
    assert oversize.value.reason_code == "INVALID_TEXT"


def test_canonical_content_hash_may_differ_from_projection_text_hash() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    payload = event()

    assert payload["content_hash"] != payload["projection_text_hash"]
    receipt = adapter.project(payload)
    response = adapter.recall(recall_request(query="Українська"))

    assert receipt["hashes"]["content_hashes"] == [payload["content_hash"]]
    assert response["results"][0]["content_hash"] == payload["content_hash"]
    assert response["results"][0]["projection_text_hash"] == payload["projection_text_hash"]


def test_historical_positive_revision_facts_remain_recallable_at_current_revision() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    rev1 = event(
        "Berlin keeps the shared historical marker.",
        canonical_revision=1,
        canonical_ref="canonical.fact.001",
        canonical_content={"fact": "berlin"},
    )
    rev2 = event(
        "Munich keeps the shared historical marker.",
        canonical_revision=2,
        canonical_ref="canonical.fact.002",
        canonical_content={"fact": "munich"},
    )

    adapter.project(rev1)
    adapter.project(rev2)
    response = adapter.recall(recall_request(query="shared historical marker", current_canonical_revision=2))

    revisions = {result["canonical_ref"]: result["canonical_revision"] for result in response["results"]}
    assert response["indexed_canonical_revision"] == 2
    assert response["current_canonical_revision"] == 2
    assert revisions == {"canonical.fact.001": 1, "canonical.fact.002": 2}


def test_public_receipt_omits_raw_scope_refs_text_provenance_and_values() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    payload = event()

    receipt = adapter.project(payload)
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)

    assert PROJECT_ID not in encoded
    assert DATASET_ID not in encoded
    assert payload["canonical_ref"] not in encoded
    assert payload["bounded_text"] not in encoded
    assert "synthetic_fixture" not in encoded
    assert "source_ref" not in encoded
    assert set(receipt) == {
        "schema",
        "status",
        "aggregate_counts",
        "canonical_revisions",
        "hashes",
        "reason_codes",
        "authoritative",
    }


def test_empty_revision_zero_recall_and_health_work() -> None:
    backend = DisposableInMemoryCogneeBackend()
    adapter = CogneeProjectionAdapter(backend)

    health = adapter.health(project_id=PROJECT_ID, dataset_id=DATASET_ID, current_canonical_revision=0)
    response = adapter.recall(recall_request(current_canonical_revision=0))

    assert health["status"] == "READY"
    assert health["canonical_revisions"]["indexed_canonical_revision"] == 0
    assert response["indexed_canonical_revision"] == 0
    assert response["current_canonical_revision"] == 0
    assert response["results"] == []


def test_projection_event_rejects_revision_zero() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())

    with pytest.raises(SemanticProjectionError) as excinfo:
        adapter.project({**event(), "canonical_revision": 0})

    assert excinfo.value.reason_code == "INVALID_CANONICAL_REVISION"


def test_exact_valid_scope_ids_are_not_treated_as_wildcards() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())

    health = adapter.health(project_id="germany", dataset_id="company_private", current_canonical_revision=0)
    request = recall_request(project_id="anydesk_project", dataset_id="company_private", current_canonical_revision=0)
    response = adapter.recall(request)

    assert health["status"] == "READY"
    assert response["project_id"] == "anydesk_project"
    assert response["dataset_id"] == "company_private"


def test_cross_project_and_cross_dataset_wildcards_blocked_by_exact_tokens_only() -> None:
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())

    for invalid_project in ("*", "all", "ALL", "any", "", "project/all", "project\\any", "valid\u0007bell"):
        with pytest.raises(SemanticProjectionError) as wildcard_project:
            adapter.recall(recall_request(project_id=invalid_project))
        assert wildcard_project.value.reason_code == "PROJECT_ID_AMBIGUOUS"

    for invalid_dataset in ("*", "all", "ANY", "", "dataset/all", "dataset\\any", "valid\u0007bell"):
        with pytest.raises(SemanticProjectionError) as wildcard_dataset:
            adapter.recall(recall_request(dataset_id=invalid_dataset))
        assert wildcard_dataset.value.reason_code == "DATASET_ID_AMBIGUOUS"


class ForeignBackend(DisposableInMemoryCogneeBackend):
    def recall(self, request):  # type: ignore[no-untyped-def]
        return (
            SemanticRecallResult(
                canonical_ref="canonical.fact.foreign",
                canonical_revision=request.current_canonical_revision,
                content_hash="1" * 64,
                projection_text_hash="2" * 64,
                score=1.0,
                metadata={"project_id": "foreign", "dataset_id": request.scope.dataset_id, "synthetic": True},
            ),
        )


def test_foreign_backend_result_blocked() -> None:
    adapter = CogneeProjectionAdapter(ForeignBackend())

    with pytest.raises(SemanticProjectionError) as excinfo:
        adapter.recall(recall_request(current_canonical_revision=0))

    assert excinfo.value.reason_code == CROSS_PROJECT_RECALL_FORBIDDEN


@pytest.mark.parametrize(
    ("returned_revision", "reason_code"),
    [
        (0, "INVALID_CANONICAL_REVISION"),
        (-1, "INVALID_CANONICAL_REVISION"),
        ("2", "INVALID_CANONICAL_REVISION"),
        (3, PROJECTION_STALE),
    ],
)
def test_unbound_malformed_and_future_backend_result_revisions_blocked(
    returned_revision: object,
    reason_code: str,
) -> None:
    class BadRevisionBackend(DisposableInMemoryCogneeBackend):
        def recall(self, request):  # type: ignore[no-untyped-def]
            return (
                SemanticRecallResult(
                    canonical_ref="canonical.fact.bad",
                    canonical_revision=returned_revision,  # type: ignore[arg-type]
                    content_hash="1" * 64,
                    projection_text_hash="2" * 64,
                    score=1.0,
                    metadata={
                        "project_id": request.scope.project_id,
                        "dataset_id": request.scope.dataset_id,
                        "synthetic": True,
                    },
                ),
            )

    adapter = CogneeProjectionAdapter(BadRevisionBackend())
    adapter.project(event(canonical_revision=2))

    with pytest.raises(SemanticProjectionError) as excinfo:
        adapter.recall(recall_request(current_canonical_revision=2))

    assert excinfo.value.reason_code == reason_code


def test_stale_recall_blocked_before_backend_use() -> None:
    backend = DisposableInMemoryCogneeBackend()
    adapter = CogneeProjectionAdapter(backend)
    adapter.project(event())

    with pytest.raises(SemanticProjectionError) as excinfo:
        adapter.recall(recall_request(current_canonical_revision=2))

    assert excinfo.value.reason_code == PROJECTION_STALE
    assert backend.recall_calls == 0


def test_optional_dependency_remains_optional_and_runtime_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    probes: list[str] = []

    def unavailable(name: str) -> None:
        probes.append(name)
        return None

    monkeypatch.setattr("core.cognee_projection_adapter.importlib.util.find_spec", unavailable)
    backend = CogneePackageBackend(runtime_enabled=False)
    adapter = CogneeProjectionAdapter(backend)

    health = adapter.health(project_id=PROJECT_ID, dataset_id=DATASET_ID, current_canonical_revision=0)
    assert probes == ["cognee"]
    assert health["reason_codes"] == [COGNEE_DEPENDENCY_UNAVAILABLE]

    with pytest.raises(SemanticProjectionError) as excinfo:
        adapter.project(event())
    assert excinfo.value.reason_code == COGNEE_DEPENDENCY_UNAVAILABLE


def test_dependency_present_runtime_enabled_package_backend_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    probes: list[str] = []

    def available(name: str) -> ModuleSpec:
        probes.append(name)
        return ModuleSpec(name, loader=None)

    monkeypatch.setattr("core.cognee_projection_adapter.importlib.util.find_spec", available)
    backend = CogneePackageBackend(runtime_enabled=True)
    adapter = CogneeProjectionAdapter(backend)

    health = adapter.health(project_id=PROJECT_ID, dataset_id=DATASET_ID, current_canonical_revision=0)
    assert probes == ["cognee"]
    assert health["status"] == "UNAVAILABLE"
    assert health["reason_codes"] == [COGNEE_RUNTIME_NOT_IMPLEMENTED]

    with pytest.raises(SemanticProjectionError) as project_exc:
        adapter.project(event())
    assert project_exc.value.reason_code == COGNEE_RUNTIME_NOT_IMPLEMENTED

    with pytest.raises(SemanticProjectionError) as recall_exc:
        adapter.recall(recall_request(current_canonical_revision=0))
    assert recall_exc.value.reason_code == COGNEE_RUNTIME_NOT_IMPLEMENTED

    with pytest.raises(SemanticProjectionError) as forget_exc:
        adapter.forget_projection(project_id=PROJECT_ID, dataset_id=DATASET_ID)
    assert forget_exc.value.reason_code == COGNEE_RUNTIME_NOT_IMPLEMENTED


def test_local_provider_config_accepts_loopback_and_rejects_cloud_credentials() -> None:
    env = {
        "SKELETON_COGNEE_LLM_ENDPOINT": "http://127.0.0.1:11434",
        "SKELETON_COGNEE_LLM_MODEL": "llama-local",
        "SKELETON_COGNEE_EMBEDDING_ENDPOINT": "http://localhost:11435",
        "SKELETON_COGNEE_EMBEDDING_MODEL": "embed-local",
        "SKELETON_COGNEE_TELEMETRY": "0",
    }

    assert validate_local_provider_config(env).llm_model == "llama-local"

    with pytest.raises(CogneeLocalRuntimeError) as cloud:
        validate_local_provider_config({**env, "SKELETON_COGNEE_LLM_ENDPOINT": "https://api.openai.com/v1"})
    assert cloud.value.reason_code == "non_loopback_provider_endpoint"

    with pytest.raises(CogneeLocalRuntimeError) as credential:
        validate_local_provider_config({**env, "OPENAI_API_KEY": "sk-private"})
    assert credential.value.reason_code == "inherited_model_credentials_rejected"

    with pytest.raises(CogneeLocalRuntimeError) as telemetry:
        validate_local_provider_config({**env, "COGNEE_TELEMETRY": "true"})
    assert telemetry.value.reason_code == "telemetry_rejected"


def test_package_backend_uses_injected_pinned_facade_with_scoped_revision_provenance(tmp_path: Path) -> None:
    class FakeCognee:
        __version__ = "1.4.0"

        def __init__(self) -> None:
            self.project_calls = 0
            self.recall_calls = 0

        def add(self, **_kwargs: object) -> None:
            self.project_calls += 1

        def search(self, **_kwargs: object) -> list[object]:
            self.recall_calls += 1
            return []

    env = {
        "SKELETON_COGNEE_LLM_ENDPOINT": "http://127.0.0.1:11434",
        "SKELETON_COGNEE_LLM_MODEL": "llama-local",
        "SKELETON_COGNEE_EMBEDDING_ENDPOINT": "http://[::1]:11435",
        "SKELETON_COGNEE_EMBEDDING_MODEL": "embed-local",
    }
    fake = FakeCognee()
    backend = CogneePackageBackend(
        private_root=tmp_path,
        runtime_enabled=True,
        facade=CogneePackageFacade(fake),
        env=env,
    )
    adapter = CogneeProjectionAdapter(backend)
    payload = event("local cognee package boundary")

    adapter.project(payload)
    adapter.project(payload)
    response = adapter.recall(recall_request(query="cognee boundary"))

    assert fake.project_calls == 1
    assert fake.recall_calls == 1
    assert response["results"][0]["canonical_ref"] == payload["canonical_ref"]
    assert response["results"][0]["canonical_revision"] == payload["canonical_revision"]
    assert response["results"][0]["content_hash"] == payload["content_hash"]
    assert response["results"][0]["metadata"]["source_kind"] == "cognee"


def test_real_cognee_package_facade_is_version_gated_when_available() -> None:
    cognee = pytest.importorskip("cognee")
    version = str(getattr(cognee, "__version__", ""))
    if version != "1.4.0":
        pytest.skip(f"cognee {version or 'unknown'} is not the pinned compatibility target")

    facade = CogneePackageFacade(cognee)

    assert facade.version == "1.4.0"


def test_improve_absent_and_local_forget_does_not_call_gateway_or_sqlite() -> None:
    backend = DisposableInMemoryCogneeBackend()
    adapter = CogneeProjectionAdapter(backend)
    adapter.project(event())

    assert not hasattr(adapter, "improve")
    receipt = adapter.forget_projection(project_id=PROJECT_ID, dataset_id=DATASET_ID)

    assert receipt["status"] == "FORGOTTEN"
    assert receipt["reason_codes"] == ["ADAPTER_LOCAL_FORGET"]
    assert not hasattr(adapter, "_memory_gateway")
    assert not hasattr(adapter, "_sqlite")
    assert adapter.health(project_id=PROJECT_ID, dataset_id=DATASET_ID, current_canonical_revision=0)["status"] == "READY"


def test_public_protocol_signatures_match_adapter_interface() -> None:
    protocol_methods = ("project", "recall", "health", "forget_projection")

    for method_name in protocol_methods:
        protocol_signature = inspect.signature(getattr(SemanticProjectionProtocol, method_name))
        adapter_signature = inspect.signature(getattr(CogneeProjectionAdapter, method_name))
        assert str(protocol_signature) == str(adapter_signature)


def test_schema_files_are_valid_and_validate_public_private_payloads() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    projected = event()
    receipt = adapter.project(projected)
    response = adapter.recall(recall_request(query="deutsche"))

    for schema_path in SCHEMA_PATHS:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)

    jsonschema.Draft202012Validator(json.loads(SCHEMA_PATHS[0].read_text(encoding="utf-8"))).validate(projected)
    jsonschema.Draft202012Validator(json.loads(SCHEMA_PATHS[1].read_text(encoding="utf-8"))).validate(recall_request())
    jsonschema.Draft202012Validator(json.loads(SCHEMA_PATHS[2].read_text(encoding="utf-8"))).validate(response)
    jsonschema.Draft202012Validator(json.loads(SCHEMA_PATHS[3].read_text(encoding="utf-8"))).validate(receipt)
