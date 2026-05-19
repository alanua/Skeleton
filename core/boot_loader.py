from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import yaml


class BootLoader:
    def __init__(self, repo_root: Union[str, Path]) -> None:
        self.root = Path(repo_root)

    def load(self, manifest_path: str = "BOOT_MANIFEST.yaml") -> dict:
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

        return {
            "schema": "skeleton.boot_report.v1",
            "repo": manifest["repo"],
            "ref": manifest["ref"],
            "entrypoint": manifest["entrypoint"],
            "loaded_sources": loaded_sources,
            "mode": "boot",
            "active_project_status": "ACTIVE_PROJECT_WAITING",
            "source_trust_map": self._build_trust_map(),
            "writes": "none",
        }

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


def main() -> int:
    loader = BootLoader(Path.cwd())
    report = loader.load()
    print(json.dumps(report))
    return 0 if report["entrypoint"] in report["loaded_sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
