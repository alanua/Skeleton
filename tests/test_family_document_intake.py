import json

from core.family_document_intake import Config, Intake, Person, TOPICS, stable_file


def people():
    return (Person('a', ('Alice',)), Person('b', ('Bob',)), Person('c', ('Carol',)))


def config(tmp_path):
    return Config(people(), tmp_path / 'archive', tmp_path / 'state.json', tmp_path / 'outbox.json', ('memory',), ('calendar',), 1)


def ok_runner(argv, input_text):
    return 0, json.dumps({'status': 'DONE'}), ''


def test_taxonomy_and_visible_name(tmp_path):
    source = tmp_path / 'scan.txt'
    source.write_text('Alice\nIssuer: Finanzamt\nRechnung 2026-07-20 Steuer', encoding='utf-8')
    plan = Intake(config(tmp_path), runner=ok_runner).plan(source)
    assert plan['ready'] is True
    assert plan['relative'].parts[1] == TOPICS[3]
    assert '2026-07-20 — invoice — Finanzamt.txt' in str(plan['relative'])


def test_ambiguous_document_routes_to_review(tmp_path):
    source = tmp_path / 'scan.txt'
    source.write_text('unknown document', encoding='utf-8')
    assert Intake(config(tmp_path), runner=ok_runner).process(source)['status'] == 'REVIEW'


def test_dry_run_has_no_side_effects(tmp_path):
    source = tmp_path / 'scan.txt'
    source.write_text('Alice\nIssuer: Finanzamt\nRechnung 2026-07-20 Steuer', encoding='utf-8')
    receipt = Intake(config(tmp_path), runner=ok_runner).process(source, dry_run=True)
    assert receipt['counts']['written'] == 0
    assert not (tmp_path / 'archive').exists()


def test_archive_precedes_memory_and_outbox_is_durable(tmp_path):
    calls = []
    def runner(argv, input_text):
        calls.append((argv, json.loads(input_text)))
        return 0, json.dumps({'status': 'DONE'}), ''
    source = tmp_path / 'scan.txt'
    source.write_text('Alice\nIssuer: Finanzamt\nRechnung 2026-07-20 Steuer deadline', encoding='utf-8')
    receipt = Intake(config(tmp_path), runner=runner).process(source)
    assert receipt['status'] == 'DONE'
    assert calls[0][1]['command'] == 'skeleton.memory.private_mutate'
    assert calls[0][1]['dataset'] == 'family_documents'
    assert json.loads((tmp_path / 'outbox.json').read_text(encoding='utf-8'))


def test_stable_file_requires_two_equal_observations(tmp_path):
    source = tmp_path / 'scan.txt'
    source.write_text('x', encoding='utf-8')
    observations = {}
    assert stable_file(source, observations, 1, 1) is False
    assert stable_file(source, observations, 2, 1) is True


def test_public_receipt_contains_no_private_filename_or_ocr(tmp_path):
    source = tmp_path / 'secret-name.txt'
    source.write_text('unknown private OCR', encoding='utf-8')
    receipt = Intake(config(tmp_path), runner=ok_runner).process(source)
    encoded = json.dumps(receipt)
    assert 'secret-name' not in encoded
    assert 'private OCR' not in encoded
