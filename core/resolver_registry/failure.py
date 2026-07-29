from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

from core.runner_task import RUNNER_TASK_SCHEMA


TRANSIENT_FAILURES = frozenset({"network", "timeout", "rate_limit", "origin_protected", "browser_challenge"})
STRUCTURAL_FAILURES = frozenset({"parser_failure", "schema_mismatch", "runtime_integration_missing"})
SECRET_KEYS = frozenset({"cookie", "cookies", "authorization", "x-signature", "token", "headers", "private_headers"})
SIGNED_URL_RE = re.compile(r"([?&](?:sig|signature|token|expires|X-Amz-Signature)=)[^&\\s]+", re.I)


@dataclass(frozen=True)
class FailureEvent:
    host: str
    failure_class: str
    diagnostics: str
    runtime_version: str
    adapter_chain: tuple[str, ...]
    negative_knowledge: Mapping[str, Any]
    occurred_at: int


@dataclass(frozen=True)
class FailureDecision:
    action: str
    failure_class: str
    dedupe_key: str
    evidence: Mapping[str, Any]
    cooldown_seconds: int = 0
    runner_task: Mapping[str, Any] | None = None


def classify_failure(error_type: str | None, detail: str | None = None) -> str:
    text = f"{error_type or ''} {detail or ''}".lower()
    if "origin_protected" in text or "cloudflare" in text or "blocked" in text:
        return "origin_protected"
    if "rate" in text and "limit" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "curl:" in text or "failed to connect" in text or "network" in text:
        return "network"
    if "browser_challenge" in text or "challenge" in text:
        return "browser_challenge"
    if "parser" in text or "xpath" in text or "no selector" in text:
        return "parser_failure"
    if "runtime import" in text or "not wired" in text or "unused resolver" in text:
        return "runtime_integration_missing"
    if "schema" in text or "contract" in text:
        return "schema_mismatch"
    return "generic"


def sanitize_evidence(raw: Mapping[str, Any], *, max_diagnostic_bytes: int = 1800) -> dict[str, Any]:
    host = str(raw.get("host") or raw.get("site_host") or "").lower()[:255]
    failure_class = classify_failure(str(raw.get("error_type") or ""), str(raw.get("error_detail") or raw.get("diagnostics") or ""))
    diagnostics = _sanitize_text(str(raw.get("diagnostics") or raw.get("error_detail") or ""))[:max_diagnostic_bytes]
    adapter_chain = tuple(str(item)[:80] for item in raw.get("adapter_chain", ()) if isinstance(item, str))[:12]
    negative = raw.get("negative_knowledge") if isinstance(raw.get("negative_knowledge"), Mapping) else {}
    return {
        "schema": "skeleton.resolver_failure_evidence.v1",
        "host": host,
        "failure_class": failure_class,
        "diagnostics": diagnostics,
        "runtime_version": str(raw.get("runtime_version") or "unknown")[:80],
        "adapter_chain": list(adapter_chain),
        "negative_knowledge": _sanitize_negative_knowledge(negative),
    }


class FailureTracker:
    def __init__(
        self,
        *,
        structural_threshold: int = 3,
        cooldown_seconds: int = 3600,
        repo: str = "alanua/Skeleton",
        branch: str = "main",
        base_sha: str = "0" * 40,
    ) -> None:
        self.structural_threshold = structural_threshold
        self.cooldown_seconds = cooldown_seconds
        self.repo = repo
        self.branch = branch
        self.base_sha = base_sha
        self._counts: dict[str, int] = {}
        self._created_tasks: set[str] = set()
        self.negative_knowledge: dict[str, dict[str, Any]] = {}

    def observe(self, raw: Mapping[str, Any]) -> FailureDecision:
        evidence = sanitize_evidence(raw)
        failure_class = str(evidence["failure_class"])
        dedupe_key = _dedupe_key(evidence)
        if failure_class in TRANSIENT_FAILURES:
            self.negative_knowledge[dedupe_key] = {
                "failure_class": failure_class,
                "cooldown_until": int(time.time()) + self.cooldown_seconds,
                "evidence": evidence,
            }
            return FailureDecision("cooldown", failure_class, dedupe_key, evidence, self.cooldown_seconds)
        if failure_class not in STRUCTURAL_FAILURES:
            return FailureDecision("record_only", failure_class, dedupe_key, evidence)

        self._counts[dedupe_key] = self._counts.get(dedupe_key, 0) + 1
        if self._counts[dedupe_key] < self.structural_threshold:
            return FailureDecision("record_only", failure_class, dedupe_key, evidence)
        if dedupe_key in self._created_tasks:
            return FailureDecision("deduplicated", failure_class, dedupe_key, evidence)
        self._created_tasks.add(dedupe_key)
        return FailureDecision("create_runner_task", failure_class, dedupe_key, evidence, runner_task=self._runner_task(evidence, dedupe_key))

    def _runner_task(self, evidence: Mapping[str, Any], dedupe_key: str) -> dict[str, Any]:
        return {
            "schema": RUNNER_TASK_SCHEMA,
            "repo": self.repo,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "task_kind": "code_edit",
            "payload": {
                "title": f"Resolver structural failure: {evidence['host']} {evidence['failure_class']}",
                "sanitized_evidence": dict(evidence),
                "preferred_order": [
                    "documented_api",
                    "structured_data",
                    "standard_embed",
                    "hls_dash",
                    "known_player_adapter",
                    "rendered_dom",
                    "site_specific_resolver",
                    "graceful_failure",
                ],
            },
            "requested_capabilities": ["repository_read", "repository_write_allowlisted", "test_execution"],
            "allowed_files": ["ops/skeleton_cast/**", "core/resolver_registry/**", "core/capabilities/**", "tests/**"],
            "forbidden_actions": ["production_activation", "secrets", "mutable_node_to_node_copy"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 1800,
            "expected_output": ["tests pass", "resolver capability wired into runtime path"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": "resolver-capability-threshold",
            "idempotency_key": "resolver-failure-" + dedupe_key,
        }


def _dedupe_key(evidence: Mapping[str, Any]) -> str:
    payload = {
        "host": evidence.get("host"),
        "failure_class": evidence.get("failure_class"),
        "adapter_chain": evidence.get("adapter_chain"),
        "negative_knowledge": evidence.get("negative_knowledge"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def _sanitize_text(text: str) -> str:
    text = SIGNED_URL_RE.sub(r"\1[redacted]", text)
    text = re.sub(r"(?i)(cookie|authorization|token|signature)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    return " ".join(text.split())


def _sanitize_negative_knowledge(value: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if lowered in SECRET_KEYS:
            continue
        if isinstance(item, str):
            clean[str(key)[:80]] = _sanitize_text(item)[:500]
        elif isinstance(item, (int, float, bool)) or item is None:
            clean[str(key)[:80]] = item
        elif isinstance(item, list):
            clean[str(key)[:80]] = [_sanitize_text(str(entry))[:200] for entry in item[:20]]
    return clean
