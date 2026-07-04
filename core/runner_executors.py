from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ExecutionContext:
    targets: Mapping[str, Mapping[str, str]]
    entrypoints: Mapping[str, Callable[[Any], Any]]
    roots: Mapping[str, Path]
    environment: Mapping[str, str]


GENERIC_EXECUTOR_CLASSES = frozenset(
    {
        "local.process",
        "remote.ssh",
        "network.http",
        "python.entrypoint",
        "filesystem",
        "repository",
        "composite",
    }
)
