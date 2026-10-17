"""Offline artifact staging from a disposable local repository only."""
from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import subprocess
import zipfile
import json
import pytest

from scripts.workstation_bundle import FILES, BundleRejected, build_bundle


def _cmd(root, *argv):
    return subprocess.check_output(
        ["git", "-C", str(root), *argv], stderr=subprocess.DEVNULL
    ).decode().strip()


@pytest.fixture
def fixture_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    origin = Path(__file__).resolve().parents[1]
    for name in FILES:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin / name, repo / name)
    _cmd(repo, "init", "-q")
    _cmd(repo, "config", "user.name", "Synthetic Tester")
    _cmd(repo, "config", "user.email", "synthetic@example.invalid")
    _cmd(repo, "add", ".")
    _cmd(repo, "commit", "-qm", "Synthetic reviewed source")
    return repo, _cmd(repo, "rev-parse", "HEAD"), tmp_path


def test_bundle_reproducible_digest_and_exact_files(fixture_repo):
    repo, head, root = fixture_repo
    a, b = root / "review1.zip", root / "review2.zip"
    status = build_bundle(repo, a, expected_head=head)
    assert status["install_ready"] is False
    assert status["file_count"] == len(FILES)
    build_bundle(repo, b, expected_head=head)
    assert a.read_bytes() == b.read_bytes()
    with zipfile.ZipFile(a) as z:
        assert set(z.namelist()) == set(FILES) | {"MANIFEST.json"}
        manifest = json.loads(z.read("MANIFEST.json"))
        assert manifest["source_head"] == head
        assert not manifest["install_ready"]
        for path in FILES:
            assert manifest["files"][path] == hashlib.sha256(z.read(path)).hexdigest()


def test_bundle_rejects_dirty_tree_wrong_head_and_in_repo_path(fixture_repo):
    repo, head, root = fixture_repo
    with pytest.raises(BundleRejected, match="HEAD_MISMATCH"):
        build_bundle(repo, root/"x.zip", expected_head="0"*40)
    with pytest.raises(BundleRejected, match="BUNDLE_INSIDE_SOURCE_REPO"):
        build_bundle(repo, repo/"x.zip", expected_head=head)
    (repo / FILES[0]).write_text("tampered")
    with pytest.raises(BundleRejected, match="DIRTY_SOURCE_TREE"):
        build_bundle(repo, root/"y.zip", expected_head=head)
