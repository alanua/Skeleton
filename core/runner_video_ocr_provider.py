from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import sys
from typing import Final


TASK_ID: Final = "install_video_ocr_provider"
APPROVAL_FIELD: Final = "OCR Provider Approval"
APPROVAL_LINE: Final = (
    "OCR Provider Approval: "
    "EXPLICIT_INSTALL_VIDEO_OCR_PROVIDER_20260728"
)
APPROVAL_TOKEN: Final = "EXPLICIT_INSTALL_VIDEO_OCR_PROVIDER_20260728"
EXPECTED_REPOSITORY: Final = "alanua/Skeleton"
EXPECTED_MAIN_SHA_FIELD: Final = "Expected Main SHA"
RUNTIME_MODE_FIELD: Final = "Mode"
TASK_ID_FIELD: Final = "Maintenance Task ID"
REPOSITORY_FIELD: Final = "Repository"
RUNTIME_MODE: Final = "RUNTIME_MAINTENANCE_TASK"
LOWER_HEX_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PACKAGES: Final = (
    "tesseract-ocr",
    "tesseract-ocr-eng",
    "tesseract-ocr-deu",
    "tesseract-ocr-rus",
    "tesseract-ocr-ukr",
)
REQUIRED_LANGUAGES: Final = ("eng", "deu", "rus", "ukr")
COMMAND_TIMEOUT_SECONDS: Final = 120
APT_UPDATE_TIMEOUT_SECONDS: Final = 300
APT_INSTALL_TIMEOUT_SECONDS: Final = 600
APT_ROLLBACK_TIMEOUT_SECONDS: Final = 600
DPKG_QUERY_COMMAND: Final = (
    "dpkg-query",
    "-W",
    "-f=${Package}\t${db:Status-Abbrev}\\n",
    *REQUIRED_PACKAGES,
)
APT_UPDATE_COMMAND: Final = ("sudo", "-n", "apt-get", "update")
APT_INSTALL_COMMAND: Final = (
    "sudo",
    "-n",
    "apt-get",
    "install",
    "-y",
    "--no-install-recommends",
    *REQUIRED_PACKAGES,
)
TESSERACT_VERSION_COMMAND: Final = ("tesseract", "--version")
TESSERACT_LIST_LANGS_COMMAND: Final = ("tesseract", "--list-langs")
APT_ROLLBACK_COMMAND_PREFIX: Final = (
    "sudo",
    "-n",
    "apt-get",
    "remove",
    "-y",
)
SUPPORTED_PLATFORM: Final = "linux"
STABLE_REASON_CODES: Final = frozenset(
    (
        "unsupported_platform",
        "package_manager_missing",
        "sudo_noninteractive_unavailable",
        "ocr_package_install_failed",
        "ocr_executable_missing",
        "ocr_language_missing",
        "ocr_verification_failed",
        "ocr_rollback_failed",
        "ocr_package_state_verification_failed",
    )
)
REQUIRED_FIELDS: Final = (
    RUNTIME_MODE_FIELD,
    TASK_ID_FIELD,
    REPOSITORY_FIELD,
    EXPECTED_MAIN_SHA_FIELD,
    APPROVAL_FIELD,
)
REQUIRED_FIELD_SET: Final = frozenset(REQUIRED_FIELDS)
EXPECTED_FIELD_VALUES: Final = {
    RUNTIME_MODE_FIELD: RUNTIME_MODE,
    TASK_ID_FIELD: TASK_ID,
    REPOSITORY_FIELD: EXPECTED_REPOSITORY,
    APPROVAL_FIELD: APPROVAL_TOKEN,
}
REJECTED_FIELD_NAMES: Final = frozenset(
    (
        "Package",
        "Packages",
        "Command",
        "Commands",
        "Path",
        "Host",
        "User",
        "Username",
        "Executable",
        "Provider Path",
        "Install Command",
        "Rollback Command",
    )
)


@dataclass(frozen=True)
class CommandResult:
    code: int
    output: str = ""


@dataclass(frozen=True)
class RuntimeRequest:
    mode: str
    task_id: str
    repository: str
    expected_main_sha: str
    approval_token: str


RunCommand = Callable[[list[str], Mapping[str, str], int], CommandResult]
MaintenanceReport = Callable[[str, str, list[str], str], str]


def sanitized_child_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = dict(os.environ if environment is None else environment)
    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL"):
        value = source.get(key)
        if isinstance(value, str) and value:
            env[key] = value
    env.setdefault("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return env


def default_run_command(
    args: list[str], env: Mapping[str, str], timeout: int
) -> CommandResult:
    completed = subprocess.run(
        args,
        check=False,
        cwd="/",
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return CommandResult(completed.returncode, completed.stdout or "")


def _call_command(
    run_command: RunCommand,
    args: tuple[str, ...],
    env: Mapping[str, str],
    timeout: int,
) -> CommandResult:
    try:
        return run_command(list(args), env, timeout)
    except subprocess.TimeoutExpired:
        return CommandResult(124, "")
    except (OSError, PermissionError):
        return CommandResult(127, "")


def parse_runtime_request(body: str) -> tuple[RuntimeRequest | None, str | None]:
    if "```" in (body or ""):
        return None, "unexpected_task_fields"

    fields: dict[str, str] = {}
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        field, separator, value = stripped.partition(":")
        value = value.strip()
        if not separator or not field or not value:
            return None, "unexpected_task_fields"
        if field in fields:
            return None, "unexpected_task_fields"
        if field in REJECTED_FIELD_NAMES or field not in REQUIRED_FIELD_SET:
            return None, "unexpected_task_fields"
        fields[field] = value

    if set(fields) != REQUIRED_FIELD_SET:
        if fields.get(APPROVAL_FIELD) != APPROVAL_TOKEN:
            return None, "malformed_approval"
        return None, "unexpected_task_fields"
    if fields[APPROVAL_FIELD] != APPROVAL_TOKEN:
        return None, "malformed_approval"
    for field, expected in EXPECTED_FIELD_VALUES.items():
        if fields[field] != expected:
            return None, "unexpected_task_fields"
    if LOWER_HEX_SHA_RE.fullmatch(fields[EXPECTED_MAIN_SHA_FIELD]) is None:
        return None, "malformed_expected_main_sha"

    return RuntimeRequest(
        mode=fields[RUNTIME_MODE_FIELD],
        task_id=fields[TASK_ID_FIELD],
        repository=fields[REPOSITORY_FIELD],
        expected_main_sha=fields[EXPECTED_MAIN_SHA_FIELD],
        approval_token=fields[APPROVAL_FIELD],
    ), None


def reject_issue_input(body: str) -> str | None:
    _request, reason = parse_runtime_request(body)
    return reason


def parse_installed_packages(output: str) -> frozenset[str]:
    installed: set[str] = set()
    for line in (output or "").splitlines():
        package, separator, status = line.partition("\t")
        if separator and package in REQUIRED_PACKAGES and status.startswith("ii "):
            installed.add(package)
    return frozenset(installed)


def parse_languages(output: str) -> frozenset[str]:
    languages: set[str] = set()
    for line in (output or "").splitlines():
        token = line.strip()
        if token in REQUIRED_LANGUAGES:
            languages.add(token)
    return frozenset(languages)


def query_installed_packages(
    run_command: RunCommand,
    env: Mapping[str, str],
) -> tuple[frozenset[str], str | None]:
    package_state = _call_command(
        run_command, DPKG_QUERY_COMMAND, env, COMMAND_TIMEOUT_SECONDS
    )
    if package_state.code not in (0, 1):
        return frozenset(), "ocr_package_state_verification_failed"
    return parse_installed_packages(package_state.output), None


def actually_added_packages(
    preexisting: frozenset[str],
    installed_after: frozenset[str],
) -> tuple[str, ...]:
    return tuple(
        package
        for package in REQUIRED_PACKAGES
        if package in installed_after and package not in preexisting
    )


def rollback_added_packages(
    *,
    run_command: RunCommand,
    env: Mapping[str, str],
    status_lines: list[str],
    packages: tuple[str, ...],
) -> tuple[bool, bool]:
    if not packages:
        return False, False
    rollback = _call_command(
        run_command,
        (*APT_ROLLBACK_COMMAND_PREFIX, *packages),
        env,
        APT_ROLLBACK_TIMEOUT_SECONDS,
    )
    rollback_applied = rollback.code == 0
    status_lines.append(
        "step=rollback_new_ocr_packages status="
        + ("done" if rollback_applied else "failed")
    )
    return True, rollback_applied


def receipt_lines(
    *,
    status_lines: list[str],
    provider_status: str,
    preexisting_count: int,
    added_count: int | str,
    install_mutation_applied: bool,
    rollback_ready: bool,
    rollback_applied: bool,
    ready_language_count: int,
    reason: str,
) -> list[str]:
    return [
        *status_lines,
        f"provider_status={provider_status}",
        f"required_language_count={len(REQUIRED_LANGUAGES)}",
        f"ready_language_count={ready_language_count}",
        f"packages_required_count={len(REQUIRED_PACKAGES)}",
        f"packages_preexisting_count={preexisting_count}",
        f"packages_added_count={added_count}",
        f"install_mutation_applied={str(install_mutation_applied).lower()}",
        f"rollback_ready={str(rollback_ready).lower()}",
        f"rollback_applied={str(rollback_applied).lower()}",
        f"reason={reason}",
    ]


def _blocked(
    maintenance_report: MaintenanceReport,
    status_lines: list[str],
    *,
    reason: str,
    preexisting_count: int = 0,
    added_count: int | str = 0,
    install_mutation_applied: bool = False,
    rollback_ready: bool = False,
    rollback_applied: bool = False,
    ready_language_count: int = 0,
) -> str:
    return maintenance_report(
        "BLOCKED",
        TASK_ID,
        receipt_lines(
            status_lines=status_lines,
            provider_status="BLOCKED",
            preexisting_count=preexisting_count,
            added_count=added_count,
            install_mutation_applied=install_mutation_applied,
            rollback_ready=rollback_ready,
            rollback_applied=rollback_applied,
            ready_language_count=ready_language_count,
            reason=reason,
        ),
        "not_met",
    )


def _verify_provider(
    run_command: RunCommand, env: Mapping[str, str], status_lines: list[str]
) -> tuple[frozenset[str], str | None]:
    version = _call_command(
        run_command, TESSERACT_VERSION_COMMAND, env, COMMAND_TIMEOUT_SECONDS
    )
    if version.code != 0:
        status_lines.append("step=verify_ocr_executable status=failed")
        return frozenset(), "ocr_executable_missing"
    status_lines.append("step=verify_ocr_executable status=done")

    langs = _call_command(
        run_command, TESSERACT_LIST_LANGS_COMMAND, env, COMMAND_TIMEOUT_SECONDS
    )
    if langs.code != 0:
        status_lines.append("step=verify_ocr_languages status=failed")
        return frozenset(), "ocr_verification_failed"

    ready = parse_languages(langs.output)
    status_lines.append(
        "step=verify_ocr_languages status="
        + ("done" if set(REQUIRED_LANGUAGES) <= ready else "failed")
    )
    if not set(REQUIRED_LANGUAGES) <= ready:
        return ready, "ocr_language_missing"
    return ready, None


def execute_install_video_ocr_provider(
    request: RuntimeRequest,
    *,
    preflight_status_lines: list[str],
    run_command: RunCommand = default_run_command,
    maintenance_report: MaintenanceReport,
    platform_name: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    environment: Mapping[str, str] | None = None,
) -> str:
    status_lines = [
        *preflight_status_lines,
        "approval_status=verified",
        f"runtime_command_timeout_seconds={COMMAND_TIMEOUT_SECONDS}",
        f"apt_update_timeout_seconds={APT_UPDATE_TIMEOUT_SECONDS}",
        f"apt_install_timeout_seconds={APT_INSTALL_TIMEOUT_SECONDS}",
        f"apt_rollback_timeout_seconds={APT_ROLLBACK_TIMEOUT_SECONDS}",
        "private_memory_accessed=false",
        "video_understanding_activated=false",
        "media_processed=false",
        "services_restarted=false",
        "ollama_altered=false",
    ]
    if platform_name != SUPPORTED_PLATFORM:
        return _blocked(maintenance_report, status_lines, reason="unsupported_platform")

    missing_tools = [
        tool
        for tool in ("dpkg-query", "apt-get", "sudo")
        if which(tool) is None
    ]
    if missing_tools:
        status_lines.append("step=verify_package_manager status=failed")
        return _blocked(
            maintenance_report,
            status_lines,
            reason="package_manager_missing",
        )
    status_lines.append("step=verify_package_manager status=done")

    env = sanitized_child_environment(environment)
    preexisting, package_state_reason = query_installed_packages(run_command, env)
    if package_state_reason is not None:
        status_lines.append("step=preflight_package_state status=failed")
        return _blocked(
            maintenance_report,
            status_lines,
            reason=package_state_reason,
        )
    status_lines.append("step=preflight_package_state status=done")

    if which("tesseract") is not None:
        ready, reason = _verify_provider(run_command, env, status_lines)
        if reason is None:
            return maintenance_report(
                "DONE",
                TASK_ID,
                receipt_lines(
                    status_lines=status_lines,
                    provider_status="READY",
                    preexisting_count=len(preexisting),
                    added_count=0,
                    install_mutation_applied=False,
                    rollback_ready=False,
                    rollback_applied=False,
                    ready_language_count=len(ready),
                    reason="ready",
                ),
                "met",
            )

    update = _call_command(
        run_command, APT_UPDATE_COMMAND, env, APT_UPDATE_TIMEOUT_SECONDS
    )
    if update.code != 0:
        status_lines.append("step=apt_update status=failed")
        return _blocked(
            maintenance_report,
            status_lines,
            reason="sudo_noninteractive_unavailable",
            preexisting_count=len(preexisting),
        )
    status_lines.append("step=apt_update status=done")

    install = _call_command(
        run_command, APT_INSTALL_COMMAND, env, APT_INSTALL_TIMEOUT_SECONDS
    )
    installed_after, package_state_reason = query_installed_packages(run_command, env)
    if package_state_reason is not None:
        status_lines.append("step=post_install_package_state status=failed")
        return _blocked(
            maintenance_report,
            status_lines,
            reason=package_state_reason,
            preexisting_count=len(preexisting),
            added_count="unknown",
            install_mutation_applied=True,
        )
    actually_added = actually_added_packages(preexisting, installed_after)
    status_lines.append("step=post_install_package_state status=done")
    if install.code != 0:
        status_lines.append("step=apt_install status=failed")
        rollback_ready, rollback_applied = rollback_added_packages(
            run_command=run_command,
            env=env,
            status_lines=status_lines,
            packages=actually_added,
        )
        reason = "ocr_package_install_failed"
        if rollback_ready and not rollback_applied:
            reason = "ocr_rollback_failed"
        return _blocked(
            maintenance_report,
            status_lines,
            reason=reason,
            preexisting_count=len(preexisting),
            added_count=len(actually_added),
            install_mutation_applied=True,
            rollback_ready=rollback_ready,
            rollback_applied=rollback_applied,
        )
    status_lines.append("step=apt_install status=done")

    if which("tesseract") is None:
        status_lines.append("step=resolve_ocr_executable status=failed")
        rollback_ready, rollback_applied = rollback_added_packages(
            run_command=run_command,
            env=env,
            status_lines=status_lines,
            packages=actually_added,
        )
        return _blocked(
            maintenance_report,
            status_lines,
            reason=(
                "ocr_executable_missing"
                if rollback_applied or not rollback_ready
                else "ocr_rollback_failed"
            ),
            preexisting_count=len(preexisting),
            added_count=len(actually_added),
            install_mutation_applied=True,
            rollback_ready=rollback_ready,
            rollback_applied=rollback_applied,
        )
    status_lines.append("step=resolve_ocr_executable status=done")

    ready, reason = _verify_provider(run_command, env, status_lines)
    if reason is None:
        return maintenance_report(
            "DONE",
            TASK_ID,
            receipt_lines(
                status_lines=status_lines,
                provider_status="READY",
                preexisting_count=len(preexisting),
                added_count=len(actually_added),
                install_mutation_applied=True,
                rollback_ready=bool(actually_added),
                rollback_applied=False,
                ready_language_count=len(ready),
                reason="ready",
            ),
            "met",
        )

    rollback_ready, rollback_applied = rollback_added_packages(
        run_command=run_command,
        env=env,
        status_lines=status_lines,
        packages=actually_added,
    )
    if rollback_ready and not rollback_applied:
        reason = "ocr_rollback_failed"

    return _blocked(
        maintenance_report,
        status_lines,
        reason=reason,
        preexisting_count=len(preexisting),
        added_count=len(actually_added),
        install_mutation_applied=True,
        rollback_ready=rollback_ready,
        rollback_applied=rollback_applied,
        ready_language_count=len(ready),
    )
