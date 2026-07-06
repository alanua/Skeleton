from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol


MEMPALACE_SYNTHETIC_BENCHMARK_SCHEMA: Final = (
    "skeleton.mempalace_synthetic_benchmark.v1"
)
MEMPALACE_SYNTHETIC_NAMESPACE: Final = "skeleton"
MEMPALACE_SYNTHETIC_PROJECT_ID: Final = "mempalace_synthetic"
MEMPALACE_SYNTHETIC_BENCHMARK_TIMEOUT_SECONDS: Final = 60
MEMPALACE_SYNTHETIC_REQUIRED_STABLE_REASONS: Final = frozenset(
    (
        "namespace_isolation_proven",
        "deletion_and_rebuild_pass",
        "source_attribution_present",
        "synthetic_quality_threshold_met",
        "bounded_resources_documented",
    )
)
MEMPALACE_SYNTHETIC_PRIVATE_MARKERS: Final = tuple(
    marker.lower()
    for marker in (
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
        "token",
        "private",
        "/tmp",
        "/home/",
        ".db",
        ".sqlite",
    )
)


class RegisteredCheckout(Protocol):
    checkout_path: Path


class RunCommand(Protocol):
    def __call__(
        self,
        args: list[str],
        cwd: str | Path | None = None,
        *,
        timeout: int | None = None,
    ) -> tuple[int, str]: ...


MaintenanceReport = Callable[[str, str, list[str], str], str]
RejectIssueInput = Callable[[str], str | None]
Preflight = Callable[
    [str],
    tuple[RegisteredCheckout | None, list[str], str | None],
]
OutputHasPrivateMarker = Callable[[str], bool]
ParseBenchmarkJson = Callable[
    [str],
    tuple[dict[str, object] | None, str | None],
]
ValidateBenchmarkReport = Callable[
    [dict[str, object]],
    str | None,
]


def reject_mempalace_runtime_smoke_issue_input(
    body: str,
    *,
    runtime_maintenance_mode: str,
    task_id: str,
) -> str | None:
    if not body:
        return None
    if "```task" in body:
        return "issue_controlled_input_not_allowed"

    metadata = body.split("```", 1)[0]
    allowed_lines = {
        f"Mode: {runtime_maintenance_mode}",
        f"Maintenance Task ID: {task_id}",
    }

    for line in metadata.splitlines():
        stripped = line.strip()
        if stripped and stripped not in allowed_lines:
            return "issue_controlled_input_not_allowed"

    return None


def mempalace_benchmark_output_has_private_marker(
    output: str,
    *,
    private_markers: tuple[str, ...] = (
        MEMPALACE_SYNTHETIC_PRIVATE_MARKERS
    ),
) -> bool:
    lowered = (output or "").lower()
    return any(marker in lowered for marker in private_markers)


def parse_mempalace_benchmark_json(
    output: str,
) -> tuple[dict[str, object] | None, str | None]:
    decoder = json.JSONDecoder()

    try:
        parsed, end = decoder.raw_decode(output)
    except json.JSONDecodeError:
        return None, "malformed_benchmark_json"

    if output[end:].strip():
        return None, "benchmark_extra_output"
    if not isinstance(parsed, dict):
        return None, "malformed_benchmark_json"

    return parsed, None


def validate_mempalace_benchmark_report(
    report: dict[str, object],
    *,
    expected_schema: str = MEMPALACE_SYNTHETIC_BENCHMARK_SCHEMA,
    expected_namespace: str = MEMPALACE_SYNTHETIC_NAMESPACE,
    expected_project_id: str = MEMPALACE_SYNTHETIC_PROJECT_ID,
    required_stable_reasons: frozenset[str] = (
        MEMPALACE_SYNTHETIC_REQUIRED_STABLE_REASONS
    ),
) -> str | None:
    if report.get("schema") != expected_schema:
        return "benchmark_schema_mismatch"

    if (
        report.get("namespace") != expected_namespace
        or report.get("project_id") != expected_project_id
    ):
        return "benchmark_scope_mismatch"

    if report.get("decision") != "PASS":
        return "benchmark_decision_not_pass"

    quality_score = report.get("quality_score")
    quality_threshold = report.get("quality_threshold")

    if (
        isinstance(quality_score, bool)
        or isinstance(quality_threshold, bool)
        or not isinstance(quality_score, (int, float))
        or not isinstance(quality_threshold, (int, float))
        or float(quality_score) < float(quality_threshold)
    ):
        return "benchmark_quality_threshold_not_met"

    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        return "benchmark_missing_checks"

    for check in checks:
        if not isinstance(check, dict) or check.get("passed") is not True:
            return "benchmark_failed_check"

    stable_reasons = report.get("stable_reasons")
    if (
        not isinstance(stable_reasons, list)
        or not all(isinstance(reason, str) for reason in stable_reasons)
        or not required_stable_reasons <= set(stable_reasons)
    ):
        return "benchmark_missing_stable_reason"

    resource_report = report.get("resource_report")
    if not isinstance(resource_report, dict):
        return "benchmark_resource_report_missing"

    for key in (
        "aggregate_disk_bytes",
        "aggregate_ram_bytes",
        "aggregate_build_ms",
    ):
        value = resource_report.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "benchmark_resource_report_malformed"

    return None


def execute_mempalace_synthetic_runtime_smoke(
    body: str,
    *,
    task_id: str,
    reject_issue_input: RejectIssueInput,
    preflight: Preflight,
    output_has_private_marker: OutputHasPrivateMarker,
    parse_benchmark_json: ParseBenchmarkJson,
    validate_benchmark_report: ValidateBenchmarkReport,
    run_command: RunCommand,
    maintenance_report: MaintenanceReport,
    timeout_seconds: int,
) -> str:
    input_reason = reject_issue_input(body)
    if input_reason is not None:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [f"reason={input_reason}"],
            "not_met",
        )

    registered_checkout, status_lines, preflight_report = preflight(task_id)

    if preflight_report is not None or registered_checkout is None:
        return preflight_report or maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_preflight_failed"],
            "not_met",
        )

    try:
        exit_code, output = run_command(
            ["python3", "scripts/mempalace_synthetic_benchmark.py"],
            cwd=registered_checkout.checkout_path,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=benchmark_timeout"],
            "not_met",
        )
    except Exception:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=benchmark_launch_failed"],
            "not_met",
        )

    if output_has_private_marker(output):
        return maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=private_like_benchmark_output"],
            "not_met",
        )

    if exit_code != 0:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"exit_code={exit_code}",
                "reason=benchmark_nonzero_exit",
            ],
            "not_met",
        )

    benchmark_report, parse_reason = parse_benchmark_json(output)

    if parse_reason is not None or benchmark_report is None:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "reason=" + (parse_reason or "malformed_benchmark_json"),
            ],
            "not_met",
        )

    validation_reason = validate_benchmark_report(benchmark_report)

    if validation_reason is not None:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={validation_reason}"],
            "not_met",
        )

    resource_report = benchmark_report["resource_report"]
    assert isinstance(resource_report, dict)

    stable_reasons = benchmark_report["stable_reasons"]
    assert isinstance(stable_reasons, list)

    checks = benchmark_report["checks"]
    assert isinstance(checks, list)

    report_lines = [
        "runtime_smoke_decision=PASS",
        f"quality_score={benchmark_report['quality_score']}",
        f"quality_threshold={benchmark_report['quality_threshold']}",
        f"runtime_smoke_check_count={len(checks)}",
        f"disk_bytes={resource_report['aggregate_disk_bytes']}",
        f"ram_bytes={resource_report['aggregate_ram_bytes']}",
        f"build_ms={resource_report['aggregate_build_ms']}",
        "live_private_ingestion=false",
        "canonical_write_enabled=false",
        "services_enabled=false",
        "ports_enabled=false",
        "network_provider_enabled=false",
        "model_credentials_used=false",
    ]

    report_lines.extend(
        f"runtime_smoke_stable_reason={reason}"
        for reason in stable_reasons
    )

    return maintenance_report(
        "DONE",
        task_id,
        report_lines,
        "met",
    )
