from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from core import cognee_worker_bootstrap as bootstrap
from core.cognee_worker_output_guard import install_cognee_worker_output_guard


class WorkerError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _write_private_noise() -> None:
    print("private python stdout")
    sys.stderr.write("private python stderr\n")
    os.write(1, b"private fd stdout\n")
    os.write(2, b"private fd stderr\n")


def test_noisy_operation_returns_without_polluting_channel(capfd) -> None:
    async def noisy_add(*args, **kwargs):
        del args, kwargs
        _write_private_noise()
        return {"projected": True}

    install_cognee_worker_output_guard()
    module = SimpleNamespace(add=noisy_add)
    assert bootstrap.install_cognee_operation_wrappers(module) is True

    assert asyncio.run(module.add(data="synthetic")) == {"projected": True}
    captured = capfd.readouterr()
    assert "private" not in captured.out
    assert "private" not in captured.err


def test_noisy_exception_keeps_existing_safe_mapping(monkeypatch, capfd) -> None:
    async def failing_add(*args, **kwargs):
        del args, kwargs
        _write_private_noise()
        raise ValueError("private exception detail")

    monkeypatch.setattr(
        sys.modules["__main__"],
        "CogneeLocalRuntimeError",
        WorkerError,
        raising=False,
    )
    install_cognee_worker_output_guard()
    module = SimpleNamespace(add=failing_add)
    assert bootstrap.install_cognee_operation_wrappers(module) is True

    with pytest.raises(WorkerError) as caught:
        asyncio.run(module.add(data="synthetic"))
    assert caught.value.reason_code == "cognee_add_exception_value_error"
    assert "private exception detail" not in str(caught.value)
    captured = capfd.readouterr()
    assert "private" not in captured.out
    assert "private" not in captured.err


def test_output_guard_installation_is_idempotent() -> None:
    install_cognee_worker_output_guard()
    assert install_cognee_worker_output_guard() is False
