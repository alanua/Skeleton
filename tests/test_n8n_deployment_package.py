from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import hashlib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "n8n"
COMPOSE_PATH = DEPLOY / "compose.yaml"
ENV_EXAMPLE = DEPLOY / "env.example"
DOC_PATH = ROOT / "docs" / "N8N_SELF_HOSTED.md"
SCRIPTS = ["backup.sh", "restore.sh", "validate.sh", "rollback.sh"]
IMAGE = "n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"
KEY = "synthetic_test_key_32_chars_long"


def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_sqlite(path: Path, value: str = "ok") -> None:
    conn = sqlite3.connect(path)
    conn.execute("create table if not exists marker(value text)")
    conn.execute("delete from marker")
    conn.execute("insert into marker(value) values (?)", (value,))
    conn.commit()
    conn.close()
    path.chmod(0o600)


def write_env(tmp_path: Path, data_dir: Path | None = None, backup_dir: Path | None = None, key: str = KEY) -> Path:
    data_dir = data_dir or tmp_path / "data"
    backup_dir = backup_dir or tmp_path / "backups"
    data_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o700)
    backup_dir.chmod(0o700)
    env_file = tmp_path / "n8n.env"
    env_file.write_text(
        "\n".join(
            [
                "COMPOSE_PROJECT_NAME=skeleton-n8n-community",
                "N8N_COMPOSE_PROJECT_NAME=skeleton-n8n-community",
                "N8N_EDITOR_PORT=5678",
                "N8N_HOST=127.0.0.1",
                f"N8N_DATA_DIR={data_dir}",
                f"N8N_BACKUP_DIR={backup_dir}",
                f"N8N_ENCRYPTION_KEY={key}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    return env_file


def fake_docker(tmp_path: Path, running: bool = False, fail_up: bool = False) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    state = tmp_path / "docker-state"
    state.write_text("running" if running else "stopped", encoding="utf-8")
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_FAKE_LOG"
if [ "$1" != "compose" ]; then exit 64; fi
shift
cmd=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file|-f) shift 2 ;;
    config) exit 0 ;;
    ps|stop|up) cmd="$1"; shift; break ;;
    *) shift ;;
  esac
done
case "$cmd" in
  ps)
    if [ "$(cat "$DOCKER_FAKE_STATE")" = "running" ]; then printf 'fake-n8n\\n'; fi
    ;;
  stop)
    printf 'stopped' > "$DOCKER_FAKE_STATE"
    ;;
  up)
    if [ "${DOCKER_FAKE_FAIL_UP:-0}" = "1" ]; then exit 42; fi
    printf 'running' > "$DOCKER_FAKE_STATE"
    ;;
  *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["DOCKER_FAKE_STATE"] = str(state)
    env["DOCKER_FAKE_LOG"] = str(log)
    if fail_up:
        env["DOCKER_FAKE_FAIL_UP"] = "1"
    return env


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def create_backup_archive(backup_sh: Path, env_file: Path, env: dict[str, str]) -> Path:
    result = run([str(backup_sh), "--env-file", str(env_file)], env=env)
    assert result.returncode == 0, result.stderr
    match = re.search(r"created (.+)", result.stdout)
    assert match, result.stdout
    return Path(match.group(1))


def test_package_files_exist() -> None:
    for path in [COMPOSE_PATH, ENV_EXAMPLE, DOC_PATH, *(DEPLOY / script for script in SCRIPTS)]:
        assert path.is_file()


def test_compose_has_one_sqlite_service_no_postgres_or_redis() -> None:
    services = compose()["services"]
    assert set(services) == {"n8n"}
    service = services["n8n"]
    assert service["image"] == IMAGE
    assert service["user"] == "1000:1000"
    assert service["read_only"] is True
    assert "/tmp:size=64m,mode=1777" in service["tmpfs"]
    assert service["ports"] == ["127.0.0.1:${N8N_EDITOR_PORT:-5678}:5678"]
    assert service["environment"]["DB_TYPE"] == "sqlite"
    assert service["environment"]["DB_SQLITE_DATABASE"] == "/home/node/.n8n/database.sqlite"
    rendered = yaml.safe_dump(compose()).lower()
    assert "postgres" not in rendered
    assert "redis" not in rendered
    assert "0.0.0.0" not in rendered


def test_compose_security_resource_bounds_and_bounded_healthcheck() -> None:
    service = compose()["services"]["n8n"]
    assert service["restart"] == "unless-stopped"
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]
    assert service["mem_limit"] == "768m"
    assert service["cpus"] == "1.00"
    assert service["pids_limit"] == 256
    probe = " ".join(service["healthcheck"]["test"])
    assert "node -e" in probe
    assert "setTimeout" in probe
    assert "healthz" not in probe


def test_docs_and_examples_cover_required_safety_properties() -> None:
    combined = "\n".join(text(p) for p in [COMPOSE_PATH, ENV_EXAMPLE, DOC_PATH])
    assert IMAGE in combined
    assert "SQLite-to-PostgreSQL migration" in combined
    assert "does not add PostgreSQL" in combined
    assert "SSH tunnel" in combined
    assert "Tailscale SSH" in combined
    assert "private-memory SQLite" in combined
    assert "REPLACE_WITH_PRIVATE_32_PLUS_CHARACTER_ENCRYPTION_KEY" in text(ENV_EXAMPLE)


def test_validate_rejects_shell_syntax_unexpected_keys_and_bad_modes(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    env_file.write_text(env_file.read_text(encoding="utf-8") + "MALICIOUS=$(id)\n", encoding="utf-8")
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert "shell syntax" in result.stderr or "unexpected env key" in result.stderr
    assert KEY not in result.stderr + result.stdout

    env_file = write_env(tmp_path / "mode-case")
    env_file.chmod(0o644)
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert "owner-only" in result.stderr


def test_validate_rejects_short_key_overlap_and_directory_modes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    env_file = write_env(tmp_path, data_dir=data, backup_dir=data, key="short")
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert "at least 32" in result.stderr
    assert "short" not in result.stderr + result.stdout

    other = tmp_path / "other"
    env_file = write_env(tmp_path / "bad-dir-mode", backup_dir=other)
    other.chmod(0o755)
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert "mode 700" in result.stderr


def test_backup_creates_manifest_sidecar_and_preserves_stopped_service(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    data_dir = tmp_path / "data"
    make_sqlite(data_dir / "database.sqlite")
    env = fake_docker(tmp_path, running=False)
    archive = create_backup_archive(DEPLOY / "backup.sh", env_file, env)
    assert archive.is_file()
    assert archive.with_suffix(archive.suffix + ".sha256").is_file()
    with tarfile.open(archive, "r:gz") as tf:
        names = set(tf.getnames())
        assert names == {"database.sqlite", "manifest.sha256"}
        manifest = tf.extractfile("manifest.sha256").read().decode()
    assert f"image={IMAGE}" in manifest
    assert "key_fingerprint_sha256=" in manifest
    assert KEY not in manifest
    log = Path(env["DOCKER_FAKE_LOG"]).read_text(encoding="utf-8")
    assert " stop " not in f" {log} "
    assert " up " not in f" {log} "


def test_restore_rejects_malicious_archive_entries(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    make_sqlite(tmp_path / "data" / "database.sqlite")
    archive = tmp_path / "backups" / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        db = tmp_path / "data" / "database.sqlite"
        tf.add(db, arcname="database.sqlite")
        tf.add(db, arcname="../escape.sqlite")
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{subprocess.check_output(['sha256sum', str(archive)], text=True).split()[0]}  {archive.name}\n",
        encoding="utf-8",
    )
    result = run([str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)], env=fake_docker(tmp_path))
    assert result.returncode != 0
    assert "unsafe archive path" in result.stderr or "unexpected archive entry" in result.stderr


def test_restore_rejects_wrong_key_fingerprint_and_corruption(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    make_sqlite(tmp_path / "data" / "database.sqlite")
    env = fake_docker(tmp_path, running=False)
    archive = create_backup_archive(DEPLOY / "backup.sh", env_file, env)

    wrong_env = write_env(tmp_path / "wrong-key", key="different_synthetic_test_key_32_chars")
    result = run([str(DEPLOY / "restore.sh"), "--env-file", str(wrong_env), "--archive", str(archive)], env=env)
    assert result.returncode != 0
    assert "fingerprint mismatch" in result.stderr
    assert "different_synthetic" not in result.stderr + result.stdout

    corrupt = tmp_path / "backups" / "corrupt.tar.gz"
    staging = tmp_path / "corrupt-stage"
    staging.mkdir()
    shutil.copy2(tmp_path / "data" / "database.sqlite", staging / "database.sqlite")
    (staging / "database.sqlite").write_bytes(b"not sqlite")
    (staging / "manifest.sha256").write_text(
        "\n".join(
            [
                "format=n8n-sqlite-backup-v1",
                f"image={IMAGE}",
                "compose_sha256=x",
                "env_config_sha256=x",
                f"key_fingerprint_sha256={hashlib.sha256(KEY.encode()).hexdigest()}",
                f"database.sqlite_sha256={subprocess.check_output(['sha256sum', str(staging / 'database.sqlite')], text=True).split()[0]}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with tarfile.open(corrupt, "w:gz") as tf:
        tf.add(staging / "database.sqlite", arcname="database.sqlite")
        tf.add(staging / "manifest.sha256", arcname="manifest.sha256")
    corrupt.with_suffix(corrupt.suffix + ".sha256").write_text(
        f"{subprocess.check_output(['sha256sum', str(corrupt)], text=True).split()[0]}  {corrupt.name}\n",
        encoding="utf-8",
    )
    result = run([str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(corrupt)], env=env)
    assert result.returncode != 0
    assert "integrity_check" in result.stderr


def test_restore_stages_uid_gid_mode_and_preserves_running_state(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    data_dir = tmp_path / "data"
    make_sqlite(data_dir / "database.sqlite", "before")
    env = fake_docker(tmp_path, running=True)
    archive = create_backup_archive(DEPLOY / "backup.sh", env_file, env)
    make_sqlite(data_dir / "database.sqlite", "after")
    result = run([str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)], env=env)
    assert result.returncode == 0, result.stderr
    st = (data_dir / "database.sqlite").stat()
    assert (st.st_uid, st.st_gid, st.st_mode & 0o777) == (1000, 1000, 0o600)
    log = Path(env["DOCKER_FAKE_LOG"]).read_text(encoding="utf-8")
    assert "stop n8n" in log
    assert "up -d n8n" in log


def test_restore_rolls_back_current_database_when_startup_fails(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    data_dir = tmp_path / "data"
    make_sqlite(data_dir / "database.sqlite", "backup")
    env = fake_docker(tmp_path, running=True)
    archive = create_backup_archive(DEPLOY / "backup.sh", env_file, env)
    make_sqlite(data_dir / "database.sqlite", "current")
    failing_env = fake_docker(tmp_path / "fail", running=True, fail_up=True)
    result = run([str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)], env=failing_env)
    assert result.returncode != 0
    conn = sqlite3.connect(data_dir / "database.sqlite")
    assert conn.execute("select value from marker").fetchone()[0] == "current"
    conn.close()


def test_rollback_is_validated_operation_not_thin_wrapper(tmp_path: Path) -> None:
    rollback = text(DEPLOY / "rollback.sh")
    assert "restore.sh" in rollback
    assert "--dry-run" in rollback
    env_file = write_env(tmp_path)
    make_sqlite(tmp_path / "data" / "database.sqlite")
    result = run([str(DEPLOY / "rollback.sh"), "--dry-run", "--env-file", str(env_file), "--archive", str(tmp_path / "backups" / "x.tar.gz")])
    assert result.returncode == 0
    assert "rollback archive validation" in result.stdout


def test_shell_syntax_checks_pass() -> None:
    for script in SCRIPTS:
        subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)


def test_git_diff_check_passes() -> None:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, env=env, check=True)
