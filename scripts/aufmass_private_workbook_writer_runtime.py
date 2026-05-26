from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import sleep
from typing import Any


DEFAULT_WRITE_VERIFY_ATTEMPTS = 3

CellValue = object
TableValues = Sequence[Sequence[CellValue]]
WriteCallable = Callable[[list[list[CellValue]]], object]
ReadCallable = Callable[[], TableValues]


@dataclass(frozen=True)
class WriteVerifyResult:
    """Public-safe status for a verified workbook table write."""

    attempts: int
    row_count: int
    column_count: int


class WriteVerificationError(RuntimeError):
    """Raised when a workbook write cannot be verified after retries."""


def table_rows_to_values(
    columns: Sequence[str], rows: Sequence[Mapping[str, CellValue]]
) -> list[list[CellValue]]:
    """Convert row mappings into a deterministic workbook table."""
    if not columns:
        raise ValueError("columns must include at least one column")
    cleaned_columns = [_clean_column(column) for column in columns]
    return [
        cleaned_columns,
        *[[row.get(column, "") for column in cleaned_columns] for row in rows],
    ]


def write_table_with_verify_retry(
    write_table: WriteCallable,
    read_table: ReadCallable,
    expected_values: TableValues,
    *,
    max_attempts: int = DEFAULT_WRITE_VERIFY_ATTEMPTS,
    retry_delay_seconds: float = 0.0,
) -> WriteVerifyResult:
    """Write a workbook table, read it back, and retry until it verifies."""
    expected = _normalize_table(expected_values)
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must not be negative")

    last_error: BaseException | None = None
    last_actual: list[list[CellValue]] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            write_table(expected)
            last_actual = _normalize_table(read_table())
            if last_actual == expected:
                return WriteVerifyResult(
                    attempts=attempt,
                    row_count=len(expected),
                    column_count=max((len(row) for row in expected), default=0),
                )
            last_error = WriteVerificationError(
                f"workbook table verification failed on attempt {attempt}"
            )
        except Exception as exc:  # noqa: BLE001 - retry boundary preserves final cause.
            last_error = exc
        if attempt < max_attempts and retry_delay_seconds:
            sleep(retry_delay_seconds)

    detail = "verification did not match expected table"
    if last_actual is not None:
        detail = (
            f"{detail}: expected {len(expected)} rows, "
            f"read back {len(last_actual)} rows"
        )
    raise WriteVerificationError(
        f"workbook table write failed verification after {max_attempts} attempts; {detail}"
    ) from last_error


def write_worksheet_range_with_verify_retry(
    worksheet: Any,
    range_name: str,
    values: TableValues,
    *,
    max_attempts: int = DEFAULT_WRITE_VERIFY_ATTEMPTS,
    retry_delay_seconds: float = 0.0,
) -> WriteVerifyResult:
    """Adapter for worksheet-like clients with update(range, values) and get(range)."""
    cleaned_range = range_name.strip()
    if not cleaned_range:
        raise ValueError("range_name must not be empty")

    def write_table(expected: list[list[CellValue]]) -> object:
        return worksheet.update(cleaned_range, expected)

    def read_table() -> TableValues:
        return worksheet.get(cleaned_range)

    return write_table_with_verify_retry(
        write_table,
        read_table,
        values,
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
    )


write_table_with_retry_verify = write_table_with_verify_retry
write_verify_retry_table = write_table_with_verify_retry


def _normalize_table(values: TableValues) -> list[list[CellValue]]:
    if isinstance(values, (str, bytes)):
        raise TypeError("table values must be a sequence of row sequences")
    normalized: list[list[CellValue]] = []
    width = 0
    for row in values:
        if isinstance(row, (str, bytes)):
            raise TypeError("table rows must be sequences, not strings")
        normalized_row = ["" if cell is None else cell for cell in row]
        normalized.append(normalized_row)
        width = max(width, len(normalized_row))
    return [row + [""] * (width - len(row)) for row in normalized]


def _clean_column(column: str) -> str:
    cleaned = column.strip()
    if not cleaned:
        raise ValueError("columns must not include empty names")
    return cleaned
