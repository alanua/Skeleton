from __future__ import annotations

from core.home_edge.modem_action import (
    CommandResult,
    HomeEdgeModemActionError,
    RESERVED_PROFILE_NAME,
    run_home_edge_01_modem_probe,
)


CREATED_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeRunner:
    def __init__(
        self, *, overrides: dict[tuple[str, ...], CommandResult] | None = None
    ) -> None:
        self.overrides = overrides or {}
        self.commands: list[list[str]] = []

    def run(self, argv: list[str], *, input_text: str | None = None) -> CommandResult:
        assert input_text is None
        self.commands.append(argv)
        key = tuple(argv)
        if key in self.overrides:
            return self.overrides[key]
        if argv == ["tailscale", "status", "--json"]:
            return CommandResult(0, "{}")
        if argv == ["ip", "route", "show", "default"]:
            return CommandResult(0, "default via 192.0.2.1 dev lan0\n")
        if argv == [
            "nmcli",
            "-t",
            "-f",
            "NAME",
            "connection",
            "show",
            RESERVED_PROFILE_NAME,
        ]:
            return CommandResult(10, "")
        if argv[:4] == ["nmcli", "connection", "add", "type"]:
            return CommandResult(
                0,
                f"Connection '{RESERVED_PROFILE_NAME}' "
                f"({CREATED_UUID}) successfully added.\n",
            )
        if argv[:4] == ["nmcli", "connection", "modify", "uuid"]:
            return CommandResult(0, "")
        if argv == ["nmcli", "connection", "up", "uuid", CREATED_UUID]:
            return CommandResult(0, "")
        if argv == [
            "nmcli",
            "-t",
            "-f",
            "GENERAL.STATE",
            "connection",
            "show",
            "uuid",
            CREATED_UUID,
        ]:
            return CommandResult(0, "GENERAL.STATE:activated\n")
        if argv == [
            "nmcli",
            "-g",
            "GENERAL.DEVICES",
            "connection",
            "show",
            "uuid",
            CREATED_UUID,
        ]:
            return CommandResult(0, "wwan0\n")
        if argv == [
            "nmcli",
            "-t",
            "-f",
            "GENERAL.STATE,IP4.CONNECTIVITY",
            "device",
            "show",
            "wwan0",
        ]:
            return CommandResult(0, "GENERAL.STATE:100 (connected)\nIP4.CONNECTIVITY:full\n")
        if argv == ["nmcli", "connection", "delete", "uuid", CREATED_UUID]:
            return CommandResult(0, "")
        raise AssertionError(f"unexpected command: {argv!r}")


def test_success_uses_created_uuid_for_modify_activation_state_probe_and_rollback() -> None:
    runner = FakeRunner()

    result = run_home_edge_01_modem_probe(runner=runner, apn="o2.private.test")

    assert result.status == "done"
    assert result.connection_test == "ok"
    assert [
        "nmcli",
        "connection",
        "modify",
        "uuid",
        CREATED_UUID,
        "connection.autoconnect",
        "no",
        "ipv4.never-default",
        "yes",
        "ipv6.never-default",
        "yes",
        "gsm.apn",
        "o2.private.test",
    ] in runner.commands
    assert ["nmcli", "connection", "up", "uuid", CREATED_UUID] in runner.commands
    assert [
        "nmcli",
        "-t",
        "-f",
        "GENERAL.STATE",
        "connection",
        "show",
        "uuid",
        CREATED_UUID,
    ] in runner.commands
    assert [
        "nmcli",
        "-g",
        "GENERAL.DEVICES",
        "connection",
        "show",
        "uuid",
        CREATED_UUID,
    ] in runner.commands
    assert ["nmcli", "connection", "delete", "uuid", CREATED_UUID] in runner.commands
    assert ["nmcli", "connection", "delete", RESERVED_PROFILE_NAME] not in runner.commands


def test_profile_state_query_exit_zero_but_inactive_remains_blocked() -> None:
    runner = FakeRunner(
        overrides={
            (
                "nmcli",
                "-t",
                "-f",
                "GENERAL.STATE",
                "connection",
                "show",
                "uuid",
                CREATED_UUID,
            ): CommandResult(0, "GENERAL.STATE:inactive\n"),
        }
    )

    result = run_home_edge_01_modem_probe(runner=runner, apn="o2.private.test")

    assert result.status == "blocked"
    assert result.connection_test == "blocked"
    assert result.reason == "active_state_validation_failed"
    assert ["nmcli", "connection", "delete", "uuid", CREATED_UUID] in runner.commands


def test_existing_same_name_profile_is_never_modified_or_deleted() -> None:
    runner = FakeRunner(
        overrides={
            (
                "nmcli",
                "-t",
                "-f",
                "NAME",
                "connection",
                "show",
                RESERVED_PROFILE_NAME,
            ): CommandResult(0, RESERVED_PROFILE_NAME),
        }
    )

    result = run_home_edge_01_modem_probe(runner=runner, apn="o2.private.test")

    assert result.status == "blocked"
    assert result.reason == "reserved_profile_name_exists"
    assert all("modify" not in command for command in runner.commands)
    assert all("delete" not in command for command in runner.commands)


def test_rollback_deletes_only_uuid_created_by_current_run() -> None:
    runner = FakeRunner(
        overrides={
            ("nmcli", "connection", "up", "uuid", CREATED_UUID): CommandResult(4, "failed"),
        }
    )

    result = run_home_edge_01_modem_probe(runner=runner, apn="o2.private.test")

    assert result.status == "blocked"
    delete_commands = [command for command in runner.commands if "delete" in command]
    assert delete_commands == [["nmcli", "connection", "delete", "uuid", CREATED_UUID]]
    assert OTHER_UUID not in " ".join(" ".join(command) for command in runner.commands)


def test_failed_activation_signal_state_validation_and_uuid_lookup_fail_closed() -> None:
    activation_runner = FakeRunner(
        overrides={
            ("nmcli", "connection", "up", "uuid", CREATED_UUID): CommandResult(4, "failed"),
        }
    )
    signal_runner = FakeRunner(
        overrides={
            (
                "nmcli",
                "-t",
                "-f",
                "GENERAL.STATE,IP4.CONNECTIVITY",
                "device",
                "show",
                "wwan0",
            ): CommandResult(4, "failed"),
        }
    )
    state_runner = FakeRunner(
        overrides={
            (
                "nmcli",
                "-t",
                "-f",
                "GENERAL.STATE",
                "connection",
                "show",
                "uuid",
                CREATED_UUID,
            ): CommandResult(4, "failed"),
        }
    )
    uuid_runner = FakeRunner(
        overrides={
            (
                "nmcli",
                "connection",
                "add",
                "type",
                "gsm",
                "ifname",
                "*",
                "con-name",
                RESERVED_PROFILE_NAME,
                "apn",
                "o2.private.test",
                "connection.autoconnect",
                "no",
                "ipv4.never-default",
                "yes",
                "ipv6.never-default",
                "yes",
            ): CommandResult(0, "Connection added.\n"),
            (
                "nmcli",
                "-g",
                "UUID",
                "connection",
                "show",
                RESERVED_PROFILE_NAME,
            ): CommandResult(10, ""),
        }
    )

    assert (
        run_home_edge_01_modem_probe(
            runner=activation_runner, apn="o2.private.test"
        ).reason
        == "connection_activation_failed"
    )
    assert (
        run_home_edge_01_modem_probe(
            runner=signal_runner, apn="o2.private.test"
        ).reason
        == "bounded_signal_probe_failed"
    )
    assert (
        run_home_edge_01_modem_probe(runner=state_runner, apn="o2.private.test").reason
        == "active_state_validation_failed"
    )
    uuid_result = run_home_edge_01_modem_probe(runner=uuid_runner, apn="o2.private.test")
    assert uuid_result.status == "blocked"
    assert uuid_result.reason == "created_uuid_lookup_failed"
    assert all("delete" not in command for command in uuid_runner.commands)


def test_default_apn_is_rejected() -> None:
    runner = FakeRunner()

    try:
        run_home_edge_01_modem_probe(runner=runner, apn="internet")
    except HomeEdgeModemActionError as exc:
        assert str(exc) == "modem_apn_must_be_explicit_non_default"
    else:
        raise AssertionError("default APN should fail closed")
    assert runner.commands == []
