from __future__ import annotations

from collections.abc import Mapping

from core import runner_video_ocr_provider as ocr


def _report(status: str, task_id: str, lines: list[str], success: str) -> str:
    return "\n".join(
        [
            f"{status}: report",
            f"maintenance_task_id={task_id}",
            *lines,
            f"success_criteria={success}",
        ]
    )


def _body(approval: str = ocr.APPROVAL_TOKEN, extra: str = "") -> str:
    parts = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {ocr.TASK_ID}",
        f"{ocr.APPROVAL_FIELD}: {approval}",
    ]
    if extra:
        parts.append(extra)
    return "\n".join(parts)


class FakeRuntime:
    def __init__(
        self,
        *,
        installed: set[str] | None = None,
        languages: tuple[str, ...] = ocr.REQUIRED_LANGUAGES,
        tesseract_available: bool = True,
        install_code: int = 0,
        version_code: int = 0,
        langs_code: int = 0,
        rollback_code: int = 0,
    ) -> None:
        self.installed = installed or set()
        self.languages = languages
        self.tesseract_available = tesseract_available
        self.install_code = install_code
        self.version_code = version_code
        self.langs_code = langs_code
        self.rollback_code = rollback_code
        self.commands: list[list[str]] = []
        self.environments: list[Mapping[str, str]] = []
        self.timeouts: list[int] = []

    def which(self, name: str) -> str | None:
        if name in {"dpkg-query", "apt-get", "sudo"}:
            return f"/usr/bin/{name}"
        if name == "tesseract" and self.tesseract_available:
            return "/usr/bin/tesseract"
        return None

    def run(
        self, args: list[str], env: Mapping[str, str], timeout: int
    ) -> ocr.CommandResult:
        self.commands.append(args)
        self.environments.append(dict(env))
        self.timeouts.append(timeout)
        if args == list(ocr.DPKG_QUERY_COMMAND):
            output = "".join(
                f"{package}\tii \n" for package in sorted(self.installed)
            )
            return ocr.CommandResult(0, output)
        if args == list(ocr.APT_UPDATE_COMMAND):
            return ocr.CommandResult(0, "private apt source details")
        if args == list(ocr.APT_INSTALL_COMMAND):
            self.tesseract_available = self.install_code == 0
            return ocr.CommandResult(self.install_code, "/tmp/raw install failure")
        if args == list(ocr.TESSERACT_VERSION_COMMAND):
            return ocr.CommandResult(self.version_code, "/usr/bin/tesseract 5.0")
        if args == list(ocr.TESSERACT_LIST_LANGS_COMMAND):
            return ocr.CommandResult(
                self.langs_code,
                "List of available languages in /private/tessdata:\n"
                + "\n".join(self.languages),
            )
        if args[: len(ocr.APT_ROLLBACK_COMMAND_PREFIX)] == list(
            ocr.APT_ROLLBACK_COMMAND_PREFIX
        ):
            return ocr.CommandResult(self.rollback_code, "rollback output")
        return ocr.CommandResult(99, "unexpected command")


def _execute(fake: FakeRuntime, body: str | None = None) -> str:
    return ocr.execute_install_video_ocr_provider(
        body or _body(),
        preflight_status_lines=[
            "target_project=skeleton",
            "target_repository=alanua/Skeleton",
            "target_project_route=registered_checkout",
        ],
        run_command=fake.run,
        maintenance_report=_report,
        which=fake.which,
        environment={
            "PATH": "/usr/bin",
            "HOME": "/private/home",
            "SECRET_TOKEN": "must-not-pass",
        },
    )


def test_exact_approval_and_task_id_validation() -> None:
    fake = FakeRuntime()

    missing = _execute(fake, _body(approval="wrong"))
    wrong_task = _execute(
        fake,
        "\n".join(
            [
                "Mode: RUNTIME_MAINTENANCE_TASK",
                "Maintenance Task ID: other",
                ocr.APPROVAL_LINE,
            ]
        ),
    )

    assert "reason=malformed_approval" in missing
    assert "reason=unexpected_task_fields" in wrong_task
    assert fake.commands == []


def test_no_issue_controlled_package_command_path_host_or_user_fields() -> None:
    for field in ("Packages", "Command", "Path", "Host", "User"):
        fake = FakeRuntime()
        report = _execute(fake, _body(extra=f"{field}: /tmp/private"))
        assert report.startswith("BLOCKED:")
        assert "reason=unexpected_task_fields" in report
        assert fake.commands == []


def test_already_ready_path_is_idempotent_and_performs_no_apt_mutation() -> None:
    fake = FakeRuntime(installed=set(ocr.REQUIRED_PACKAGES))
    report = _execute(fake)

    assert report.startswith("DONE:")
    assert "provider_status=READY" in report
    assert "packages_preexisting_count=5" in report
    assert "packages_added_count=0" in report
    assert "install_mutation_applied=false" in report
    assert fake.commands == [
        list(ocr.DPKG_QUERY_COMMAND),
        list(ocr.TESSERACT_VERSION_COMMAND),
        list(ocr.TESSERACT_LIST_LANGS_COMMAND),
    ]


def test_missing_provider_installs_exact_fixed_packages_only() -> None:
    fake = FakeRuntime(tesseract_available=False)
    report = _execute(fake)

    assert report.startswith("DONE:")
    assert "packages_added_count=5" in report
    assert "install_mutation_applied=true" in report
    assert list(ocr.APT_INSTALL_COMMAND) in fake.commands
    install_commands = [command for command in fake.commands if "install" in command]
    assert install_commands == [list(ocr.APT_INSTALL_COMMAND)]


def test_partial_preexisting_set_is_preserved_and_only_absent_packages_count_added() -> None:
    preexisting = {"tesseract-ocr", "tesseract-ocr-eng"}
    fake = FakeRuntime(installed=preexisting, tesseract_available=False)
    report = _execute(fake)

    assert "packages_preexisting_count=2" in report
    assert "packages_added_count=3" in report
    assert list(ocr.APT_ROLLBACK_COMMAND_PREFIX) not in fake.commands


def test_post_install_language_verification_requires_all_required_languages() -> None:
    fake = FakeRuntime(tesseract_available=False, languages=("eng", "deu", "rus"))
    report = _execute(fake)

    assert report.startswith("BLOCKED:")
    assert "reason=ocr_language_missing" in report
    assert "ready_language_count=3" in report


def test_install_failure_produces_stable_sanitized_reason() -> None:
    fake = FakeRuntime(tesseract_available=False, install_code=1)
    report = _execute(fake)

    assert report.startswith("BLOCKED:")
    assert "reason=ocr_package_install_failed" in report
    assert "/tmp/raw install failure" not in report
    assert "private apt source details" not in report


def test_verification_failure_rolls_back_only_newly_added_fixed_packages() -> None:
    preexisting = {"tesseract-ocr", "tesseract-ocr-eng"}
    fake = FakeRuntime(
        installed=preexisting,
        tesseract_available=False,
        languages=("eng", "deu"),
    )
    report = _execute(fake)

    rollback_commands = [
        command
        for command in fake.commands
        if command[: len(ocr.APT_ROLLBACK_COMMAND_PREFIX)]
        == list(ocr.APT_ROLLBACK_COMMAND_PREFIX)
    ]
    assert report.startswith("BLOCKED:")
    assert "rollback_ready=true" in report
    assert "rollback_applied=true" in report
    assert rollback_commands == [
        [
            *ocr.APT_ROLLBACK_COMMAND_PREFIX,
            "tesseract-ocr-deu",
            "tesseract-ocr-rus",
            "tesseract-ocr-ukr",
        ]
    ]


def test_preexisting_packages_are_never_removed() -> None:
    fake = FakeRuntime(
        installed=set(ocr.REQUIRED_PACKAGES),
        languages=("eng",),
    )
    report = _execute(fake)

    assert report.startswith("BLOCKED:")
    assert "rollback_ready=false" in report
    assert not any("remove" in command for command in fake.commands)


def test_rollback_failure_is_explicit() -> None:
    fake = FakeRuntime(
        tesseract_available=False,
        languages=("eng",),
        rollback_code=1,
    )
    report = _execute(fake)

    assert report.startswith("BLOCKED:")
    assert "reason=ocr_rollback_failed" in report
    assert "rollback_applied=false" in report


def test_public_output_contains_no_private_markers_paths_or_raw_command_output() -> None:
    fake = FakeRuntime(tesseract_available=False, install_code=1)
    report = _execute(fake)

    forbidden = (
        "/tmp",
        "/private",
        "/usr/bin",
        "SECRET_TOKEN",
        "must-not-pass",
        "raw install",
        "apt source",
        "tessdata",
    )
    assert not any(marker in report for marker in forbidden)


def test_child_environment_is_bounded_and_sanitized() -> None:
    fake = FakeRuntime()
    _execute(fake)

    assert fake.environments
    assert set(fake.environments[0]) == {"PATH", "LANG", "DEBIAN_FRONTEND"}
    assert fake.timeouts == [
        ocr.COMMAND_TIMEOUT_SECONDS,
        ocr.COMMAND_TIMEOUT_SECONDS,
        ocr.COMMAND_TIMEOUT_SECONDS,
    ]
