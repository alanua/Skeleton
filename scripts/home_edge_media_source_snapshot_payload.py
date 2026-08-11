from __future__ import annotations
import ast
import base64
import hashlib
import json
import os
import re
import stat

TASK_ID = 'home_edge_01_media_source_snapshot_v1'
SOURCE_PATH = '/opt/skeleton/cast/app.py'
MAX_SOURCE_BYTES = 716800
SECRET_SUFFIXES = ('api_key', 'access_token', 'auth_token', 'bot_token', 'client_secret', 'hmac_secret', 'secret_key', 'secret', 'password', 'passwd', 'private_key', 'credential', 'tmdb_api_key', 'tmdb_key', 'openai_api_key', 'telegram_bot_token', 'skeleton_secret', 'brave_key')

def blocked(reason, source=b""):
    sha = hashlib.sha256(source).hexdigest() if source else "unavailable"
    return {
        "public": {
            "maintenance_task_id": TASK_ID,
            "source_identity": "blocked",
            "source_version_marker": "unknown",
            "source_bytes": len(source),
            "source_sha256": sha,
            "python_parse_status": "blocked",
            "video_route_present": False,
            "health_route_present": False,
            "private_artifact_written": False,
            "private_artifact_hash_matches": False,
            "executor_receipt_hash": "pending",
            "stable_reason": reason,
            "success_criteria": "not_met",
        }
    }

def file_id(st):
    return {
        "mode": stat.S_IFMT(st.st_mode),
        "dev": getattr(st, "st_dev", None),
        "ino": getattr(st, "st_ino", None),
        "size": st.st_size,
        "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)),
    }

def same_file(before, after):
    return file_id(before) == file_id(after)

def assignment_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None

def secret_identifier(name):
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if any(normalized == suffix or normalized.endswith("_" + suffix) for suffix in SECRET_SUFFIXES):
        return True
    return False

def dict_key_is_secret_identifier(key):
    return isinstance(key, ast.Constant) and isinstance(key.value, str) and secret_identifier(key.value)

def placeholder(value):
    return re.fullmatch(
        r"(?i)^(?:|x+|test(?:ing)?(?:[-_ ]?(?:secret|token|key|password|value))?|dummy(?:[-_ ]?(?:secret|token|key|password|value))?|placeholder|example|sample|changeme|change_me|replace_me|not[-_ ]?set|todo|none|null|fake[-_ ]?(?:secret|token|key|password|value)?|sk-[x*]+)$",
        value.strip(),
    ) is not None

def literal_is_live_secret(value):
    return isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value.strip()) >= 8 and not placeholder(value.value)

def has_credential_literal(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = assignment_name(target)
                if name and secret_identifier(name) and literal_is_live_secret(node.value):
                    return True
        elif isinstance(node, ast.AnnAssign):
            name = assignment_name(node.target)
            if name and node.value is not None and secret_identifier(name) and literal_is_live_secret(node.value):
                return True
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if dict_key_is_secret_identifier(key) and literal_is_live_secret(value):
                    return True
    return False

def flask_app_names(tree):
    flask_names = {"Flask"}
    app_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "flask":
            for alias in node.names:
                if alias.name == "Flask":
                    flask_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id in flask_names:
                for target in node.targets:
                    name = assignment_name(target)
                    if name:
                        app_names.add(name)
    return app_names

def route_path(node, apps):
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr in ("get", "route")
        and isinstance(func.value, ast.Name)
        and func.value.id in apps
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return None
    return node.args[0].value

def route_markers(tree):
    video = False
    health = False
    apps = flask_app_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = route_path(decorator, apps)
            if route == "/video":
                video = True
            if route in ("/health", "/healthz"):
                health = True
    return video, health

def main():
    try:
        st_l = os.lstat(SOURCE_PATH)
    except OSError:
        print(json.dumps(blocked("source_lstat_failed"), sort_keys=True, separators=(",", ":")))
        return
    if stat.S_ISLNK(st_l.st_mode):
        print(json.dumps(blocked("source_symlink"), sort_keys=True, separators=(",", ":")))
        return
    if not stat.S_ISREG(st_l.st_mode):
        print(json.dumps(blocked("source_not_regular"), sort_keys=True, separators=(",", ":")))
        return
    if st_l.st_mode & stat.S_IWOTH:
        print(json.dumps(blocked("source_world_writable"), sort_keys=True, separators=(",", ":")))
        return
    if st_l.st_size > MAX_SOURCE_BYTES:
        print(json.dumps(blocked("source_oversize"), sort_keys=True, separators=(",", ":")))
        return
    if not os.access(SOURCE_PATH, os.R_OK):
        print(json.dumps(blocked("source_unreadable"), sort_keys=True, separators=(",", ":")))
        return
    with open(SOURCE_PATH, "rb") as handle:
        source = handle.read(MAX_SOURCE_BYTES + 1)
    try:
        st_after = os.lstat(SOURCE_PATH)
    except OSError:
        print(json.dumps(blocked("source_lstat_changed"), sort_keys=True, separators=(",", ":")))
        return
    if not same_file(st_l, st_after):
        print(json.dumps(blocked("source_changed_during_read"), sort_keys=True, separators=(",", ":")))
        return
    if len(source) > MAX_SOURCE_BYTES:
        print(json.dumps(blocked("source_oversize"), sort_keys=True, separators=(",", ":")))
        return
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        print(json.dumps(blocked("source_not_utf8", source), sort_keys=True, separators=(",", ":")))
        return
    try:
        tree = ast.parse(text, filename=SOURCE_PATH)
        compile(tree, SOURCE_PATH, "exec")
    except SyntaxError:
        print(json.dumps(blocked("python_parse_failed", source), sort_keys=True, separators=(",", ":")))
        return
    if has_credential_literal(tree):
        print(json.dumps(blocked("suspicious_credential_literal", source), sort_keys=True, separators=(",", ":")))
        return
    video, health = route_markers(tree)
    lowered = text.lower()
    has_flask = any(
        isinstance(node, ast.ImportFrom) and node.module == "flask"
        or isinstance(node, ast.Import) and any(alias.name == "flask" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not video:
        print(json.dumps(blocked("video_route_missing", source), sort_keys=True, separators=(",", ":")))
        return
    if not health:
        print(json.dumps(blocked("health_route_missing", source), sort_keys=True, separators=(",", ":")))
        return
    if not (has_flask and "skeleton" in lowered and "cast" in lowered and "media" in lowered):
        print(json.dumps(blocked("skeleton_cast_markers_missing", source), sort_keys=True, separators=(",", ":")))
        return
    sha = hashlib.sha256(source).hexdigest()
    print(json.dumps({
        "public": {
            "maintenance_task_id": TASK_ID,
            "source_identity": "verified",
            "source_version_marker": "v63" if re.search(r"(?i)\bv63\b", text) else "unknown",
            "source_bytes": len(source),
            "source_sha256": sha,
            "python_parse_status": "ok",
            "video_route_present": True,
            "health_route_present": True,
            "private_artifact_written": False,
            "private_artifact_hash_matches": False,
            "executor_receipt_hash": "pending",
            "stable_reason": "validated",
            "success_criteria": "pending",
        },
        "private_source_b64": base64.b64encode(source).decode("ascii"),
    }, sort_keys=True, separators=(",", ":")))

main()
