from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_default_boot_route_is_manifest_only() -> None:
    manifest = load_yaml("BOOT_MANIFEST.yaml")
    read_order = manifest["read_order"]

    assert read_order == [
        "BOOT_MANIFEST.yaml",
        "COMMANDS.yaml",
        "OPERATOR_RULES.yaml",
        "MODES.yaml",
        "SOURCE_REGISTRY.yaml",
        "MEMORY_ROUTING.yaml",
        "PROJECT_INDEX.yaml",
        "STATUS_CODES.yaml",
    ]


def test_markdown_files_do_not_define_alternate_active_boot_route() -> None:
    stale_patterns = [
        "required boot sequence",
        "active startup route",
        "default boot chain",
        "START_HERE_FOR_CHATGPT.md -> MEMORY_POLICY.md",
        "assistant_diary.md",
        "CHATGPT_BRANCH_CONTINUITY_BOOT.md",
    ]

    allowed_reference_paths = {
        "docs/MIGRATION_FROM_JEEVES_REPO.md",
    }

    offenders: list[str] = []
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed_reference_paths:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        if any(pattern.lower() in lowered for pattern in stale_patterns):
            offenders.append(rel)

    assert offenders == []


def test_skeleton_freshness_docs_keep_github_main_as_canon() -> None:
    operating_standard = (ROOT / "projects/skeleton/PROJECT_OPERATING_STANDARD.md").read_text(encoding="utf-8")
    runner_tasks = (ROOT / "docs/RUNNER_MAINTENANCE_TASKS.md").read_text(encoding="utf-8")

    assert "## 5. Skeleton freshness check" in operating_standard
    assert "GitHub `main` is the source of truth" in operating_standard
    assert "live Runner checkout" in operating_standard
    assert "`docs/NOTEBOOKLM_SOURCEPACK.md` freshness" in operating_standard
    assert "open PRs or issues" in operating_standard
    assert "old chats, old branches, and old local notes are not canon" in operating_standard

    assert "`check_skeleton_freshness`" in runner_tasks
    assert "GitHub `main` is the source of truth" in runner_tasks
    assert "current GitHub `main` SHA" in runner_tasks
    assert "old chats and old branches are not canon" in runner_tasks
