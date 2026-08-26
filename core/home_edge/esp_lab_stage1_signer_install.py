from __future__ import annotations

from collections.abc import Callable, Mapping
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Final


HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID: Final = (
    "home_edge_01_esp_lab_stage1_signer_install_v1"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA: Final = (
    "8e049eb631f63d81ab932eac6ab0cf3d3d5a5949"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA: Final = (
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH: Final = (
    "scripts/install_home_edge_esp_lab_activation_signer.sh"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB: Final = (
    "ef285000113c1254170b8924b4c3ab8d82250423"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE: Final = "100755"
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB: Final = (
    "9e349149ea17c38284c8bda1051b3d0de9688d4c"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB: Final = (
    "d248088477a7c59219a9c19c47bcfc464c6dcd27"
)
HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB: Final = (
    "4db8042020915dbcdd261accc5c87a75682fa115"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256: Final = (
    "b7e0c12abca7dd59238f285dff3c83b4f8c6bbf26235154c45e54c8a705f34a4"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL: Final = (
    "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_V2_APPROVED"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER: Final = Path(
    "/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/"
    "install_home_edge_esp_lab_activation_signer.sh"
)
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_PARENT_MODE: Final = 0o755
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLED_ARTIFACTS: Mapping[Path, tuple[str, int]] = {
    Path("/usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer"): (
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB,
        0o555,
    ),
    Path("/usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py"): (
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB,
        0o555,
    ),
    Path("/usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh"): (
        HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB,
        0o444,
    ),
    Path("/etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer"): (
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256,
        0o440,
    ),
}
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_MAX_INSTALLER_BYTES: Final = 128 * 1024
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TIMEOUT_SECONDS: Final = 60
HOME_EDGE_ESP_LAB_STAGE1_SIGNER_EXEC_TIMEOUT_SECONDS: Final = 120
_SUDO_BIN: Final = "/usr/bin/sudo"
_INSTALL_BIN: Final = "/usr/bin/install"
_GIT_BIN: Final = "/usr/bin/git"
_RUNUSER_BIN: Final = "/usr/sbin/runuser"
_GIT_USER: Final = "agent"
_CANONICAL_REMOTE_MAIN_URL: Final = "https://github.com/alanua/Skeleton.git"
_REPO_OUTPUT_LIMIT_BYTES: Final = 64 * 1024
_REMOTE_MAIN_CWD: Final = Path("/")
_CLEAN_GIT_ENV: Final = {
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


class RepositoryMaintenanceBlocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


RepositoryRunCommand = Callable[
    [list[str], Path | None, int | None, Mapping[str, str] | None],
    tuple[int, str],
]
ProtectedRunCommand = Callable[[list[str], int | None], tuple[int, str]]


def _protected_receipt(
    status: str,
    reason: str,
    *,
    expected_main_sha: str | None = None,
    installer_sha256: str | None = None,
    artifacts_ok: bool = False,
    protected_copy_verified: bool | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
        "status": status,
        "reason": re.sub(r"[^A-Z0-9_]+", "_", reason.upper()).strip("_") or "BLOCKED",
        "repository": "alanua/Skeleton",
        "expected_main_sha": expected_main_sha
        or HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA,
        "target": "runner-controller",
        "source_blob": HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB,
        "protected_copy_verified": status == "DONE"
        if protected_copy_verified is None
        else protected_copy_verified,
        "installed_artifacts_verified": artifacts_ok,
        "activation_executed": False,
        "private_evidence_exposed": False,
    }
    if installer_sha256 is not None:
        receipt["installer_sha256"] = installer_sha256
    return receipt


def _protected_result(status: str, receipt: Mapping[str, object]) -> str:
    return (
        f"RESULT: {status}\n"
        "Executor: repository_maintenance.home_edge_esp_lab_stage1_signer_install\n"
        "Model providers called: 0\n"
        "Receipt:\n"
        f"{json.dumps(receipt, indent=2, sort_keys=True)}"
    )


def _protected_blocked(
    reason: str,
    installer_sha256: str | None = None,
    *,
    expected_main_sha: str | None = None,
    protected_copy_verified: bool = False,
) -> tuple[int, str]:
    return 0, _protected_result(
        "NEEDS_OPERATOR",
        _protected_receipt(
            "NEEDS_OPERATOR",
            reason,
            expected_main_sha=expected_main_sha,
            installer_sha256=installer_sha256,
            protected_copy_verified=protected_copy_verified,
        ),
    )


def _repository_run_command(
    args: list[str],
    cwd: Path | None,
    timeout: int | None,
    env: Mapping[str, str] | None,
) -> tuple[int, str]:
    if not args:
        raise RepositoryMaintenanceBlocked("REPOSITORY_COMMAND_EMPTY")
    child_args = list(args)
    if child_args[0] == "git":
        child_args[0] = _GIT_BIN
    if child_args[0] == _GIT_BIN and os.geteuid() == 0:
        child_args = [_RUNUSER_BIN, "-u", _GIT_USER, "--", *child_args]
    clean_env = dict(_CLEAN_GIT_ENV if args[0] in {"git", _GIT_BIN} else {})
    if env:
        clean_env.update({key: value for key, value in env.items() if key in {"PATH", "LANG", "LC_ALL"}})
    completed = subprocess.run(
        child_args,
        cwd=str(cwd) if cwd is not None else None,
        env=clean_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    output = "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    )
    if len(output.encode("utf-8", "replace")) > _REPO_OUTPUT_LIMIT_BYTES:
        raise RepositoryMaintenanceBlocked("REPOSITORY_COMMAND_OUTPUT_TOO_LARGE")
    return completed.returncode, output


def _git_blob_bytes(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
    blob_sha: str,
) -> bytes:
    code, output = run_command([_GIT_BIN, "cat-file", "-s", blob_sha], checkout_path, 30, None)
    if code != 0 or not output.strip().isdecimal():
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_BLOB_UNAVAILABLE")
    size = int(output.strip())
    if size <= 0 or size > HOME_EDGE_ESP_LAB_STAGE1_SIGNER_MAX_INSTALLER_BYTES:
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_BLOB_SIZE_UNSAFE")
    code, output = run_command([_GIT_BIN, "cat-file", "-p", blob_sha], checkout_path, 30, None)
    if len(output.encode("utf-8", "replace")) > _REPO_OUTPUT_LIMIT_BYTES:
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_BLOB_OUTPUT_TOO_LARGE")
    if code != 0:
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_BLOB_UNAVAILABLE")
    data = output.encode("utf-8")
    if len(data) != size:
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_BLOB_SIZE_MISMATCH")
    return data


def _verify_signer_installer_tree_entry(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
    main_sha: str,
) -> None:
    code, output = run_command(
        [_GIT_BIN, "ls-tree", main_sha, HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH],
        checkout_path,
        30,
        None,
    )
    if code != 0:
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_TREE_ENTRY_UNAVAILABLE")
    expected = (
        f"{HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE} blob "
        f"{HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB}\t"
        f"{HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH}"
    )
    if output.strip() != expected:
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_TREE_ENTRY_MISMATCH")
    code, object_type = run_command(
        [_GIT_BIN, "cat-file", "-t", HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB],
        checkout_path,
        30,
        None,
    )
    if code != 0 or object_type.strip() != "blob":
        raise RepositoryMaintenanceBlocked("SIGNER_INSTALLER_BLOB_TYPE_MISMATCH")


def _verify_exact_main_inputs(
    *,
    expected_main_sha: str,
    registered_clean_main_sha: str,
    github_main_sha: str,
    checkout_head_sha: str,
    checkout_origin_main_sha: str,
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_main_sha or "") is None:
        raise RepositoryMaintenanceBlocked("EXPECTED_MAIN_SHA_INVALID")
    if (
        registered_clean_main_sha != expected_main_sha
        or github_main_sha != expected_main_sha
        or checkout_head_sha != expected_main_sha
        or checkout_origin_main_sha != expected_main_sha
    ):
        raise RepositoryMaintenanceBlocked("EXPECTED_MAIN_SHA_MISMATCH")


def _git_exact_sha(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
    ref: str,
) -> str:
    code, output = run_command(
        [_GIT_BIN, "rev-parse", "--verify", f"{ref}^{{commit}}"],
        checkout_path,
        30,
        None,
    )
    lines = output.strip().splitlines()
    sha = lines[0] if len(lines) == 1 else ""
    if code != 0 or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise RepositoryMaintenanceBlocked("CHECKOUT_AUTHORITY_UNAVAILABLE")
    return sha


def _git_fresh_remote_main_sha(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
) -> str:
    code, output = run_command(
        [_GIT_BIN, "ls-remote", "--exit-code", _CANONICAL_REMOTE_MAIN_URL, "refs/heads/main"],
        _REMOTE_MAIN_CWD,
        30,
        None,
    )
    lines = output.splitlines()
    if code != 0 or len(lines) != 1:
        raise RepositoryMaintenanceBlocked("REMOTE_MAIN_UNAVAILABLE")
    match = re.fullmatch(r"([0-9a-f]{40})\trefs/heads/main", lines[0])
    if match is None:
        raise RepositoryMaintenanceBlocked("REMOTE_MAIN_MALFORMED")
    return match.group(1)


def _verify_fresh_remote_main(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
    expected_main_sha: str,
) -> None:
    if _git_fresh_remote_main_sha(run_command, checkout_path) != expected_main_sha:
        raise RepositoryMaintenanceBlocked("REMOTE_MAIN_SHA_MISMATCH")


def _verify_clean_trusted_checkout(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
    expected_main_sha: str,
) -> tuple[str, str]:
    head_sha = _git_exact_sha(run_command, checkout_path, "HEAD")
    origin_main_sha = _git_exact_sha(run_command, checkout_path, "origin/main")
    if head_sha != expected_main_sha or origin_main_sha != expected_main_sha:
        raise RepositoryMaintenanceBlocked("CHECKOUT_MAIN_SHA_MISMATCH")
    code, output = run_command(
        [_GIT_BIN, "status", "--porcelain", "--untracked-files=all"],
        checkout_path,
        30,
        None,
    )
    if code != 0:
        raise RepositoryMaintenanceBlocked("CHECKOUT_STATUS_UNAVAILABLE")
    if output.strip():
        raise RepositoryMaintenanceBlocked("CHECKOUT_DIRTY")
    code, _output = run_command(
        [
            _GIT_BIN,
            "merge-base",
            "--is-ancestor",
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA,
            expected_main_sha,
        ],
        checkout_path,
        30,
        None,
    )
    if code != 0:
        raise RepositoryMaintenanceBlocked("TRUSTED_SOURCE_ANCESTOR_MISSING")
    _verify_signer_installer_tree_entry(run_command, checkout_path, expected_main_sha)
    return head_sha, origin_main_sha


def _materialize_private_staging_file(data: bytes) -> tuple[Path, str]:
    staging_dir = Path(tempfile.mkdtemp(prefix="skeleton-esp-stage1-signer-"))
    staging_dir.chmod(0o700)
    staged = staging_dir / "install_home_edge_esp_lab_activation_signer.sh"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(staged, flags, 0o500)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    staged.chmod(0o500)
    digest = hashlib.sha256(data).hexdigest()
    _verify_regular_file(staged, digest, 0o500, owner_uid=os.getuid(), group_gid=os.getgid())
    return staged, digest


def _verify_regular_file(
    path: Path,
    expected_sha256: str | None,
    expected_mode: int,
    *,
    owner_uid: int = 0,
    group_gid: int = 0,
) -> None:
    stat_result = os.lstat(path)
    if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISREG(stat_result.st_mode):
        raise RepositoryMaintenanceBlocked("FILE_NOT_REGULAR")
    if stat_result.st_uid != owner_uid or stat_result.st_gid != group_gid:
        raise RepositoryMaintenanceBlocked("FILE_OWNERSHIP_MISMATCH")
    if stat.S_IMODE(stat_result.st_mode) != expected_mode:
        raise RepositoryMaintenanceBlocked("FILE_MODE_MISMATCH")
    if expected_sha256 is not None:
        size = stat_result.st_size
        if size <= 0 or size > HOME_EDGE_ESP_LAB_STAGE1_SIGNER_MAX_INSTALLER_BYTES:
            raise RepositoryMaintenanceBlocked("FILE_SIZE_UNSAFE")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            if getattr(exc, "errno", None) in {errno.ELOOP, errno.ENXIO}:
                raise RepositoryMaintenanceBlocked("FILE_NOT_REGULAR") from exc
            raise
        try:
            open_stat = os.fstat(fd)
            if (
                stat.S_ISLNK(open_stat.st_mode)
                or not stat.S_ISREG(open_stat.st_mode)
                or open_stat.st_dev != stat_result.st_dev
                or open_stat.st_ino != stat_result.st_ino
            ):
                raise RepositoryMaintenanceBlocked("FILE_NOT_REGULAR")
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        finally:
            if fd != -1:
                os.close(fd)
        if digest.hexdigest() != expected_sha256:
            raise RepositoryMaintenanceBlocked("FILE_CONTENT_MISMATCH")


def _verify_protected_installer_parent(*, allow_absent: bool = False) -> None:
    parent = HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER.parent
    try:
        stat_result = os.lstat(parent)
    except FileNotFoundError:
        if allow_absent:
            return
        raise RepositoryMaintenanceBlocked("PROTECTED_PARENT_ABSENT")
    if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISDIR(stat_result.st_mode):
        raise RepositoryMaintenanceBlocked("PROTECTED_PARENT_NOT_DIRECTORY")
    if stat_result.st_uid != 0 or stat_result.st_gid != 0:
        raise RepositoryMaintenanceBlocked("PROTECTED_PARENT_OWNERSHIP_MISMATCH")
    if stat.S_IMODE(stat_result.st_mode) != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_PARENT_MODE:
        raise RepositoryMaintenanceBlocked("PROTECTED_PARENT_MODE_MISMATCH")


def _git_blob_sha256(
    run_command: RepositoryRunCommand,
    checkout_path: Path,
    blob_sha: str,
) -> str:
    return hashlib.sha256(_git_blob_bytes(run_command, checkout_path, blob_sha)).hexdigest()


def execute_home_edge_esp_lab_stage1_signer_install(
    *,
    expected_main_sha: str,
    registered_clean_main_sha: str,
    github_main_sha: str,
    checkout_path: Path,
    checkout_head_sha: str,
    checkout_origin_main_sha: str,
    run_command: RepositoryRunCommand = _repository_run_command,
    protected_run_command: ProtectedRunCommand,
    before_protected_copy: Callable[[Path], None] | None = None,
) -> tuple[int, str]:
    staged: Path | None = None
    installer_sha256: str | None = None
    protected_copy_verified = False
    try:
        _verify_exact_main_inputs(
            expected_main_sha=expected_main_sha,
            registered_clean_main_sha=registered_clean_main_sha,
            github_main_sha=github_main_sha,
            checkout_head_sha=checkout_head_sha,
            checkout_origin_main_sha=checkout_origin_main_sha,
        )
        live_head_sha, live_origin_main_sha = _verify_clean_trusted_checkout(
            run_command,
            checkout_path,
            expected_main_sha,
        )
        _verify_exact_main_inputs(
            expected_main_sha=expected_main_sha,
            registered_clean_main_sha=registered_clean_main_sha,
            github_main_sha=github_main_sha,
            checkout_head_sha=live_head_sha,
            checkout_origin_main_sha=live_origin_main_sha,
        )
        _verify_fresh_remote_main(run_command, checkout_path, expected_main_sha)
        data = _git_blob_bytes(
            run_command,
            checkout_path,
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB,
        )
        installer_sha256 = hashlib.sha256(data).hexdigest()
        staged, staged_sha256 = _materialize_private_staging_file(data)
        if staged_sha256 != installer_sha256:
            raise RepositoryMaintenanceBlocked("STAGING_HASH_MISMATCH")
        if before_protected_copy is not None:
            before_protected_copy(staged)
        _verify_regular_file(
            staged,
            installer_sha256,
            0o500,
            owner_uid=os.getuid(),
            group_gid=os.getgid(),
        )
        _verify_protected_installer_parent(allow_absent=True)

        install_argv = [
            _SUDO_BIN,
            "-n",
            _INSTALL_BIN,
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0555",
            str(staged),
            str(HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER),
        ]
        code, _output = protected_run_command(
            install_argv,
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TIMEOUT_SECONDS,
        )
        if code != 0:
            return _protected_blocked("PRIVILEGE_UNAVAILABLE", installer_sha256)

        _verify_protected_installer_parent()
        _verify_regular_file(
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER,
            installer_sha256,
            0o555,
        )
        protected_copy_verified = True

        _verify_exact_main_inputs(
            expected_main_sha=expected_main_sha,
            registered_clean_main_sha=registered_clean_main_sha,
            github_main_sha=github_main_sha,
            checkout_head_sha=checkout_head_sha,
            checkout_origin_main_sha=checkout_origin_main_sha,
        )
        live_head_sha, live_origin_main_sha = _verify_clean_trusted_checkout(
            run_command,
            checkout_path,
            expected_main_sha,
        )
        _verify_exact_main_inputs(
            expected_main_sha=expected_main_sha,
            registered_clean_main_sha=registered_clean_main_sha,
            github_main_sha=github_main_sha,
            checkout_head_sha=live_head_sha,
            checkout_origin_main_sha=live_origin_main_sha,
        )
        _verify_fresh_remote_main(run_command, checkout_path, expected_main_sha)
        exec_argv = [
            _SUDO_BIN,
            "-n",
            str(HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER),
            "--repo-root",
            str(checkout_path),
        ]
        code, _output = protected_run_command(
            exec_argv,
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_EXEC_TIMEOUT_SECONDS,
        )
        if code != 0:
            return _protected_blocked(
                "SIGNER_INSTALLER_FAILED",
                installer_sha256,
                expected_main_sha=expected_main_sha,
                protected_copy_verified=protected_copy_verified,
            )

        for path, (blob_sha, mode) in HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLED_ARTIFACTS.items():
            expected_hash = (
                blob_sha
                if re.fullmatch(r"[0-9a-f]{64}", blob_sha)
                else _git_blob_sha256(run_command, checkout_path, blob_sha)
            )
            _verify_regular_file(path, expected_hash, mode)
        receipt = _protected_receipt(
            "DONE",
            "SIGNER_INSTALLATION_VERIFIED",
            expected_main_sha=expected_main_sha,
            installer_sha256=installer_sha256,
            artifacts_ok=True,
        )
        return 0, _protected_result("DONE", receipt)
    except RepositoryMaintenanceBlocked as exc:
        return _protected_blocked(
            exc.reason_code,
            installer_sha256,
            expected_main_sha=expected_main_sha,
            protected_copy_verified=protected_copy_verified,
        )
    except (OSError, subprocess.SubprocessError):
        return _protected_blocked(
            "PRIVILEGE_UNAVAILABLE",
            installer_sha256,
            expected_main_sha=expected_main_sha,
            protected_copy_verified=protected_copy_verified,
        )
    finally:
        if staged is not None:
            shutil.rmtree(staged.parent, ignore_errors=True)
