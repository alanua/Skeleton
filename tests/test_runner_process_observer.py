from __future__ import annotations

from core.runner_process_observer import (
    BLOCKER_SIGNATURE,
    MANIFEST_DENIED_PATH,
    TARGET_DENIED_PATH,
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


def test_parse_target_denied_event_emits_minimal_public_metadata() -> None:
    trace = (
        f'4242 1756544400.125000 openat(AT_FDCWD, "{TARGET_DENIED_PATH}", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )

    evidence = parse_first_denied_filesystem_event(
        trace,
        provider_started_at_epoch=1756544400.100,
        executable="/usr/local/bin/codex",
    )

    assert evidence is not None
    assert evidence.blocker_signature == BLOCKER_SIGNATURE
    assert evidence.syscall == "openat"
    assert evidence.path == TARGET_DENIED_PATH
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


def test_parse_manifest_denied_event_emits_only_bounded_public_target() -> None:
    trace = (
        f'4242 1756544400.125000 openat(AT_FDCWD, "{MANIFEST_DENIED_PATH}", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )

    evidence = parse_first_denied_filesystem_event(
        trace,
        provider_started_at_epoch=1756544400.100,
        executable="/usr/local/bin/codex",
    )

    assert evidence is not None
    assert evidence.path == MANIFEST_DENIED_PATH
    assert evidence.blocker_signature == BLOCKER_SIGNATURE


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


def test_unrelated_denied_event_is_skipped_before_target_event() -> None:
    trace = (
        '4241 1756544400.110000 openat(AT_FDCWD, "/unrelated", O_RDONLY) = -1 EACCES (Permission denied)\n'
        f'4242 1756544400.125000 openat(AT_FDCWD, "{TARGET_DENIED_PATH}", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )

    evidence = parse_first_denied_filesystem_event(
        trace,
        provider_started_at_epoch=1756544400.100,
        executable="/usr/local/bin/codex",
    )

    assert evidence is not None
    assert evidence.path == TARGET_DENIED_PATH
    assert evidence.process_id == 4242


def test_unsafe_expected_path_fails_closed_without_leaking_value() -> None:
    unsafe_path = "/srv/secrets/provider-token"
    assert not public_safe_path(unsafe_path)
    trace = (
        f'4242 1756544400.125000 openat(AT_FDCWD, "{unsafe_path}", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )

    assert (
        parse_first_denied_filesystem_event(
            trace,
            provider_started_at_epoch=1756544400.100,
            executable="/usr/local/bin/codex",
            expected_path=unsafe_path,
        )
        is None
    )


def test_arbitrary_public_safe_expected_path_is_not_eligible() -> None:
    arbitrary = "other.json"
    trace = (
        f'4242 1756544400.125000 openat(AT_FDCWD, "{arbitrary}", O_RDONLY) = -1 EACCES (Permission denied)\n'
    )
    assert (
        parse_first_denied_filesystem_event(
            trace,
            provider_started_at_epoch=1756544400.100,
            executable="/usr/local/bin/codex",
            expected_path=arbitrary,
        )
        is None
    )
