from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from core.home_edge import esp_lab
from core.home_edge import esp_lab_activation
from core.home_edge import esp_lab_stage1_signer_install


READINESS_SCHEMA: Final = "skeleton.home_edge.esp_lab_readiness.v1"
REPOSITORY: Final = "alanua/Skeleton"

STAGE_FILES: Final[Mapping[str, tuple[str, ...]]] = {
    "read_only_lab": (
        "core/home_edge/esp_lab.py",
        "schemas/home_edge_esp_lab_job.schema.json",
        "schemas/home_edge_esp_lab_observation.schema.json",
        "schemas/home_edge_esp_lab_receipt.schema.json",
        "scripts/install_home_edge_esp_lab.sh",
    ),
    "windows_stage_b_connector": (
        "core/home_edge/esp_lab_connector.py",
        "scripts/home_edge_esp_lab_windows_connector.py",
        "scripts/espconnect_windows_stage_b_serve.ps1",
        "scripts/espconnect_windows_stage_b_install.ps1",
        "schemas/home_edge_esp_lab_connector_job.schema.json",
        "schemas/home_edge_esp_lab_connector_receipt.schema.json",
    ),
}

SIGNER_INSTALL_FILES: Final[tuple[str, ...]] = (
    "core/home_edge/esp_lab_stage1_signer_install.py",
    "scripts/install_home_edge_esp_lab_activation_signer.sh",
    "scripts/home_edge_esp_lab_activation_signer",
    "scripts/home_edge_esp_lab_activation_signer_payload.py",
    "scripts/install_home_edge_esp_lab.sh",
)

ACTIVATION_FILES: Final[tuple[str, ...]] = (
    "core/home_edge/esp_lab_activation.py",
    "core/home_edge/executor.py",
    "core/home_edge/executor_gateway.py",
    "scripts/home_edge_esp_lab_activation_signer",
    "scripts/home_edge_esp_lab_activation_signer_payload.py",
)

SIGNER_INSTALL_CONSTANTS: Final[tuple[str, ...]] = (
    "HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID",
    "HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL",
    "HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH",
    "HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB",
    "HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB",
    "HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB",
    "HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB",
)

ACTIVATION_CONSTANTS: Final[tuple[str, ...]] = (
    "TASK_ID",
    "TARGET_NODE",
    "EXECUTION_LANE",
    "RUN_AS",
    "OPERATOR_APPROVAL_REF",
    "APPROVED_SOURCE_SHA",
    "INSTALLER_REPO_PATH",
    "INSTALLER_GIT_BLOB_SHA",
    "ESP_MODULE_REPO_PATH",
    "ESP_MODULE_GIT_BLOB_SHA",
    "PAYLOAD_SCHEMA",
    "RESULT_SCHEMA",
)


def build_readiness_report(repo_root: str | Path | None = None) -> dict[str, object]:
    root = Path.cwd() if repo_root is None else Path(repo_root)
    blockers: list[str] = []
    stage_availability = {
        stage: _files_present(root, files, blockers, f"missing_stage_file:{stage}:")
        for stage, files in STAGE_FILES.items()
    }

    signer_install_present = _files_present(
        root,
        SIGNER_INSTALL_FILES,
        blockers,
        "missing_signer_install_file:",
    ) and _constants_present(
        esp_lab_stage1_signer_install,
        SIGNER_INSTALL_CONSTANTS,
        blockers,
        "missing_signer_install_contract:",
    )
    activation_present = _files_present(
        root,
        ACTIVATION_FILES,
        blockers,
        "missing_activation_file:",
    ) and _constants_present(
        esp_lab_activation,
        ACTIVATION_CONSTANTS,
        blockers,
        "missing_activation_contract:",
    )

    read_only_operations = tuple(esp_lab.SUPPORTED_OPERATIONS)
    report = {
        "schema": READINESS_SCHEMA,
        "repository": REPOSITORY,
        "status": "READY" if not blockers else "BLOCKED",
        "stage_availability": stage_availability,
        "contracts": {
            "signer_install": signer_install_present,
            "activation": activation_present,
        },
        "read_only_operations": list(read_only_operations),
        "firmware_flash_allowed": False,
        "live_home_edge_action_allowed": False,
        "privileged_mutation_allowed": False,
        "blockers": sorted(blockers),
    }
    return report


def _files_present(root: Path, files: tuple[str, ...], blockers: list[str], prefix: str) -> bool:
    present = True
    for relpath in files:
        path = root / relpath
        if not path.is_file() or path.is_symlink():
            blockers.append(f"{prefix}{relpath}")
            present = False
    return present


def _constants_present(
    module: object,
    names: tuple[str, ...],
    blockers: list[str],
    prefix: str,
) -> bool:
    present = True
    for name in names:
        value = getattr(module, name, None)
        if not isinstance(value, str) or not value:
            blockers.append(f"{prefix}{name}")
            present = False
    return present


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report public ESP Lab readiness without live action")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    print(json.dumps(build_readiness_report(args.repo_root), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
