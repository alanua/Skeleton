from __future__ import annotations

from typing import Mapping

from scripts import activate_five_layer_private_memory as activation


def test_quiet_installer_rewrites_exact_pinned_cognee_to_ollama_extra(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(
        command: list[str],
        *,
        env: Mapping[str, str],
        text: bool,
        stdout: object,
        stderr: object,
        timeout: int,
        check: bool,
    ) -> Completed:
        captured["command"] = command
        captured["env"] = dict(env)
        captured["text"] = text
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["timeout"] = timeout
        captured["check"] = check
        return Completed()

    monkeypatch.setattr(activation.subprocess, "run", fake_run)
    code, output = activation._quiet_installer(
        [
            "/private/venv/bin/python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "cognee==1.4.0",
        ],
        {"HOME": "/private"},
    )

    assert code == 0
    assert output == ""
    assert captured["command"] == [
        "/private/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "cognee[ollama]==1.4.0",
    ]
    assert captured["env"] == {"HOME": "/private"}
    assert captured["timeout"] == 600
    assert captured["check"] is False


def test_quiet_installer_fails_closed_on_unexpected_requirement(monkeypatch) -> None:
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(activation.subprocess, "run", fake_run)
    code, output = activation._quiet_installer(
        ["python", "-m", "pip", "install", "cognee==1.4.1"],
        {},
    )

    assert (code, output) == (1, "")
    assert called is False
