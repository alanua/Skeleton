from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.skeleton_core.runner_inbox import RunnerInboxReport, process_runner_inbox


DEFAULT_TRANSPORT_ROOT = Path("var/runner_inbox")
INBOX_DIRNAME = "inbox"
DONE_DIRNAME = "done"
FAILED_DIRNAME = "failed"
REPORT_FILENAME = "runner_inbox_report.json"
PACKET_SUFFIXES = frozenset({".yaml", ".yml", ".json"})


@dataclass(frozen=True)
class RunnerInboxTransportReport:
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
    repo_root: str | Path | None = None,
    report_path: str | Path | None = None,
) -> RunnerInboxTransportReport:
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
        report = RunnerInboxTransportReport(
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

    processor_report: RunnerInboxReport | None = None
    failure_reason: str | None = None
    try:
        processor_report = process_runner_inbox(packet, repo_root=repo_root)
        destination_dir = done_dir if processor_report.status == "appended" else failed_dir
        status = "done" if processor_report.status == "appended" else "failed"
    except Exception as exc:  # noqa: BLE001 - transport must quarantine malformed packets.
        destination_dir = failed_dir
        status = "failed"
        failure_reason = f"{type(exc).__name__}: {exc}"

    moved_to = _move_packet(packet, destination_dir)
    report = RunnerInboxTransportReport(
        status=status,
        transport_root=str(root),
        inbox_dir=str(inbox_dir),
        report_path=str(report_file),
        packet=str(packet),
        moved_to=str(moved_to),
        processor=asdict(processor_report) if processor_report is not None else None,
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
    parser = argparse.ArgumentParser(description="Poll one bounded local Runner inbox packet.")
    parser.add_argument(
        "--transport-root",
        default=str(DEFAULT_TRANSPORT_ROOT),
        help="Local Runner inbox transport root. Defaults to var/runner_inbox.",
    )
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--report-path", default=None, help="Optional compact JSON report path.")
    args = parser.parse_args(argv)

    report = poll_once(args.transport_root, repo_root=args.repo_root, report_path=args.report_path)
    print(json.dumps(report.compact(), sort_keys=True, separators=(",", ":")))
    return 0 if report.status in {"done", "no-op"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
