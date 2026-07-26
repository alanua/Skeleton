from __future__ import annotations

import json
from pathlib import Path

from core.family_document_intake import MfpScanSessionAssembler
from core.family_document_runtime import FamilyDocumentRuntime, private_repair_handoff, read_json
from core.family_document_sources import MfpSourceProfile
from core.local_document_ocr import STRICT_SEPARATOR_MARKER, synthetic_pdf_page_texts


def _pdf(path: Path, *labels: str) -> Path:
    from core.family_document_intake import _minimal_pdf

    path.write_bytes(_minimal_pdf(list(labels)))
    return path


def _assembler(tmp_path: Path) -> tuple[MfpScanSessionAssembler, MfpSourceProfile, FamilyDocumentRuntime]:
    runtime = FamilyDocumentRuntime.open(tmp_path / "runtime")
    return MfpScanSessionAssembler(runtime), MfpSourceProfile("mfp-a", "duplex"), runtime


def _finalized_records(runtime: FamilyDocumentRuntime) -> list[dict[str, object]]:
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((runtime.root / "records").glob("*.json"))]
    return sorted(records, key=lambda record: (str(record["session_id"]), int(record["document_sequence"])))


def test_two_one_page_pdfs_in_one_session_become_one_two_page_pdf(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    first = _pdf(tmp_path / "a.pdf", "a1")
    second = _pdf(tmp_path / "b.pdf", "b1")

    assembler.ingest(first, profile, discovered_at=1)
    receipt = assembler.ingest(second, profile, discovered_at=10)
    final = assembler.finalize_session(str(receipt["session_id"]))

    assert final["document_count"] == 1
    assert final["total_output_pages"] == 2
    record = _finalized_records(runtime)[0]
    assert synthetic_pdf_page_texts(str(record["assembled_pdf"])) == ["a1", "b1"]


def test_multi_page_plus_one_page_input_preserves_order(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    multi = _pdf(tmp_path / "multi.pdf", "m1", "m2")
    one = _pdf(tmp_path / "one.pdf", "o1")

    assembler.ingest(multi, profile, discovered_at=1)
    receipt = assembler.ingest(one, profile, discovered_at=2)
    assembler.finalize_session(str(receipt["session_id"]))

    record = _finalized_records(runtime)[0]
    assert synthetic_pdf_page_texts(str(record["assembled_pdf"])) == ["m1", "m2", "o1"]
    assert [page["component_page_index"] for page in record["page_provenance"]] == [0, 1, 0]


def test_separator_creates_two_documents_and_is_excluded_from_output(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "batch.pdf", "left", STRICT_SEPARATOR_MARKER, "right")

    claim = assembler.ingest(source, profile, discovered_at=1)
    final = assembler.finalize_session(str(claim["session_id"]))

    records = _finalized_records(runtime)
    outputs = [synthetic_pdf_page_texts(str(record["assembled_pdf"])) for record in records]
    assert final["document_count"] == 2
    assert final["separator_pages_removed"] == 1
    assert outputs == [["left"], ["right"]]


def test_blank_pages_and_conflicting_ocr_topic_content_do_not_split(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "topics.pdf", "", "issuer alpha date 1", "different topic name beta")

    claim = assembler.ingest(source, profile, discovered_at=1)
    final = assembler.finalize_session(str(claim["session_id"]))

    assert final["document_count"] == 1
    assert synthetic_pdf_page_texts(str(_finalized_records(runtime)[0]["assembled_pdf"])) == [
        "",
        "issuer alpha date 1",
        "different topic name beta",
    ]


def test_inactivity_boundary_creates_a_new_document(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    first = _pdf(tmp_path / "first.pdf", "first")
    second = _pdf(tmp_path / "second.pdf", "second")

    assembler.ingest(first, profile, discovered_at=1)
    assembler.ingest(second, profile, discovered_at=62)
    assembler.recover_stale_sessions(now=200)

    records = _finalized_records(runtime)
    assert [synthetic_pdf_page_texts(str(record["assembled_pdf"])) for record in records] == [["first"], ["second"]]


def test_different_source_profile_identities_never_merge(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    other = MfpSourceProfile("mfp-a", "simplex")

    assembler.ingest(_pdf(tmp_path / "a.pdf", "a"), profile, discovered_at=1)
    assembler.ingest(_pdf(tmp_path / "b.pdf", "b"), other, discovered_at=2)
    assembler.recover_stale_sessions(now=100)

    records = sorted(_finalized_records(runtime), key=lambda record: record["assembled_pdf"])
    assert {record["source_identity"] for record in records} == {"mfp-a:duplex", "mfp-a:simplex"}


def test_duplicate_events_and_restart_never_duplicate_pages(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "dup.pdf", "once")

    first = assembler.ingest(source, profile, discovered_at=1)
    duplicate = MfpScanSessionAssembler(FamilyDocumentRuntime.open(runtime.root)).ingest(source, profile, discovered_at=2)
    final = assembler.finalize_session(str(first["session_id"]))

    assert duplicate["status"] == "DUPLICATE"
    assert final["total_output_pages"] == 1
    assert synthetic_pdf_page_texts(str(_finalized_records(runtime)[0]["assembled_pdf"])) == ["once"]


def test_crash_recovery_is_idempotent_at_claim_assembly_and_finalization(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "crash.pdf", "stable")

    claim = assembler.ingest(source, profile, discovered_at=1)
    restarted = MfpScanSessionAssembler(FamilyDocumentRuntime.open(runtime.root))
    first = restarted.finalize_session(str(claim["session_id"]))
    second = restarted.finalize_session(str(claim["session_id"]))

    assert first == second
    records = _finalized_records(runtime)
    assert len(records) == 1
    assert len(list((runtime.root / "assemblies").glob("*.pdf"))) == 1


def test_ambiguous_separator_fails_closed_to_review(tmp_path: Path) -> None:
    assembler, profile, _runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "ambiguous.pdf", f"note {STRICT_SEPARATOR_MARKER}")

    claim = assembler.ingest(source, profile, discovered_at=1)
    final = assembler.finalize_session(str(claim["session_id"]))

    assert final["status"] == "REVIEW"
    assert final["reason"] == "AMBIGUOUS_SEPARATOR"


def test_readback_verification_precedes_ocr_and_originals_stay_unchanged(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "source.pdf", "immutable")
    before = source.read_bytes()

    claim = assembler.ingest(source, profile, discovered_at=1)
    final = assembler.finalize_session(str(claim["session_id"]))
    record = _finalized_records(runtime)[0]

    assert final["readback_verified_before_ocr"] is True
    assert record["readback_verified_before_ocr"] is True
    assert source.read_bytes() == before


def test_public_output_has_no_private_markers_or_per_document_metadata(tmp_path: Path) -> None:
    assembler, profile, runtime = _assembler(tmp_path)
    source = _pdf(tmp_path / "private.pdf", "private name alpha")

    claim = assembler.ingest(source, profile, discovered_at=1)
    final = assembler.finalize_session(str(claim["session_id"]))
    public_json = json.dumps(final, sort_keys=True)

    assert "private name alpha" not in public_json
    assert str(source) not in public_json
    assert "assembled_pdf" not in public_json
    handoff = private_repair_handoff(
        runtime=runtime,
        repair_id="repair-synthetic-1",
        component_record_ids=["component-a", "component-b"],
        supersedes_document_ids=["doc-left", "doc-right"],
        merged_document_id="doc-merged",
    )
    assert handoff["delete_original_records"] is False
    assert handoff["relations"] == ["component_of", "supersedes"]
