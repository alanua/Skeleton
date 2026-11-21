from pathlib import Path

import pytest

from core import runner_media_snapshot_signer_install as signer


def test_approval_gates_all_io(monkeypatch):
    monkeypatch.setattr(signer, "_git", lambda *a: pytest.fail("unexpected git"))
    result = signer.preflight_media_snapshot_signer_reconcile(
        skeleton_checkout=Path("/missing"), media_checkout=Path("/missing"),
        operator_approval_ref="",
    )
    assert result["reason"] == "operator_approval_required"
    assert result["status"] == "BLOCKED"


@pytest.fixture
def synthetic(monkeypatch):
    calls = []
    data = {path: path.encode() for path in signer.BLOBS}
    pins = {path: (mode, signer._blob(data[path])) for path, (mode, _) in signer.BLOBS.items()}
    monkeypatch.setattr(signer, "BLOBS", pins)
    monkeypatch.setattr(signer, "PROTECTED_FILES", {Path("/protected/installer"): ("0" * 40, 0o555)})

    def git(path, *args):
        calls.append(args)
        sha = signer.SKELETON_SHA if path == Path("/skeleton") else signer.MEDIA_SHA
        if args[:3] == ("remote", "get-url", "origin"):
            return ("https://github.com/alanua/" + ("Skeleton" if path == Path("/skeleton") else "skeleton-media") + ".git").encode()
        if args[0] == "rev-parse":
            return sha.encode()
        if args[0] == "status":
            return b""
        if args[0] == "ls-remote":
            return (sha + "\trefs/heads/main\n").encode()
        if args[0] == "ls-tree":
            mode, blob = pins[args[-1]]
            return f"{mode} blob {blob}\t{args[-1]}".encode()
        if args[0] == "cat-file":
            return next(data[p] for p, (_, blob) in pins.items() if blob == args[-1])
        pytest.fail(str(args))

    monkeypatch.setattr(signer, "_git", git)
    monkeypatch.setattr(signer, "_regular_bytes", lambda path, mode=None: b"old installer" if mode else data[str(path.relative_to('/media'))])
    return git, calls


def run():
    return signer.preflight_media_snapshot_signer_reconcile(
        skeleton_checkout=Path("/skeleton"), media_checkout=Path("/media"),
        operator_approval_ref=signer.OPERATOR_APPROVAL,
    )


def test_old_protected_installer_missing_transport_no_side_effects(synthetic):
    result = run()
    assert result == {
        "action_id": signer.ACTION_ID, "status": "BLOCKED",
        "reason": "protected_transport_unavailable", "source_verified": True,
        "protected_revision": "older_or_different", "mutation_performed": False,
        "request_submitted": False,
    }
    assert all(args[0] in {"remote", "rev-parse", "status", "ls-remote", "ls-tree", "cat-file"} for args in synthetic[1])


@pytest.mark.parametrize("command,output,reason", [
    ("status", b" M tracked", "checkout_dirty"),
    ("rev-parse", b"a" * 40, "checkout_sha_mismatch"),
    ("ls-remote", b"a" * 40, "canonical_main_sha_mismatch"),
    ("ls-tree", b"100644 blob bad\tsource", "reviewed_blob_mismatch"),
    ("cat-file", b"tampered", "reviewed_blob_mismatch"),
    ("remote", b"https://example.invalid/repo", "canonical_origin_mismatch"),
])
def test_invalid_repository_inputs(synthetic, monkeypatch, command, output, reason):
    git, _ = synthetic
    monkeypatch.setattr(signer, "_git", lambda path, *args: output if args[0] == command else git(path, *args))
    assert run()["reason"] == reason


def test_symlink_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"inert")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(OSError):
        signer._regular_bytes(link)
    directory = tmp_path / "directory"
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(signer.PreflightBlocked, match="unsafe_path"):
        signer._regular_bytes(directory / "source")


def test_reviewed_pins():
    assert signer.BLOBS[signer.INSTALLER_SOURCE][1] == "b26493da46686a96d0edc5c79efb6ecba6a0045c"
    assert {blob for _, blob in signer.BLOBS.values()} >= {
        "71bd0a237f4180fc52694a86c596fadf0b2ed687",
        "24620d9e9fe4f62c055113e6aeefb2d0984be2d5",
        "3e0a27bdb229db594813c2ea7bd8af035625e565",
    }


@pytest.mark.parametrize("uid,gid,mode", [(1, 0, 0o555), (0, 1, 0o555), (0, 0, 0o755)])
def test_protected_owner_and_exact_mode(tmp_path, monkeypatch, uid, gid, mode):
    import os
    import stat
    from types import SimpleNamespace

    source = tmp_path / "installer"
    source.write_bytes(b"inert")
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0))
    monkeypatch.setattr(os, "fstat", lambda fd: SimpleNamespace(
        st_mode=stat.S_IFREG | mode, st_uid=uid, st_gid=gid, st_size=5))
    with pytest.raises(signer.PreflightBlocked, match="protected_file_unsafe"):
        signer._regular_bytes(source, 0o555)
