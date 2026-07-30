from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.scan_report_manifest import (
    PrivateDownloadLinkProvider,
    ScanReportDeliveryStore,
    ScanReportError,
    build_scan_report_manifest,
    deliver_scan_report,
    render_telegram_report,
    report_idempotency_key,
    validate_scan_report_manifest,
    verify_pdf_artifact,
)


def _pdf(path: Path, pages: int) -> Path:
    objects: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{3 + index} 0 R' for index in range(pages))}] /Count {pages} >>",
    ]
    for index in range(pages):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents {3 + pages + index} 0 R >>")
    for _index in range(pages):
        objects.append("<< /Length 0 >>\nstream\n\nendstream")
    body = "%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body.encode("latin-1")))
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    xref_at = len(body.encode("latin-1"))
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n"
    body += f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_at}\n%%EOF\n"
    path.write_bytes(body.encode("latin-1"))
    return path


def _provider() -> PrivateDownloadLinkProvider:
    return PrivateDownloadLinkProvider(
        base_url="https://download.example.invalid",
        secret="synthetic-secret",
        ttl_seconds=900,
    )


def _classification(overall: float = 0.96) -> dict[str, object]:
    return {
        "route": "ACCEPT" if overall >= 0.8 else "REVIEW",
        "principal_subject_alias": "person-a",
        "topic_alias": "04 work_tax_and_business",
        "jurisdiction_country": "Germany",
        "document_date": "2026-07-29",
        "document_type": "Bescheid",
        "issuer": "Synthetic Sender",
        "summary": "Synthetic document about a benefits decision and next steps.",
        "confidence": {
            "overall": overall,
            "owner": overall,
            "topic": overall,
            "jurisdiction": overall,
            "date": overall,
            "document_type": overall,
            "issuer": overall,
        },
        "reason_codes": [] if overall >= 0.8 else ["LOW_CONFIDENCE"],
    }


def _package(tmp_path: Path, *, low_confidence: bool = False, bad_second_pdf: bool = False) -> dict[str, object]:
    doc1_original = _pdf(tmp_path / "doc1-original.pdf", 2)
    doc1_searchable = _pdf(tmp_path / "doc1-searchable.pdf", 2)
    doc2_original = _pdf(tmp_path / "doc2-original.pdf", 1 if bad_second_pdf else 2)
    doc2_searchable = _pdf(tmp_path / "doc2-searchable.pdf", 2)
    return {
        "session_id": "session-2026-07-29-a",
        "package_id": "package-2026-07-29-a",
        "physical_page_count": 4,
        "mandatory_stages": [
            "scan",
            "session_assembly",
            "document_boundary_decision",
            "classification",
            "local_storage",
            "page_count_and_hash_verification",
        ],
        "documents": [
            {
                "document_id": "doc-001",
                "pages": [1, 2],
                "title": "Jobcenter decision",
                "sender": "Synthetic Sender",
                "recipient_owner": "person-a",
                "date": "2026-07-29",
                "country": "Germany",
                "document_type": "Bescheid",
                "topic": "work_tax_and_business",
                "classification": _classification(),
                "ocr_quality": 0.95,
                "original_stitched_pdf": str(doc1_original),
                "searchable_pdf": str(doc1_searchable),
                "human_storage_path": "person-a/documents/work_tax_and_business/2026",
            },
            {
                "document_id": "doc-002",
                "pages": [3, 4],
                "title": "Insurance notice",
                "sender": "Synthetic Insurer",
                "recipient_owner": "person-a",
                "date": "2026-07-29",
                "country": "Germany",
                "document_type": "Notice",
                "topic": "health_and_insurance",
                "classification": _classification(0.42 if low_confidence else 0.91),
                "ocr_quality": 0.55 if low_confidence else 0.91,
                "original_stitched_pdf": str(doc2_original),
                "searchable_pdf": str(doc2_searchable),
                "human_storage_path": "person-a/documents/health_and_insurance/2026",
            },
        ],
    }


def test_synthetic_multi_document_package_builds_manifest_and_cards(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path), link_provider=_provider())
    validate_scan_report_manifest(manifest)

    assert manifest["package_summary"] == {
        "package_id": "package-2026-07-29-a",
        "physical_page_count": 4,
        "logical_document_count": 2,
        "completed_count": 2,
        "review_required_count": 0,
        "failed_count": 0,
        "overall_status": "success",
    }
    assert [document["page_range"] for document in manifest["documents"]] == ["1-2", "3-4"]
    messages = render_telegram_report(manifest)
    assert len(messages) == 3
    assert "Сканування завершено" in messages[0][0]
    assert "person-a/documents/work_tax_and_business/2026" in messages[1][0]


def test_original_download_is_verified_stitched_pdf_with_hash(tmp_path: Path) -> None:
    package = _package(tmp_path)
    manifest = build_scan_report_manifest(package, link_provider=_provider())
    original = manifest["documents"][0]["artifacts"]["original_stitched_pdf"]

    assert original["page_count"] == 2
    assert re.fullmatch(r"[a-f0-9]{64}", original["sha256"])
    assert original["link"]["url"].startswith("https://download.example.invalid/download?")
    assert "doc1-original.pdf" not in original["link"]["url"]
    verified = verify_pdf_artifact(
        package["documents"][0]["original_stitched_pdf"],
        expected_page_count=2,
        artifact_id="synthetic-artifact",
        kind="original_stitched_pdf",
    )
    assert verified.sha256 == original["sha256"]


def test_low_confidence_is_marked_review_required_without_certainty(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path, low_confidence=True), link_provider=_provider())
    document = manifest["documents"][1]

    assert manifest["overall_status"] == "review_required"
    assert document["processing_status"] == "review_required"
    assert document["summary_reliability"] == "unreliable"
    assert document["summary"].startswith("UNRELIABLE - review required:")
    assert "LOW_CONFIDENCE_OR_OCR" in document["review_reason_codes"]
    assert "REVIEW REQUIRED" in render_telegram_report(manifest)[2][0]


def test_partial_run_records_failed_stage_and_retained_artifacts(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path, bad_second_pdf=True), link_provider=_provider())
    assert manifest["overall_status"] == "partial_success"
    assert manifest["package_summary"]["failed_count"] == 1
    assert manifest["audit"]["verification_status"] == "failed"
    assert manifest["audit"]["failures"][0]["stage"] == "original_stitched_pdf"
    assert "searchable_pdf" in manifest["documents"][1]["artifacts"]


def test_duplicate_replay_does_not_send_duplicate_telegram_messages(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path), link_provider=_provider())
    store = ScanReportDeliveryStore(tmp_path / "scan-report.sqlite")
    sent: list[str] = []

    def sender(text: str, _reply_markup: dict[str, object] | None) -> int:
        sent.append(text)
        return len(sent)

    first = deliver_scan_report(manifest, store=store, sender=sender)
    second = deliver_scan_report(manifest, store=store, sender=sender)

    assert first["status"] == "delivered"
    assert second["idempotency"] == "duplicate_replay"
    assert len(sent) == 3
    assert store.audit_count(report_idempotency_key("session-2026-07-29-a", 1)) == 1


def test_changed_boundary_supersedes_prior_audit_record(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path), link_provider=_provider())
    changed_root = tmp_path / "changed"
    changed_root.mkdir()
    changed_package = _package(changed_root)
    changed_package["documents"][0]["pages"] = [1]
    changed_package["documents"][0]["original_stitched_pdf"] = str(_pdf(tmp_path / "changed-doc1-original.pdf", 1))
    changed_package["documents"][0]["searchable_pdf"] = str(_pdf(tmp_path / "changed-doc1-searchable.pdf", 1))
    changed_package["documents"][1]["pages"] = [2, 3, 4]
    changed_package["documents"][1]["original_stitched_pdf"] = str(_pdf(tmp_path / "changed-doc2-original.pdf", 3))
    changed_package["documents"][1]["searchable_pdf"] = str(_pdf(tmp_path / "changed-doc2-searchable.pdf", 3))
    changed_manifest = build_scan_report_manifest(changed_package, link_provider=_provider())
    store = ScanReportDeliveryStore(tmp_path / "scan-report.sqlite")

    deliver_scan_report(manifest, store=store, sender=lambda *_args: 1)
    receipt = deliver_scan_report(changed_manifest, store=store, sender=lambda *_args: 2)

    assert receipt["idempotency"] == "superseded"
    assert store.audit_count(report_idempotency_key("session-2026-07-29-a", 1)) == 3


def test_telegram_failure_is_queued_for_retry_without_rebuilding_artifacts(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path), link_provider=_provider())
    store = ScanReportDeliveryStore(tmp_path / "scan-report.sqlite")
    calls = 0

    def failing_sender(_text: str, _reply_markup: dict[str, object] | None) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("telegram-token-never-leaks")

    receipt = deliver_scan_report(manifest, store=store, sender=failing_sender)
    assert receipt["status"] == "failed"
    assert "telegram-token-never-leaks" not in json.dumps(receipt)
    assert store.retry_count() == 1
    assert calls == 1


def test_telegram_text_contains_no_raw_paths_or_secrets(tmp_path: Path) -> None:
    manifest = build_scan_report_manifest(_package(tmp_path), link_provider=_provider())
    rendered = json.dumps(render_telegram_report(manifest), sort_keys=True)

    assert str(tmp_path) not in rendered
    assert "synthetic-secret" not in rendered
    assert "SKELETON_TG_BOT" not in rendered
    assert "https://download.example.invalid/download?" in rendered


def test_inconsistent_page_mapping_fails_before_reporting(tmp_path: Path) -> None:
    package = _package(tmp_path)
    package["documents"][1]["pages"] = [2, 4]
    with pytest.raises(ScanReportError, match="duplicate"):
        build_scan_report_manifest(package, link_provider=_provider())
