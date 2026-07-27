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
    """Discard Python streams and direct writes to process fds 1 and 2."""

    saved_stdout: int | None = None
    saved_stderr: int | None = None
    try:
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)
    except OSError:
        if saved_stdout is not None:
            os.close(saved_stdout)
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
        return

    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            _flush_stream(sys.stdout)
            _flush_stream(sys.stderr)
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            try:
                with redirect_stdout(sink), redirect_stderr(sink):
                    yield
            finally:
                _flush_stream(sys.stdout)
                _flush_stream(sys.stderr)
                os.dup2(saved_stdout, 1)
                os.dup2(saved_stderr, 2)
    finally:
        os.close(saved_stdout)
        os.close(saved_stderr)


def _flush_stream(stream: Any) -> None:
    try:
        stream.flush()
    except (AttributeError, OSError, ValueError):
        pass
