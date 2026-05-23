from __future__ import annotations

from unittest.mock import Mock

import pytest

from core.aufmass_workbook_writer import (
    WorkbookRetryPolicy,
    batch_update_with_cancelled_retry,
    is_cancelled_batch_update_error,
    write_rows_to_worksheet,
)


class CancelledBatchUpdate(Exception):
    pass


class ApiError(Exception):
    def __init__(self, message: str, *, status: object = None) -> None:
        super().__init__(message)
        self.status = status


def test_write_rows_builds_single_batch_update_without_retry() -> None:
    worksheet = Mock()
    worksheet.batch_update.return_value = {"updated": True}
    sleeper = Mock()

    result = write_rows_to_worksheet(
        worksheet,
        columns=["room_id", "room_name", "net_wall_area"],
        rows=[
            {"room_id": "synthetic-room-1", "room_name": "Synthetic Room", "net_wall_area": 12.5}
        ],
        start_cell="Rooms!A1",
        sleeper=sleeper,
    )

    assert result == {"updated": True}
    worksheet.batch_update.assert_called_once_with(
        [
            {
                "range": "Rooms!A1",
                "values": [
                    ["room_id", "room_name", "net_wall_area"],
                    ["synthetic-room-1", "Synthetic Room", 12.5],
                ],
            }
        ],
        value_input_option="RAW",
    )
    sleeper.assert_not_called()


def test_cancelled_batch_update_retries_with_bounded_backoff_then_succeeds() -> None:
    worksheet = Mock()
    worksheet.batch_update.side_effect = [
        CancelledBatchUpdate("cancelled batch update"),
        {"updated": True},
    ]
    sleeper = Mock()

    result = batch_update_with_cancelled_retry(
        worksheet,
        [{"range": "A1", "values": [["synthetic"]]}],
        retry_policy=WorkbookRetryPolicy(
            max_attempts=3,
            initial_delay_seconds=0.1,
            backoff_factor=2.0,
            max_delay_seconds=0.15,
        ),
        sleeper=sleeper,
    )

    assert result == {"updated": True}
    assert worksheet.batch_update.call_count == 2
    sleeper.assert_called_once_with(0.1)


def test_cancelled_batch_update_retry_exhaustion_raises_original_error() -> None:
    error = CancelledBatchUpdate("worksheet batch update was cancelled")
    worksheet = Mock()
    worksheet.batch_update.side_effect = error
    sleeper = Mock()

    with pytest.raises(CancelledBatchUpdate) as exc_info:
        batch_update_with_cancelled_retry(
            worksheet,
            [{"range": "A1", "values": [["synthetic"]]}],
            retry_policy=WorkbookRetryPolicy(
                max_attempts=4,
                initial_delay_seconds=0.1,
                backoff_factor=3.0,
                max_delay_seconds=0.25,
            ),
            sleeper=sleeper,
        )

    assert exc_info.value is error
    assert worksheet.batch_update.call_count == 4
    assert [call.args[0] for call in sleeper.call_args_list] == [0.1, 0.25, 0.25]


def test_non_cancelled_batch_update_error_is_not_retried() -> None:
    worksheet = Mock()
    worksheet.batch_update.side_effect = RuntimeError("permission denied")
    sleeper = Mock()

    with pytest.raises(RuntimeError, match="permission denied"):
        batch_update_with_cancelled_retry(
            worksheet,
            [{"range": "A1", "values": [["synthetic"]]}],
            sleeper=sleeper,
        )

    worksheet.batch_update.assert_called_once()
    sleeper.assert_not_called()


def test_cancelled_error_detection_accepts_google_style_status_code() -> None:
    assert is_cancelled_batch_update_error(ApiError("request failed", status=499))


def test_cancelled_error_detection_accepts_uppercase_status_name() -> None:
    assert is_cancelled_batch_update_error(ApiError("request failed", status="CANCELLED"))


def test_error_detection_requires_cancelled_batch_update_text() -> None:
    assert not is_cancelled_batch_update_error(RuntimeError("cancelled unrelated operation"))


def test_retry_policy_validation_rejects_unbounded_attempts() -> None:
    worksheet = Mock()

    with pytest.raises(ValueError, match="max_attempts"):
        batch_update_with_cancelled_retry(
            worksheet,
            [{"range": "A1", "values": [["synthetic"]]}],
            retry_policy=WorkbookRetryPolicy(max_attempts=0),
        )


def test_write_rows_requires_columns() -> None:
    worksheet = Mock()

    with pytest.raises(ValueError, match="columns"):
        write_rows_to_worksheet(worksheet, columns=[], rows=[])
