#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.bitwarden_secret_store import (
    BITWARDEN_API_URL,
    BITWARDEN_IDENTITY_URL,
    BitwardenReferenceDiscoveryError,
    derive_bitwarden_organization_id_from_machine_token,
    discover_gmail_primary_reference_with_sdk,
    public_reference_discovery_receipt,
    bitwarden_machine_token_from_systemd,
)


DEFAULT_SDK_PYTHON = "/opt/skeleton-bitwarden-sdk/venv/bin/python3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Discover the registered Gmail-primary Bitwarden UUID without reading secret values."
        )
    )
    parser.add_argument(
        "--sdk-python",
        default=os.environ.get("SKELETON_BITWARDEN_SDK_PYTHON", DEFAULT_SDK_PYTHON),
    )
    parser.add_argument("--identity-url", default=BITWARDEN_IDENTITY_URL)
    parser.add_argument("--api-url", default=BITWARDEN_API_URL)
    parser.add_argument(
        "--emit-reference-fd",
        type=int,
        default=None,
        help="Optional parent-owned fd for the one opaque UUID; public stdout never includes it.",
    )
    args = parser.parse_args(argv)

    try:
        token = bitwarden_machine_token_from_systemd(os.environ)
        organization_id = derive_bitwarden_organization_id_from_machine_token(
            token,
            identity_url=args.identity_url,
        )
        reference_id = discover_gmail_primary_reference_with_sdk(
            sdk_python=str(Path(args.sdk_python)),
            access_token=token,
            organization_id=organization_id,
            api_url=args.api_url,
            identity_url=args.identity_url,
        )
    except BitwardenReferenceDiscoveryError as exc:
        receipt = public_reference_discovery_receipt(
            status="BLOCKED",
            reason=str(exc),
            match_count=(
                0
                if str(exc).endswith("zero_matches")
                else 2
                if str(exc).endswith("many_matches")
                else -1
            ),
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 1

    if args.emit_reference_fd is not None:
        with os.fdopen(
            args.emit_reference_fd,
            "w",
            encoding="utf-8",
            closefd=False,
        ) as handle:
            handle.write(reference_id)
            handle.write("\n")
            handle.flush()
    receipt = public_reference_discovery_receipt(
        status="PASS",
        reason="OK",
        match_count=1,
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
