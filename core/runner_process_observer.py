from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Sequence


BLOCKER_SIGNATURE = "e3d01b54774a3957"
TARGET_DENIED_PATH = "f" * 40
TRACE_SYSCALLS = "open,openat,openat2,creat,mkdir,mkdirat,rename,renameat,renameat2,unlink,unlinkat,stat,lstat,newfstatat,access,faccessat,faccessat2,chdir"
_DENIED_ERRNOS = frozenset({"EACCES", "EPERM"})
_TRACE_LINE = re.compile(
    r"^(?P<pid>\d+)\s+(?P<stamp>\d+(?:\.\d+)?)\s+(?P<syscall>[a-zA-Z0-9_]+)\((?P<args>.*)\)\s+=\s+-1\s+(?P<errno>EACCES|EPERM)\b"
)
_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')
_UNSAFE_PATH_MARKERS = (
    "/secrets/",
    "/credentials/",
    "/private/",
    "token=",
    "password=",
    "secret=",
)


@dataclass(frozen=True)
class SpawnDiagnosticEvidence:
    blocker_signature: str
    syscall: str
    path: str
    provider_offset_ms: int
    process_id: int
    executable: str
    phase: str = "provider_spawn"

    def as_public_dict(self) -> dict[str, object]:
        return {
            "blocker_signature": self.blocker_signature,
            "syscall": self.syscall,
            "path": self.path,
            "provider_offset_ms": self.provider_offset_ms,
            "process_id": self.process_id,
            "executable": self.executable,
            "phase": self.phase,
        }


def build_spawn_trace_command(
    command: Sequence[str],
    *,
    trace_path: str | Path,
) -> list[str]:
    """Wrap one real provider process launch without changing its env/cwd contract."""
    if not command:
        raise ValueError("command must not be empty")
    return [
        "strace",
        "-f",
        "-ttt",
        "-s",
        "4096",
        "-e",
        f"trace={TRACE_SYSCALLS}",
        "-o",
        str(trace_path),
        "--",
        *command,
    ]


def _first_quoted_path(args: str) -> str | None:
    match = _QUOTED.search(args)
    if match is None:
        return None
    raw = match.group(1)
    return bytes(raw, "utf-8").decode("unicode_escape")


def public_safe_path(path: str) -> bool:
    lowered = path.lower()
    if any(marker in lowered for marker in _UNSAFE_PATH_MARKERS):
        return False
    if "\x00" in path or "\n" in path or "\r" in path:
        return False
    return True


def parse_first_denied_filesystem_event(
    trace_text: str,
    *,
    provider_started_at_epoch: float,
    executable: str,
    expected_path: str = TARGET_DENIED_PATH,
    blocker_signature: str = BLOCKER_SIGNATURE,
) -> SpawnDiagnosticEvidence | None:
    """Return only the target EACCES/EPERM event, never an arbitrary denied path.

    Env values, prompt text and arbitrary argv are intentionally never emitted.
    Unrelated denied events are skipped so they cannot be misclassified as this blocker.
    """
    if not public_safe_path(expected_path):
        return None
    for line in trace_text.splitlines():
        match = _TRACE_LINE.match(line.strip())
        if match is None or match.group("errno") not in _DENIED_ERRNOS:
            continue
        path = _first_quoted_path(match.group("args"))
        if path != expected_path:
            continue
        offset_ms = max(
            0,
            int(round((float(match.group("stamp")) - provider_started_at_epoch) * 1000)),
        )
        return SpawnDiagnosticEvidence(
            blocker_signature=blocker_signature,
            syscall=match.group("syscall"),
            path=path,
            provider_offset_ms=offset_ms,
            process_id=int(match.group("pid")),
            executable=Path(executable).name,
        )
    return None
