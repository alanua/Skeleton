from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mempalace_adapter import MemPalaceAdapter
from core.mempalace_projection import MEMPALACE_SYNTHETIC_NAMESPACE, MEMPALACE_SYNTHETIC_PROJECT_ID
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError


PROJECTION_PATH = ROOT / "tests" / "fixtures" / "mempalace_synthetic" / "projection.json"
QUERY_CASES = (
    ("door attribution", "synthetic-door-policy"),
    ("window caution", "synthetic-window-threshold"),
    ("ventilation timing", "synthetic-ventilation-schedule"),
)
FORBIDDEN_REPORT_MARKERS = (
    "aufmass",
    "bauclock",
    "legal",
    "contact",
    "address",
    "phone",
    "email",
    "secret",
    "password",
    "credential",
    "/tmp",
    ".db",
    ".sqlite",
)
SYNTHETIC_PROJECTION: dict[str, Any] = {
    "schema": "skeleton.mempalace_projection.v1",
    "namespace": "skeleton",
    "project_id": "mempalace_synthetic",
    "source_snapshot_id": "snapshot-mempalace-synthetic-0001",
    "current_canonical_revision": 3,
    "indexed_at": "2026-06-29T00:00:00Z",
    "documents": [
        {
            "item_id": "synthetic-door-policy",
            "canonical_ref": "canon-synth-door-policy",
            "canonical_revision": 3,
            "title": "Door Sensor Policy",
            "bounded_text": "Door sensor events use monotonic ordering and require source attribution before retrieval.",
            "tags": ["door", "sensor", "policy", "attribution"],
            "source_attribution": [
                {
                    "source_ref": "src-synth-door-policy",
                    "kind": "synthetic_projection",
                    "evidence_hash": "1" * 64,
                }
            ],
            "deleted": False,
        },
        {
            "item_id": "synthetic-window-threshold",
            "canonical_ref": "canon-synth-window-threshold",
            "canonical_revision": 3,
            "title": "Window Threshold Rule",
            "bounded_text": "Window signal quality below seven requires caution status and a bounded reviewer note.",
            "tags": ["window", "threshold", "quality", "caution"],
            "source_attribution": [
                {
                    "source_ref": "src-synth-window-threshold",
                    "kind": "synthetic_projection",
                    "evidence_hash": "2" * 64,
                }
            ],
            "deleted": False,
        },
        {
            "item_id": "synthetic-ventilation-schedule",
            "canonical_ref": "canon-synth-ventilation-schedule",
            "canonical_revision": 3,
            "title": "Ventilation Schedule",
            "bounded_text": "Ventilation review runs weekly and records aggregate timing without raw discussion fields.",
            "tags": ["ventilation", "schedule", "aggregate", "timing"],
            "source_attribution": [
                {
                    "source_ref": "src-synth-ventilation-schedule",
                    "kind": "synthetic_projection",
                    "evidence_hash": "3" * 64,
                }
            ],
            "deleted": False,
        },
    ],
}


def load_projection() -> dict[str, Any]:
    if PROJECTION_PATH.exists():
        return json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))
    return deepcopy(SYNTHETIC_PROJECTION)


def run_benchmark() -> dict[str, object]:
    projection = load_projection()
    adapter = MemPalaceAdapter(projection)
    gateway = MemoryGateway(capability_token(namespaces=(MEMPALACE_SYNTHETIC_NAMESPACE,)), mempalace_adapter=adapter)

    checks: list[dict[str, object]] = []
    try:
        gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "bauclock",
                "command": "bauclock.memory.search_semantic",
                "payload": {"project_id": MEMPALACE_SYNTHETIC_PROJECT_ID, "query": "door"},
            }
        )
        isolated = False
    except MemoryGatewayPolicyError:
        isolated = True
    checks.append({"check": "namespace_isolation", "passed": isolated})

    hits = 0
    for query, expected_item_id in QUERY_CASES:
        response = gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": MEMPALACE_SYNTHETIC_NAMESPACE,
                "command": f"{MEMPALACE_SYNTHETIC_NAMESPACE}.memory.search_semantic",
                "payload": {"project_id": MEMPALACE_SYNTHETIC_PROJECT_ID, "query": query},
            }
        )
        results = response["payload"]["results"]
        top = results[0] if results else {}
        passed = bool(results) and top["result_refs"][0] == expected_item_id and bool(top["source_attribution"])
        hits += 1 if passed else 0
        checks.append({"check": f"retrieval_{expected_item_id}", "passed": passed})

    deleted = adapter.delete_item("synthetic-door-policy")
    deletion_results = deleted.search_semantic(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
        query="door attribution",
    )["results"]
    checks.append(
        {
            "check": "deletion_removes_retrieval_result",
            "passed": all(result["result_refs"][0] != "synthetic-door-policy" for result in deletion_results),
        }
    )
    checks.append({"check": "clean_rebuild_manifest", "passed": adapter.manifest == adapter.rebuild_manifest()})

    freshness = adapter.get_index_freshness(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
        current_canonical_revision=4,
    )
    checks.append({"check": "stale_revision_surfaces", "passed": freshness["stale"] is True})

    quality = round(hits / len(QUERY_CASES), 3)
    resource_report = adapter.resource_report()
    checks.append(
        {
            "check": "bounded_resources",
            "passed": resource_report["aggregate_disk_bytes"] < 50000
            and resource_report["aggregate_ram_bytes"] < 100000
            and resource_report["aggregate_build_ms"] < 1000,
        }
    )
    public_report = {
        "schema": "skeleton.mempalace_synthetic_benchmark.v1",
        "namespace": MEMPALACE_SYNTHETIC_NAMESPACE,
        "project_id": MEMPALACE_SYNTHETIC_PROJECT_ID,
        "quality_threshold": 0.8,
        "quality_score": quality,
        "resource_report": resource_report,
        "checks": checks,
    }
    public_report["decision"] = _decision(public_report)
    public_report["stable_reasons"] = _stable_reasons(public_report)
    return public_report


def _decision(report: dict[str, object]) -> str:
    checks = report["checks"]
    if not isinstance(checks, list):
        return "REJECT"
    failed = [check for check in checks if isinstance(check, dict) and not check.get("passed")]
    if any(
        check.get("check") in {"namespace_isolation", "deletion_removes_retrieval_result", "clean_rebuild_manifest"}
        for check in failed
    ):
        return "REJECT"
    if float(report["quality_score"]) >= float(report["quality_threshold"]) and not failed:
        return "PASS"
    return "CAUTION"


def _stable_reasons(report: dict[str, object]) -> list[str]:
    reasons = []
    if report["decision"] == "PASS":
        reasons.extend(
            [
                "namespace_isolation_proven",
                "deletion_and_rebuild_pass",
                "source_attribution_present",
                "synthetic_quality_threshold_met",
                "bounded_resources_documented",
            ]
        )
    else:
        for check in report["checks"]:
            if isinstance(check, dict) and not check.get("passed"):
                reasons.append(f"failed_{check.get('check')}")
    return reasons


def main() -> int:
    report = run_benchmark()
    serialized = json.dumps(report, sort_keys=True)
    lowered = serialized.lower()
    if any(marker in lowered for marker in FORBIDDEN_REPORT_MARKERS):
        report["decision"] = "REJECT"
        report["stable_reasons"] = ["private_like_marker_in_public_report"]
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
