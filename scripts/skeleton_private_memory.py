#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.private_memory_stack import PrivateMemoryStack, PrivateMemoryStackError, sanitize_cli_report
from core.task_memory_context import TaskMemoryContextError, build_task_memory_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skeleton-memory",
        description="Local-private Skeleton memory stack CLI.",
    )
    parser.add_argument("--root", help="Private memory root. Defaults to SKELETON_PRIVATE_MEMORY_ROOT or user-local storage.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create private root, SQLite authority, seed approved manifest, and rebuild indexes.")

    put = sub.add_parser("put", help="Write one canonical fact and rebuild derived indexes atomically.")
    put.add_argument("namespace")
    put.add_argument("fact_id")
    put.add_argument("--json", required=True, help="Fact value as JSON.")
    put.add_argument("--actor", default="operator")
    put.add_argument("--reason", default="operator-put")
    put.add_argument("--approval", default="local-operator")

    get = sub.add_parser("get", help="Read one exact canonical fact directly from SQLite.")
    get.add_argument("namespace")
    get.add_argument("fact_id")

    search = sub.add_parser("search", help="Run bounded non-authoritative MemPalace semantic search.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)

    relations = sub.add_parser("relations", help="Run bounded non-authoritative Graphify relationship query.")
    relations.add_argument("query")
    relations.add_argument("--limit", type=int, default=5)

    sub.add_parser("rebuild", help="Rebuild both derived indexes from active canonical SQLite facts.")

    backup = sub.add_parser("backup", help="Create a local-private SQLite backup under the private root.")
    backup.add_argument("--snapshot-id")

    import_bundle = sub.add_parser("import-bundle", help="Import an operator-approved local-private inbox bundle.")
    import_bundle.add_argument("basename")
    import_bundle.add_argument("--expected-sha256", required=True)
    import_bundle.add_argument("--create-backup", action="store_true")

    task_context = sub.add_parser("task-context", help="Build a public-safe task memory context receipt.")
    task_context.add_argument("--project-id", required=True)
    task_context.add_argument("--task-route", required=True)
    task_context.add_argument("--profile", required=True, choices=["public_control", "private_runtime", "none"])
    task_context.add_argument("--query", required=True)
    task_context.add_argument("--namespace", action="append", dest="namespaces")
    task_context.add_argument("--required", action="store_true")
    task_context.add_argument("--limit", type=int, default=10)
    task_context.add_argument("--max-chars", type=int, default=6000)

    sub.add_parser("status", help="Print aggregate READY/STALE/BLOCKED status without raw private content.")

    delete = sub.add_parser("delete", help=argparse.SUPPRESS)
    delete.add_argument("namespace")
    delete.add_argument("fact_id")
    delete.add_argument("--actor", default="operator")
    delete.add_argument("--reason", default="operator-delete")
    delete.add_argument("--approval", default="local-operator")

    args = parser.parse_args(argv)
    stack = PrivateMemoryStack(args.root)
    try:
        if args.command == "init":
            payload = stack.init()
        elif args.command == "put":
            payload = stack.put(
                namespace=args.namespace,
                fact_id=args.fact_id,
                value=_loads_json(args.json),
                actor_ref=args.actor,
                reason_code=args.reason,
                approval_ref=args.approval,
            )
        elif args.command == "get":
            payload = stack.get(namespace=args.namespace, fact_id=args.fact_id)
        elif args.command == "search":
            payload = stack.search(query=args.query, limit=args.limit)
        elif args.command == "relations":
            payload = stack.relations(query=args.query, limit=args.limit)
        elif args.command == "rebuild":
            payload = stack.rebuild()
        elif args.command == "backup":
            payload = stack.backup(snapshot_id=args.snapshot_id)
        elif args.command == "import-bundle":
            payload = stack.import_bundle(
                args.basename,
                expected_sha256=args.expected_sha256,
                create_backup=args.create_backup,
            )
        elif args.command == "task-context":
            payload = build_task_memory_context(
                stack,
                project_id=args.project_id,
                task_route=args.task_route,
                profile=args.profile,
                query=args.query,
                namespaces=args.namespaces,
                required=args.required,
                limit=args.limit,
                max_chars=args.max_chars,
            ).public_receipt()
        elif args.command == "status":
            payload = stack.status()
        elif args.command == "delete":
            payload = stack.delete(
                namespace=args.namespace,
                fact_id=args.fact_id,
                actor_ref=args.actor,
                reason_code=args.reason,
                approval_ref=args.approval,
            )
        else:
            parser.error("unsupported command")
    except (PrivateMemoryStackError, TaskMemoryContextError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(sanitize_cli_report(payload), indent=2, sort_keys=True))
    return 0


def _loads_json(value: str) -> Any:
    return json.loads(value)


if __name__ == "__main__":
    raise SystemExit(main())
