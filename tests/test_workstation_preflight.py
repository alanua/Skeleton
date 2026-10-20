"""No real device or CAD access: deterministic Windows preflight tests."""
from __future__ import annotations

from scripts.workstation_preflight import inspect_environment, main


def test_supported_synthetic_windows_environment():
    result = inspect_environment(os_name="Windows", python_version=(3, 12),
                                 dependency_present=lambda _name: True)
    assert result["source_only_checks_passed"] is True
    assert result["runtime_authorized"] is False
    assert result["installation_performed"] is False
    assert result["private_cad_adapter_verified"] is False
    assert "node_id" not in result


def test_unsupported_os_missing_python_or_dependency():
    for system, ver, dep in (
        ("Linux", (3, 12), True),
        ("Windows", (3, 10), True),
        ("Windows", (3, 12), False),
    ):
        result = inspect_environment(
            os_name=system, python_version=ver,
            dependency_present=lambda _name: dep,
        )
        assert result["source_only_checks_passed"] is False


def test_cli_is_read_only_and_redacted(capsys):
    rc = main(["--json"])
    data = capsys.readouterr().out
    assert rc in {0, 2}
    assert "hostname" not in data.lower()
    assert "path" not in data.lower()
    assert "password" not in data.lower()
