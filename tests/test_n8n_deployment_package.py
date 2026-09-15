from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
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
NODE_EXCLUSIONS = [
    "n8n-nodes-base.code",
    "n8n-nodes-base.executeCommand",
    "n8n-nodes-base.ssh",
    "n8n-nodes-base.localFileTrigger",
    "n8n-nodes-base.readWriteFile",
    "n8n-nodes-base.readBinaryFile",
    "n8n-nodes-base.readBinaryFiles",
    "n8n-nodes-base.writeBinaryFile",
]


def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def own_1000(path: Path) -> None:
    if os.geteuid() == 0:
        os.chown(path, 1000, 1000)
    else:
        assert path.stat().st_uid == 1000 and path.stat().st_gid == 1000


def make_sqlite(path: Path, value: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("create table if not exists marker(value text)")
    conn.execute("delete from marker")
    conn.execute("insert into marker(value) values (?)", (value,))
    conn.commit()
    conn.close()
    path.chmod(0o600)


def marker(path: Path) -> str:
    conn = sqlite3.connect(path)
    value = conn.execute("select value from marker").fetchone()[0]
    conn.close()
    return value


def write_env(
    tmp_path: Path,
    data_dir: Path | None = None,
    backup_dir: Path | None = None,
    key: str = KEY,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    data_dir = data_dir or tmp_path / "data"
    backup_dir = backup_dir or tmp_path / "backups"
    data_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for directory in (data_dir, backup_dir):
        directory.chmod(0o700)
        own_1000(directory)
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


def fake_docker(
    tmp_path: Path,
    *,
    running: bool = False,
    inspect_sequence: str = "healthy",
    fail_first_up: bool = False,
) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "docker-state"
    state.write_text("running" if running else "stopped", encoding="utf-8")
    log = tmp_path / "docker.log"
    inspect_count = tmp_path / "inspect-count"
    inspect_count.write_text("0", encoding="utf-8")
    up_count = tmp_path / "up-count"
    up_count.write_text("0", encoding="utf-8")
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_FAKE_LOG"
if [ "$1" = "inspect" ]; then
  count=$(( $(cat "$DOCKER_FAKE_INSPECT_COUNT") + 1 ))
  printf '%s' "$count" > "$DOCKER_FAKE_INSPECT_COUNT"
  IFS=',' read -r -a values <<< "$DOCKER_FAKE_INSPECT_SEQUENCE"
  index=$((count - 1))
  if [ "$index" -ge "${#values[@]}" ]; then index=$((${#values[@]} - 1)); fi
  printf '%s\\n' "${values[$index]}"
  exit 0
fi
[ "$1" = "compose" ] || exit 64
shift
cmd=""
rest=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file|-f) shift 2 ;;
    config) exit 0 ;;
    ps|stop|up) cmd="$1"; shift; rest="$*"; break ;;
    *) shift ;;
  esac
done
case "$cmd" in
  ps)
    if [[ " $rest " == *" --status running "* ]]; then
      [ "$(cat "$DOCKER_FAKE_STATE")" = "running" ] && printf 'fake-n8n\\n'
    elif [ "$(cat "$DOCKER_FAKE_STATE")" != "stopped" ]; then
      printf 'fake-n8n\\n'
    fi
    ;;
  stop)
    printf 'stopped' > "$DOCKER_FAKE_STATE"
    ;;
  up)
    count=$(( $(cat "$DOCKER_FAKE_UP_COUNT") + 1 ))
    printf '%s' "$count" > "$DOCKER_FAKE_UP_COUNT"
    if [ "${DOCKER_FAKE_FAIL_FIRST_UP:-0}" = "1" ] && [ "$count" -eq 1 ]; then exit 42; fi
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
    env["DOCKER_FAKE_INSPECT_COUNT"] = str(inspect_count)
    env["DOCKER_FAKE_UP_COUNT"] = str(up_count)
    env["DOCKER_FAKE_INSPECT_SEQUENCE"] = inspect_sequence
    env["N8N_MAINTENANCE_HEALTH_TIMEOUT_SECONDS"] = "2"
    env["N8N_MAINTENANCE_HEALTH_POLL_SECONDS"] = "1"
    if fail_first_up:
        env["DOCKER_FAKE_FAIL_FIRST_UP"] = "1"
    return env


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )


def create_backup_archive(env_file: Path, env: dict[str, str]) -> Path:
    result = run([str(DEPLOY / "backup.sh"), "--env-file", str(env_file)], env=env)
    assert result.returncode == 0, result.stderr
    match = re.search(r"created (.+)", result.stdout)
    assert match, result.stdout
    return Path(match.group(1))


def rewrite_archive_manifest(archive: Path, transform) -> Path:
    work = archive.parent / f"rewrite-{archive.stem}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(work, filter="data")
    manifest = work / "manifest.sha256"
    manifest.write_text(transform(manifest.read_text(encoding="utf-8")), encoding="utf-8")
    replacement = archive.with_name(f"changed-{archive.name}")
    with tarfile.open(replacement, "w:gz") as tf:
        for name in sorted(path.name for path in work.iterdir()):
            tf.add(work / name, arcname=name)
    sidecar = Path(f"{replacement}.sha256")
    sidecar.write_text(
        f"{hashlib.sha256(replacement.read_bytes()).hexdigest()}  {replacement.name}\n",
        encoding="utf-8",
    )
    sidecar.chmod(0o600)
    return replacement


def test_package_files_and_shell_syntax() -> None:
    for path in [COMPOSE_PATH, ENV_EXAMPLE, DOC_PATH, *(DEPLOY / script for script in SCRIPTS)]:
        assert path.is_file()
    for script in SCRIPTS:
        subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)


def test_compose_has_exact_internal_runner_and_security_contract() -> None:
    service = compose()["services"]["n8n"]
    environment = service["environment"]
    assert service["image"] == IMAGE
    assert service["ports"] == ["127.0.0.1:${N8N_EDITOR_PORT:-5678}:5678"]
    assert environment["DB_TYPE"] == "sqlite"
    assert environment["DB_SQLITE_DATABASE"] == "/home/node/.n8n/database.sqlite"
    assert environment["N8N_RUNNERS_MODE"] == "internal"
    assert environment["N8N_RUNNERS_INSECURE_MODE"] == "false"
    assert environment["N8N_RUNNERS_BROKER_LISTEN_ADDRESS"] == "127.0.0.1"
    assert environment["N8N_RUNNERS_MAX_CONCURRENCY"] == "1"
    assert environment["N8N_RUNNERS_TASK_TIMEOUT"] == "60"
    assert "N8N_RUNNERS_ENABLED" not in environment
    assert "N8N_RUNNERS_AUTH_TOKEN" not in environment
    assert environment["N8N_BLOCK_ENV_ACCESS_IN_NODE"] == "true"
    assert environment["N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES"] == "true"
    assert environment["N8N_RESTRICT_FILE_ACCESS_TO"] == "/home/node/.n8n-files-disabled"
    assert environment["N8N_PYTHON_ENABLED"] == "false"
    assert yaml.safe_load(environment["NODES_EXCLUDE"]) == NODE_EXCLUSIONS
    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]
    assert "/var/run/docker.sock" not in yaml.safe_dump(service)


def test_docs_record_official_runner_contract_and_boundaries() -> None:
    combined = "\n".join(text(path) for path in [COMPOSE_PATH, ENV_EXAMPLE, DOC_PATH])
    assert IMAGE in combined
    assert "SQLite-to-PostgreSQL migration" in combined
    assert "does not add PostgreSQL" in combined
    assert "SSH tunnel" in combined and "Tailscale SSH" in combined
    assert "private-memory SQLite" in combined
    assert "N8N_RUNNERS_MODE" in combined
    assert "N8N_RUNNERS_ENABLED" in combined and "deprecated" in combined
    assert "REPLACE_WITH_PRIVATE_32_PLUS_CHARACTER_ENCRYPTION_KEY" in text(ENV_EXAMPLE)


def test_validate_rejects_shell_syntax_duplicate_keys_and_bad_modes(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / "syntax")
    env_file.write_text(env_file.read_text() + "MALICIOUS=$(id)\n", encoding="utf-8")
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert KEY not in result.stderr + result.stdout

    env_file = write_env(tmp_path / "duplicate")
    env_file.write_text(env_file.read_text() + "N8N_HOST=duplicate\n", encoding="utf-8")
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert "duplicate env key" in result.stderr

    env_file = write_env(tmp_path / "mode")
    env_file.chmod(0o644)
    result = run([str(DEPLOY / "validate.sh"), "--env-file", str(env_file)])
    assert result.returncode != 0
    assert "owner-only" in result.stderr


def test_backup_preserves_stopped_and_running_service_states(tmp_path: Path) -> None:
    stopped_env_file = write_env(tmp_path / "stopped")
    make_sqlite(tmp_path / "stopped" / "data" / "database.sqlite")
    stopped_env = fake_docker(tmp_path / "stopped-docker", running=False)
    archive = create_backup_archive(stopped_env_file, stopped_env)
    assert Path(stopped_env["DOCKER_FAKE_STATE"]).read_text() == "stopped"
    assert "stop n8n" not in Path(stopped_env["DOCKER_FAKE_LOG"]).read_text()
    assert archive.stat().st_mode & 0o077 == 0
    assert Path(f"{archive}.sha256").stat().st_mode & 0o077 == 0

    running_env_file = write_env(tmp_path / "running")
    make_sqlite(tmp_path / "running" / "data" / "database.sqlite")
    running_env = fake_docker(tmp_path / "running-docker", running=True)
    create_backup_archive(running_env_file, running_env)
    assert Path(running_env["DOCKER_FAKE_STATE"]).read_text() == "running"
    log = Path(running_env["DOCKER_FAKE_LOG"]).read_text()
    assert "stop n8n" in log and "up -d n8n" in log and "inspect" in log


def test_backup_failure_after_stop_recovers_running_state_and_publishes_nothing(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    (tmp_path / "data" / "database.sqlite").write_bytes(b"not sqlite")
    env = fake_docker(tmp_path / "docker", running=True)
    result = run([str(DEPLOY / "backup.sh"), "--env-file", str(env_file)], env=env)
    assert result.returncode != 0
    assert Path(env["DOCKER_FAKE_STATE"]).read_text() == "running"
    assert not list((tmp_path / "backups").glob("n8n-sqlite-*.tar.gz"))
    assert not list((tmp_path / "backups").glob(".archive.*"))


def test_restore_rejects_duplicate_archive_entries_and_unsafe_sidecar(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    db = tmp_path / "data" / "database.sqlite"
    make_sqlite(db)
    archive = tmp_path / "backups" / "duplicate.tar.gz"
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text("format=x\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(db, arcname="database.sqlite")
        tf.add(db, arcname="database.sqlite")
        tf.add(manifest, arcname="manifest.sha256")
    sidecar = Path(f"{archive}.sha256")
    sidecar.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    sidecar.chmod(0o600)
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)],
        env=fake_docker(tmp_path / "docker"),
    )
    assert result.returncode != 0
    assert "duplicate archive entry" in result.stderr

    safe_env = fake_docker(tmp_path / "safe-docker")
    valid = create_backup_archive(env_file, safe_env)
    valid_sidecar = Path(f"{valid}.sha256")
    valid_sidecar.chmod(0o644)
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(valid)],
        env=safe_env,
    )
    assert result.returncode != 0
    assert "sidecar must be owner-only" in result.stderr


def test_restore_rejects_duplicate_malformed_manifest_and_config_hash_mismatch(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    make_sqlite(tmp_path / "data" / "database.sqlite", "backup")
    env = fake_docker(tmp_path / "docker")
    archive = create_backup_archive(env_file, env)

    duplicate = rewrite_archive_manifest(archive, lambda value: value + value.splitlines()[0] + "\n")
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(duplicate)],
        env=env,
    )
    assert result.returncode != 0
    assert "duplicate manifest key" in result.stderr

    malformed = rewrite_archive_manifest(
        archive,
        lambda value: value.replace(
            re.search(r"compose_sha256=[0-9a-f]{64}", value).group(),
            "compose_sha256=x",
        ),
    )
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(malformed)],
        env=env,
    )
    assert result.returncode != 0
    assert "manifest checksum is malformed" in result.stderr

    env_file.write_text(
        env_file.read_text().replace("N8N_EDITOR_PORT=5678", "N8N_EDITOR_PORT=5679"),
        encoding="utf-8",
    )
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)],
        env=env,
    )
    assert result.returncode != 0
    assert "environment config hash mismatch" in result.stderr


def test_restore_failure_immediately_after_stop_restores_service_state(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    db = tmp_path / "data" / "database.sqlite"
    make_sqlite(db, "backup")
    archive = create_backup_archive(env_file, fake_docker(tmp_path / "backup-docker"))
    target = tmp_path / "current.sqlite"
    make_sqlite(target, "current")
    db.unlink()
    db.symlink_to(target)
    (tmp_path / "data" / "database.sqlite.new").write_text("stale")
    env = fake_docker(tmp_path / "restore-docker", running=True)
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)],
        env=env,
    )
    assert result.returncode != 0
    assert Path(env["DOCKER_FAKE_STATE"]).read_text() == "running"
    assert not (tmp_path / "data" / "database.sqlite.new").exists()


def test_restore_unhealthy_start_rolls_back_database_and_recovers_service(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    db = tmp_path / "data" / "database.sqlite"
    make_sqlite(db, "backup")
    archive = create_backup_archive(env_file, fake_docker(tmp_path / "backup-docker"))
    make_sqlite(db, "current")
    env = fake_docker(tmp_path / "restore-docker", running=True, inspect_sequence="unhealthy,healthy")
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(env_file), "--archive", str(archive)],
        env=env,
    )
    assert result.returncode != 0
    assert marker(db) == "current"
    assert Path(env["DOCKER_FAKE_STATE"]).read_text() == "running"
    assert not list((tmp_path / "data").glob("*.new"))


def test_restore_success_and_stopped_state_are_deterministic(tmp_path: Path) -> None:
    running_env_file = write_env(tmp_path / "running")
    running_db = tmp_path / "running" / "data" / "database.sqlite"
    make_sqlite(running_db, "backup")
    archive = create_backup_archive(running_env_file, fake_docker(tmp_path / "running-backup"))
    make_sqlite(running_db, "current")
    running_env = fake_docker(tmp_path / "running-restore", running=True)
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(running_env_file), "--archive", str(archive)],
        env=running_env,
    )
    assert result.returncode == 0, result.stderr
    assert marker(running_db) == "backup"
    assert Path(running_env["DOCKER_FAKE_STATE"]).read_text() == "running"

    stopped_env_file = write_env(tmp_path / "stopped")
    stopped_db = tmp_path / "stopped" / "data" / "database.sqlite"
    make_sqlite(stopped_db, "backup")
    stopped_archive = create_backup_archive(stopped_env_file, fake_docker(tmp_path / "stopped-backup"))
    make_sqlite(stopped_db, "current")
    stopped_env = fake_docker(tmp_path / "stopped-restore", running=False)
    result = run(
        [str(DEPLOY / "restore.sh"), "--env-file", str(stopped_env_file), "--archive", str(stopped_archive)],
        env=stopped_env,
    )
    assert result.returncode == 0, result.stderr
    assert marker(stopped_db) == "backup"
    assert Path(stopped_env["DOCKER_FAKE_STATE"]).read_text() == "stopped"
    assert "up -d n8n" not in Path(stopped_env["DOCKER_FAKE_LOG"]).read_text()


def test_rollback_reuses_all_restore_validation_in_dry_run(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    make_sqlite(tmp_path / "data" / "database.sqlite")
    env = fake_docker(tmp_path / "docker")
    archive = create_backup_archive(env_file, env)
    result = run(
        [
            str(DEPLOY / "rollback.sh"),
            "--dry-run",
            "--env-file",
            str(env_file),
            "--archive",
            str(archive),
        ],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "archive, manifest, config hashes" in result.stdout


def test_git_diff_check_passes() -> None:
    if (ROOT / ".git").exists():
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        subprocess.run(["git", "diff", "--check"], cwd=ROOT, env=env, check=True)
