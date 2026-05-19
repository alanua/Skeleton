from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import yaml


EXPECTED_STATE_ROLE = "handoff_not_canon_truth"


class ProjectLoader:
    def __init__(self, repo_root: Union[str, Path]) -> None:
        self.root = Path(repo_root)

    def list_projects(self) -> list:
        index = self._load_index()
        projects = index.get("projects", {})

        if isinstance(projects, dict):
            return list(projects.keys())

        if isinstance(projects, list):
            return [
                project["project_id"]
                for project in projects
                if isinstance(project, dict) and "project_id" in project
            ]

        return []

    def activate(self, project_id: str) -> dict:
        index = self._load_index()
        project = self._find_project(index, project_id)
        if project is None:
            known = ", ".join(self.list_projects())
            raise ValueError(f"Unknown project_id {project_id!r}. Known projects: {known}")

        manifest_path = self._project_manifest_path(project)
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)

        manifest = self._load_yaml(manifest_path)
        state_path = self._project_state_path(manifest, manifest_path)
        if not state_path.is_file():
            raise FileNotFoundError(state_path)

        state = self._load_yaml(state_path)
        state_role = state.get("state_role")
        if state_role != EXPECTED_STATE_ROLE:
            raise ValueError(
                f"Invalid state_role for {project_id!r}: {state_role!r}; "
                f"expected {EXPECTED_STATE_ROLE!r}"
            )

        return {
            "project_id": project_id,
            "manifest": manifest,
            "state": state,
            "state_role": EXPECTED_STATE_ROLE,
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _load_index(self) -> dict:
        return self._load_yaml(self.root / "PROJECT_INDEX.yaml")

    def _load_yaml(self, path: Path) -> dict:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _find_project(self, index: dict, project_id: str) -> dict | None:
        projects = index.get("projects", {})

        if isinstance(projects, dict):
            project = projects.get(project_id)
            return project if isinstance(project, dict) else None

        if isinstance(projects, list):
            for project in projects:
                if (
                    isinstance(project, dict)
                    and project.get("project_id") == project_id
                ):
                    return project

        return None

    def _project_manifest_path(self, project: dict) -> Path:
        entrypoint = project.get("entrypoint")
        if not entrypoint:
            raise FileNotFoundError(self.root / "PROJECT_MANIFEST.yaml")
        return self.root / entrypoint

    def _project_state_path(self, manifest: dict, manifest_path: Path) -> Path:
        for source in manifest.get("read_order", []):
            if isinstance(source, str) and source.endswith("STATE.yaml"):
                return self.root / source
        return manifest_path.parent / "STATE.yaml"
