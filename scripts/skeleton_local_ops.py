#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from core.aufmass_engine import AufmassInput, Opening, Point, RoomInput, calculate_aufmass
from core.aufmass_exporter import aufmass_result_to_json_dict, aufmass_result_to_rows
from core.private_memory import CanonicalPrivateMemoryStore
from core.private_memory_backup import create_snapshot, restore_snapshot_to_isolated_target, snapshot_file_path
from core.private_memory_history import content_hash

MEMORY_PACKET_SCHEMA = "skeleton.local_memory_packet.v1"
AUFMASS_INPUT_SCHEMA = "skeleton.aufmass.local_input.v1"
SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
QUANTITY_FIELDS = {
    "floor_area", "ceiling_area", "perimeter", "gross_wall_area",
    "openings_area", "net_wall_area", "volume",
}


class LocalOpsError(RuntimeError):
    pass


def safe_token(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise LocalOpsError(f"{name} must be text")
    value = value.strip()
    if not value or len(value) > 128 or any(ch not in SAFE for ch in value):
        raise LocalOpsError(f"invalid {name}")
    return value


def contained(root: Path, *parts: str) -> Path:
    root = root.expanduser().resolve()
    candidate = root.joinpath(*parts).resolve()
    if candidate != root and root not in candidate.parents:
        raise LocalOpsError("path escaped private root")
    return candidate


def private_root(raw: str | None) -> Path:
    raw = raw or os.environ.get("SKELETON_PRIVATE_ROOT")
    if not raw:
        raise LocalOpsError("private root is required")
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    for rel in ("memory", "memory/backups", "memory/manifests", "memory/verify", "locks", "aufmass"):
        path = contained(root, *rel.split("/"))
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return root


def database_path(root: Path) -> Path:
    return contained(root, "memory", "canonical.sqlite")


@contextmanager
def memory_lock(root: Path):
    path = contained(root, "locks", "memory.lock")
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def store(root: Path) -> CanonicalPrivateMemoryStore:
    return CanonicalPrivateMemoryStore(database_path(root))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode())


def read_json(path: str | Path, *, max_bytes: int = 4 * 1024 * 1024) -> Any:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > max_bytes:
        raise LocalOpsError("input unavailable or too large")
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalOpsError("invalid JSON input") from exc


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def done(action: str, **fields: Any) -> dict[str, Any]:
    return {"status": "DONE", "action": action, **fields}


def tx_fact_id(transaction: str) -> str:
    return f"tx.{safe_token(transaction, 'transaction')}"


def tx_fingerprint(action: str, payload: Any) -> dict[str, Any]:
    return {"action": action, "payload_hash": content_hash(payload)}


def existing_transaction(memory: CanonicalPrivateMemoryStore, transaction: str, action: str, payload: Any) -> dict[str, Any] | None:
    record = memory.get_active_fact(namespace="system.transactions", fact_id=tx_fact_id(transaction))
    if record is None:
        return None
    expected = tx_fingerprint(action, payload)
    if not isinstance(record, dict) or record.get("action") != expected["action"] or record.get("payload_hash") != expected["payload_hash"]:
        raise LocalOpsError("transaction reused with different data")
    return record


def record_transaction(memory: CanonicalPrivateMemoryStore, args: argparse.Namespace, action: str, payload: Any) -> int:
    memory.put_fact(
        namespace="system.transactions",
        fact_id=tx_fact_id(args.transaction),
        value=tx_fingerprint(action, payload),
        actor_ref=safe_token(args.actor, "actor"),
        reason_code=safe_token(args.reason, "reason"),
        approval_ref=safe_token(args.approval, "approval"),
        transaction_ref=safe_token(f"record.{args.transaction}", "transaction"),
    )
    return memory.current_revision()


def memory_init(root: Path) -> dict[str, Any]:
    with memory_lock(root):
        report = store(root).initialize()
    return done("memory.init", canonical_revision=report.get("canonical_revision", 0), integrity_ok=report.get("integrity_ok", False), wal_enabled=report.get("wal_enabled", False))


def memory_health(root: Path) -> dict[str, Any]:
    if not database_path(root).is_file():
        raise LocalOpsError("memory is not initialized")
    with memory_lock(root):
        report = store(root).integrity_report()
    if report.get("status") != "DONE":
        raise LocalOpsError("memory integrity check failed")
    return done("memory.health", canonical_revision=report["canonical_revision"], fact_count=report["fact_count"], event_count=report["event_count"], tombstone_count=report["tombstone_count"], integrity_ok=True)


def memory_put(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    namespace = safe_token(args.namespace, "namespace")
    fact_id = safe_token(args.fact_id, "fact_id")
    if namespace.startswith("system."):
        raise LocalOpsError("system namespace is reserved")
    try:
        value = json.loads(args.value_json)
    except json.JSONDecodeError as exc:
        raise LocalOpsError("invalid value JSON") from exc
    payload = {"namespace": namespace, "fact_id": fact_id, "value": value}
    with memory_lock(root):
        memory = store(root)
        previous = existing_transaction(memory, args.transaction, "memory.put", payload)
        if previous is not None:
            return done("memory.put", idempotent=True, canonical_revision=memory.current_revision(), value_hash=content_hash(value))
        memory.put_fact(namespace=namespace, fact_id=fact_id, value=value, actor_ref=safe_token(args.actor, "actor"), reason_code=safe_token(args.reason, "reason"), approval_ref=safe_token(args.approval, "approval"), transaction_ref=safe_token(args.transaction, "transaction"))
        if memory.get_active_fact(namespace=namespace, fact_id=fact_id) != value:
            raise LocalOpsError("memory readback failed")
        revision = record_transaction(memory, args, "memory.put", payload)
        if memory.integrity_report().get("status") != "DONE":
            raise LocalOpsError("memory integrity failed")
    return done("memory.put", idempotent=False, canonical_revision=revision, value_hash=content_hash(value))


def memory_get(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    with memory_lock(root):
        memory = store(root)
        value = memory.get_active_fact(namespace=safe_token(args.namespace, "namespace"), fact_id=safe_token(args.fact_id, "fact_id"))
        revision = memory.current_revision()
    result = done("memory.get", found=value is not None, canonical_revision=revision)
    if value is not None:
        result["value_hash"] = content_hash(value)
        if args.show_value:
            result["value"] = value
    return result


def memory_history(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    with memory_lock(root):
        events = store(root).history(namespace=safe_token(args.namespace, "namespace"), fact_id=safe_token(args.fact_id, "fact_id"))
    safe_events = [{key: event[key] for key in ("event_type", "canonical_revision", "timestamp", "previous_hash", "new_hash")} for event in events]
    return done("memory.history", event_count=len(safe_events), events=safe_events)


def memory_delete(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    namespace = safe_token(args.namespace, "namespace")
    fact_id = safe_token(args.fact_id, "fact_id")
    if namespace.startswith("system."):
        raise LocalOpsError("system namespace is reserved")
    payload = {"namespace": namespace, "fact_id": fact_id}
    with memory_lock(root):
        memory = store(root)
        previous = existing_transaction(memory, args.transaction, "memory.delete", payload)
        if previous is not None:
            return done("memory.delete", idempotent=True, canonical_revision=memory.current_revision())
        memory.tombstone_fact(namespace=namespace, fact_id=fact_id, actor_ref=safe_token(args.actor, "actor"), reason_code=safe_token(args.reason, "reason"), approval_ref=safe_token(args.approval, "approval"), transaction_ref=safe_token(args.transaction, "transaction"))
        if memory.get_active_fact(namespace=namespace, fact_id=fact_id) is not None:
            raise LocalOpsError("delete readback failed")
        revision = record_transaction(memory, args, "memory.delete", payload)
    return done("memory.delete", idempotent=False, canonical_revision=revision)


def validate_memory_packet(packet: Any) -> list[dict[str, Any]]:
    if not isinstance(packet, dict) or packet.get("schema") != MEMORY_PACKET_SCHEMA:
        raise LocalOpsError("invalid memory packet")
    facts = packet.get("facts")
    if not isinstance(facts, list) or not 1 <= len(facts) <= 500:
        raise LocalOpsError("memory packet must contain 1..500 facts")
    result, seen = [], set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {"namespace", "fact_id", "value"}:
            raise LocalOpsError("invalid memory fact")
        namespace = safe_token(fact["namespace"], "namespace")
        fact_id = safe_token(fact["fact_id"], "fact_id")
        if namespace.startswith("system.") or (namespace, fact_id) in seen:
            raise LocalOpsError("reserved or duplicate memory fact")
        seen.add((namespace, fact_id))
        result.append({"namespace": namespace, "fact_id": fact_id, "value": fact["value"]})
    return result


def create_backup(root: Path, prefix: str = "snapshot") -> tuple[dict[str, Any], Path, Path]:
    snapshot_id = safe_token(f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.urandom(4).hex()}", "snapshot_id")
    backup_dir = contained(root, "memory", "backups")
    manifest = create_snapshot(database_path(root), backup_dir, snapshot_id=snapshot_id)
    snapshot = snapshot_file_path(backup_dir, snapshot_id)
    manifest_path = contained(root, "memory", "manifests", f"{snapshot_id}.json")
    write_json(manifest_path, manifest)
    return manifest, snapshot, manifest_path


def memory_import(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    packet = read_json(args.packet)
    facts = validate_memory_packet(packet)
    payload = {"packet_hash": content_hash(packet), "fact_count": len(facts)}
    with memory_lock(root):
        memory = store(root)
        previous = existing_transaction(memory, args.transaction, "memory.import", payload)
        if previous is not None:
            return done("memory.import", idempotent=True, imported_count=len(facts), canonical_revision=memory.current_revision())
        manifest, snapshot, _ = create_backup(root, "preimport")
        events = memory.bulk_put_facts(facts, actor_ref=safe_token(args.actor, "actor"), reason_code=safe_token(args.reason, "reason"), approval_ref=safe_token(args.approval, "approval"), transaction_ref=safe_token(args.transaction, "transaction"), pre_operation_snapshot={"manifest": manifest, "snapshot_path": str(snapshot)})
        if any(memory.get_active_fact(namespace=f["namespace"], fact_id=f["fact_id"]) != f["value"] for f in facts):
            raise LocalOpsError("import readback failed")
        revision = record_transaction(memory, args, "memory.import", payload)
    return done("memory.import", idempotent=False, imported_count=len(events), canonical_revision=revision, packet_hash=payload["packet_hash"])


def memory_backup(root: Path) -> dict[str, Any]:
    with memory_lock(root):
        manifest, _, _ = create_backup(root)
    return done("memory.backup", snapshot_id=manifest["snapshot_id"], canonical_revision=manifest["canonical_revision"], content_hash=manifest["content_hash"], canonical_state_hash=manifest["canonical_state_hash"])


def load_manifest(root: Path, raw_path: str) -> tuple[dict[str, Any], Path]:
    path = Path(raw_path).expanduser().resolve()
    manifest_root = contained(root, "memory", "manifests")
    if manifest_root not in path.parents:
        raise LocalOpsError("manifest must be inside private root")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise LocalOpsError("invalid manifest")
    snapshot = snapshot_file_path(contained(root, "memory", "backups"), safe_token(manifest.get("snapshot_id"), "snapshot_id"))
    return manifest, snapshot


def memory_verify(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    manifest, snapshot = load_manifest(root, args.manifest)
    verify_root = contained(root, "memory", "verify", os.urandom(6).hex())
    verify_root.mkdir(parents=True)
    try:
        report = restore_snapshot_to_isolated_target(snapshot, contained(verify_root, "canonical.sqlite"), manifest)
        if report.get("status") != "DONE":
            raise LocalOpsError("backup verification failed")
    finally:
        shutil.rmtree(verify_root, ignore_errors=True)
    return done("memory.verify-backup", snapshot_id=manifest["snapshot_id"], canonical_revision=manifest["canonical_revision"], content_hash=manifest["content_hash"])


def memory_restore(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    manifest, snapshot = load_manifest(root, args.manifest)
    target_root = Path(args.target_root).expanduser().resolve()
    if target_root == root or target_root in root.parents or root in target_root.parents:
        raise LocalOpsError("restore target must be separate")
    if target_root.exists() and any(target_root.iterdir()):
        raise LocalOpsError("restore target must be absent or empty")
    target_root.mkdir(parents=True, exist_ok=True)
    os.chmod(target_root, 0o700)
    target = contained(target_root, "memory", "canonical.sqlite")
    report = restore_snapshot_to_isolated_target(snapshot, target, manifest)
    if report.get("status") != "DONE":
        raise LocalOpsError("restore failed")
    return done("memory.restore", snapshot_id=manifest["snapshot_id"], canonical_revision=report["canonical_revision"], activation_required=True, activated=False)


def parse_point(value: Any, label: str) -> Point:
    if not isinstance(value, list) or len(value) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise LocalOpsError(f"invalid {label}")
    return Point(float(value[0]), float(value[1]))


def validate_aufmass(payload: Any) -> tuple[AufmassInput, list[dict[str, str]], dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema") != AUFMASS_INPUT_SCHEMA or payload.get("unit") != "m":
        raise LocalOpsError("invalid Aufmass packet")
    project_ref = safe_token(payload.get("project_ref"), "project_ref")
    rooms = payload.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise LocalOpsError("rooms are required")
    accepted, blocked, normalized, seen = [], [], [], set()
    for room in rooms:
        if not isinstance(room, dict):
            raise LocalOpsError("invalid room")
        room_id = safe_token(room.get("room_id"), "room_id")
        if room_id in seen:
            raise LocalOpsError("duplicate room")
        seen.add(room_id)
        status = room.get("calculation_status")
        if status not in {"accepted_input", "estimate_with_review", "blocked"}:
            raise LocalOpsError("invalid room status")
        if status == "blocked":
            blocked.append({"room_id": room_id, "reason": safe_token(room.get("blocker", "blocked_input"), "blocker")})
            normalized.append({"room_id": room_id, "calculation_status": status})
            continue
        polygon_raw = room.get("polygon")
        if not isinstance(polygon_raw, list) or len(polygon_raw) < 3:
            raise LocalOpsError("room polygon is required")
        polygon = [parse_point(point, "polygon point") for point in polygon_raw]
        height = room.get("height_m")
        if isinstance(height, bool) or not isinstance(height, (int, float)) or height <= 0:
            raise LocalOpsError("room height is required")
        if room.get("height_status") not in {"confirmed", "estimated_review"}:
            raise LocalOpsError("room height status is required")
        openings = []
        normalized_openings = []
        for opening in room.get("openings", []):
            if not isinstance(opening, dict):
                raise LocalOpsError("invalid opening")
            opening_id = safe_token(opening.get("opening_id"), "opening_id")
            width, opening_height, count = opening.get("width_m"), opening.get("height_m"), opening.get("count", 1)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 for v in (width, opening_height)) or isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise LocalOpsError("opening dimensions are required")
            if opening.get("status") not in {"confirmed", "estimated_review"}:
                raise LocalOpsError("opening status is required")
            openings.append(Opening(width=float(width), height=float(opening_height), count=count, opening_id=opening_id, source="local_packet"))
            normalized_openings.append({"opening_id": opening_id, "width_m": float(width), "height_m": float(opening_height), "count": count, "status": opening["status"]})
        accepted.append(RoomInput(room_id=room_id, name=None, height=float(height), polygon=polygon, openings=openings, source="local_packet"))
        normalized.append({"room_id": room_id, "calculation_status": status, "polygon": [[p.x, p.y] for p in polygon], "height_m": float(height), "height_status": room["height_status"], "openings": normalized_openings, "source_evidence_refs": sorted(room.get("source_evidence_refs", []))})
    if not accepted:
        raise LocalOpsError("no calculable rooms")
    return AufmassInput(project_id=project_ref, unit="m", rooms=accepted), blocked, {"schema": AUFMASS_INPUT_SCHEMA, "project_ref": project_ref, "unit": "m", "rooms": normalized}


def review_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        for field in QUANTITY_FIELDS:
            if isinstance(item.get(field), (int, float)):
                item[field] = round(float(item[field]), 1)
        result.append(item)
    return result


def csv_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    with tempfile.SpooledTemporaryFile(mode="w+", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.seek(0)
        return handle.read()


def aufmass_example(args: argparse.Namespace) -> dict[str, Any]:
    packet = {"schema": AUFMASS_INPUT_SCHEMA, "project_ref": "example-project", "unit": "m", "rooms": [{"room_id": "room-001", "calculation_status": "accepted_input", "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]], "height_m": 2.5, "height_status": "confirmed", "openings": [{"opening_id": "door-001", "width_m": 0.9, "height_m": 2.0, "count": 1, "status": "confirmed"}], "source_evidence_refs": ["example-evidence-001"]}]}
    write_json(Path(args.output).expanduser().resolve(), packet)
    return done("aufmass.example", template_hash=content_hash(packet))


def aufmass_validate(args: argparse.Namespace) -> dict[str, Any]:
    calculation, blocked, normalized = validate_aufmass(read_json(args.input))
    return done("aufmass.validate", project_ref=calculation.project_id, accepted_room_count=len(calculation.rooms), blocked_room_count=len(blocked), input_hash=content_hash(normalized))


def aufmass_calculate(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    packet = read_json(args.input)
    calculation, blocked, normalized = validate_aufmass(packet)
    raw_result = calculate_aufmass(calculation)
    raw = aufmass_result_to_json_dict(raw_result)
    rows = review_rows(aufmass_result_to_rows(raw_result))
    review = {"schema": "skeleton.aufmass.review_results.v1", "project_ref": calculation.project_id, "unit": "m", "status": "PARTIAL_WITH_BLOCKERS" if blocked else "READY_FOR_OPERATOR_REVIEW", "rooms": [row for row in rows if row["row_type"] == "room"], "summary": next(row for row in rows if row["row_type"] == "summary"), "blocked_rooms": blocked}
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    results_json = contained(output_dir, "aufmass_results.json")
    results_csv = contained(output_dir, "aufmass_results.csv")
    audit_json = contained(output_dir, "aufmass_audit.json")
    write_json(results_json, review)
    atomic_write(results_csv, csv_text(rows).encode())
    audit = {"schema": "skeleton.aufmass.audit.v1", "status": review["status"], "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "input_hash": content_hash(normalized), "accepted_room_count": len(raw_result.rooms), "blocked_room_count": len(blocked), "engine": "core.aufmass_engine.calculate_aufmass", "raw_result": raw, "output_hashes": {"aufmass_results.json": sha256_file(results_json), "aufmass_results.csv": sha256_file(results_csv)}}
    write_json(audit_json, audit)
    audit["output_hashes"]["aufmass_audit.json"] = sha256_file(audit_json)
    memory_revision = None
    if args.write_memory:
        if not all((args.actor, args.reason, args.approval, args.transaction)):
            raise LocalOpsError("memory metadata is required")
        memory_args = argparse.Namespace(namespace="aufmass", fact_id=f"calculation.{calculation.project_id}", value_json=json.dumps({"schema": "skeleton.aufmass.calculation_record.v1", "project_ref": calculation.project_id, "status": audit["status"], "input_hash": audit["input_hash"], "output_hashes": audit["output_hashes"], "accepted_room_count": audit["accepted_room_count"], "blocked_room_count": audit["blocked_room_count"]}), actor=args.actor, reason=args.reason, approval=args.approval, transaction=args.transaction)
        memory_revision = memory_put(memory_args, root)["canonical_revision"]
    return done("aufmass.calculate", calculation_status=audit["status"], accepted_room_count=audit["accepted_room_count"], blocked_room_count=audit["blocked_room_count"], input_hash=audit["input_hash"], output_hashes=audit["output_hashes"], memory_written=memory_revision is not None, memory_revision=memory_revision)


def add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--fact-id", required=True)


def add_mutation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--transaction", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Skeleton memory and Aufmass operations")
    parser.add_argument("--private-root", default=os.environ.get("SKELETON_PRIVATE_ROOT"))
    domains = parser.add_subparsers(dest="domain", required=True)
    memory = domains.add_parser("memory").add_subparsers(dest="command", required=True)
    memory.add_parser("init")
    memory.add_parser("health")
    put = memory.add_parser("put"); add_target(put); put.add_argument("--value-json", required=True); add_mutation(put)
    get = memory.add_parser("get"); add_target(get); get.add_argument("--show-value", action="store_true")
    history = memory.add_parser("history"); add_target(history)
    delete = memory.add_parser("delete"); add_target(delete); add_mutation(delete)
    import_cmd = memory.add_parser("import"); import_cmd.add_argument("--packet", required=True); add_mutation(import_cmd)
    memory.add_parser("backup")
    verify = memory.add_parser("verify-backup"); verify.add_argument("--manifest", required=True)
    restore = memory.add_parser("restore"); restore.add_argument("--manifest", required=True); restore.add_argument("--target-root", required=True)
    aufmass = domains.add_parser("aufmass").add_subparsers(dest="command", required=True)
    example = aufmass.add_parser("example"); example.add_argument("--output", required=True)
    validate = aufmass.add_parser("validate"); validate.add_argument("--input", required=True)
    calculate = aufmass.add_parser("calculate"); calculate.add_argument("--input", required=True); calculate.add_argument("--output-dir", required=True); calculate.add_argument("--write-memory", action="store_true"); calculate.add_argument("--actor"); calculate.add_argument("--reason"); calculate.add_argument("--approval"); calculate.add_argument("--transaction")
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    root = private_root(args.private_root)
    if args.domain == "memory":
        return {"init": memory_init, "health": memory_health, "put": memory_put, "get": memory_get, "history": memory_history, "delete": memory_delete, "import": memory_import, "backup": memory_backup, "verify-backup": memory_verify, "restore": memory_restore}[args.command](*(([args, root]) if args.command not in {"init", "health", "backup"} else ([root])))
    return {"example": aufmass_example, "validate": aufmass_validate, "calculate": aufmass_calculate}[args.command](*(([args, root]) if args.command == "calculate" else ([args])))


def main() -> int:
    args = build_parser().parse_args()
    action = f"{args.domain}.{args.command}"
    try:
        print(json.dumps(dispatch(args), sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:  # fail closed and do not print private exception text
        print(json.dumps({"status": "BLOCKED", "action": action, "error_class": type(exc).__name__, "next_operator_action": "inspect_local_logs"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
