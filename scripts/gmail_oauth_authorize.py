#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from adapters.gmail_oauth_client import (  # noqa: E402
    GMAIL_READONLY_SCOPE,
    GmailCredentialStore,
    build_authorization_url,
    exchange_authorization_code,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Onboard a local private Gmail OAuth account")
    parser.add_argument("--account-ref", required=True)
    parser.add_argument("--client-id-file", type=Path, required=True)
    parser.add_argument("--client-secret-file", type=Path, required=True)
    parser.add_argument("--credential-root", type=Path)
    parser.add_argument("--redirect-uri", default="http://127.0.0.1:8765/oauth2callback")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("url")
    exchange = subparsers.add_parser("exchange")
    exchange.add_argument("--code-file", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        client_id = _read_secret(args.client_id_file)
        client_secret = _read_secret(args.client_secret_file)
        if args.command == "url":
            url = build_authorization_url(
                client_id=client_id,
                redirect_uri=args.redirect_uri,
                state=secrets.token_urlsafe(16),
            )
            print(
                json.dumps(
                    {
                        "schema": "skeleton.gmail_oauth_onboarding_receipt.v1",
                        "status": "AUTHORIZATION_URL_READY",
                        "authorization_url": url,
                        "scope": GMAIL_READONLY_SCOPE,
                        "public_safe": True,
                        "private_payloads_included": False,
                        "external_side_effects_executed": False,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0

        store = GmailCredentialStore(args.credential_root)
        path = exchange_authorization_code(
            account_ref=args.account_ref,
            client_id=client_id,
            client_secret=client_secret,
            code=_read_secret(args.code_file),
            redirect_uri=args.redirect_uri,
            credential_store=store,
        )
        print(
            json.dumps(
                {
                    "schema": "skeleton.gmail_oauth_onboarding_receipt.v1",
                    "status": "CREDENTIAL_STORED",
                    "account_ref": args.account_ref,
                    "credential_path": str(path),
                    "scope": GMAIL_READONLY_SCOPE,
                    "public_safe": True,
                    "private_payloads_included": False,
                    "external_side_effects_executed": True,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        reason = getattr(exc, "reason_code", None) or exc.__class__.__name__
        print(
            json.dumps(
                {
                    "schema": "skeleton.gmail_oauth_onboarding_receipt.v1",
                    "status": "BLOCKED",
                    "reason": str(reason),
                    "public_safe": True,
                    "private_payloads_included": False,
                    "external_side_effects_executed": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


def _read_secret(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    raise SystemExit(main())
