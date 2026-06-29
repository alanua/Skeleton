from __future__ import annotations

from core.home_edge.diagnostics import _redact


def test_sensitive_identifier_fields_are_redacted() -> None:
    field = "".join(chr(value) for value in (105, 109, 101, 105))
    result = _redact({field: "test-value", "safe": "visible"})

    assert result["safe"] == "visible"
    assert result[field] == "[redacted]"


def test_paths_addresses_and_host_fields_are_redacted() -> None:
    result = _redact(
        {
            "hostname": "runtime-host",
            "address": "100.64.10.74",
            "gateway": "192.0.2.254",
            "identity_path": "/home/runtime/.ssh/id_ed25519",
            "safe_count": 3,
        }
    )

    assert result["hostname"] == "[redacted]"
    assert result["address"] == "[redacted]"
    assert result["gateway"] == "[redacted]"
    assert result["identity_path"] == "[redacted]"
    assert result["safe_count"] == 3
