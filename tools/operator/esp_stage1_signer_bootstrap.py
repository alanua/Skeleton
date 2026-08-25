#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
EXPECTED_MAIN = '8b04a008ddea5cde84c7c25923505e770646d399'
EXPECTED_REMOTE = 'https://github.com/alanua/Skeleton.git'
INSTALLER_SOURCE = 'scripts/install_home_edge_esp_lab_activation_signer.sh'
INSTALLER_BLOB = 'e75960eb28ec59c3c0c78a052c3563b911b0423a'
INSTALLER_SHA256 = '3051ff5009b5a3d370b1f55aa60c01d63baef58931fccbfde8a482e43b11a8d0'
WRAPPER_BLOB = 'd248088477a7c59219a9c19c47bcfc464c6dcd27'
PAYLOAD_BLOB = '7c86372f8eaacc9e4100070eee07336bf2703249'
STAGE1_INSTALLER_BLOB = '1527705a28127a88cf24199706a75fd77a79894c'
SUDOERS_SHA256 = 'b7e0c12abca7dd59238f285dff3c83b4f8c6bbf26235154c45e54c8a705f34a4'
PROTECTED_INSTALLER = Path('/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh')
WRAPPER = Path('/usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer')
PAYLOAD = Path('/usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py')
STAGE1_INSTALLER = Path('/usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh')
SUDOERS = Path('/etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer')


def run(argv: list[str], *, cwd: Path | None = None, timeout: int = 60) -> str:
    cp = subprocess.run(argv, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if cp.returncode != 0:
        raise RuntimeError(f'command_failed:{argv[0]}:{cp.returncode}')
    return cp.stdout.strip()


def git(*args: str) -> str:
    return run(['/usr/bin/git', *args], cwd=REPO)


def require_regular(path: Path, mode: int, *, uid: int = 0, gid: int = 0) -> bytes:
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise RuntimeError('post_audit_not_regular')
    if st.st_uid != uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != mode:
        raise RuntimeError('post_audit_metadata_mismatch')
    return path.read_bytes()


def blob_bytes(blob: str) -> bytes:
    cp = subprocess.run(['/usr/bin/git', 'cat-file', 'blob', blob], cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if cp.returncode != 0 or not cp.stdout:
        raise RuntimeError('trusted_blob_unavailable')
    return cp.stdout


def verify_blob_file(path: Path, blob: str, mode: int) -> None:
    actual = require_regular(path, mode)
    if actual != blob_bytes(blob):
        raise RuntimeError('installed_blob_mismatch')


def main() -> int:
    if os.geteuid() != 0:
        print('STATUS=NEEDS_ROOT')
        return 20
    if not REPO.is_dir() or REPO.is_symlink():
        raise RuntimeError('canonical_checkout_unavailable')
    if git('config', '--get', 'remote.origin.url') != EXPECTED_REMOTE:
        raise RuntimeError('origin_mismatch')
    if git('branch', '--show-current') != 'main':
        raise RuntimeError('branch_mismatch')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('checkout_dirty')
    git('fetch', '--quiet', 'origin', 'main')
    head = git('rev-parse', '--verify', 'HEAD^{commit}')
    origin_main = git('rev-parse', '--verify', 'origin/main^{commit}')
    remote_line = run(['/usr/bin/git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/main'], cwd=REPO)
    if remote_line != f'{EXPECTED_MAIN}\trefs/heads/main':
        raise RuntimeError('remote_main_mismatch')
    if head != EXPECTED_MAIN or origin_main != EXPECTED_MAIN:
        raise RuntimeError('exact_main_mismatch')
    tree_line = git('ls-tree', EXPECTED_MAIN, INSTALLER_SOURCE)
    if tree_line != f'100755 blob {INSTALLER_BLOB}\t{INSTALLER_SOURCE}':
        raise RuntimeError('installer_tree_mismatch')
    installer = blob_bytes(INSTALLER_BLOB)
    if hashlib.sha256(installer).hexdigest() != INSTALLER_SHA256:
        raise RuntimeError('installer_hash_mismatch')

    tmp_path: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(prefix='skeleton-esp-stage1-signer-', dir='/run')
        tmp_path = Path(raw)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(installer)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(tmp_path, 0, 0)
        os.chmod(tmp_path, 0o500)
        if tmp_path.read_bytes() != installer:
            raise RuntimeError('staging_mismatch')

        run(['/usr/bin/install', '-D', '-o', 'root', '-g', 'root', '-m', '0555', str(tmp_path), str(PROTECTED_INSTALLER)])
        protected = require_regular(PROTECTED_INSTALLER, 0o555)
        if protected != installer:
            raise RuntimeError('protected_installer_mismatch')

        # Re-check authority immediately before the privileged installer runs.
        if git('status', '--porcelain', '--untracked-files=all'):
            raise RuntimeError('checkout_became_dirty')
        if git('rev-parse', '--verify', 'HEAD^{commit}') != EXPECTED_MAIN:
            raise RuntimeError('head_moved')
        if run(['/usr/bin/git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/main'], cwd=REPO) != f'{EXPECTED_MAIN}\trefs/heads/main':
            raise RuntimeError('remote_main_moved')

        run([str(PROTECTED_INSTALLER), '--repo-root', str(REPO)], timeout=120)

        verify_blob_file(WRAPPER, WRAPPER_BLOB, 0o555)
        verify_blob_file(PAYLOAD, PAYLOAD_BLOB, 0o555)
        verify_blob_file(STAGE1_INSTALLER, STAGE1_INSTALLER_BLOB, 0o444)
        sudoers = require_regular(SUDOERS, 0o440)
        if hashlib.sha256(sudoers).hexdigest() != SUDOERS_SHA256:
            raise RuntimeError('sudoers_hash_mismatch')
        run(['/usr/sbin/visudo', '-cf', str(SUDOERS)])

        print('STATUS=DONE')
        print(f'MAIN_SHA={EXPECTED_MAIN}')
        print(f'INSTALLER_SHA256={INSTALLER_SHA256}')
        print('SIGNER_VERIFIED=true')
        print('SUDOERS_VERIFIED=true')
        print('ACTIVATION_EXECUTED=false')
        return 0
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('STATUS=BLOCKED')
        print(f'REASON={str(exc).splitlines()[0][:160]}')
        print('ACTIVATION_EXECUTED=false')
        raise SystemExit(1)
