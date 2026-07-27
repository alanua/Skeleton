from __future__ import annotations

from core import cognee_worker_bootstrap as bootstrap
from core.cognee_validation_error_detail import (
    install_pydantic_validation_error_detail,
)


def _validation_error(*, error_type: str, location: object) -> BaseException:
    def errors(self, **kwargs):
        assert kwargs == {
            "include_url": False,
            "include_context": False,
            "include_input": False,
        }
        return [
            {
                "type": error_type,
                "loc": location,
                "msg": "private worker detail",
                "input": "private input",
            }
        ]

    error_class = type(
        "ValidationError",
        (ValueError,),
        {
            "__module__": "pydantic_core._pydantic_core",
            "errors": errors,
        },
    )
    return error_class()


def test_pydantic_detail_precedes_generic_value_error(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_safe_exception_reason",
        lambda base_reason, exc: f"{base_reason}_value_error",
    )
    assert install_pydantic_validation_error_detail() is True

    reason = bootstrap._safe_exception_reason(
        "cognee_add_exception",
        _validation_error(error_type="model_type", location=("data",)),
    )

    assert reason == (
        "cognee_add_exception_pydantic_core_validation_error_model_type_data"
    )
    assert "private" not in reason


def test_unknown_validation_detail_is_omitted(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_safe_exception_reason",
        lambda base_reason, exc: f"{base_reason}_value_error",
    )
    assert install_pydantic_validation_error_detail() is True

    reason = bootstrap._safe_exception_reason(
        "cognee_add_exception",
        _validation_error(
            error_type="private_customer_rule",
            location=("customer_secret",),
        ),
    )

    assert reason == "cognee_add_exception_pydantic_core_validation_error"
    assert "customer" not in reason
    assert "secret" not in reason


def test_non_pydantic_errors_keep_existing_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_safe_exception_reason",
        lambda base_reason, exc: f"{base_reason}_type_error",
    )
    assert install_pydantic_validation_error_detail() is True

    assert (
        bootstrap._safe_exception_reason("cognee_add_exception", TypeError())
        == "cognee_add_exception_type_error"
    )
