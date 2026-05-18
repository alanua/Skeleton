from pathlib import Path

import yaml

from core.capability_checker import CapabilityChecker


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "CAPABILITY_REGISTRY.yaml"


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


def test_registry_has_all_adapter_contracts_available() -> None:
    adapter_contracts = load_registry()["capabilities"]["adapter_contracts"]
    assert adapter_contracts["status"] == "available"
    assert adapter_contracts["module"] == "adapters/"


def test_registry_has_planned_future_capabilities() -> None:
    capabilities = load_registry()["capabilities"]
    for capability_id in ("boot_loader", "project_loader", "runner_bridge", "memory_manager"):
        assert capabilities[capability_id]["status"] == "planned"


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
    assert "boot_loader" in planned


def test_capability_checker_is_available() -> None:
    checker = CapabilityChecker(REGISTRY_PATH)
    assert checker.is_available("write_gate") is True


def test_capability_checker_unknown_is_not_available() -> None:
    checker = CapabilityChecker(REGISTRY_PATH)
    assert checker.is_available("missing_capability") is False
