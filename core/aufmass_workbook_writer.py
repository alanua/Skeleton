from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Any, Callable, Mapping, Protocol, Sequence


DEFAULT_VALUE_INPUT_OPTION = "RAW"
DEFAULT_START_CELL = "A1"


class WorkbookWorksheet(Protocol):
    def batch_update(
        self,
        data: Sequence[Mapping[str, object]],
        *,
        value_input_option: str = DEFAULT_VALUE_INPUT_OPTION,
    ) -> object:
        """Write a batch of range updates to a worksheet-like object."""


@dataclass(frozen=True)
class WorkbookRetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.25
    backoff_factor: float = 2.0
    max_delay_seconds: float = 2.0


def write_rows_to_worksheet(
    worksheet: WorkbookWorksheet,
    *,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    start_cell: str = DEFAULT_START_CELL,
    value_input_option: str = DEFAULT_VALUE_INPUT_OPTION,
    retry_policy: WorkbookRetryPolicy | None = None,
    sleeper: Callable[[float], None] = sleep,
) -> object:
    """Write tabular rows to a worksheet with bounded retry for cancelled updates."""
    if not columns:
        raise ValueError("columns must not be empty.")
    if not start_cell:
        raise ValueError("start_cell must not be empty.")

    values = [list(columns)]
    values.extend([[row.get(column, "") for column in columns] for row in rows])
    return batch_update_with_cancelled_retry(
        worksheet,
        [{"range": start_cell, "values": values}],
        value_input_option=value_input_option,
        retry_policy=retry_policy,
        sleeper=sleeper,
    )


def batch_update_with_cancelled_retry(
    worksheet: WorkbookWorksheet,
    data: Sequence[Mapping[str, object]],
    *,
    value_input_option: str = DEFAULT_VALUE_INPUT_OPTION,
    retry_policy: WorkbookRetryPolicy | None = None,
    sleeper: Callable[[float], None] = sleep,
) -> object:
    """Run worksheet.batch_update, retrying transient cancellation failures only."""
    policy = retry_policy or WorkbookRetryPolicy()
    _validate_retry_policy(policy)

    attempt = 1
    delay = policy.initial_delay_seconds
    while True:
        try:
            return worksheet.batch_update(data, value_input_option=value_input_option)
        except Exception as exc:
            if attempt >= policy.max_attempts or not is_cancelled_batch_update_error(exc):
                raise
            sleeper(delay)
            attempt += 1
            delay = min(delay * policy.backoff_factor, policy.max_delay_seconds)


def is_cancelled_batch_update_error(exc: BaseException) -> bool:
    """Return whether an exception looks like a cancelled worksheet batch update."""
    code = _exception_code(exc)
    code_text = "" if code is None else str(code).lower()
    if code_text in {"499", "cancelled", "canceled", "cancelled_error", "canceled_error"}:
        return True

    class_name = exc.__class__.__name__.lower()
    if class_name in {"cancelled", "canceled", "cancellederror", "cancelederror"}:
        return True

    message = _exception_text(exc).lower()
    if "batch" not in message and "update" not in message:
        return False
    return "cancelled" in message or "canceled" in message or "operation was cancelled" in message


def _validate_retry_policy(policy: WorkbookRetryPolicy) -> None:
    if policy.max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")
    if policy.initial_delay_seconds < 0:
        raise ValueError("initial_delay_seconds must not be negative.")
    if policy.backoff_factor < 1:
        raise ValueError("backoff_factor must be at least 1.")
    if policy.max_delay_seconds < policy.initial_delay_seconds:
        raise ValueError("max_delay_seconds must be at least initial_delay_seconds.")


def _exception_code(exc: BaseException) -> object:
    for attr in ("code", "status", "status_code"):
        value = getattr(exc, attr, None)
        if value is not None:
            return value

    response = getattr(exc, "resp", None)
    if response is not None:
        for attr in ("status", "status_code"):
            value = getattr(response, attr, None)
            if value is not None:
                return value

    return None


def _exception_text(exc: BaseException) -> str:
    parts = [str(exc)]
    for attr in ("reason", "message", "content"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(_stringify(value))

    response = getattr(exc, "resp", None)
    if response is not None:
        for attr in ("reason", "message"):
            value = getattr(response, attr, None)
            if value is not None:
                parts.append(_stringify(value))

    return " ".join(parts)


def _stringify(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
