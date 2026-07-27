from __future__ import annotations
import gzip, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/home_edge/resolver_federation/resolver_sync.py"


def run(tmp_path: pathlib.Path, node: str, *args: str, input_obj=None):
    env = os.environ.copy()
    env.update({
        "SKELETON_RESOLVER_NODE_ID": node,
        "SKELETON_RESOLVER_STATE": str(tmp_path / node),
        "SKELETON_RESOLVER_DB": str(tmp_path / node / "events.sqlite3"),
    })
    data = None if input_obj is None else json.dumps(input_obj)
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=data, text=True, capture_output=True, env=env)


def event():
    return {
        "domain": "example.invalid",
        "resolver": "browser-v2",
        "event_type": "extract_success",
        "confidence": 91,
        "payload": {"adapter": "cinemar", "script_fingerprint": "sha256:abc", "qualities": ["720p"]},
    }


def test_record_export_import_is_idempotent(tmp_path):
    first = run(tmp_path, "node-a", "record", input_obj=event())
    assert first.returncode == 0, first.stderr
    bundle = tmp_path / "bundle.jsonl.gz"
    exported = run(tmp_path, "node-a", "export", "--out", str(bundle))
    assert exported.returncode == 0, exported.stderr
    imported = run(tmp_path, "node-b", "import", str(bundle))
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["inserted"] == 1
    imported_again = run(tmp_path, "node-b", "import", str(bundle))
    assert json.loads(imported_again.stdout)["inserted"] == 0
    status = run(tmp_path, "node-b", "status")
    parsed = json.loads(status.stdout)
    assert parsed["remote_evidence"] == 1
    assert parsed["origins"] == {"node-a": 1}


def test_rejects_urls_and_sensitive_keys(tmp_path):
    bad = event(); bad["payload"]["stream_url"] = "https://secret.invalid/x?token=y"
    result = run(tmp_path, "node-a", "record", input_obj=bad)
    assert result.returncode == 2
    assert "forbidden resolver field" in result.stderr


def test_local_scope_is_not_exported(tmp_path):
    value = event(); value["share_scope"] = "local"
    assert run(tmp_path, "node-a", "record", input_obj=value).returncode == 0
    bundle = tmp_path / "local.jsonl.gz"
    assert run(tmp_path, "node-a", "export", "--out", str(bundle)).returncode == 0
    with gzip.open(bundle, "rt", encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        assert header["event_count"] == 0
        assert fh.read() == ""
