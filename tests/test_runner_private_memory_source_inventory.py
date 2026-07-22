from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import stat

import pytest

from core import runner_private_memory_source_inventory as inventory


def _task_body(text: str) -> str:
    return f"```task\n{text}\n```"


def _read_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _env(tmp_path: Path, private_root: Path, **extra: str) -> dict[str, str]:
    if private_root.is_absolute():
        private_root.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(tmp_path / "home"),
        "SKELETON_PRIVATE_MEMORY_ROOT": str(private_root),
    }
    env.update(extra)
    return env


def test_task_id_and_six_alias_allowlist_dispatch(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "notes.md").write_text("PRIVATE_MARKER", encoding="utf-8")

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    assert inventory.TASK_ID == "private_memory_phase_a_inventory"
    assert inventory.FIXED_ALIASES == (
        "private_memory_root",
        "runner_memory_root",
        "hermes_runtime_root",
        "agent_dev_root",
        "hermes_workspace_root",
        "hermes_artifacts_root",
    )
    assert result.status == "DONE"
    assert result.private_report_path is not None
    report = _read_report(result.private_report_path)
    assert report["payload"]["task_id"] == inventory.TASK_ID


def test_alias_resolution_precedence_paths_use_synthetic_dirs(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in inventory.FIXED_ALIASES}
    private_root = roots["private_memory_root"]
    for alias, root in roots.items():
        root.mkdir(parents=True)
        (root / f"{alias}.txt").write_text("not read", encoding="utf-8")
    db_path = tmp_path / "runner-db-parent" / "memory.sqlite"
    ledger_path = tmp_path / "runner-ledger-parent" / "ledger.jsonl"
    db_path.parent.mkdir()
    ledger_path.parent.mkdir()
    env = _env(
        tmp_path,
        private_root,
        SKELETON_RUNNER_MEMORY_DIR=str(roots["runner_memory_root"]),
        SKELETON_RUNNER_MEMORY_DB=str(db_path),
        SKELETON_RUNNER_MEMORY_LEDGER=str(ledger_path),
        SKELETON_HERMES_RUNTIME_ROOT=str(roots["hermes_runtime_root"]),
        HERMES_RUNTIME_ROOT=str(tmp_path / "ignored-runtime"),
        RUNNER_APPROVED_WORKSPACE_ROOT=str(roots["agent_dev_root"]),
        SKELETON_HERMES_WORKSPACE_ROOT=str(roots["hermes_workspace_root"]),
        HERMES_WORKSPACE_ROOT=str(tmp_path / "ignored-workspace"),
        SKELETON_HERMES_ARTIFACTS_ROOT=str(roots["hermes_artifacts_root"]),
        HERMES_ARTIFACTS_ROOT=str(tmp_path / "ignored-artifacts"),
    )

    result = inventory.execute_private_memory_phase_a_inventory(env=env)

    assert result.status == "DONE"
    report = _read_report(result.private_report_path)
    root_records = {root["alias"]: root for root in report["payload"]["roots"]}
    assert set(root_records) == set(inventory.FIXED_ALIASES)
    assert all(root_records[alias]["state"] == "readable" for alias in roots)

    env_without_runner_dir = dict(env)
    env_without_runner_dir.pop("SKELETON_RUNNER_MEMORY_DIR")
    assert inventory._resolve_alias_text("runner_memory_root", env_without_runner_dir) == str(db_path.parent)
    env_without_runner_dir.pop("SKELETON_RUNNER_MEMORY_DB")
    assert inventory._resolve_alias_text("runner_memory_root", env_without_runner_dir) == str(ledger_path.parent)
    env_without_skeleton = dict(env)
    env_without_skeleton.pop("SKELETON_HERMES_RUNTIME_ROOT")
    assert inventory._resolve_alias_text("hermes_runtime_root", env_without_skeleton) == str(
        tmp_path / "ignored-runtime"
    )
    env_without_skeleton.pop("SKELETON_HERMES_WORKSPACE_ROOT")
    assert inventory._resolve_alias_text("hermes_workspace_root", env_without_skeleton) == str(
        tmp_path / "ignored-workspace"
    )
    env_without_skeleton.pop("SKELETON_HERMES_ARTIFACTS_ROOT")
    assert inventory._resolve_alias_text("hermes_artifacts_root", env_without_skeleton) == str(
        tmp_path / "ignored-artifacts"
    )


@pytest.mark.parametrize(
    "task_text,reason",
    [
        ("aliases:\n  - unknown_alias", "unknown_alias"),
        ("aliases:\n  - /tmp/source", "invalid_alias_token"),
        ("aliases:\n  - ../source", "invalid_alias_token"),
        ("aliases:\n  - https://example.test/source", "invalid_alias_token"),
        ("aliases:\n  - runner_memory_root; rm -rf x", "invalid_alias_token"),
        ("enabled_aliases:\n  - runner_memory_root\nunexpected: value", "unknown_option_key"),
        ("aliases:\n  - runner_memory_root\nmax_depth: true", "invalid_limit_value"),
        ("aliases:\n  - runner_memory_root\nmax_depth: 99", "limit_out_of_range"),
    ],
)
def test_arbitrary_issue_paths_urls_shell_syntax_and_unknown_aliases_rejected(
    tmp_path: Path, task_text: str, reason: str
) -> None:
    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body(task_text),
        env=_env(tmp_path, tmp_path / "private"),
    )

    assert result.status == "BLOCKED"
    assert f"reason={reason}" in result.lines
    assert result.private_report_path is None


def test_regular_candidate_file_content_is_never_opened_or_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    candidate = source_root / "chat-export.json"
    candidate.write_text("PRIVATE_CONTENT_MARKER", encoding="utf-8")
    real_open = builtins.open

    def guarded_open(file: object, *args: object, **kwargs: object) -> object:
        if isinstance(file, (str, os.PathLike)) and Path(file) == candidate:
            raise AssertionError("candidate content was opened")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    assert result.status == "DONE"
    assert result.private_report_path is not None


def test_symlink_roots_children_ancestors_review_dir_and_report_targets_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "doc.md").write_text("not read", encoding="utf-8")
    os.symlink(source_root / "doc.md", source_root / "linked-doc.md")
    symlink_root = tmp_path / "symlink-root"
    os.symlink(source_root, symlink_root)

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert result.status == "DONE"
    report = _read_report(result.private_report_path)
    assert report["payload"]["aggregate"]["symlink_count"] == 1
    assert all("linked-doc.md" not in item["path"] for item in report["payload"]["candidates"])

    blocked_root = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, tmp_path / "private2", SKELETON_RUNNER_MEMORY_DIR=str(symlink_root)),
    )
    assert blocked_root.status == "BLOCKED"
    assert "reason=no_readable_roots" in blocked_root.lines

    real_ancestor = tmp_path / "real-ancestor"
    real_ancestor.mkdir()
    nested_source = real_ancestor / "source"
    nested_source.mkdir()
    (nested_source / "followed.md").write_text("not read", encoding="utf-8")
    linked_ancestor = tmp_path / "linked-ancestor"
    os.symlink(real_ancestor, linked_ancestor)
    blocked_source_ancestor = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(
            tmp_path,
            tmp_path / "private-source-ancestor",
            SKELETON_RUNNER_MEMORY_DIR=str(linked_ancestor / "source"),
        ),
    )
    assert blocked_source_ancestor.status == "BLOCKED"
    assert "reason=no_readable_roots" in blocked_source_ancestor.lines
    assert blocked_source_ancestor.private_report_path is None

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    os.symlink(real_parent, linked_parent)
    blocked_ancestor = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, linked_parent / "private", SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert blocked_ancestor.status == "BLOCKED"
    assert "reason=path_component_symlink" in blocked_ancestor.lines

    review_private = tmp_path / "review-private"
    review_private.mkdir()
    os.symlink(tmp_path, review_private / "phase-a-review")
    blocked_review = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, review_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert blocked_review.status == "BLOCKED"
    assert "reason=review_dir_symlink" in blocked_review.lines

    monkeypatch.setattr(inventory.time, "time", lambda: 1234.0)
    target_private = tmp_path / "target-private"
    first_target = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, target_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert first_target.private_report_path is not None
    first_target.private_report_path.unlink()
    os.symlink(tmp_path / "elsewhere", first_target.private_report_path)
    blocked_target = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, target_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert blocked_target.status == "BLOCKED"
    assert "reason=report_target_symlink" in blocked_target.lines

    non_regular_private = tmp_path / "non-regular-private"
    monkeypatch.setattr(inventory.time, "time", lambda: 5678.0)
    first_non_regular = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, non_regular_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert first_non_regular.private_report_path is not None
    first_non_regular.private_report_path.unlink()
    first_non_regular.private_report_path.mkdir()
    blocked_non_regular = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, non_regular_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert blocked_non_regular.status == "BLOCKED"
    assert "reason=report_target_not_regular" in blocked_non_regular.lines

    temp_symlink_private = tmp_path / "temp-symlink-private"
    monkeypatch.setattr(inventory.time, "time", lambda: 9012.0)
    first_temp = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, temp_symlink_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert first_temp.private_report_path is not None
    temp_name = f".{first_temp.private_report_path.name}.tmp"
    redirected = tmp_path / "redirected-report"
    os.symlink(redirected, first_temp.private_report_path.parent / temp_name)
    blocked_temp = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, temp_symlink_private, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert blocked_temp.status == "BLOCKED"
    assert "reason=report_temp_symlink" in blocked_temp.lines
    assert not redirected.exists()


def test_relative_private_root_blocks(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env={
            "SKELETON_PRIVATE_MEMORY_ROOT": "relative-private",
            "SKELETON_RUNNER_MEMORY_DIR": str(source_root),
        },
    )

    assert result.status == "BLOCKED"
    assert "reason=private_root_not_absolute" in result.lines
    assert result.private_report_path is None


def test_zero_readable_roots_and_initial_scandir_failure_block_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    real_scandir = os.scandir

    def failing_scandir(path: object) -> object:
        if Path(path) == source_root:
            raise PermissionError("synthetic")
        return real_scandir(path)

    monkeypatch.setattr(inventory.os, "scandir", failing_scandir)

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    assert result.status == "BLOCKED"
    assert "reason=no_readable_roots" in result.lines
    assert result.private_report_path is None
    assert not (private_root / "phase-a-review").exists()


@pytest.mark.parametrize(
    "limit_key",
    ("max_entries_per_root", "max_total_entries"),
)
def test_scandir_streaming_stops_before_consuming_beyond_configured_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit_key: str
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    limit = 2
    real_scandir = inventory.os.scandir

    class SyntheticEntry:
        def __init__(self, index: int) -> None:
            self.name = f"doc-{index}.md"
            self.path = str(source_root / self.name)

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            assert follow_symlinks is False
            return os.stat_result((stat.S_IFREG | 0o600, 0, 0, 0, 0, 0, 10, 1, 1, 1))

    class BoundedScandir:
        def __init__(self, path: object) -> None:
            self.path = Path(path)
            self.index = 0
            self.closed = False

        def __enter__(self) -> "BoundedScandir":
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def __iter__(self) -> "BoundedScandir":
            return self

        def __next__(self) -> SyntheticEntry:
            if self.index >= limit:
                raise AssertionError("scandir consumed beyond configured limit")
            entry = SyntheticEntry(self.index)
            self.index += 1
            return entry

        def close(self) -> None:
            self.closed = True

    synthetic_iterators: list[BoundedScandir] = []

    def bounded_scandir(path: object) -> object:
        if Path(path) == source_root:
            iterator = BoundedScandir(path)
            synthetic_iterators.append(iterator)
            return iterator
        return real_scandir(path)

    monkeypatch.setattr(inventory.os, "scandir", bounded_scandir)
    limits = {
        "max_depth": 3,
        "max_entries_per_root": 500,
        "max_total_entries": 2500,
        "timeout_seconds": 5,
        limit_key: limit,
    }
    limit_lines = "\n".join(f"{key}: {value}" for key, value in limits.items())

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body(f"aliases:\n  - runner_memory_root\n{limit_lines}"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    assert result.status == "DONE"
    assert "reason=truncated" in result.lines
    assert len(synthetic_iterators) == 2
    assert all(iterator.closed for iterator in synthetic_iterators)
    assert synthetic_iterators[1].index == limit


def test_private_report_write_uses_directory_fd_no_follow_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "notes.md").write_text("not read", encoding="utf-8")
    open_calls: list[tuple[object, int, dict[str, object]]] = []
    replace_calls: list[tuple[object, object, dict[str, object]]] = []
    real_open = inventory.os.open
    real_replace = inventory.os.replace

    def recording_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        open_calls.append((path, flags, dict(kwargs)))
        return real_open(path, flags, *args, **kwargs)

    def recording_replace(src: object, dst: object, **kwargs: object) -> None:
        replace_calls.append((src, dst, dict(kwargs)))
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(inventory.os, "open", recording_open)
    monkeypatch.setattr(inventory.os, "replace", recording_replace)

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    assert result.status == "DONE"
    review_dir = private_root / "phase-a-review"
    review_open = next(call for call in open_calls if call[0] == review_dir)
    assert review_open[1] & os.O_RDONLY == os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        assert review_open[1] & os.O_DIRECTORY == os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        assert review_open[1] & os.O_NOFOLLOW == os.O_NOFOLLOW
    temp_open = next(call for call in open_calls if isinstance(call[0], str) and call[0].startswith("."))
    assert temp_open[1] & os.O_CREAT == os.O_CREAT
    assert temp_open[1] & os.O_EXCL == os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        assert temp_open[1] & os.O_NOFOLLOW == os.O_NOFOLLOW
    assert replace_calls
    src, dst, replace_kwargs = replace_calls[-1]
    assert isinstance(src, str) and src.startswith(".")
    assert isinstance(dst, str) and dst.endswith(".json")
    assert replace_kwargs["src_dir_fd"] == replace_kwargs["dst_dir_fd"]
    assert isinstance(replace_kwargs["src_dir_fd"], int)
    assert temp_open[2]["dir_fd"] == replace_kwargs["src_dir_fd"]


def test_normal_inventory_stores_exact_paths_only_in_private_report_with_modes(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    candidate = source_root / "handoff-summary.md"
    candidate.write_text("PRIVATE_CONTENT_MARKER", encoding="utf-8")

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    assert result.status == "DONE"
    assert result.private_report_path is not None
    assert oct((private_root / "phase-a-review").stat().st_mode & 0o777) == "0o700"
    assert oct(result.private_report_path.stat().st_mode & 0o777) == "0o600"
    report = _read_report(result.private_report_path)
    assert str(candidate) in json.dumps(report)
    assert "PRIVATE_CONTENT_MARKER" not in json.dumps(report)
    public_text = "\n".join(result.lines)
    assert str(tmp_path) not in public_text
    assert candidate.name not in public_text
    assert "PRIVATE_CONTENT_MARKER" not in public_text


def test_category_classification_and_metadata_fingerprint_are_deterministic(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    for name in (
        "chat-export.json",
        "memory.sqlite",
        "source-manifest.yaml",
        "project-handoff.txt",
        "notes.pdf",
        "blob.bin",
    ):
        (source_root / name).write_text("not read", encoding="utf-8")

    body = _task_body("aliases:\n  - runner_memory_root")
    env = _env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root))
    first = inventory.execute_private_memory_phase_a_inventory(body, env=env)
    second = inventory.execute_private_memory_phase_a_inventory(body, env=env)

    first_candidates = _read_report(first.private_report_path)["payload"]["candidates"]
    second_candidates = _read_report(second.private_report_path)["payload"]["candidates"]
    assert {item["category"] for item in first_candidates} == set(inventory.CANDIDATE_CATEGORIES)
    assert {
        item["metadata_fingerprint"] for item in first_candidates
    } == {item["metadata_fingerprint"] for item in second_candidates}


def test_limits_and_timeout_truncate_with_stable_evidence(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    for index in range(5):
        (source_root / f"doc-{index}.md").write_text("not read", encoding="utf-8")

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root\nmax_entries_per_root: 2"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )
    assert result.status == "DONE"
    assert "reason=truncated" in result.lines
    assert "truncation_evidence=true" in result.lines

    ticks = iter([0.0, 2.0])
    timed = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root\ntimeout_seconds: 1"),
        env=_env(tmp_path, tmp_path / "private-timeout", SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
        now=lambda: next(ticks, 2.0),
    )
    assert timed.status == "DONE"
    assert "reason=truncated" in timed.lines


def test_public_receipt_has_aggregate_hash_and_no_private_values(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    source_root = tmp_path / "source"
    source_root.mkdir()
    candidate = source_root / "candidate-private-name.md"
    candidate.write_text("PRIVATE_CONTENT_MARKER", encoding="utf-8")

    result = inventory.execute_private_memory_phase_a_inventory(
        _task_body("aliases:\n  - runner_memory_root"),
        env=_env(tmp_path, private_root, SKELETON_RUNNER_MEMORY_DIR=str(source_root)),
    )

    text = "\n".join(result.lines)
    assert "private_report_sha256=" in text
    assert "content_files_read=false" in text
    assert "symlink_traversal=false" in text
    assert "runtime_private_action=false" in text
    assert "public_safe_report_ok=true" in text
    assert str(tmp_path) not in text
    assert candidate.name not in text
    assert "PRIVATE_CONTENT_MARKER" not in text
