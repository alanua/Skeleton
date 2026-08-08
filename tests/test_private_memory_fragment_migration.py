from __future__ import annotations

import json
from pathlib import Path

from core.graphify_adapter import LocalGraphifyIndex
from core.mempalace_adapter import LocalMemPalaceIndex
from core.private_memory_migration import (
    apply_migration_plan,
    build_migration_plan,
    render_public_report,
    run_fragmented_memory_migration,
    scan_fragmented_memory_sources,
)
from core.private_memory_stack import PrivateMemoryStack


def _write_fact_source(
    path: Path,
    *,
    namespace: str = "skeleton",
    fact_id: str = "fixture_fact",
    value: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    fact = {
        "schema": "skeleton.fragmented_memory_fact.v1",
        "namespace": namespace,
        "fact_id": fact_id,
        "privacy_class": "private",
        "confidence": "high",
        "observed_at": "2026-07-28T00:00:00Z",
        "value": value or {"decision": "use MemoryGateway for durable writes"},
    }
    if extra:
        fact.update(extra)
    path.write_text(json.dumps(fact, sort_keys=True), encoding="utf-8")


def test_fragment_scan_excludes_secret_and_raw_artifact_sources(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fact_source(repo / "durable_memory.json")
    (repo / "api_token_memory.json").write_text('{"token":"secret"}', encoding="utf-8")
    (repo / "scan.pdf").write_bytes(b"%PDF synthetic")

    inventory, candidates = scan_fragmented_memory_sources([repo], repo_root=repo)
    counts = {item["classification"]: 0 for item in inventory}
    for item in inventory:
        counts[item["classification"]] = counts.get(item["classification"], 0) + 1

    assert len(candidates) == 1
    assert counts["MIGRATE_DURABLE_FACTS"] == 1
    assert counts["SECRET_OR_RESTRICTED"] == 1
    assert counts["KEEP_AS_ARTIFACT"] == 1


def test_duplicate_facts_deduplicate_and_idempotent_rerun_advances_zero_revisions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fact_source(repo / "one.json")
    _write_fact_source(repo / "two.json", extra={"confidence": "medium"})
    inventory, candidates = scan_fragmented_memory_sources([repo], repo_root=repo)
    plan = build_migration_plan(candidates)
    stack = PrivateMemoryStack(tmp_path / "private")
    stack.init(import_manifest=False)

    first = apply_migration_plan(
        plan,
        stack=stack,
        ledger_path=tmp_path / "private" / "fragmented_memory_migration_ledger.sqlite",
        expected_revision=0,
        actor_ref="test",
        reason_code="fragmented-memory-migration",
        approval_ref="test-approval",
    )
    revision_after_first = stack.status()["canonical_sqlite"]["canonical_revision"]
    second = apply_migration_plan(
        plan,
        stack=stack,
        ledger_path=tmp_path / "private" / "fragmented_memory_migration_ledger.sqlite",
        expected_revision=revision_after_first,
        actor_ref="test",
        reason_code="fragmented-memory-migration",
        approval_ref="test-approval",
    )

    assert len(inventory) == 2
    assert plan["accepted_count"] == 1
    assert plan["duplicate_count"] == 1
    assert first["migrated_count"] == 1
    assert second["migrated_count"] == 0
    assert second["ledger_skipped_count"] == 1
    assert stack.status()["canonical_sqlite"]["canonical_revision"] == revision_after_first


def test_conflicting_facts_fail_closed_for_operator_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fact_source(repo / "one.json", value={"decision": "alpha"})
    _write_fact_source(repo / "two.json", value={"decision": "beta"})

    _inventory, candidates = scan_fragmented_memory_sources([repo], repo_root=repo)
    plan = build_migration_plan(candidates)

    assert plan["accepted_count"] == 0
    assert plan["conflict_count"] == 1


def test_correction_supersedes_prior_fact_when_evidence_links_source_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = repo / "one.json"
    second = repo / "two.json"
    _write_fact_source(first, value={"state": "old"})
    _write_fact_source(second, value={"state": "corrected"}, extra={"correction_of": "pending"})
    inventory, candidates = scan_fragmented_memory_sources([repo], repo_root=repo)
    first_ref = inventory[0]["source_ref"]
    second.write_text(
        second.read_text(encoding="utf-8").replace('"pending"', json.dumps(first_ref)),
        encoding="utf-8",
    )
    _inventory, candidates = scan_fragmented_memory_sources([repo], repo_root=repo)

    plan = build_migration_plan(candidates)

    assert plan["accepted_count"] == 1
    accepted = plan["accepted"][0]
    assert accepted.value["value"]["state"] == "corrected"


def test_apply_reports_derived_index_degradation_without_rollback(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fact_source(repo / "durable.json")
    _inventory, candidates = scan_fragmented_memory_sources([repo], repo_root=repo)
    plan = build_migration_plan(candidates)
    stack = PrivateMemoryStack(tmp_path / "private")
    stack.init(import_manifest=False)

    def fail_graphify(_root: Path, *, facts: list[dict[str, object]], canonical_revision: int) -> None:
        raise RuntimeError("synthetic derived index failure")

    monkeypatch.setattr(LocalMemPalaceIndex, "rebuild_from_facts", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(LocalGraphifyIndex, "rebuild_from_facts", staticmethod(fail_graphify))

    receipt = apply_migration_plan(
        plan,
        stack=stack,
        ledger_path=tmp_path / "private" / "fragmented_memory_migration_ledger.sqlite",
        expected_revision=0,
        actor_ref="test",
        reason_code="fragmented-memory-migration",
        approval_ref="test-approval",
    )

    assert receipt["migrated_count"] == 1
    assert stack.status()["canonical_sqlite"]["canonical_revision"] == 1
    assert stack.get(namespace="skeleton", fact_id="fixture_fact")["value"]["value"]["decision"]


def test_public_report_contains_only_aggregates_hashes_and_revisions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    private_root = tmp_path / "private"
    _write_fact_source(repo / "durable.json", value={"private_value": "do not publish"})

    dry_run = run_fragmented_memory_migration(repo_root=repo, private_root=private_root, apply=False)
    report = render_public_report(dry_run)
    serialized = json.dumps(report, sort_keys=True)

    assert report["mode"] == "dry_run"
    assert report["plan"]["accepted_count"] == 1
    assert "do not publish" not in serialized
    assert str(repo) not in serialized
    assert str(private_root) not in serialized
