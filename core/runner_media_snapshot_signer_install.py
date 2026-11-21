"""Read-only preflight for issue 4922; deliberately not a registered write action.

Future transport contract (not implemented or accepted as a callback here): an
already audited authenticated maintenance endpoint must bind the fixed action,
operator approval reference, both canonical main SHAs, and all four blob IDs.
It must independently revalidate these inputs, stage/hash the installer as inert
root:root 0555 data at PROTECTED_INSTALLER (with root:root 0755 parents), then
execute only that protected copy with the validated media checkout. It must
preserve the reviewed installer's rollback and verify all resulting artifacts.
Neither a generic command executor nor the older ESP signer gateway satisfies
this contract. No request may be sent until that endpoint is separately audited.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess

ACTION_ID = "home_edge_media_snapshot_signer_reconcile_v1"
SKELETON_SHA = "3d347a7e54639b86c34c52eecb23fb83fd021871"
MEDIA_SHA = "475df11fa38f910cc4f81a4dfbf535d04b7ad092"
# A required operator reference, not an assertion that approval has been granted.
OPERATOR_APPROVAL = "alanua/Skeleton#4922:home_edge_media_snapshot_signer_reconcile_v1"
INSTALLER_SOURCE = "scripts/install_home_edge_media_source_snapshot_signer.sh"
BLOBS = {
    INSTALLER_SOURCE: ("100755", "b26493da46686a96d0edc5c79efb6ecba6a0045c"),
    "scripts/home_edge_media_source_snapshot_signer_payload.py": ("100644", "71bd0a237f4180fc52694a86c596fadf0b2ed687"),
    "scripts/home_edge_media_source_snapshot_signer": ("100644", "24620d9e9fe4f62c055113e6aeefb2d0984be2d5"),
    "skeleton_media/home_edge/media_source_snapshot.py": ("100644", "3e0a27bdb229db594813c2ea7bd8af035625e565"),
}
PROTECTED_INSTALLER = Path("/usr/local/libexec/skeleton/home-edge/media-source-snapshot-installer/install_home_edge_media_source_snapshot_signer.sh")
PROTECTED_FILES = {
    PROTECTED_INSTALLER: (BLOBS[INSTALLER_SOURCE][1], 0o555),
    Path("/usr/local/lib/skeleton/home-edge/media-source-snapshot/signer_payload.py"): (BLOBS["scripts/home_edge_media_source_snapshot_signer_payload.py"][1], 0o555),
    Path("/usr/local/lib/skeleton/home-edge/media-source-snapshot/contract_source.py"): (BLOBS["skeleton_media/home_edge/media_source_snapshot.py"][1], 0o444),
    Path("/usr/local/libexec/skeleton/home-edge/media-source-snapshot/signer"): (BLOBS["scripts/home_edge_media_source_snapshot_signer"][1], 0o555),
}


class PreflightBlocked(Exception):
    pass


def _git(checkout: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "--no-replace-objects", *args], cwd=checkout,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
    )
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise PreflightBlocked("repository_verification_failed")
    return result.stdout


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _regular_bytes(path: Path, mode: int | None = None) -> bytes:
    # Reject symlinks in every component, including checkout and protected parents.
    for parent in reversed(path.absolute().parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise PreflightBlocked("unsafe_path")
        if mode is not None and (info.st_uid or info.st_gid or stat.S_IMODE(info.st_mode) & 0o022):
            raise PreflightBlocked("protected_parent_unsafe")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256 * 1024:
            raise PreflightBlocked("unsafe_file")
        if mode is not None and (info.st_uid or info.st_gid or stat.S_IMODE(info.st_mode) != mode):
            raise PreflightBlocked("protected_file_unsafe")
        if mode is None and stat.S_IMODE(info.st_mode) & 0o022:
            raise PreflightBlocked("source_file_unsafe")
        return source.read(256 * 1024 + 1)


def _checkout(path: Path, sha: str, url: str) -> None:
    if path.is_symlink() or path.resolve() != path.absolute():
        raise PreflightBlocked("unsafe_checkout")
    if _git(path, "remote", "get-url", "origin").strip() != url.encode():
        raise PreflightBlocked("canonical_origin_mismatch")
    for ref in ("HEAD", "refs/remotes/origin/main"):
        if _git(path, "rev-parse", "--verify", ref + "^{commit}").strip() != sha.encode():
            raise PreflightBlocked("checkout_sha_mismatch")
    if _git(path, "status", "--porcelain", "--untracked-files=all"):
        raise PreflightBlocked("checkout_dirty")
    if _git(path, "ls-remote", "--exit-code", url, "refs/heads/main").strip() != (sha + "\trefs/heads/main").encode():
        raise PreflightBlocked("canonical_main_sha_mismatch")


def preflight_media_snapshot_signer_reconcile(
    *, skeleton_checkout: Path, media_checkout: Path, operator_approval_ref: str,
) -> dict[str, object]:
    """Inspect only. Always BLOCKED; no transport, staging, root execution or writes."""
    receipt: dict[str, object] = {
        "action_id": ACTION_ID, "status": "BLOCKED", "reason": "protected_transport_unavailable",
        "mutation_performed": False, "request_submitted": False,
        "source_verified": False, "protected_revision": "unverified",
    }
    try:
        if operator_approval_ref != OPERATOR_APPROVAL:
            raise PreflightBlocked("operator_approval_required")
        _checkout(skeleton_checkout, SKELETON_SHA, "https://github.com/alanua/Skeleton.git")
        _checkout(media_checkout, MEDIA_SHA, "https://github.com/alanua/skeleton-media.git")
        for relative, (mode, blob) in BLOBS.items():
            expected = f"{mode} blob {blob}\t{relative}".encode()
            if _git(media_checkout, "ls-tree", MEDIA_SHA, "--", relative).strip() != expected:
                raise PreflightBlocked("reviewed_blob_mismatch")
            if _blob(_git(media_checkout, "cat-file", "blob", blob)) != blob:
                raise PreflightBlocked("reviewed_blob_mismatch")
            if _blob(_regular_bytes(media_checkout / relative)) != blob:
                raise PreflightBlocked("source_bytes_mismatch")
        receipt["source_verified"] = True
        current = True
        for path, (blob, mode) in PROTECTED_FILES.items():
            current = (_blob(_regular_bytes(path, mode)) == blob) and current
        receipt["protected_revision"] = "reviewed" if current else "older_or_different"
    except PreflightBlocked as exc:
        receipt["reason"] = str(exc)
    except (OSError, subprocess.SubprocessError):
        receipt["reason"] = "preflight_unavailable"
    return receipt
