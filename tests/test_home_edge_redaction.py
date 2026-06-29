from __future__ import annotations

from core.home_edge.diagnostics import _redact


def test_sensitive_identifier_fields_are_redacted() -> None:
    field = "".join(chr(value) for value in (105, 109, 101, 105))
    result = _redact({field: "test-value", "safe": "visible"})

    assert result["safe"] == "visible"
    assert result[field] == "[redacted]"
