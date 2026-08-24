#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 0 ]; then
  printf '%s\n' '{"status":"BLOCKED","reason":"argv_not_allowed"}' >&2
  exit 2
fi

if env | grep -q '^SKELETON_ESP_LAB_TEST_'; then
  if [ "${SKELETON_ESP_LAB_INSTALLER_TEST_MODE:-}" != "1" ] || [ -z "${PYTEST_CURRENT_TEST:-}" ]; then
    printf '%s\n' '{"status":"BLOCKED","reason":"unguarded_test_environment"}' >&2
    exit 2
  fi
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export SKELETON_ESP_LAB_INSTALLER_SCRIPT="$SCRIPT_DIR/$(basename -- "${BASH_SOURCE[0]}")"
PAYLOAD_FILE="$(mktemp -t skeleton-esp-lab-payload.XXXXXXXXXX)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE"

/usr/bin/python3 - "$PAYLOAD_FILE" <<'PY'
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PAYLOAD_SCHEMA = "skeleton.home_edge.esp_lab_stage1_payload.v1"
RESULT_SCHEMA = "skeleton.home_edge.esp_lab_stage1_activation_result.v1"
MANIFEST_SCHEMA = "skeleton.home_edge.esp_lab_stage1_manifest.v1"
EXPECTED_PATHS = ("core/__init__.py", "core/home_edge/esp_lab.py")
MAX_ENCODED = 230000
MAX_DECODED = 220000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_PAYLOAD_KEYS = {"port"}


class InstallerBlocked(RuntimeError):
    pass


def blocked(reason: str) -> None:
    print(json.dumps({"status": "BLOCKED", "reason": reason}, separators=(",", ":")), file=sys.stderr)
    raise SystemExit(1)


def lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def is_root_owned(st: os.stat_result) -> bool:
    return st.st_uid == 0 and st.st_gid == 0


def is_safe_existing_base(path: Path, *, production: bool) -> bool:
    st = lstat_optional(path)
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return False
    if st.st_mode & 0o022:
        return False
    if production and not is_root_owned(st):
        return False
    return True


def is_exact_wrapper(path: Path, expected: bytes, *, production: bool) -> bool:
    st = lstat_optional(path)
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    if stat.S_IMODE(st.st_mode) != 0o755:
        return False
    if production and not is_root_owned(st):
        return False
    return path.read_bytes() == expected


def canonical_manifest(source_sha: str, files: list[dict[str, str]]) -> bytes:
    data = {
        "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files],
        "schema": MANIFEST_SCHEMA,
        "source_sha": source_sha,
    }
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def validate_target(path: Path, source_sha: str, files: list[dict[str, str]], *, production: bool) -> bool:
    root_st = lstat_optional(path)
    if root_st is None or stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        return False
    if stat.S_IMODE(root_st.st_mode) != 0o555:
        return False
    if production and not is_root_owned(root_st):
        return False

    allowed_dirs = {path, path / "core", path / "core" / "home_edge"}
    allowed_files = {path / "manifest.json"} | {path / item["path"] for item in files}
    seen_dirs: set[Path] = set()
    seen_files: set[Path] = set()
    for current, dirs, filenames in os.walk(path):
        current_path = Path(current)
        current_st = os.lstat(current_path)
        if stat.S_ISLNK(current_st.st_mode) or not stat.S_ISDIR(current_st.st_mode):
            return False
        if current_path not in allowed_dirs or stat.S_IMODE(current_st.st_mode) != 0o555:
            return False
        if production and not is_root_owned(current_st):
            return False
        seen_dirs.add(current_path)
        for name in dirs:
            child = current_path / name
            child_st = os.lstat(child)
            if stat.S_ISLNK(child_st.st_mode) or child not in allowed_dirs:
                return False
        for name in filenames:
            child = current_path / name
            child_st = os.lstat(child)
            if stat.S_ISLNK(child_st.st_mode) or not stat.S_ISREG(child_st.st_mode):
                return False
            if child not in allowed_files or stat.S_IMODE(child_st.st_mode) != 0o444:
                return False
            if production and not is_root_owned(child_st):
                return False
            seen_files.add(child)
    if seen_dirs != allowed_dirs or seen_files != allowed_files:
        return False
    if (path / "manifest.json").read_bytes() != canonical_manifest(source_sha, files):
        return False
    for item in files:
        import hashlib

        if hashlib.sha256((path / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            return False
    return True


def read_payload() -> tuple[str, list[dict[str, str]], dict[str, bytes]]:
    raw = Path(sys.argv[1]).read_bytes()
    if len(raw) > MAX_ENCODED:
        blocked("payload_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        blocked("invalid_json")
    if not isinstance(payload, dict) or set(payload) != {"schema", "source_sha", "files"}:
        blocked("invalid_payload_keys")
    if any(key in FORBIDDEN_PAYLOAD_KEYS for key in payload):
        blocked("authority_field_rejected")
    if payload["schema"] != PAYLOAD_SCHEMA:
        blocked("invalid_schema")
    source_sha = payload["source_sha"]
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        blocked("invalid_source_sha")
    files = payload["files"]
    if not isinstance(files, list) or len(files) != 2:
        blocked("invalid_files")
    decoded: dict[str, bytes] = {}
    manifest_files: list[dict[str, str]] = []
    total = 0
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "base64"}:
            blocked("invalid_file_keys")
        if item["path"] != EXPECTED_PATHS[index]:
            blocked("invalid_file_order")
        sha256 = item["sha256"]
        encoded = item["base64"]
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            blocked("invalid_sha256")
        if not isinstance(encoded, str):
            blocked("invalid_base64")
        try:
            data = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error):
            blocked("invalid_base64")
        total += len(data)
        if total > MAX_DECODED:
            blocked("decoded_payload_too_large")
        import hashlib

        if hashlib.sha256(data).hexdigest() != sha256:
            blocked("hash_mismatch")
        decoded[item["path"]] = data
        manifest_files.append({"path": item["path"], "sha256": sha256})
    return source_sha, manifest_files, decoded


def require_test_root() -> Path | None:
    guarded = os.environ.get("SKELETON_ESP_LAB_INSTALLER_TEST_MODE") == "1" and bool(os.environ.get("PYTEST_CURRENT_TEST"))
    test_vars = [key for key in os.environ if key.startswith("SKELETON_ESP_LAB_TEST_")]
    if test_vars and not guarded:
        blocked("unguarded_test_environment")
    if not guarded:
        return None
    root_text = os.environ.get("SKELETON_ESP_LAB_TEST_ROOT", "")
    if not root_text:
        blocked("missing_test_root")
    root = Path(root_text)
    if not root.is_absolute():
        blocked("invalid_test_root")
    resolved = root.resolve(strict=True)
    tmp = Path("/tmp").resolve(strict=True)
    if resolved == tmp or tmp not in resolved.parents:
        blocked("invalid_test_root")
    st = os.lstat(resolved)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o022:
        blocked("invalid_test_root")
    return resolved


def read_os_release(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def preflight(test_root: Path | None) -> dict[str, Path | bool]:
    production = test_root is None
    prefix = Path("/") if production else test_root
    runtime_base = prefix / "opt" / "skeleton" / "esp-lab"
    wrapper = prefix / "usr" / "local" / "bin" / "skeleton-esp-lab"
    os_release = prefix / "etc" / "os-release"
    sysfs = prefix / "sys" / "class" / "tty"
    python_path = prefix / "usr" / "bin" / "python3"
    apt_path = prefix / "usr" / "bin" / "apt-get"
    if production and os.geteuid() != 0:
        blocked("production_requires_root")
    if os.uname().nodename != "home-edge-01" and production:
        blocked("wrong_hostname")
    try:
        os_values = read_os_release(os_release)
    except OSError:
        blocked("missing_os_release")
    if os_values.get("ID") != "debian" or os_values.get("VERSION_ID") != "13":
        blocked("wrong_os")
    if not os.access(python_path, os.X_OK):
        blocked("missing_python")
    return {
        "production": production,
        "runtime_base": runtime_base,
        "wrapper": wrapper,
        "sysfs": sysfs,
        "python": python_path,
        "apt": apt_path,
    }


def wrapper_bytes(runtime_target: Path, python_path: Path) -> bytes:
    return (
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "cd /tmp\n"
        f'PYTHONPATH={runtime_target} exec {python_path} -m core.home_edge.esp_lab "$@"\n'
    ).encode("utf-8")


def snapshot_regular(path: Path) -> dict[str, Any]:
    st = os.lstat(path)
    return {
        "data": path.read_bytes(),
        "mode": stat.S_IMODE(st.st_mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
    }


def restore_regular(path: Path, snap: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".skeleton-esp-lab-restore.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(snap["data"])
        os.chmod(tmp, snap["mode"])
        try:
            os.chown(tmp, snap["uid"], snap["gid"])
        except PermissionError:
            pass
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def replace_regular(path: Path, data: bytes, mode: int, *, production: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".skeleton-esp-lab-wrapper.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        if production:
            os.chown(tmp, 0, 0)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def make_tree(stage: Path, source_sha: str, files: list[dict[str, str]], decoded: dict[str, bytes], *, production: bool) -> None:
    target = stage / source_sha
    for directory in (target, target / "core", target / "core" / "home_edge"):
        directory.mkdir(parents=True, exist_ok=True)
    for rel, data in decoded.items():
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        os.chmod(out, 0o444)
    manifest = target / "manifest.json"
    manifest.write_bytes(canonical_manifest(source_sha, files))
    os.chmod(manifest, 0o444)
    if production:
        for current, dirs, filenames in os.walk(target):
            os.chown(current, 0, 0)
            for name in dirs:
                os.chown(Path(current) / name, 0, 0)
            for name in filenames:
                os.chown(Path(current) / name, 0, 0)


def freeze_tree(target: Path) -> None:
    for directory in (target / "core" / "home_edge", target / "core", target):
        os.chmod(directory, 0o555)


def remove_created_target(target: Path, *, production: bool) -> None:
    if not production and target.exists() and not target.is_symlink():
        for directory in (target, target / "core", target / "core" / "home_edge"):
            if directory.exists() and not directory.is_symlink():
                os.chmod(directory, 0o755)
    shutil.rmtree(target, ignore_errors=True)


def command_exists(name: str, prefix: Path, production: bool) -> bool:
    if production:
        return shutil.which(name) is not None
    paths = [prefix / "usr" / "local" / "bin" / name, prefix / "usr" / "bin" / name]
    return any(path.exists() and os.access(path, os.X_OK) for path in paths)


def install_dependency(apt_path: Path, prefix: Path, production: bool) -> bool:
    if command_exists("esptool", prefix, production):
        return False
    if production:
        subprocess.run(["/usr/bin/apt-get", "update"], check=True)
        subprocess.run(
            ["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "esptool"],
            check=True,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
    else:
        subprocess.run([str(apt_path), "update"], check=True)
        subprocess.run(
            [str(apt_path), "install", "-y", "--no-install-recommends", "esptool"],
            check=True,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
    return True


def remove_operation_dependency(prefix: Path, production: bool) -> None:
    if production:
        tool = shutil.which("esptool")
        if tool:
            Path(tool).unlink(missing_ok=True)
    else:
        for path in (prefix / "usr" / "local" / "bin" / "esptool", prefix / "usr" / "bin" / "esptool"):
            path.unlink(missing_ok=True)


def run_canary(wrapper: Path, sysfs: Path) -> int:
    canary_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""}
    if os.environ.get("SKELETON_ESP_LAB_INSTALLER_TEST_MODE") == "1":
        for key in ("ESP_LAB_ARGV_LOG", "ESP_LAB_CANARY_FAIL"):
            if key in os.environ:
                canary_env[key] = os.environ[key]
    completed = subprocess.run(
        [str(wrapper), "discover", "--sysfs-root", str(sysfs)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        cwd="/tmp",
        env=canary_env,
    )
    if completed.returncode != 0:
        raise InstallerBlocked("canary_failed")
    try:
        data = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallerBlocked("canary_invalid_json") from exc
    if not isinstance(data, list):
        raise InstallerBlocked("canary_invalid_json")
    return len(data)


def main() -> None:
    test_root = require_test_root()
    source_sha, files, decoded = read_payload()
    paths = preflight(test_root)
    production = bool(paths["production"])
    prefix = Path("/") if production else test_root
    runtime_base = Path(paths["runtime_base"])
    target = runtime_base / source_sha
    wrapper = Path(paths["wrapper"])
    expected_wrapper = wrapper_bytes(target, Path(paths["python"]))

    temp_root = Path(tempfile.mkdtemp(prefix="skeleton-esp-lab-installer.", dir="/tmp"))
    wrapper_snap: dict[str, Any] | None = None
    target_created = False
    base_created = False
    base_mode_before: int | None = None
    dependency_installed = False
    exact_target_at_start = False
    exact_wrapper_at_start = False
    try:
        base_st = lstat_optional(runtime_base)
        if base_st is not None:
            if not is_safe_existing_base(runtime_base, production=production):
                blocked("runtime_base_mismatch")
            base_mode_before = stat.S_IMODE(base_st.st_mode)

        target_st = lstat_optional(target)
        if target_st is not None:
            if not validate_target(target, source_sha, files, production=production):
                blocked("target_mismatch")
            exact_target_at_start = True

        wrapper_st = lstat_optional(wrapper)
        if wrapper_st is not None:
            if stat.S_ISLNK(wrapper_st.st_mode) or not stat.S_ISREG(wrapper_st.st_mode):
                blocked("wrapper_mismatch")
            exact_wrapper_at_start = is_exact_wrapper(wrapper, expected_wrapper, production=production)
            if not exact_wrapper_at_start:
                wrapper_snap = snapshot_regular(wrapper)

        dependency_installed = install_dependency(Path(paths["apt"]), prefix, production)

        if base_st is None:
            runtime_base.mkdir(parents=True, exist_ok=True)
            base_created = True
        if not production:
            os.chmod(runtime_base, 0o755)

        if not exact_target_at_start:
            stage_parent = runtime_base.parent
            stage = Path(tempfile.mkdtemp(prefix=".esp-lab-stage.", dir=str(stage_parent)))
            try:
                make_tree(stage, source_sha, files, decoded, production=production)
                os.replace(stage / source_sha, target)
                freeze_tree(target)
                target_created = True
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        if production:
            os.chown(runtime_base, 0, 0)
        os.chmod(runtime_base, 0o555)

        if not exact_wrapper_at_start:
            replace_regular(wrapper, expected_wrapper, 0o755, production=production)

        count = run_canary(wrapper, Path(paths["sysfs"]))
        result = {
            "schema": RESULT_SCHEMA,
            "runtime_state": "READY",
            "source_sha": source_sha,
            "candidate_count": count,
            "device_canary": "awaiting_physical_device" if count == 0 else "serial_candidates_present",
            "dependency_installed_by_operation": dependency_installed,
            "idempotent_reuse": exact_target_at_start and exact_wrapper_at_start,
        }
        print(json.dumps(result, separators=(",", ":")), flush=True)
    except InstallerBlocked as exc:
        if wrapper_snap is not None:
            restore_regular(wrapper, wrapper_snap)
        if target_created:
            if not production and runtime_base.exists() and not runtime_base.is_symlink():
                os.chmod(runtime_base, 0o755)
            remove_created_target(target, production=production)
        if dependency_installed:
            remove_operation_dependency(prefix, production)
        if not production and base_mode_before is not None and runtime_base.exists() and not runtime_base.is_symlink():
            os.chmod(runtime_base, base_mode_before)
        if base_created:
            try:
                runtime_base.rmdir()
            except OSError:
                pass
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        if wrapper_snap is not None:
            restore_regular(wrapper, wrapper_snap)
        if target_created:
            if not production and runtime_base.exists() and not runtime_base.is_symlink():
                os.chmod(runtime_base, 0o755)
            remove_created_target(target, production=production)
        if dependency_installed:
            remove_operation_dependency(prefix, production)
        if not production and base_mode_before is not None and runtime_base.exists() and not runtime_base.is_symlink():
            os.chmod(runtime_base, base_mode_before)
        if base_created:
            try:
                runtime_base.rmdir()
            except OSError:
                pass
        print(json.dumps({"status": "BLOCKED", "reason": f"{type(exc).__name__}:{exc}"}, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(1)
    finally:
        if not production and base_mode_before is not None and runtime_base.exists() and not runtime_base.is_symlink():
            os.chmod(runtime_base, 0o555 if target.exists() else base_mode_before)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
PY
