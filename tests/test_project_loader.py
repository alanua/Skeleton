from datetime import datetime
from pathlib import Path

import pytest
import yaml

from core.project_loader import ProjectLoader
from core.session_state import SessionState


ROOT = Path(__file__).parents[1]


def write_project(
    root: Path,
    project_id: str = "example",
    state_role: str = "handoff_not_canon_truth",
    include_manifest: bool = True,
    include_state: bool = True,
) -> None:
    project_dir = root / "projects" / project_id
    project_dir.mkdir(parents=True)
    (root / "PROJECT_INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "skeleton.project_index.v1",
                "projects": {
                    project_id: {
                        "entrypoint": f"projects/{project_id}/PROJECT_MANIFEST.yaml",
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    if include_manifest:
        (project_dir / "PROJECT_MANIFEST.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema": "skeleton.project_manifest.v1",
                    "project_id": project_id,
                    "name": "Example",
                    "status": "ACTIVE_TEST_PROJECT",
                    "entrypoint": f"projects/{project_id}/PROJECT_MANIFEST.yaml",
                    "read_order": [
                        f"projects/{project_id}/PROJECT_MANIFEST.yaml",
                        f"projects/{project_id}/STATE.yaml",
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    if include_state:
        (project_dir / "STATE.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema": "skeleton.project_state.v1",
                    "project_id": project_id,
                    "status": "DRAFT_HANDOFF",
                    "state_role": state_role,
                    "last_verified": "2026-05-19",
                    "evidence_source": "test fixture",
                    "summary": ["Fixture state."],
                    "next_actions": ["Keep testing."],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )


def test_loader_lists_projects() -> None:
    projects = ProjectLoader(ROOT).list_projects()

    assert "skeleton" in projects
    assert projects[0] == "skeleton"


def test_loader_activates_known_project() -> None:
    context = ProjectLoader(ROOT).activate("skeleton")

    assert context["project_id"] == "skeleton"


def test_loader_returns_project_context() -> None:
    context = ProjectLoader(ROOT).activate("skeleton")

    assert set(context) == {
        "project_id",
        "manifest",
        "state",
        "state_role",
        "loaded_at",
    }
    assert context["state_role"] == "handoff_not_canon_truth"
    datetime.fromisoformat(context["loaded_at"])


def test_loader_context_contains_manifest_and_state() -> None:
    context = ProjectLoader(ROOT).activate("skeleton")

    assert context["manifest"]["project_id"] == "skeleton"
    assert context["state"]["project_id"] == "skeleton"
    assert context["state"]["state_role"] == "handoff_not_canon_truth"


def test_loader_fails_on_unknown_project() -> None:
    with pytest.raises(ValueError, match="Unknown project_id"):
        ProjectLoader(ROOT).activate("missing-project")


def test_loader_validates_state_role(tmp_path: Path) -> None:
    write_project(tmp_path, state_role="canon_truth")

    with pytest.raises(ValueError, match="Invalid state_role"):
        ProjectLoader(tmp_path).activate("example")


def test_loader_requires_manifest_file(tmp_path: Path) -> None:
    write_project(tmp_path, include_manifest=False)

    with pytest.raises(FileNotFoundError, match="PROJECT_MANIFEST.yaml"):
        ProjectLoader(tmp_path).activate("example")


def test_loader_requires_state_file(tmp_path: Path) -> None:
    write_project(tmp_path, include_state=False)

    with pytest.raises(FileNotFoundError, match="STATE.yaml"):
        ProjectLoader(tmp_path).activate("example")


def test_session_state_activate() -> None:
    session = SessionState()
    context = {"project_id": "skeleton"}

    session.activate("skeleton", context)

    assert session.is_active() is True
    assert session.active_project() == "skeleton"
    assert session.context() == context


def test_session_state_deactivate() -> None:
    session = SessionState()
    session.activate("skeleton", {"project_id": "skeleton"})

    session.deactivate()

    assert session.is_active() is False
    assert session.active_project() is None


def test_session_state_resets_context_on_deactivate() -> None:
    session = SessionState()
    session.activate("skeleton", {"project_id": "skeleton"})

    session.deactivate()

    assert session.context() is None


def test_session_state_starts_inactive() -> None:
    session = SessionState()

    assert session.is_active() is False
    assert session.active_project() is None
    assert session.context() is None


def test_session_state_never_writes_to_disk(tmp_path: Path) -> None:
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    session = SessionState()

    session.activate("skeleton", {"project_id": "skeleton"})
    session.deactivate()

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before
