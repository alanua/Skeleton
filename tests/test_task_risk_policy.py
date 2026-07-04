from __future__ import annotations

from core.task_risk_policy import minimum_risk


def test_read_only_http_is_green() -> None:
    assert minimum_risk(
        "network.http",
        ({"method": "GET"},),
    ) == "green"


def test_http_mutation_is_yellow() -> None:
    assert minimum_risk(
        "network.http",
        ({"method": "POST"},),
    ) == "yellow"


def test_local_process_is_yellow() -> None:
    assert minimum_risk(
        "local.process",
        ({"argv": ["true"]},),
    ) == "yellow"


def test_filesystem_write_is_yellow() -> None:
    assert minimum_risk(
        "filesystem",
        ({"operation": "write_text"},),
    ) == "yellow"
