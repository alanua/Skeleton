#!/usr/bin/env python3
"""Local-first, idempotent resolver-learning federation."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
from typing import Any, Iterable

SCHEMA = "skeleton.resolver_learning.v1"
BUNDLE_SCHEMA = "skeleton.resolver_bundle.v1"
MAX_EVENT_BYTES = 131072
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
DOMAIN_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
FORBIDDEN_KEY_PARTS = {
    "authorization", "cookie", "password", "passwd", "secret", "token",
    "credential", "signed_url", "stream_url", "manifest_url", "page_url",
    "user_id", "email", "phone", "history",
}
URL_RE = re.compile(r"(?:https?|rtmp|rtsp|magnet)://", re.I)


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def state_root() -> pathlib.Path:
    return pathlib.Path(env("SKELETON_RESOLVER_STATE", "/var/lib/skeleton-resolver"))


def db_path() -> pathlib.Path:
    return pathlib.Path(env("SKELETON_RESOLVER_DB", str(state_root() / "resolver-learning.sqlite3")))


def node_id() -> str:
    value = env("SKELETON_RESOLVER_NODE_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", value):
        raise SystemExit("SKELETON_RESOLVER_NODE_ID is missing or invalid")
    return value


def connect(path: pathlib.Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS meta(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY,
          origin_node TEXT NOT NULL,
          origin_sequence INTEGER NOT NULL,
          observed_at TEXT NOT NULL,
          domain TEXT NOT NULL,
          resolver TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          share_scope TEXT NOT NULL,
          confidence INTEGER NOT NULL,
          content_hash TEXT NOT NULL,
          activation_status TEXT NOT NULL,
          imported_from TEXT,
          received_at TEXT NOT NULL,
          UNIQUE(origin_node, origin_sequence)
        );
        CREATE INDEX IF NOT EXISTS events_domain_idx ON events(domain, resolver, event_type);
        CREATE INDEX IF NOT EXISTS events_origin_idx ON events(origin_node, origin_sequence);
        """
    )
    return con


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def assert_safe(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_s = str(key).lower()
            if any(part in key_s for part in FORBIDDEN_KEY_PARTS) or key_s == "url" or key_s.endswith("_url"):
                raise ValueError(f"forbidden resolver field: {path}.{key}")
            assert_safe(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            assert_safe(item, f"{path}[{idx}]")
    elif isinstance(value, str):
        if URL_RE.search(value):
            raise ValueError(f"URL values are forbidden in federated resolver evidence: {path}")
        if len(value.encode("utf-8")) > 16384:
            raise ValueError(f"string too large: {path}")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError(f"unsupported value type at {path}")


def normalize_input(raw: dict[str, Any]) -> dict[str, Any]:
    domain = str(raw.get("domain", "")).strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(domain) or ".." in domain:
        raise ValueError("invalid domain")
    resolver = str(raw.get("resolver", "")).strip()
    event_type = str(raw.get("event_type", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", resolver):
        raise ValueError("invalid resolver")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", event_type):
        raise ValueError("invalid event_type")
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    assert_safe(payload)
    if len(canonical(payload)) > MAX_EVENT_BYTES:
        raise ValueError("resolver event is too large")
    share_scope = str(raw.get("share_scope", "federated"))
    if share_scope not in {"federated", "local"}:
        raise ValueError("share_scope must be federated or local")
    confidence = int(raw.get("confidence", 50))
    if confidence < 0 or confidence > 100:
        raise ValueError("confidence must be 0..100")
    observed_at = str(raw.get("observed_at") or utcnow())
    return {
        "domain": domain,
        "resolver": resolver,
        "event_type": event_type,
        "payload": payload,
        "share_scope": share_scope,
        "confidence": confidence,
        "observed_at": observed_at,
    }


def next_sequence(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT value FROM meta WHERE key='local_sequence'").fetchone()
    seq = int(row[0]) + 1 if row else 1
    con.execute(
        "INSERT INTO meta(key,value) VALUES('local_sequence',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(seq),),
    )
    return seq


def hash_material(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "origin_node": event["origin_node"],
        "origin_sequence": int(event["origin_sequence"]),
        "observed_at": event["observed_at"],
        "domain": event["domain"],
        "resolver": event["resolver"],
        "event_type": event["event_type"],
        "payload": event["payload"],
        "share_scope": event["share_scope"],
        "confidence": int(event["confidence"]),
    }


def finalize_event(event: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(canonical(hash_material(event))).hexdigest()
    event = dict(event)
    event["content_hash"] = digest
    event["event_id"] = hashlib.sha256((event["origin_node"] + ":" + str(event["origin_sequence"]) + ":" + digest).encode()).hexdigest()
    return event


def insert_event(con: sqlite3.Connection, event: dict[str, Any], imported_from: str | None) -> bool:
    normalized = normalize_input(event)
    rebuilt = {
        **normalized,
        "origin_node": str(event["origin_node"]),
        "origin_sequence": int(event["origin_sequence"]),
    }
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", rebuilt["origin_node"]):
        raise ValueError("invalid origin_node")
    rebuilt = finalize_event(rebuilt)
    if event.get("content_hash") != rebuilt["content_hash"] or event.get("event_id") != rebuilt["event_id"]:
        raise ValueError("resolver event hash mismatch")
    activation = "remote_evidence" if imported_from else str(event.get("activation_status") or "candidate")
    cur = con.execute(
        """INSERT OR IGNORE INTO events(
        event_id,origin_node,origin_sequence,observed_at,domain,resolver,event_type,
        payload_json,share_scope,confidence,content_hash,activation_status,imported_from,received_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rebuilt["event_id"], rebuilt["origin_node"], rebuilt["origin_sequence"],
            rebuilt["observed_at"], rebuilt["domain"], rebuilt["resolver"],
            rebuilt["event_type"], json.dumps(rebuilt["payload"], ensure_ascii=False, sort_keys=True),
            rebuilt["share_scope"], rebuilt["confidence"], rebuilt["content_hash"],
            activation, imported_from, utcnow(),
        ),
    )
    return cur.rowcount == 1


def record(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_input(raw)
    with connect() as con:
        event = finalize_event({
            **normalized,
            "origin_node": node_id(),
            "origin_sequence": next_sequence(con),
        })
        event["activation_status"] = "candidate"
        insert_event(con, event, imported_from=None)
    return event


def rows_to_events(rows: Iterable[sqlite3.Row]) -> Iterable[dict[str, Any]]:
    for row in rows:
        yield {
            "event_id": row["event_id"],
            "origin_node": row["origin_node"],
            "origin_sequence": row["origin_sequence"],
            "observed_at": row["observed_at"],
            "domain": row["domain"],
            "resolver": row["resolver"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "share_scope": row["share_scope"],
            "confidence": row["confidence"],
            "content_hash": row["content_hash"],
            "activation_status": row["activation_status"],
        }


def export_bundle(out: pathlib.Path) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM events WHERE share_scope='federated' ORDER BY origin_node,origin_sequence"
        ).fetchall()
    header = {"schema": BUNDLE_SCHEMA, "created_at": utcnow(), "exporter": node_id(), "event_count": len(rows)}
    tmp = out.with_suffix(out.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
        fh.write(json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n")
        for event in rows_to_events(rows):
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    if tmp.stat().st_size > MAX_BUNDLE_BYTES:
        tmp.unlink(missing_ok=True)
        raise ValueError("resolver bundle exceeds maximum size")
    tmp.replace(out)
    return header


def import_bundle(path: pathlib.Path, imported_from: str | None = None) -> dict[str, Any]:
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("resolver bundle exceeds maximum size")
    inserted = 0
    seen = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh, connect() as con:
        header_line = fh.readline()
        if not header_line:
            raise ValueError("empty resolver bundle")
        header = json.loads(header_line)
        if header.get("schema") != BUNDLE_SCHEMA:
            raise ValueError("unsupported resolver bundle schema")
        source = imported_from or str(header.get("exporter") or "unknown")
        for line in fh:
            if not line.strip():
                continue
            seen += 1
            if seen > 100000:
                raise ValueError("too many resolver events")
            event = json.loads(line)
            if insert_event(con, event, imported_from=source):
                inserted += 1
    return {"seen": seen, "inserted": inserted, "source": source}


def import_inbox() -> dict[str, int]:
    root = state_root()
    inbox = root / "inbox"
    archive = root / "archive"
    failed = root / "failed"
    for directory in (inbox, archive, failed):
        directory.mkdir(parents=True, exist_ok=True)
    ok = errors = inserted = 0
    for path in sorted(inbox.glob("*.jsonl.gz")):
        try:
            result = import_bundle(path)
            inserted += int(result["inserted"])
            shutil.move(str(path), archive / path.name)
            ok += 1
        except Exception as exc:
            error_path = failed / (path.name + ".error.txt")
            error_path.write_text(str(exc) + "\n", encoding="utf-8")
            shutil.move(str(path), failed / path.name)
            errors += 1
    return {"bundles": ok, "errors": errors, "inserted": inserted}


def peer_targets() -> list[str]:
    raw = env("SKELETON_RESOLVER_PEERS", "").strip()
    if not raw:
        return []
    targets: list[str] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        target = entry.split("=", 1)[-1].strip()
        if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,255}", target):
            raise ValueError(f"invalid peer target: {target}")
        targets.append(target)
    return targets


def sync() -> dict[str, Any]:
    imported = import_inbox()
    targets = peer_targets()
    sent: list[dict[str, Any]] = []
    if not targets:
        return {"import": imported, "sent": sent, "status": "no_peers_configured"}
    root = state_root()
    outbox = root / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"resolver-{node_id()}-{stamp}.jsonl.gz"
    bundle = outbox / filename
    header = export_bundle(bundle)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    identity = pathlib.Path(env("SKELETON_RESOLVER_SSH_IDENTITY", "/etc/skeleton/resolver-sync/id_ed25519"))
    remote_user = env("SKELETON_RESOLVER_SSH_USER", "skeleton-resolver")
    if not identity.is_file():
        raise RuntimeError(f"resolver sync SSH identity is missing: {identity}")
    for target in targets:
        remote = target if "@" in target else f"{remote_user}@{target}"
        cmd = [
            "ssh", "-i", str(identity), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes", remote,
            "/usr/local/bin/skeleton-resolver-receive", filename, digest,
        ]
        with bundle.open("rb") as fh:
            proc = subprocess.run(cmd, stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        sent.append({"peer": target, "returncode": proc.returncode})
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr.decode("utf-8", "replace")[:4000])
    return {"import": imported, "sent": sent, "bundle": filename, "events": header["event_count"]}


def status() -> dict[str, Any]:
    with connect() as con:
        total = con.execute("SELECT count(*) FROM events").fetchone()[0]
        local = con.execute("SELECT count(*) FROM events WHERE imported_from IS NULL").fetchone()[0]
        remote = total - local
        origins = {row[0]: row[1] for row in con.execute("SELECT origin_node,count(*) FROM events GROUP BY origin_node")}
    return {
        "schema": SCHEMA,
        "node_id": node_id(),
        "database": str(db_path()),
        "events": total,
        "local_events": local,
        "remote_evidence": remote,
        "origins": origins,
        "peers": peer_targets(),
    }


def load_json(path: str | None) -> dict[str, Any]:
    if path:
        data = pathlib.Path(path).read_text(encoding="utf-8")
    else:
        data = sys.stdin.read()
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_record = sub.add_parser("record")
    p_record.add_argument("--file")
    p_export = sub.add_parser("export")
    p_export.add_argument("--out", required=True)
    p_import = sub.add_parser("import")
    p_import.add_argument("bundle")
    sub.add_parser("import-inbox")
    sub.add_parser("sync")
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "record":
            result = record(load_json(args.file))
        elif args.command == "export":
            result = export_bundle(pathlib.Path(args.out))
        elif args.command == "import":
            result = import_bundle(pathlib.Path(args.bundle))
        elif args.command == "import-inbox":
            result = import_inbox()
        elif args.command == "sync":
            result = sync()
        else:
            result = status()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
