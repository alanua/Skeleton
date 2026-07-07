from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "n8n"
COMPOSE_PATH = DEPLOY / "compose.yaml"
ENV_EXAMPLE = DEPLOY / "env.example"
DOC_PATH = ROOT / "docs" / "N8N_SELF_HOSTED.md"
SCRIPTS = ["backup.sh", "restore.sh", "validate.sh", "rollback.sh"]


def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_package_files_exist() -> None:
    for path in [
        COMPOSE_PATH,
        ENV_EXAMPLE,
        DOC_PATH,
        *(DEPLOY / script for script in SCRIPTS),
    ]:
        assert path.is_file()


def test_compose_has_exactly_one_application_service_and_no_postgres_or_redis() -> None:
    services = compose()["services"]
    assert set(services) == {"n8n"}
    serialized = yaml.safe_dump(compose()).lower()
    assert "postgres" not in serialized
    assert "redis" not in serialized


def test_image_is_exact_version_and_digest_pinned() -> None:
    image = compose()["services"]["n8n"]["image"]
    assert re.fullmatch(r"n8nio/n8n:1\.97\.1@sha256:[0-9a-f]{64}", image)
    assert image in text(DOC_PATH)


def test_loopback_editor_bind_and_no_public_webhook_default() -> None:
    service = compose()["services"]["n8n"]
    assert service["ports"] == ["127.0.0.1:${N8N_EDITOR_PORT:-5678}:5678"]
    rendered = text(COMPOSE_PATH)
    assert "0.0.0.0" not in rendered
    assert "WEBHOOK_URL" not in rendered
    assert "N8N_EDITOR_BASE_URL" not in rendered


def test_sqlite_database_settings_are_dedicated_and_wal_enabled() -> None:
    env = compose()["services"]["n8n"]["environment"]
    assert env["DB_TYPE"] == "sqlite"
    assert env["DB_SQLITE_DATABASE"] == "/home/node/.n8n/database.sqlite"
    assert int(env["DB_SQLITE_POOL_SIZE"]) > 0
    assert env["N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS"] == "true"
    assert env["GENERIC_TIMEZONE"] == "Europe/Berlin"
    assert env["TZ"] == "Europe/Berlin"


def test_persistent_owner_only_mapping_exists() -> None:
    service = compose()["services"]["n8n"]
    assert service["user"] == "1000:1000"
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "${N8N_DATA_DIR:?set an owner-only host directory for n8n data}",
            "target": "/home/node/.n8n",
        }
    ]
    assert "mode 700" in text(ENV_EXAMPLE)


def test_no_dangerous_container_privileges_or_mounts() -> None:
    service = compose()["services"]["n8n"]
    assert service.get("privileged") is None
    assert service.get("network_mode") is None
    assert service.get("pid") is None
    serialized = yaml.safe_dump(service)
    assert "/var/run/docker.sock" not in serialized
    assert "/:" not in serialized
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]


def test_health_restart_and_resource_bounds() -> None:
    service = compose()["services"]["n8n"]
    assert service["restart"] == "unless-stopped"
    assert "healthcheck" in service
    assert service["mem_limit"] == "768m"
    assert service["cpus"] == "1.00"
    assert service["pids_limit"] == 256
    assert service["deploy"]["resources"]["limits"] == {
        "cpus": "1.00",
        "memory": "768M",
    }


def test_no_secret_literals_or_private_memory_paths() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [COMPOSE_PATH, ENV_EXAMPLE, DOC_PATH, *(DEPLOY / s for s in SCRIPTS)]
    )
    assert "BEGIN OPENSSH PRIVATE KEY" not in combined
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", combined.replace("127.0.0.1", ""))
    assert "private_memory" not in combined
    assert "private-memory SQLite" in combined
    assert "Skeleton/private memory" not in combined
    assert "REPLACE_WITH_PRIVATE_32_PLUS_CHARACTER_ENCRYPTION_KEY" in text(ENV_EXAMPLE)


def test_no_community_nodes_external_credentials_or_active_workflows() -> None:
    rendered = text(COMPOSE_PATH)
    assert "N8N_COMMUNITY_PACKAGES_ENABLED: \"false\"" in rendered
    assert "N8N_REINSTALL_MISSING_PACKAGES: \"false\"" in rendered
    for forbidden in ["credentials.json", "workflow.json", "active workflows"]:
        assert forbidden not in rendered


def test_conservative_execution_data_pruning() -> None:
    env = compose()["services"]["n8n"]["environment"]
    assert env["EXECUTIONS_DATA_SAVE_ON_SUCCESS"] == "none"
    assert env["EXECUTIONS_DATA_SAVE_ON_ERROR"] == "all"
    assert env["EXECUTIONS_DATA_PRUNE"] == "true"
    assert int(env["EXECUTIONS_DATA_MAX_AGE"]) <= 168
    assert int(env["EXECUTIONS_DATA_PRUNE_MAX_COUNT"]) <= 10000


def test_scripts_reject_descriptors_and_unsafe_paths(tmp_path: Path) -> None:
    env_file = tmp_path / "bad.env"
    env_file.write_text(
        "\n".join(
            [
                "N8N_ENCRYPTION_KEY=REPLACE_WITH_PRIVATE_32_PLUS_CHARACTER_ENCRYPTION_KEY",
                "N8N_DATA_DIR=/tmp",
                "N8N_BACKUP_DIR=/tmp",
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(DEPLOY / "validate.sh"), "--dry-run", "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "descriptor" in result.stderr


def test_backup_restore_and_rollback_dry_run_use_synthetic_values_only(tmp_path: Path) -> None:
    env_file = tmp_path / "synthetic.env"
    data_dir = tmp_path / "synthetic-n8n-data"
    backup_dir = tmp_path / "synthetic-n8n-backups"
    env_file.write_text(
        "\n".join(
            [
                "N8N_ENCRYPTION_KEY=synthetic_test_key_32_chars_long",
                f"N8N_DATA_DIR={data_dir}",
                f"N8N_BACKUP_DIR={backup_dir}",
            ]
        ),
        encoding="utf-8",
    )

    commands = [
        [str(DEPLOY / "validate.sh"), "--dry-run", "--env-file", str(env_file)],
        [str(DEPLOY / "backup.sh"), "--dry-run", "--env-file", str(env_file)],
        [
            str(DEPLOY / "restore.sh"),
            "--dry-run",
            "--env-file",
            str(env_file),
            "--archive",
            str(backup_dir / "synthetic.tar.gz"),
        ],
        [
            str(DEPLOY / "rollback.sh"),
            "--dry-run",
            "--env-file",
            str(env_file),
            "--archive",
            str(backup_dir / "synthetic.tar.gz"),
        ],
    ]
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "dry-run" in result.stdout


def test_shell_syntax_checks_pass() -> None:
    for script in SCRIPTS:
        subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)


def test_compose_parses_with_pyyaml() -> None:
    parsed = compose()
    assert parsed["services"]["n8n"]["environment"]["DB_TYPE"] == "sqlite"


def test_documentation_covers_private_access_and_migration_triggers() -> None:
    doc = text(DOC_PATH)
    assert "SSH tunnel" in doc
    assert "Tailscale SSH" in doc
    assert "SQLite-to-PostgreSQL migration" in doc
    assert "does not add PostgreSQL" in doc


def test_git_diff_check_passes() -> None:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, env=env, check=True)
