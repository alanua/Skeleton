from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from core.private_memory import (
    PRIVATE_MEMORY_CONFIG_ENV,
    PrivateMemoryConnector,
)


GRAPH_MEMORY_INDEX_REPORT_SCHEMA = "skeleton.graph_memory_index.report.v1"
GRAPH_MEMORY_INDEX_CONFIG_ENV = "SKELETON_GRAPH_MEMORY_INDEX_CONFIG"

_UNSAFE_REPORT_KEYS = frozenset({"path", "payload", "content", "locator", "source_locator"})
_UNSAFE_REPORT_VALUE_RE = re.compile(
    r"(?i)(/|\\|file:|\.sqlite\b|\.db\b|secret|token|password|credential|select\s|create\s+table)"
)


@dataclass(frozen=True)
class GraphMemoryIndexReport:
    schema: str
    status: str
    source_record_count: int
    node_count: int
    edge_count: int
    json_written: bool
    graphml_written: bool
    provenance_record_count: int
    canonical_write_attempted: bool
    error_class: str | None
    next_operator_action: str


def build_graph_memory_index(
    *,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    builder = GraphMemoryIndexBuilder(config_path=config_path, output_dir=output_dir, env=env)
    return builder.build()


class GraphMemoryIndexBuilder:
    def __init__(
        self,
        config_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path is not None else None
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.env = env if env is not None else os.environ

    def build(self) -> dict[str, object]:
        try:
            config_path = self._resolve_config_path()
            db_path = PrivateMemoryConnector(config_path=config_path, env=self.env)._load_db_path()
            output_dir = self.output_dir or self._load_output_dir(config_path)
            records = _load_canonical_records(db_path)
            graph = _build_graph(records)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "private_memory_graph_index.json").write_text(
                json.dumps(graph, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            (output_dir / "private_memory_graph_index.graphml").write_text(
                _graphml(graph),
                encoding="utf-8",
            )
            return _sanitize_report(
                GraphMemoryIndexReport(
                    schema=GRAPH_MEMORY_INDEX_REPORT_SCHEMA,
                    status="DONE",
                    source_record_count=len(records),
                    node_count=len(graph["nodes"]),
                    edge_count=len(graph["edges"]),
                    json_written=True,
                    graphml_written=True,
                    provenance_record_count=len(graph["provenance"]),
                    canonical_write_attempted=False,
                    error_class=None,
                    next_operator_action="none",
                )
            )
        except Exception as exc:  # noqa: BLE001 - public reports fail closed.
            return _sanitize_report(
                GraphMemoryIndexReport(
                    schema=GRAPH_MEMORY_INDEX_REPORT_SCHEMA,
                    status="BLOCKED",
                    source_record_count=0,
                    node_count=0,
                    edge_count=0,
                    json_written=False,
                    graphml_written=False,
                    provenance_record_count=0,
                    canonical_write_attempted=False,
                    error_class=_safe_error_class(type(exc).__name__),
                    next_operator_action="operator_review_graph_memory_index",
                )
            )

    def _resolve_config_path(self) -> Path:
        if self.config_path is not None:
            return self.config_path
        raw_path = self.env.get(GRAPH_MEMORY_INDEX_CONFIG_ENV) or self.env.get(
            PRIVATE_MEMORY_CONFIG_ENV
        )
        if not raw_path:
            raise GraphMemoryIndexError("missing config")
        return Path(raw_path)

    def _load_output_dir(self, config_path: Path) -> Path:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, Mapping):
            raise GraphMemoryIndexError("invalid config")
        raw_dir = None
        graph_config = config.get("graph_memory_index")
        if isinstance(graph_config, Mapping):
            raw_dir = graph_config.get("output_dir")
        if raw_dir is None:
            seed_config = config.get("private_memory_seed")
            if isinstance(seed_config, Mapping):
                raw_dir = seed_config.get("graph_output_dir")
        if not isinstance(raw_dir, str) or not raw_dir:
            raise GraphMemoryIndexError("missing output dir")
        output_dir = Path(raw_dir)
        if not output_dir.is_absolute():
            output_dir = config_path.parent / output_dir
        return output_dir


class GraphMemoryIndexError(Exception):
    """Raised when derived graph index generation cannot proceed."""


def _load_canonical_records(db_path: Path) -> list[sqlite3.Row]:
    if not db_path.is_file():
        raise GraphMemoryIndexError("database not found")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise GraphMemoryIndexError("integrity check failed")
        table_row = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'private_memory_import_records'
            """
        ).fetchone()
        if table_row is None:
            raise GraphMemoryIndexError("canonical import table missing")
        return connection.execute(
            """
            SELECT package_sha256, seed_record_id, payload_class, seed_created_at, imported_at
            FROM private_memory_import_records
            ORDER BY package_sha256, seed_record_id
            """
        ).fetchall()


def _build_graph(records: list[sqlite3.Row]) -> dict[str, object]:
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    provenance: list[dict[str, str]] = []
    batch_nodes: set[str] = set()
    class_nodes: set[str] = set()
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for record in records:
        batch_id = f"batch:{record['package_sha256'][:16]}"
        class_id = f"class:{record['payload_class']}"
        record_id = f"record:{record['package_sha256'][:16]}:{record['seed_record_id']}"
        if batch_id not in batch_nodes:
            nodes.append({"id": batch_id, "kind": "import_batch"})
            batch_nodes.add(batch_id)
        if class_id not in class_nodes:
            nodes.append({"id": class_id, "kind": "payload_class"})
            class_nodes.add(class_id)
        nodes.append({"id": record_id, "kind": "canonical_record"})
        edges.append({"source": batch_id, "target": record_id, "kind": "imported"})
        edges.append({"source": record_id, "target": class_id, "kind": "classified_as"})
        provenance.append(
            {
                "record_node": record_id,
                "batch_node": batch_id,
                "built_at": built_at,
                "derived_from": "private_memory_import_records",
            }
        )
    return {"nodes": nodes, "edges": edges, "provenance": provenance}


def _graphml(graph: Mapping[str, object]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="kind" for="all" attr.name="kind" attr.type="string"/>',
        '  <graph id="private_memory_graph" edgedefault="directed">',
    ]
    for node in graph["nodes"]:  # type: ignore[index]
        node_id = html.escape(str(node["id"]), quote=True)
        kind = html.escape(str(node["kind"]), quote=True)
        lines.extend(
            (
                f'    <node id="{node_id}">',
                f"      <data key=\"kind\">{kind}</data>",
                "    </node>",
            )
        )
    for index, edge in enumerate(graph["edges"]):  # type: ignore[index]
        source = html.escape(str(edge["source"]), quote=True)
        target = html.escape(str(edge["target"]), quote=True)
        kind = html.escape(str(edge["kind"]), quote=True)
        lines.extend(
            (
                f'    <edge id="e{index}" source="{source}" target="{target}">',
                f"      <data key=\"kind\">{kind}</data>",
                "    </edge>",
            )
        )
    lines.extend(("  </graph>", "</graphml>", ""))
    return "\n".join(lines)


def _sanitize_report(report: GraphMemoryIndexReport) -> dict[str, object]:
    data = asdict(report)
    for key, value in data.items():
        lowered = key.lower()
        if lowered in _UNSAFE_REPORT_KEYS:
            raise GraphMemoryIndexError("unsafe report key")
        if isinstance(value, str) and _UNSAFE_REPORT_VALUE_RE.search(value):
            raise GraphMemoryIndexError("unsafe report value")
    return data


def _safe_error_class(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", value):
        return "GraphMemoryIndexError"
    return value
