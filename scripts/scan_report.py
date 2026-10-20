from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core.scan_report_manifest import (
    PrivateDownloadLinkProvider,
    ScanReportDeliveryStore,
    build_scan_report_manifest,
    deliver_scan_report,
    render_telegram_report,
    validate_scan_report_manifest,
    write_scan_report_manifest,
)


def _read_json(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("input must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Build, inspect, or resend Skeleton scan report manifests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--input", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--base-url", default=os.environ.get("SKELETON_PRIVATE_DOWNLOAD_BASE_URL"))
    build.add_argument("--ttl-seconds", type=int, default=3600)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--manifest", required=True)

    resend = subparsers.add_parser("resend")
    resend.add_argument("--manifest", required=True)
    resend.add_argument("--state-db", required=True)

    args = parser.parse_args()
    if args.command == "build":
        provider = PrivateDownloadLinkProvider(base_url=args.base_url, ttl_seconds=args.ttl_seconds)
        manifest = build_scan_report_manifest(_read_json(args.input), link_provider=provider)
        write_scan_report_manifest(manifest, args.output)
        print(json.dumps({"status": "DONE", "manifest_hash": manifest["manifest_hash"]}, sort_keys=True))
        return 0

    manifest = _read_json(args.manifest)
    validate_scan_report_manifest(manifest)
    if args.command == "inspect":
        messages = render_telegram_report(manifest)
        print(json.dumps({
            "status": "DONE",
            "manifest_hash": manifest["manifest_hash"],
            "telegram_message_count": len(messages),
            "overall_status": manifest["overall_status"],
        }, sort_keys=True))
        return 0

    receipt = deliver_scan_report(manifest, store=ScanReportDeliveryStore(args.state_db))
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "delivered" else 2


if __name__ == "__main__":
    raise SystemExit(main())
