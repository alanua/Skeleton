"""Bitwarden Secrets Manager provider for the existing Skeleton SecretStoreGate.

Only the locally deployed trusted adapter calls this provider. The secret is
never placed in HomeEdgeExec argv/stdin/environment or its audit receipts.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from typing import Callable

from core.secret_store import (
    ResolvedSecret, SecretMissing, SecretProviderUnavailable,
    SecretReference, SecretResolutionContext,
)

_UUID = re.compile(r"^[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$")


class BitwardenSecretsProvider:
    provider = "bitwarden"

    def __init__(
        self, *,
        token_reader: Callable[[], str],
        command: str = "/usr/local/bin/bws",
        timeout_seconds: int = 15,
    ) -> None:
        self._token_reader = token_reader
        self._command = command
        self._timeout = timeout_seconds

    def resolve(
        self, reference: SecretReference, context: SecretResolutionContext
    ) -> ResolvedSecret:
        if reference.provider != self.provider or not _UUID.fullmatch(reference.reference_id):
            raise SecretProviderUnavailable("invalid_bitwarden_reference")
        # Do not let a generic vault listing interface leak into device adapters.
        if context.machine_identity != "home-edge-01":
            raise SecretProviderUnavailable("wrong_execution_node")
        token = self._token_reader()
        if not isinstance(token, str) or not token.strip():
            raise SecretProviderUnavailable("bitwarden_credential_unavailable")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "BWS_ACCESS_TOKEN": token}
        try:
            cp = subprocess.run(
                [self._command, "secret", "get", reference.reference_id,
                 "--output", "json"],
                env=env, capture_output=True, text=True, timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SecretProviderUnavailable("bitwarden_request_failed") from None
        finally:
            token = ""
        if cp.returncode != 0:
            raise SecretProviderUnavailable("bitwarden_request_failed")
        try:
            item = json.loads(cp.stdout)
        except (ValueError, TypeError):
            raise SecretProviderUnavailable("bitwarden_invalid_response") from None
        if not isinstance(item, Mapping) or item.get("id") != reference.reference_id:
            raise SecretProviderUnavailable("bitwarden_reference_mismatch")
        value = item.get("value")
        if not isinstance(value, str) or not value:
            raise SecretMissing("bitwarden_secret_missing")
        return ResolvedSecret(value)
