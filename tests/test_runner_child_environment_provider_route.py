from __future__ import annotations

import os
from pathlib import Path
import subprocess

import core.runner_child_environment as child_env


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _run_wrapper(
    tmp_path: Path,
    *,
    codex_body: str,
    argv: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    wrapper = bin_dir / "codex"
    codex_argv = tmp_path / "codex-argv"
    fallback_marker = tmp_path / "openhands-called"
    _write_executable(codex, codex_body)
    _write_executable(
        openhands,
        "#!/bin/sh\nprintf '%s\\n' called > \"$OPENHANDS_MARKER\"\nexit 0\n",
    )
    _write_executable(wrapper, child_env._WRAPPER)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "CODEX_ARGV_MARKER": str(codex_argv),
            "OPENHANDS_MARKER": str(fallback_marker),
        }
    )
    args = argv or ["exec", "--sandbox", "read-only", "--cd", str(workdir), "-"]
    result = subprocess.run(
        [str(wrapper), *args],
        input="synthetic bounded task",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    return result, codex_argv, fallback_marker


def test_wrapper_defaults_codex_to_gpt_5_6_and_reports_codex_provider(tmp_path: Path) -> None:
    result, codex_argv, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body=(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$CODEX_ARGV_MARKER\"\n"
            "printf '%s\\n' 'RESULT: OK'\n"
            "exit 0\n"
        ),
    )
    assert result.returncode == 0
    assert "SKELETON_CODEGEN_PROVIDER=codex" in result.stdout
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert codex_argv.read_text(encoding="utf-8").splitlines()[:3] == [
        "exec",
        "--model",
        "gpt-5.6",
    ]
    assert not fallback_marker.exists()


def test_wrapper_preserves_explicit_trusted_model(tmp_path: Path) -> None:
    result, codex_argv, _fallback_marker = _run_wrapper(
        tmp_path,
        codex_body=(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$CODEX_ARGV_MARKER\"\n"
            "exit 0\n"
        ),
        argv=["exec", "--model", "gpt-5.6-sol", "--cd", str(tmp_path / "work"), "-"],
    )
    assert result.returncode == 0
    args = codex_argv.read_text(encoding="utf-8").splitlines()
    assert args.count("--model") == 1
    assert "gpt-5.6-sol" in args


def test_wrapper_reports_bounded_primary_failure_before_openhands_fallback(tmp_path: Path) -> None:
    result, _codex_argv, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'usage limit reached' >&2\nexit 1\n",
    )
    assert result.returncode == 0
    assert "SKELETON_CODEGEN_PROVIDER=openhands" in result.stdout
    assert "SKELETON_CODEGEN_PRIMARY_FAILURE=quota_or_provider_outage" in result.stdout
    assert "RESULT: OK" in result.stdout
    assert fallback_marker.read_text(encoding="utf-8").strip() == "called"
