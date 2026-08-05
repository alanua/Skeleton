from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
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
EXPECTED_MAIN_SHA: Final = "bdebfde2fda56c5e27ce18afbf96e5e36563bae1"
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
    )
)
ALLOWED_METADATA_LINES: Final = frozenset(
    (
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {TASK_ID}",
        APPROVAL_LINE,
    )
)
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


def reject_issue_input(body: str) -> str | None:
    if "```" in (body or ""):
        return "unexpected_task_fields"

    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        field, separator, _value = stripped.partition(":")
        if field == APPROVAL_FIELD and stripped != APPROVAL_LINE:
            return "malformed_approval"
        if field in REJECTED_FIELD_NAMES:
            return "unexpected_task_fields"
        if not separator or stripped not in ALLOWED_METADATA_LINES:
            return "unexpected_task_fields"

    approval_count = sum(
        1 for line in (body or "").splitlines() if line.strip() == APPROVAL_LINE
    )
    if approval_count != 1:
        return "malformed_approval"
    return None


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


def receipt_lines(
    *,
    status_lines: list[str],
    provider_status: str,
    preexisting_count: int,
    added_count: int,
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
    added_count: int = 0,
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
    body: str,
    *,
    preflight_status_lines: list[str],
    run_command: RunCommand = default_run_command,
    maintenance_report: MaintenanceReport,
    platform_name: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    environment: Mapping[str, str] | None = None,
) -> str:
    input_reason = reject_issue_input(body)
    if input_reason is not None:
        return _blocked(maintenance_report, [], reason=input_reason)

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
    package_state = _call_command(
        run_command, DPKG_QUERY_COMMAND, env, COMMAND_TIMEOUT_SECONDS
    )
    if package_state.code not in (0, 1):
        status_lines.append("step=preflight_package_state status=failed")
        return _blocked(
            maintenance_report,
            status_lines,
            reason="package_manager_missing",
        )
    preexisting = parse_installed_packages(package_state.output)
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
    absent_before = tuple(
        package for package in REQUIRED_PACKAGES if package not in preexisting
    )
    if install.code != 0:
        status_lines.append("step=apt_install status=failed")
        return _blocked(
            maintenance_report,
            status_lines,
            reason="ocr_package_install_failed",
            preexisting_count=len(preexisting),
            added_count=len(absent_before),
            install_mutation_applied=True,
            rollback_ready=bool(absent_before),
        )
    status_lines.append("step=apt_install status=done")

    if which("tesseract") is None:
        status_lines.append("step=resolve_ocr_executable status=failed")
        rollback_applied = False
        if absent_before:
            rollback_command = [*APT_ROLLBACK_COMMAND_PREFIX, *absent_before]
            rollback = _call_command(
                run_command,
                tuple(rollback_command),
                env,
                APT_ROLLBACK_TIMEOUT_SECONDS,
            )
            rollback_applied = rollback.code == 0
            status_lines.append(
                "step=rollback_new_ocr_packages status="
                + ("done" if rollback_applied else "failed")
            )
        return _blocked(
            maintenance_report,
            status_lines,
            reason=(
                "ocr_executable_missing"
                if rollback_applied or not absent_before
                else "ocr_rollback_failed"
            ),
            preexisting_count=len(preexisting),
            added_count=len(absent_before),
            install_mutation_applied=True,
            rollback_ready=bool(absent_before),
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
                added_count=len(absent_before),
                install_mutation_applied=True,
                rollback_ready=bool(absent_before),
                rollback_applied=False,
                ready_language_count=len(ready),
                reason="ready",
            ),
            "met",
        )

    rollback_applied = False
    if absent_before:
        rollback_command = [*APT_ROLLBACK_COMMAND_PREFIX, *absent_before]
        rollback = _call_command(
            run_command,
            tuple(rollback_command),
            env,
            APT_ROLLBACK_TIMEOUT_SECONDS,
        )
        rollback_applied = rollback.code == 0
        status_lines.append(
            "step=rollback_new_ocr_packages status="
            + ("done" if rollback_applied else "failed")
        )
        if not rollback_applied:
            reason = "ocr_rollback_failed"

    return _blocked(
        maintenance_report,
        status_lines,
        reason=reason,
        preexisting_count=len(preexisting),
        added_count=len(absent_before),
        install_mutation_applied=True,
        rollback_ready=bool(absent_before),
        rollback_applied=rollback_applied,
        ready_language_count=len(ready),
    )
