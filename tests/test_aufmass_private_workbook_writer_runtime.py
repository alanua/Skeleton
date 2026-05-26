from __future__ import annotations

import pytest

from scripts.aufmass_private_workbook_writer_runtime import (
    WriteVerificationError,
    table_rows_to_values,
    write_table_with_verify_retry,
    write_worksheet_range_with_verify_retry,
)


class FakeWorksheet:
    def __init__(self, stale_reads: int = 0) -> None:
        self.values: list[list[object]] = []
        self.update_calls: list[tuple[str, list[list[object]]]] = []
        self.get_calls: list[str] = []
        self.stale_reads = stale_reads

    def update(self, range_name: str, values: list[list[object]]) -> None:
        self.update_calls.append((range_name, values))
        self.values = values

    def get(self, range_name: str) -> list[list[object]]:
        self.get_calls.append(range_name)
        if len(self.get_calls) <= self.stale_reads:
            return [["stale"]]
        return self.values


def test_table_rows_to_values_uses_declared_columns() -> None:
    values = table_rows_to_values(
        ["room_id", "status", "operator_note"],
        [
            {"room_id": "R-001", "status": "review", "ignored": "not exported"},
            {"room_id": "R-002", "operator_note": "synthetic note"},
        ],
    )

    assert values == [
        ["room_id", "status", "operator_note"],
        ["R-001", "review", ""],
        ["R-002", "", "synthetic note"],
    ]


def test_write_table_with_verify_retry_retries_until_readback_matches() -> None:
    attempts = 0
    written: list[list[object]] = []

    def write_table(values: list[list[object]]) -> None:
        nonlocal attempts, written
        attempts += 1
        written = values

    def read_table() -> list[list[object]]:
        if attempts == 1:
            return [["stale"]]
        return written

    result = write_table_with_verify_retry(
        write_table,
        read_table,
        [["room_id", "status"], ["R-001", "reviewed"]],
    )

    assert result.attempts == 2
    assert result.row_count == 2
    assert result.column_count == 2


def test_write_table_with_verify_retry_retries_after_write_error() -> None:
    attempts = 0
    written: list[list[object]] = []

    def write_table(values: list[list[object]]) -> None:
        nonlocal attempts, written
        attempts += 1
        if attempts == 1:
            raise TimeoutError("synthetic transient failure")
        written = values

    result = write_table_with_verify_retry(
        write_table,
        lambda: written,
        [["room_id"], ["R-001"]],
    )

    assert result.attempts == 2


def test_write_table_with_verify_retry_raises_after_unverified_attempts() -> None:
    write_count = 0

    def write_table(values: list[list[object]]) -> None:
        nonlocal write_count
        write_count += 1

    with pytest.raises(WriteVerificationError, match="after 3 attempts"):
        write_table_with_verify_retry(
            write_table,
            lambda: [["stale"]],
            [["room_id"], ["R-001"]],
        )

    assert write_count == 3


def test_worksheet_adapter_verifies_update_range_before_success() -> None:
    worksheet = FakeWorksheet(stale_reads=1)

    result = write_worksheet_range_with_verify_retry(
        worksheet,
        "Aufmass Review!A1:B2",
        [["room_id", "status"], ["R-001", "reviewed"]],
    )

    assert result.attempts == 2
    assert worksheet.update_calls == [
        ("Aufmass Review!A1:B2", [["room_id", "status"], ["R-001", "reviewed"]]),
        ("Aufmass Review!A1:B2", [["room_id", "status"], ["R-001", "reviewed"]]),
    ]
    assert worksheet.get_calls == ["Aufmass Review!A1:B2", "Aufmass Review!A1:B2"]
