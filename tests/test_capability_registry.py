from pathlib import Path

import yaml

from core.capability_checker import CapabilityChecker


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "CAPABILITY_REGISTRY.yaml"
RUNTIME_TRUTH_DOC_PATH = ROOT / "docs" / "CAPABILITY_RUNTIME_TRUTH.md"


def load_registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.is_file()
    assert not (ROOT / "skeleton" / "CAPABILITY_REGISTRY.yaml").exists()


def test_registry_has_version_and_capabilities() -> None:
    registry = load_registry()
    assert registry["version"] == "1.0.0"
    assert isinstance(registry["capabilities"], dict)
    assert registry["capabilities"]


def test_registry_has_write_gate_available() -> None:
    write_gate = load_registry()["capabilities"]["write_gate"]
    assert write_gate["status"] == "available"
    assert write_gate["module"] == "core/gate_engine.py"


def test_registry_has_boot_loader_available() -> None:
    boot_loader = load_registry()["capabilities"]["boot_loader"]
    assert boot_loader["status"] == "available"
    assert boot_loader["module"] == "core/boot_loader.py"
    assert boot_loader["tested"] is True


def test_registry_has_project_loader_available() -> None:
    project_loader = load_registry()["capabilities"]["project_loader"]
    assert project_loader["status"] == "available"
    assert project_loader["module"] == "core/project_loader.py"
    assert project_loader["tested"] is True


def test_registry_has_all_adapter_contracts_available() -> None:
    adapter_contracts = load_registry()["capabilities"]["adapter_contracts"]
    assert adapter_contracts["status"] == "available"
    assert adapter_contracts["module"] == "adapters/"


def test_registry_has_stage1_dry_run_capabilities() -> None:
    capabilities = load_registry()["capabilities"]
    runner_bridge = capabilities["runner_bridge"]
    assert runner_bridge["status"] == "stage1_dry_run"
    assert runner_bridge["tested"] is True
    assert runner_bridge["live_runtime_execution"] is False

    memory_manager = capabilities["memory_manager"]
    assert memory_manager["status"] == "stage1_dry_run"
    assert memory_manager["tested"] is True
    assert memory_manager["live_runtime_execution"] is False


def test_registry_has_current_truth_awareness_and_intake_primitives() -> None:
    capabilities = load_registry()["capabilities"]

    expected = {
        "capability_runtime_truth": {
            "module": "core/capability_runtime_truth.py",
            "entry": "reconcile_capability_runtime_truth",
        },
        "awareness_context": {
            "module": "core/awareness_context.py",
            "entry": "assemble_awareness_context",
        },
        "awareness_hydrator": {
            "module": "core/awareness_hydrator.py",
            "entry": "hydrate_awareness_context",
        },
        "intake_lifecycle": {
            "module": "core/intake_lifecycle.py",
            "entry": "IntakeLifecycleStore",
        },
        "knowledge_intake_review_queue": {
            "module": "projects/skeleton/REVIEW_QUEUE.yaml",
        },
    }

    for capability_id, expectation in expected.items():
        capability = capabilities[capability_id]
        assert capability["status"] == "available"
        assert capability["module"] == expectation["module"]
        assert capability["tested"] is True
        assert capability["live_runtime_execution"] is False
        if "entry" in expectation:
            assert capability["entry"] == expectation["entry"]


def test_registry_does_not_declare_runtime_truth_or_freshness() -> None:
    forbidden_static_runtime_fields = {
        "effective_status",
        "freshness",
        "runtime_bound",
        "source_runtime_parity",
        "runtime_evidence",
        "evidence_observed_at",
        "evidence_kinds",
        "drift",
    }

    for capability_id, capability in load_registry()["capabilities"].items():
        leaked = forbidden_static_runtime_fields & set(capability)
        assert not leaked, f"{capability_id}: {sorted(leaked)}"
        assert capability.get("status") != "LIVE"


def test_capability_runtime_truth_doc_preserves_boundary() -> None:
    doc = " ".join(RUNTIME_TRUTH_DOC_PATH.read_text(encoding="utf-8").split())

    for phrase in [
        "CAPABILITY_REGISTRY.yaml is a static source declaration",
        "does not declare LIVE",
        "does not declare FRESH",
        "Only typed runtime evidence can produce LIVE",
        "Only typed runtime evidence can produce FRESH",
        "runtime_probe_performed: false",
        "runtime_mutation_performed: false",
    ]:
        assert phrase in doc


def test_registry_has_planned_future_capabilities() -> None:
    capabilities = load_registry()["capabilities"]
    assert capabilities["memory_manager_live_storage"]["status"] == "planned"


def test_no_available_capability_without_module_field() -> None:
    for capability in load_registry()["capabilities"].values():
        if capability["status"] == "available":
            assert capability.get("module")


def test_available_capability_module_paths_exist_on_disk() -> None:
    for capability in load_registry()["capabilities"].values():
        if capability["status"] != "available":
            continue

        module = capability["module"]
        module_path = ROOT / module

        if module.endswith("/"):
            assert module_path.is_dir(), module
        else:
            assert module_path.is_file(), module


def test_available_capability_requires_paths_exist_on_disk() -> None:
    write_gate = load_registry()["capabilities"]["write_gate"]
    assert "schemas/patch_plan.schema.json" in write_gate["requires"]
    assert "core/patch_validator.py" in write_gate["requires"]

    for capability in load_registry()["capabilities"].values():
        if capability["status"] != "available":
            continue

        for required_path in capability.get("requires", []):
            assert (ROOT / required_path).exists(), required_path


def test_planned_capability_module_paths_may_be_missing() -> None:
    planned = {
        capability_id: capability
        for capability_id, capability in load_registry()["capabilities"].items()
        if capability["status"] == "planned"
    }

    assert planned
    assert any(not (ROOT / capability["module"]).exists() for capability in planned.values())


def test_capability_checker_loads_registry() -> None:
    registry = CapabilityChecker(REGISTRY_PATH).load()
    assert registry["version"] == "1.0.0"


def test_capability_checker_available_list_non_empty() -> None:
    available = CapabilityChecker(REGISTRY_PATH).available()
    assert "write_gate" in available


def test_capability_checker_planned_list_non_empty() -> None:
    planned = CapabilityChecker(REGISTRY_PATH).planned()
    assert "memory_manager_live_storage" in planned
    assert "runner_bridge" not in planned
    assert "memory_manager" not in planned


def test_capability_checker_is_available() -> None:
    checker = CapabilityChecker(REGISTRY_PATH)
    assert checker.is_available("write_gate") is True
    assert checker.is_available("project_loader") is True


def test_capability_checker_unknown_is_not_available() -> None:
    checker = CapabilityChecker(REGISTRY_PATH)
    assert checker.is_available("missing_capability") is False
