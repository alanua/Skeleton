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
