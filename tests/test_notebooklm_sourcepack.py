from __future__ import annotations

from pathlib import Path

from scripts.build_notebooklm_sourcepack import build_sourcepack, write_sourcepack


ROOT = Path(__file__).resolve().parents[1]
SOURCEPACK_PATH = ROOT / "docs" / "NOTEBOOKLM_SOURCEPACK.md"
WORKFLOW_PATH = ROOT / "docs" / "NOTEBOOKLM_WORKFLOW.md"


def test_sourcepack_file_exists() -> None:
    assert SOURCEPACK_PATH.is_file()


def test_sourcepack_has_required_sections() -> None:
    sourcepack = SOURCEPACK_PATH.read_text(encoding="utf-8")

    for section in [
        "## Canon Note",
        "## Source Inputs",
        "## Boot Entrypoint",
        "## Available Capabilities",
        "## Planned Capabilities",
        "## Project States",
        "## Runner Queue Status",
        "## Next Safe Steps",
    ]:
        assert section in sourcepack


def test_sourcepack_records_notebooklm_as_mirror_and_github_as_canon() -> None:
    sourcepack = SOURCEPACK_PATH.read_text(encoding="utf-8")

    assert "NotebookLM is a mirror" in sourcepack
    assert "GitHub is canon" in sourcepack


def test_sourcepack_includes_all_project_state_files() -> None:
    sourcepack = SOURCEPACK_PATH.read_text(encoding="utf-8")

    for state_path in sorted((ROOT / "projects").glob("*/STATE.yaml")):
        rel = state_path.relative_to(ROOT).as_posix()
        project_id = state_path.parent.name
        assert f"### {project_id}" in sourcepack
        assert f"`{rel}`" in sourcepack


def test_sourcepack_is_deterministic_against_checked_in_file() -> None:
    assert SOURCEPACK_PATH.read_text(encoding="utf-8") == build_sourcepack(ROOT)


def test_write_sourcepack_supports_explicit_output(tmp_path: Path) -> None:
    output = tmp_path / "sourcepack.md"

    written = write_sourcepack(ROOT, output)

    assert written == output
    assert output.read_text(encoding="utf-8") == build_sourcepack(ROOT)


def test_workflow_documents_manual_refresh_and_no_live_calls() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Google Doc" in workflow
    assert "NotebookLM" in workflow
    assert "No live Google calls" in workflow
    assert "No NotebookLM API calls" in workflow
