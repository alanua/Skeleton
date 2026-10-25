from __future__ import annotations

from core.family_document_sources import LocalDirectoryDocumentSource


def test_stable_file_gate_requires_two_identical_observations(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "scan.txt").write_text("synthetic", encoding="utf-8")
    source = LocalDirectoryDocumentSource(inbox)

    assert source.scan() == []
    stable = source.scan()

    assert len(stable) == 1
    assert stable[0].stable is True
    assert len(stable[0].sha256) == 64


def test_restart_requires_two_new_observations(tmp_path):
    (tmp_path / "scan.txt").write_bytes(b"synthetic")
    assert LocalDirectoryDocumentSource(tmp_path).scan() == []
    restarted = LocalDirectoryDocumentSource(tmp_path)
    assert restarted.scan() == []
    assert len(restarted.scan()) == 1


def test_mutation_during_hash_is_retryable(tmp_path, monkeypatch):
    import core.family_document_sources as module

    path = tmp_path / "scan.txt"
    path.write_bytes(b"synthetic")
    source = LocalDirectoryDocumentSource(tmp_path)
    source.scan()
    original = module._sha256_file

    def mutate(candidate):
        digest = original(candidate)
        candidate.write_bytes(b"synthetic replacement")
        return digest

    monkeypatch.setattr(module, "_sha256_file", mutate)
    assert source.scan() == []
    monkeypatch.setattr(module, "_sha256_file", original)
    assert source.scan() == []
    assert len(source.scan()) == 1


def test_mutation_during_descriptor_readback_is_rejected(tmp_path, monkeypatch):
    import pytest
    import core.family_document_sources as module

    path = tmp_path / "scan.txt"
    path.write_bytes(b"synthetic")
    original = module.os.fstat
    calls = []

    def mutate(descriptor):
        calls.append(1)
        if len(calls) == 2:
            path.write_bytes(b"synthetic changed during readback")
        return original(descriptor)

    monkeypatch.setattr(module.os, "fstat", mutate)
    with pytest.raises(ValueError, match="source snapshot changed"):
        module.read_snapshot(path)


def test_scan_output_without_session_verifier_is_never_exposed(tmp_path):
    (tmp_path / ('scan-' + 'a' * 64 + '.pdf')).write_bytes(b'synthetic orphan')
    source = LocalDirectoryDocumentSource(tmp_path)
    assert source.scan() == []
    assert source.scan() == []


def test_scan_verifier_does_not_block_regular_documents(tmp_path):
    (tmp_path / 'ordinary.txt').write_text('synthetic document')
    source = LocalDirectoryDocumentSource(tmp_path, scan_sessions=object())
    assert source.scan() == []
    documents = source.scan()
    assert [document.path.name for document in documents] == ['ordinary.txt']
    assert documents[0].scan_provenance is None

