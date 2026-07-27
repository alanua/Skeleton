from __future__ import annotations

import functools
import inspect
import os
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any, Iterator

from core import cognee_worker_bootstrap as _bootstrap

_PATCH_MARKER = "__skeleton_cognee_output_guard__"


def install_cognee_worker_output_guard() -> bool:
    """Keep the isolated worker stdout channel reserved for its JSON response."""

    current = _bootstrap._stage_wrapper
    if getattr(current, _PATCH_MARKER, False):
        return False

    def guarded_stage_wrapper(
        operation: Any, operation_name: str, reason: str
    ) -> Any:
        wrapped = current(operation, operation_name, reason)

        @functools.wraps(wrapped)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            with _silence_process_output():
                result = wrapped(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result

        setattr(guarded, _bootstrap._WRAPPER_MARKER, True)
        return guarded

    setattr(guarded_stage_wrapper, _PATCH_MARKER, True)
    _bootstrap._stage_wrapper = guarded_stage_wrapper
    return True


@contextmanager
def _silence_process_output() -> Iterator[None]:
    """Discard Python and direct fd writes, restoring both streams reliably."""

    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
        saved_stdout = os.dup(stdout_fd)
        saved_stderr = os.dup(stderr_fd)
    except (AttributeError, OSError, ValueError):
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
        return

    null_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        _flush_stream(sys.stdout)
        _flush_stream(sys.stderr)
        os.dup2(null_fd, stdout_fd)
        os.dup2(null_fd, stderr_fd)
        try:
            yield
        finally:
            _flush_stream(sys.stdout)
            _flush_stream(sys.stderr)
            os.dup2(saved_stdout, stdout_fd)
            os.dup2(saved_stderr, stderr_fd)
    finally:
        os.close(null_fd)
        os.close(saved_stdout)
        os.close(saved_stderr)


def _flush_stream(stream: Any) -> None:
    try:
        stream.flush()
    except (AttributeError, OSError, ValueError):
        pass
