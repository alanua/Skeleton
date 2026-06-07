from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.skeleton_core.host_maintenance import HostMaintenanceReport, process_host_maintenance


DEFAULT_TRANSPORT_ROOT = Path("/home/agent/agent-dev/host_maintenance")
DEFAULT_WORKTREE_ROOT = Path("/home/agent/agent-dev/worktrees/skeleton")
INBOX_DIRNAME = "inbox"
DONE_DIRNAME = "done"
FAILED_DIRNAME = "failed"
REPORT_FILENAME = "host_maintenance_transport_report.json"
PACKET_SUFFIXES = frozenset({".yaml", ".yml", ".json"})


@dataclass(frozen=True)
class HostMaintenanceTransportReport:
    status: str
    transport_root: str
    inbox_dir: str
    report_path: str
    packet: str | None
    moved_to: str | None
    processor: dict[str, Any] | None
    reason: str | None = None

    def compact(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "status": self.status,
            "packet": self.packet,
            "moved_to": self.moved_to,
            "reason": self.reason,
        }
        if self.processor is not None:
            report["processor"] = self.processor
        return {key: value for key, value in report.items() if value is not None}


def poll_once(
    transport_root: str | Path = DEFAULT_TRANSPORT_ROOT,
    *,
    worktree_root: str | Path = DEFAULT_WORKTREE_ROOT,
    report_path: str | Path | None = None,
) -> HostMaintenanceTransportReport:
    root = Path(transport_root)
    inbox_dir = root / INBOX_DIRNAME
    done_dir = root / DONE_DIRNAME
    failed_dir = root / FAILED_DIRNAME
    for directory in (inbox_dir, done_dir, failed_dir):
        directory.mkdir(parents=True, exist_ok=True)

    report_file = Path(report_path) if report_path is not None else root / REPORT_FILENAME
    report_file.parent.mkdir(parents=True, exist_ok=True)

    packet = _next_packet(inbox_dir)
    if packet is None:
        report = HostMaintenanceTransportReport(
            status="no-op",
            transport_root=str(root),
            inbox_dir=str(inbox_dir),
            report_path=str(report_file),
            packet=None,
            moved_to=None,
            processor=None,
            reason="empty inbox",
        )
        _write_report(report_file, report.compact())
        return report

    processor_report: HostMaintenanceReport | None = None
    failure_reason: str | None = None
    try:
        processor_report = process_host_maintenance(
            packet,
            report_path=report_file,
            worktree_root=worktree_root,
        )
        destination_dir = done_dir if processor_report.status == "ok" else failed_dir
        status = "done" if processor_report.status == "ok" else "failed"
    except Exception as exc:  # noqa: BLE001 - malformed packets must be moved out of inbox.
        destination_dir = failed_dir
        status = "failed"
        failure_reason = f"{type(exc).__name__}: {exc}"

    moved_to = _move_packet(packet, destination_dir)
    report = HostMaintenanceTransportReport(
        status=status,
        transport_root=str(root),
        inbox_dir=str(inbox_dir),
        report_path=str(report_file),
        packet=str(packet),
        moved_to=str(moved_to),
        processor=processor_report.compact() if processor_report is not None else None,
        reason=failure_reason,
    )
    _write_report(report_file, report.compact())
    return report


def _next_packet(inbox_dir: Path) -> Path | None:
    packets = sorted(
        path
        for path in inbox_dir.iterdir()
        if path.is_file() and path.suffix.lower() in PACKET_SUFFIXES
    )
    return packets[0] if packets else None


def _move_packet(packet: Path, destination_dir: Path) -> Path:
    destination = _unique_destination(destination_dir / packet.name)
    return packet.replace(destination)


def _unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    for index in range(1, 1000):
        candidate = destination.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate packet destination for {destination.name}")


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll one bounded local host maintenance packet.")
    parser.add_argument(
        "--transport-root",
        default=str(DEFAULT_TRANSPORT_ROOT),
        help="Local host maintenance transport root. Defaults to /home/agent/agent-dev/host_maintenance.",
    )
    parser.add_argument(
        "--worktree-root",
        default=str(DEFAULT_WORKTREE_ROOT),
        help="Skeleton issue worktree root. Defaults to /home/agent/agent-dev/worktrees/skeleton.",
    )
    parser.add_argument("--report-path", default=None, help="Optional compact JSON report path.")
    args = parser.parse_args(argv)

    report = poll_once(args.transport_root, worktree_root=args.worktree_root, report_path=args.report_path)
    print(json.dumps(report.compact(), sort_keys=True, separators=(",", ":")))
    return 0 if report.status in {"done", "no-op"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
