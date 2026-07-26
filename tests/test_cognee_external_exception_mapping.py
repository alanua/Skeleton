from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from core.cognee_worker_bootstrap import install_cognee_operation_wrappers


class WorkerError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _run_add_failure(monkeypatch, exception_type: type[Exception]) -> WorkerError:
    async def failing_add(*args, **kwargs):
        del args, kwargs
        raise exception_type("private worker detail")

    module = SimpleNamespace(add=failing_add)
    monkeypatch.setattr(
        sys.modules["__main__"], "CogneeLocalRuntimeError", WorkerError, raising=False
    )
    assert install_cognee_operation_wrappers(module) is True
    with pytest.raises(WorkerError) as caught:
        asyncio.run(module.add(data="synthetic"))
    assert "private worker detail" not in str(caught.value)
    return caught.value


def test_allowed_pydantic_core_class_maps_to_safe_reason(monkeypatch) -> None:
    external_error = type(
        "ValidationError",
        (Exception,),
        {"__module__": "pydantic_core._pydantic_core"},
    )
    error = _run_add_failure(monkeypatch, external_error)
    assert error.reason_code == "cognee_add_exception_pydantic_core_validation_error"


def test_allowed_camel_case_class_normalizes_to_snake_case(monkeypatch) -> None:
    external_error = type(
        "ConnectError",
        (Exception,),
        {"__module__": "httpx"},
    )
    error = _run_add_failure(monkeypatch, external_error)
    assert error.reason_code == "cognee_add_exception_httpx_connect_error"


def test_unknown_module_remains_generic_stage(monkeypatch) -> None:
    external_error = type(
        "PrivateCustomerValueError",
        (Exception,),
        {"__module__": "private_runtime.customer"},
    )
    error = _run_add_failure(monkeypatch, external_error)
    assert error.reason_code == "cognee_add_exception"
