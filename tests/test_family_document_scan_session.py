import json
from pathlib import Path

import pytest

from core.family_document_scan_session import ScanSessionAssembler, SessionReview, digest
from core.family_document_state import FamilyDocumentReceiptOutbox


class SyntheticCodec:
    def count(self, data):
        return len(json.loads(data))

    def assemble(self, pages):
        return json.dumps([json.loads(data)[index] for data, index in pages]).encode()


@pytest.fixture
def producer(tmp_path):
    return ScanSessionAssembler(FamilyDocumentReceiptOutbox(tmp_path / 'state.sqlite'), tmp_path / 'output', codec=SyntheticCodec())


def event(kind, event_id, **extra):
    return dict(session_id='synthetic-session', scanner_id='scanner-a', profile_id='profile-a', kind=kind, event_id=event_id, **extra)


def start(producer, sheets=8):
    return producer.apply(event('start', 'start', sheet_count=sheets))


def scan_pass(tmp_path, side, sheets, *, absent=(), blank=(), event_id=None):
    path = tmp_path / (side + '.pdf')
    data = json.dumps([f'{side}-{sheet}' for sheet in sheets]).encode()
    path.write_bytes(data)
    pages = [dict(sheet=sheet, kind='blank' if sheet in blank else 'page', path=str(path), page_index=i, sha256=digest(data)) for i, sheet in enumerate(sheets)]
    pages += [dict(sheet=sheet, kind='absent') for sheet in absent]
    return event('pass', event_id or side, pass_id=side, side=side, orientation='physical_slots', pages=pages)


def ready(producer, tmp_path):
    start(producer)
    producer.apply(scan_pass(tmp_path, 'front', list(range(1, 9))))
    producer.apply(scan_pass(tmp_path, 'back', [6, 5, 4, 3, 2, 1], absent=[7, 8]))
    return producer.apply(event('complete', 'complete'))


def test_eight_front_six_back_reverse_order_and_receipt(producer, tmp_path):
    session = ready(producer, tmp_path)
    assert session['status'] == 'FINALIZED'
    document, receipt = producer.verified_document('synthetic-session')
    assert receipt['page_count'] == 14
    assert json.loads(document.path.read_bytes()) == [label for sheet in range(1, 9) for label in ([f'front-{sheet}', f'back-{sheet}'] if sheet <= 6 else [f'front-{sheet}'])]
    assert len(receipt['slots']) == 16
    assert receipt['slots'][1]['page_index'] == 5
    assert 'path' not in json.dumps(receipt)
    assert receipt['completion_event_id'] == 'complete'


def test_blank_and_absent_do_not_shift_later_sheet(producer, tmp_path):
    start(producer, 3)
    producer.apply(scan_pass(tmp_path, 'front', [1, 2, 3]))
    producer.apply(scan_pass(tmp_path, 'back', [3, 1], absent=[2], blank=[1]))
    result = producer.apply(event('complete', 'complete'))
    assert result['status'] == 'FINALIZED'
    doc, receipt = producer.verified_document('synthetic-session')
    assert json.loads(doc.path.read_bytes()) == ['front-1', 'back-1', 'front-2', 'front-3', 'back-3']
    assert receipt['slots'][1]['kind'] == 'blank'
    assert receipt['slots'][3]['kind'] == 'absent'


def test_gap_timeout_restart_and_explicit_completion(producer, tmp_path):
    start(producer, 1)
    front = scan_pass(tmp_path, 'front', [1]); front['observed_at'] = 0
    producer.apply(front)
    assert producer.apply(event('timeout', 'timeout'))['status'] == 'REVIEW'
    assert not producer.output_directory.exists()
    restarted = ScanSessionAssembler(producer.state, producer.output_directory, codec=SyntheticCodec())
    back = scan_pass(tmp_path, 'back', [1]); back['observed_at'] = 1000
    restarted.apply(back)
    assert restarted.apply(event('complete', 'complete'))['status'] == 'FINALIZED'
    assert restarted.apply(event('complete', 'complete')) == restarted.get('synthetic-session')
    assert restarted.apply(front)['status'] == 'FINALIZED'
    before = restarted.get('synthetic-session')
    with pytest.raises(SessionReview, match='IMMUTABLE'):
        restarted.apply(event('complete', 'complete', sheet_count=7))
    assert restarted.get('synthetic-session') == before
    with pytest.raises(SessionReview, match='IMMUTABLE'):
        restarted.apply(event('timeout', 'late-timeout'))


@pytest.mark.parametrize('change', [{'scanner_id': 'scanner-b'}, {'profile_id': 'profile-b'}, {'orientation': 'unknown'}, {'session_id': 'other-session'}])
def test_no_implicit_pairing(producer, tmp_path, change):
    start(producer, 1)
    producer.apply(scan_pass(tmp_path, 'front', [1]))
    back = scan_pass(tmp_path, 'back', [1]); back.update(change)
    assert producer.apply(back)['status'] == 'REVIEW'
    assert producer.apply(event('complete', 'complete'))['status'] == 'REVIEW'
    assert not producer.output_directory.exists()


def test_missing_back_and_conflicting_slots_review(producer, tmp_path):
    start(producer, 2)
    producer.apply(scan_pass(tmp_path, 'front', [1, 2]))
    assert producer.apply(event('complete', 'too-soon'))['status'] == 'REVIEW'
    bad = scan_pass(tmp_path, 'back', [1, 1])
    assert producer.apply(bad)['status'] == 'REVIEW'
    assert not producer.output_directory.exists()


def test_component_snapshot_tamper_review(producer, tmp_path):
    start(producer, 1)
    producer.apply(scan_pass(tmp_path, 'front', [1]))
    producer.apply(scan_pass(tmp_path, 'back', [], absent=[1]))
    (tmp_path / 'front.pdf').write_text('["changed"]')
    assert producer.apply(event('complete', 'complete'))['reason'] == 'COMPONENT_SNAPSHOT_MISMATCH'
    assert not producer.output_directory.exists()


def test_bad_output_count_never_publishes(producer, tmp_path):
    producer.codec.assemble = lambda pages: b'[]'
    assert ready(producer, tmp_path)['reason'] == 'ASSEMBLED_PAGE_COUNT_MISMATCH'
    assert not producer.output_directory.exists()


def test_changed_output_never_reaches_ocr(producer, tmp_path, monkeypatch):
    ready(producer, tmp_path)
    doc, _ = producer.verified_document('synthetic-session')
    doc.path.write_text('["tampered"]')
    monkeypatch.setattr('core.family_document_intake.read_local_document', lambda path: pytest.fail('OCR before verification'))
    with pytest.raises(SessionReview, match='FINAL_SOURCE_CHANGED'):
        producer.write_handoff('synthetic-session', tmp_path / 'handoff')


def test_handoff_retry_uses_existing_consumer_packet(producer, tmp_path, monkeypatch):
    from core.local_document_ocr import OcrResult
    ready(producer, tmp_path)
    doc, receipt = producer.verified_document('synthetic-session')
    monkeypatch.setattr('core.family_document_intake.read_local_document', lambda path: OcrResult('synthetic', 14, 'application/pdf', doc.sha256))
    root = tmp_path / 'handoff'
    packet = producer.write_handoff('synthetic-session', root)
    from core.family_document_local_inference import _HANDOFF_SCHEMA
    from core.local_inference_adapters import validate_json_schema
    validate_json_schema(json.loads(packet.read_bytes()), _HANDOFF_SCHEMA)
    accepted_packet = json.loads(packet.read_bytes())
    assert accepted_packet['idempotency_key'] == receipt['idempotency_key']
    provenance = accepted_packet['payload']['scan_provenance']
    assert (provenance['session_id'], provenance['scanner_id'], provenance['profile_id']) == (
        receipt['session_id'], receipt['scanner_id'], receipt['profile_id'])
    assert [item['page'] for item in provenance['pages']] == list(range(1, 15))
    assert all(set(item) == {'pass_id', 'sheet', 'side', 'page'} for item in provenance['pages'])
    assert 'path' not in str(provenance) and 'component_sha256' not in str(provenance)
    (root / 'accepted').mkdir()
    accepted = root / 'accepted' / packet.name
    packet.rename(accepted)
    assert producer.write_handoff('synthetic-session', root) == accepted
    assert not packet.exists()


def test_final_readback_failure_not_finalized(producer, tmp_path, monkeypatch):
    import core.family_document_scan_session as module
    original = module.atomic_write
    def corrupt(path, data):
        original(path, b'[]')
    monkeypatch.setattr(module, 'atomic_write', corrupt)
    assert ready(producer, tmp_path)['reason'] == 'FINAL_READBACK_FAILED'
    assert list(producer.output_directory.glob('*.pdf')) == []


def test_stable_source_only_exposes_finalized_verified_outputs(producer, tmp_path):
    from core.family_document_sources import LocalDirectoryDocumentSource
    source = LocalDirectoryDocumentSource(producer.output_directory, scan_sessions=producer)
    producer.output_directory.mkdir()
    orphan = producer.output_directory / ('scan-' + 'a' * 64 + '.pdf')
    orphan.write_text('["orphan"]')
    assert source.scan() == []
    assert source.scan() == []
    ready(producer, tmp_path)
    source.scan()
    documents = source.scan()
    assert len(documents) == 1
    assert documents[0].scan_provenance['page_count'] == 14
    documents[0].path.write_text('["modified"]')
    source.scan()
    assert source.scan() == []


def test_crash_after_publish_before_commit_can_replay(producer, tmp_path, monkeypatch):
    start(producer, 1)
    producer.apply(scan_pass(tmp_path, 'front', [1]))
    producer.apply(scan_pass(tmp_path, 'back', [], absent=[1]))
    original = producer._assemble
    def crash(session):
        original(session)
        raise KeyboardInterrupt('synthetic process crash')
    monkeypatch.setattr(producer, '_assemble', crash)
    with pytest.raises(KeyboardInterrupt):
        producer.apply(event('complete', 'complete'))
    assert producer.get('synthetic-session')['status'] == 'OPEN'
    from core.family_document_sources import LocalDirectoryDocumentSource
    source = LocalDirectoryDocumentSource(producer.output_directory, scan_sessions=producer)
    source.scan()
    assert source.scan() == []
    restarted = ScanSessionAssembler(producer.state, producer.output_directory, codec=SyntheticCodec())
    assert restarted.apply(event('complete', 'complete'))['status'] == 'FINALIZED'


def test_component_mutation_during_assembly_is_not_published(producer, tmp_path):
    assemble = producer.codec.assemble
    def mutate(pages):
        (tmp_path / 'front.pdf').write_text('["changed"]')
        return assemble(pages)
    producer.codec.assemble = mutate
    assert ready(producer, tmp_path)['reason'] == 'COMPONENT_CHANGED_DURING_ASSEMBLY'
    assert not producer.output_directory.exists()


def test_verified_producer_flows_through_existing_runtime_once(producer, tmp_path, monkeypatch):
    from core.family_document_runtime import FamilyDocumentRuntime
    from core.family_document_sources import LocalDirectoryDocumentSource
    from core.local_document_ocr import OcrResult
    ready(producer, tmp_path)
    document, receipt = producer.verified_document('synthetic-session')
    original = document.path.read_bytes()
    calls = []
    def ocr(path):
        assert path.read_bytes() == original
        calls.append('ocr')
        return OcrResult('synthetic document', 14, 'application/pdf', document.sha256)
    from tests.test_family_document_runtime import VerifiedSyntheticArchive, synthetic_classification

    class Archive(VerifiedSyntheticArchive):
        def archive(self, record, *, source_path=None):
            calls.append('archive')
            return super().archive(record, source_path=source_path)

    monkeypatch.setattr('core.family_document_intake.read_local_document', ocr)
    runtime = FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(producer.output_directory, scan_sessions=producer),
        archive_sink=Archive(tmp_path / 'archive'),
        outbox=producer.state,
        classifier=lambda _: synthetic_classification(),
        allowed_subject_aliases=('person-a', 'person-b', 'person-c'),
    )
    assert runtime.scan_once(drain=False)['completed_intakes'] == 0
    assert runtime.scan_once(drain=False)['completed_intakes'] == 1
    assert calls == ['ocr', 'archive']
    assert runtime.scan_once(drain=False)['completed_intakes'] == 0
    assert calls == ['ocr', 'archive']
    safe_receipt = producer.state.receipts()[0]['payload']['scan_provenance'][0]
    assert (safe_receipt['session_id'], safe_receipt['scanner_id'], safe_receipt['profile_id']) == (
        receipt['session_id'], receipt['scanner_id'], receipt['profile_id'])
    assert [page['page'] for page in safe_receipt['pages']] == list(range(1, 15))
    assert 'component_sha256' not in str(safe_receipt) and 'event_id' not in str(safe_receipt)
    assert document.path.read_bytes() == original


def test_safe_scan_receipt_schema(producer, tmp_path):
    import jsonschema
    ready(producer, tmp_path)
    from core.family_document_scan_session import safe_scan_provenance
    schema = json.loads((Path(__file__).parents[1] / 'schemas/family_document_receipt.schema.json').read_text())
    safe = safe_scan_provenance(producer.get('synthetic-session')['receipt'])
    jsonschema.validate({
        'schema': 'skeleton.family_document_receipt.v1',
        'receipt_key': 'synthetic-package:1',
        'receipt_type': 'package_part',
        'status': 'DONE',
        'message': 'Synthetic package complete',
        'scan_provenance': [safe],
    }, schema)


def test_unavailable_pdf_backend_routes_review(producer, tmp_path, monkeypatch):
    from core.family_document_scan_session import PdfCodec
    def unavailable(data):
        raise ImportError('synthetic unavailable dependency')
    producer.codec = PdfCodec()
    monkeypatch.setattr(producer.codec, 'reader', unavailable)
    assert ready(producer, tmp_path)['reason'] == 'PDF_VERIFICATION_FAILED'
    assert not producer.output_directory.exists()