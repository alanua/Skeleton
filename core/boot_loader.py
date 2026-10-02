from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

import yaml

from core.capability_checker import CapabilityChecker
from core.capability_runtime_truth import RuntimeCapabilityEvidence


class BootReport(dict):
    """Boot report mapping with legacy v1 iteration compatibility."""

    _ITERATION_FIELDS = (
        "schema",
        "repo",
        "ref",
        "entrypoint",
        "loaded_sources",
        "mode",
        "active_project_status",
        "source_trust_map",
        "writes",
    )

    def __iter__(self):
        return iter(self._ITERATION_FIELDS)


class BootLoader:
    def __init__(
        self,
        repo_root: Union[str, Path],
        runtime_evidence: Iterable[RuntimeCapabilityEvidence] = (),
    ) -> None:
        self.root = Path(repo_root)
        self.runtime_evidence = tuple(runtime_evidence)

    def load(
        self,
        manifest_path: str = "BOOT_MANIFEST.yaml",
        runtime_evidence: Iterable[RuntimeCapabilityEvidence] | None = None,
        runtime_truth_checked_at: datetime | str | None = None,
    ) -> dict:
        manifest_file = self.root / manifest_path
        manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))

        required_fields = (
            "repo",
            "ref",
            "entrypoint",
            "read_order",
            "boot_output",
            "failure_statuses",
        )
        for field in required_fields:
            if field not in manifest:
                raise ValueError(f"BOOT_MANIFEST.yaml missing required field: {field}")

        loaded_sources = self._check_sources(manifest["read_order"])

        return BootReport({
            "schema": "skeleton.boot_report.v1",
            "repo": manifest["repo"],
            "ref": manifest["ref"],
            "entrypoint": manifest["entrypoint"],
            "loaded_sources": loaded_sources,
            "mode": "boot",
            "active_project_status": "ACTIVE_PROJECT_WAITING",
            "source_trust_map": self._build_trust_map(),
            "capability_runtime_truth": self._build_capability_runtime_truth(
                self.runtime_evidence if runtime_evidence is None else runtime_evidence,
                now=runtime_truth_checked_at,
            ),
            "writes": "none",
        })

    def _check_sources(self, read_order: list) -> list:
        return [str(path) for path in read_order if (self.root / str(path)).is_file()]

    def _build_trust_map(self) -> dict:
        registry_path = self.root / "SOURCE_REGISTRY.yaml"
        if not registry_path.is_file():
            return {}

        try:
            registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return {}

        sources = registry.get("sources", {}) if isinstance(registry, dict) else {}
        if not isinstance(sources, dict):
            return {}

        trust_map = {}
        for source_name, source_data in sources.items():
            if isinstance(source_data, dict) and "trust" in source_data:
                trust_map[source_name] = source_data["trust"]
        return trust_map

    def _build_capability_runtime_truth(
        self,
        runtime_evidence: Iterable[RuntimeCapabilityEvidence] = (),
        *,
        now: datetime | str | None = None,
    ) -> dict:
        registry_path = self.root / "CAPABILITY_REGISTRY.yaml"
        if not registry_path.is_file():
            return {}
        return CapabilityChecker(registry_path).runtime_truth(runtime_evidence, now=now)


def main() -> int:
    loader = BootLoader(Path.cwd())
    report = loader.load()
    print(json.dumps(report))
    return 0 if report["entrypoint"] in report["loaded_sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
