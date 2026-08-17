#!/usr/bin/env bash
set -euo pipefail

REPO=/home/agent/agent-dev/repos/Skeleton
WT=/home/agent/agent-dev/worktrees/phase0a-2919-v2
BRANCH=runner/runner-timer-scope-codex-only-v1
OLD_HEAD=44d4a378bd33bb123de7aea22032773888519667
MAIN=34654047c2b36da89dd5ac54440a42ee66325cac

cd "$REPO"

echo "=== CANONICAL CHECKOUT SAFETY ==="
test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$MAIN"
test -z "$(git status --porcelain --untracked-files=all)"

git fetch origin main "$BRANCH"
test "$(git rev-parse origin/main)" = "$MAIN"
test "$(git rev-parse origin/$BRANCH)" = "$OLD_HEAD"
test ! -e "$WT"
if git worktree list --porcelain | grep -Fq "branch refs/heads/$BRANCH"; then
  echo "BLOCKED: PR branch already checked out in another worktree"
  exit 1
fi

mkdir -p "$(dirname "$WT")"
git branch -f "$BRANCH" "origin/$BRANCH"
git worktree add "$WT" "$BRANCH"

cd "$WT"

echo "=== REBASE SAME PR ON CURRENT MAIN ==="
git rebase origin/main

echo "=== REALIGN STALE TESTS ==="
cat > tests/test_runner_child_environment_openrouter.py <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment


def test_codegen_child_environment_scrubs_all_secret_sources(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    environment = {
        "PATH": "/usr/bin",
        "OPENROUTER_API_KEY": "must-not-reach-codex",
        "BWS_ACCESS_TOKEN": "must-not-reach-codex",
        "CREDENTIALS_DIRECTORY": "/run/credentials/private",
        "LLM_API_KEY": "overlay-key",
        "LLM_MODEL": "overlay-model",
        "MAX_BUDGET_PER_TASK": "999",
        "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "also-scrubbed",
        "SAFE_SETTING": "kept",
    }

    sanitized = sanitize_codegen_child_environment(environment, authority_environment=environment)

    assert sanitized == {"PATH": "/usr/bin", "SAFE_SETTING": "kept"}
    assert environment["OPENROUTER_API_KEY"] == "must-not-reach-codex"
    assert environment["BWS_ACCESS_TOKEN"] == "must-not-reach-codex"


def test_child_environment_has_no_implicit_openrouter_binding() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert not hasattr(child_env, "_bind_trusted_openrouter")
    assert "bind_registered_environment_credential" not in source
    assert "runner-openhands" not in source
    assert "bind-openrouter-fallback" not in source


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _run_quota_wrapper(
    tmp_path: Path,
    *,
    include_fallback_key: bool,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    wrapper = bin_dir / "codex"
    marker = tmp_path / "openhands-called"

    _write_executable(
        codex,
        "#!/bin/sh\n"
        "test -z \"${LLM_API_KEY:-}\" || exit 21\n"
        "test -z \"${OPENROUTER_API_KEY:-}\" || exit 22\n"
        "test -z \"${BWS_ACCESS_TOKEN:-}\" || exit 23\n"
        "test -z \"${CREDENTIALS_DIRECTORY:-}\" || exit 24\n"
        "printf '%s\\n' 'usage limit reached' >&2\n"
        "exit 1\n",
    )
    _write_executable(
        openhands,
        "#!/bin/sh\n"
        "printf '%s\\n' called > \"$OPENHANDS_MARKER\"\n"
        "exit 0\n",
    )
    _write_executable(wrapper, child_env._WRAPPER)

    environment = dict(os.environ)
    environment.update(
        {
            "PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_OPENHANDS_OPENROUTER_REQUIRED": "1",
            "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/synthetic",
            "OPENROUTER_API_KEY": "must-not-leak",
            "BWS_ACCESS_TOKEN": "must-not-leak",
            "CREDENTIALS_DIRECTORY": "/must/not/leak",
            "OPENHANDS_MARKER": str(marker),
        }
    )
    if include_fallback_key:
        environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "synthetic-openrouter-key"

    result = subprocess.run(
        [str(wrapper), "exec", "--cd", str(workdir), "-"],
        input="synthetic bounded task",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    return result, marker


def test_wrapper_never_exposes_openrouter_key_or_calls_openhands_on_quota(tmp_path: Path) -> None:
    result, marker = _run_quota_wrapper(tmp_path, include_fallback_key=True)
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "synthetic-openrouter-key" not in result.stdout
    assert "synthetic-openrouter-key" not in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not marker.exists()


def test_wrapper_ignores_obsolete_openrouter_required_toggle_and_preserves_codex_failure(tmp_path: Path) -> None:
    result, marker = _run_quota_wrapper(tmp_path, include_fallback_key=False)
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "SKELETON_CODEGEN_FALLBACK_CONFIG_UNAVAILABLE" not in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert not marker.exists()
PY

cat > tests/test_runner_child_environment_provider_route.py <<'PY'
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


def test_wrapper_preserves_primary_failure_without_external_fallback(tmp_path: Path) -> None:
    result, _codex_argv, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'usage limit reached' >&2\nexit 1\n",
    )
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "SKELETON_CODEGEN_PRIMARY_FAILURE" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not fallback_marker.exists()
PY

cat > tests/test_runner_credential_binding.py <<'PY'
from __future__ import annotations

from pathlib import Path

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment


def test_runner_child_environment_has_no_implicit_openrouter_credential_runtime() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert not hasattr(child_env, "_bind_trusted_openrouter")
    assert "bind_registered_environment_credential" not in source
    assert "RegisteredCredentialRuntimeError" not in source
    assert "runner-openhands" not in source
    assert "bind-openrouter-fallback" not in source


def test_runner_consumer_has_no_direct_bitwarden_or_secretstore_resolution_imports() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert "BwsCliSecretsManagerStore" not in source
    assert "bitwarden_reference_from_systemd_credential" not in source
    assert "SecretStoreGate" not in source
    assert "SecretAccessPolicy" not in source


def test_sanitize_strips_provider_credentials_without_resolving_them(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    environment = {
        "HOME": "/overlay/home",
        "PATH": "/overlay/bin",
        "BWS_ACCESS_TOKEN": "caller-must-not-win",
        "OPENROUTER_API_KEY": "caller-must-not-win",
        "LLM_API_KEY": "caller-must-not-win",
        "LLM_MODEL": "attacker/model",
        "SKELETON_OPENHANDS_BIN": "/untrusted/openhands",
        "SKELETON_OPENROUTER_FALLBACK_API_KEY": "caller-must-not-win",
        "SKELETON_OPENROUTER_FALLBACK_MODEL": "attacker/model",
        "UNRELATED": "keep",
    }

    sanitized = sanitize_codegen_child_environment(
        environment,
        authority_environment={"HOME": "/trusted", "PATH": "/trusted/bin"},
    )

    assert sanitized == {
        "HOME": "/overlay/home",
        "PATH": "/overlay/bin",
        "UNRELATED": "keep",
    }
PY

echo "=== ALLOWLIST ==="
CHANGED="$({ git diff --name-only origin/main...HEAD; git diff --name-only; } | sed '/^$/d' | sort -u)"
printf '%s\n' "$CHANGED"
for f in $CHANGED; do
  case "$f" in
    core/runner_child_environment.py|tests/test_runner_child_environment.py|tests/test_runner_child_environment_openrouter.py|tests/test_runner_child_environment_provider_route.py|tests/test_runner_credential_binding.py)
      ;;
    *)
      echo "BLOCKED: unexpected file $f"
      exit 1
      ;;
  esac
done

! grep -q '_bind_trusted_openrouter' core/runner_child_environment.py
! grep -q 'bind_registered_environment_credential' core/runner_child_environment.py

echo "=== FOCUSED ==="
python3 -m pytest -q \
  tests/test_runner_child_environment.py \
  tests/test_runner_child_environment_openrouter.py \
  tests/test_runner_child_environment_provider_route.py \
  tests/test_runner_credential_binding.py
python3 -m pytest -q tests/test_runner_repository_maintenance_executor.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py

echo "=== STATIC ==="
python3 -m py_compile \
  core/runner_child_environment.py \
  tests/test_runner_child_environment.py \
  tests/test_runner_child_environment_openrouter.py \
  tests/test_runner_child_environment_provider_route.py \
  tests/test_runner_credential_binding.py
git diff --check

echo "=== FULL ==="
python3 -m pytest -q

echo "=== COMMIT TEST REALIGNMENT ==="
git add \
  tests/test_runner_child_environment_openrouter.py \
  tests/test_runner_child_environment_provider_route.py \
  tests/test_runner_credential_binding.py
git diff --cached --check
git commit -m "P0 realign child environment tests to explicit binding"

FINAL_CHANGED="$(git diff --name-only origin/main...HEAD | sort -u)"
printf '%s\n' "$FINAL_CHANGED"
for f in $FINAL_CHANGED; do
  case "$f" in
    core/runner_child_environment.py|tests/test_runner_child_environment.py|tests/test_runner_child_environment_openrouter.py|tests/test_runner_child_environment_provider_route.py|tests/test_runner_credential_binding.py)
      ;;
    *)
      echo "BLOCKED: unexpected committed file $f"
      exit 1
      ;;
  esac
done

git diff --check origin/main...HEAD
NEW_HEAD="$(git rev-parse HEAD)"

echo "=== UPDATE SAME PR BRANCH ==="
git push \
  --force-with-lease="refs/heads/$BRANCH:$OLD_HEAD" \
  origin "HEAD:refs/heads/$BRANCH"

echo "=== CLEANUP ISOLATED WORKTREE ==="
cd "$REPO"
git worktree remove "$WT"

test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$MAIN"
test -z "$(git status --porcelain --untracked-files=all)"

echo "PR=2919"
echo "HEAD=$NEW_HEAD"
echo "CANONICAL_RUNTIME_BRANCH=$(git branch --show-current)"
echo "CANONICAL_RUNTIME_HEAD=$(git rev-parse HEAD)"
echo "DONE"
