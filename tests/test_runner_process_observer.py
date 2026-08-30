from __future__ import annotations

from core.runner_process_observer import (
    BLOCKER_SIGNATURE,
    build_spawn_trace_command,
    parse_first_denied_filesystem_event,
    public_safe_path,
)


def test_build_spawn_trace_command_wraps_exact_child_command() -> None:
    command = ["/usr/local/bin/codex", "exec", "--json"]
    wrapped = build_spawn_trace_command(command, trace_path="/tmp/trace.log")

    assert wrapped[:2] == ["strace", "-f"]
    assert wrapped[-4:] == ["--", "/usr/local/bin/codex", "exec", "--json"]
    assert "/tmp/trace.log" in wrapped


def test_parse_first_denied_event_emits_minimal_public_metadata() -> None:
    trace = (
        '4242 1756544400.125000 openat(AT_FDCWD, "ffffffffffffffffffffffffffffffffffffffff", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )

    evidence = parse_first_denied_filesystem_event(
        trace,
        provider_started_at_epoch=1756544400.100,
        executable="/usr/local/bin/codex",
    )

    assert evidence is not None
    assert evidence.blocker_signature == BLOCKER_SIGNATURE
    assert evidence.syscall == "openat"
    assert evidence.path == "ffffffffffffffffffffffffffffffffffffffff"
    assert evidence.provider_offset_ms == 25
    assert evidence.process_id == 4242
    assert evidence.executable == "codex"
    assert set(evidence.as_public_dict()) == {
        "blocker_signature",
        "syscall",
        "path",
        "provider_offset_ms",
        "process_id",
        "executable",
        "phase",
    }


def test_parse_ignores_non_permission_failures() -> None:
    trace = (
        '4242 1756544400.125000 openat(AT_FDCWD, "/missing", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
    )

    assert (
        parse_first_denied_filesystem_event(
            trace,
            provider_started_at_epoch=1756544400.100,
            executable="/usr/local/bin/codex",
        )
        is None
    )


def test_unsafe_path_fails_closed_without_leaking_value() -> None:
    assert not public_safe_path("/srv/secrets/provider-token")
    trace = (
        '4242 1756544400.125000 openat(AT_FDCWD, "/srv/secrets/provider-token", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )

    assert (
        parse_first_denied_filesystem_event(
            trace,
            provider_started_at_epoch=1756544400.100,
            executable="/usr/local/bin/codex",
        )
        is None
    )
