"""Review supplied, sanitized evidence; never execute tools or authorize a release."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "skeleton.home.media_regression_guard.v1"
LEVELS = {"source": 0, "unit": 1, "android_ui": 2, "device": 3}
FEATURE_MINIMUM = {
    "home_cold_start": "android_ui", "home_warm_start": "android_ui",
    "endpoint_selection": "android_ui", "mode_routing": "android_ui",
    "incoming_youtube_cast": "device", "poster_stopped": "android_ui",
    "poster_playing": "android_ui", "poster_paused": "android_ui",
    "episode_arrows": "android_ui", "season_persistence": "android_ui",
    "history_membership": "android_ui", "history_hidden": "android_ui",
    "source_links_favicon": "android_ui", "samsung_separation": "device",
    "secrets": "unit", "updater_signing_lineage": "unit",
}
TOOLS = {"git", "diffoscope", "jadx", "androguard", "rea", "ghidra", "bindiff", "diaphora", "frida"}
RESULTS = {"pass", "fail", "unverified", "not_applicable"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\Z")


class InvalidManifest(ValueError):
    pass


def _check(ok: bool, code: str) -> None:
    if not ok:
        raise InvalidManifest(code)


def _obj(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    _check(isinstance(value, dict), "object_required")
    _check(required <= value.keys() and value.keys() <= required | (optional or set()), "object_fields")
    return value


def _hash(value: Any, nullable: bool = False) -> None:
    _check((nullable and value is None) or isinstance(value, str) and bool(SHA.fullmatch(value)), "digest_format")


def _string(value: Any) -> None:
    _check(isinstance(value, str) and bool(value.strip()) and len(value) <= 500, "reason_required")


def _time(value: Any) -> datetime:
    _check(isinstance(value, str), "timestamp_format")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidManifest("timestamp_format") from exc
    _check(result.tzinfo is not None and result.utcoffset() is not None, "timestamp_timezone")
    return result


def _artifact(value: Any) -> dict:
    d = _obj(value, {"version_name", "version_code", "source_sha256", "source_commit", "sha256", "signer_sha256", "application_id", "build_scope"})
    _check(isinstance(d["version_name"], str) and bool(re.fullmatch(r"\d+\.\d+\.\d+", d["version_name"])), "version_name")
    _check(type(d["version_code"]) is int and d["version_code"] > 0, "version_code")
    for key in ["source_sha256", "sha256", "signer_sha256"]:
        _hash(d[key], nullable=True)
    _check(d["source_commit"] is None or isinstance(d["source_commit"], str) and bool(re.fullmatch(r"[0-9a-f]{40}", d["source_commit"])), "commit_format")
    _check(d["build_scope"] in {"production", "production_candidate", "debug", "source"}, "build_scope")
    _check(d["application_id"] in {"com.skeleton.home", "com.skeleton.home.preview"}, "application_id")
    return d


def _unique(items: Any, key: str) -> dict[str, dict]:
    _check(isinstance(items, list) and len(items) <= 200, "bounded_list")
    result = {}
    for item in items:
        _check(isinstance(item, dict) and isinstance(item.get(key), str) and bool(IDENT.fullmatch(item[key])), "identifier_format")
        _check(item[key] not in result, "duplicate_identifier")
        result[item[key]] = item
    return result


def _validate(doc: Any) -> tuple[dict, dict, dict, dict, dict, set[str]]:
    d = _obj(doc, {"schema", "baseline", "candidate", "baseline_features", "features", "tools", "required_tools"})
    _check(d["schema"] == SCHEMA, "schema")
    baseline, candidate = _artifact(d["baseline"]), _artifact(d["candidate"])
    specs, features, tools = (_unique(d[k], key) for k, key in [("baseline_features", "id"), ("features", "id"), ("tools", "name")])
    _check(isinstance(d["required_tools"], list) and all(isinstance(n, str) and n in TOOLS for n in d["required_tools"]), "required_tools")
    _check(len(set(d["required_tools"])) == len(d["required_tools"]), "duplicate_required_tool")
    for spec in specs.values():
        _obj(spec, {"id", "required", "minimum_evidence"})
        _check(type(spec["required"]) is bool and spec["minimum_evidence"] in LEVELS, "feature_spec")
    for feature in features.values():
        _obj(feature, {"id", "result", "reason", "evidence"})
        _check(feature["result"] in RESULTS, "feature_result")
        _string(feature["reason"])
        evidence = _unique(feature["evidence"], "report_id")
        for proof in evidence.values():
            _obj(proof, {"report_id", "report_sha256", "kind", "result", "source_sha256", "sha256"})
            for key in ["report_sha256", "source_sha256"]:
                _hash(proof[key])
            _hash(proof["sha256"], nullable=True)
            _check(proof["kind"] in LEVELS and proof["result"] in {"pass", "fail", "unverified"}, "evidence_result")
    for tool in tools.values():
        _obj(tool, {"name", "required", "state", "version", "provenance", "verified_at", "valid_until", "reason"})
        _check(tool["name"] in TOOLS and type(tool["required"]) is bool, "tool_identity")
        _check(tool["state"] in {"currently_verified", "historically_verified", "unavailable", "not_required"}, "tool_state")
        _string(tool["reason"])
        for key in ["version", "provenance"]:
            _check(tool[key] is None or isinstance(tool[key], str) and bool(IDENT.fullmatch(tool[key])), "tool_metadata")
        for key in ["verified_at", "valid_until"]:
            if tool[key] is not None:
                _time(tool[key])
        if tool["state"] in {"currently_verified", "historically_verified"}:
            _check(all(tool[k] is not None for k in ["version", "provenance", "verified_at"]), "tool_provenance_missing")
        if tool["state"] == "currently_verified":
            _check(tool["valid_until"] is not None, "tool_expiry_missing")
    return baseline, candidate, specs, features, tools, set(d["required_tools"])


def evaluate(doc: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Aggregate evidence. Authenticity of the supplied reports is a caller responsibility."""
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        baseline, candidate, specs, features, tools, required_tools = _validate(doc)
        now = now or datetime.now(timezone.utc)
        _check(now.tzinfo is not None and now.utcoffset() is not None, "timestamp_timezone")
    except (InvalidManifest, TypeError) as exc:
        code = str(exc) if isinstance(exc, InvalidManifest) else "invalid_type"
        return {"schema": SCHEMA, "status": "INVALID_INPUT", "authorization": "none", "blockers": [code], "warnings": []}

    for label, artifact in [("baseline", baseline), ("candidate", candidate)]:
        for key in ["source_sha256", "sha256", "signer_sha256"]:
            if artifact[key] is None:
                blockers.append(f"{label}_{key}_unknown")
        if artifact["application_id"] != "com.skeleton.home":
            blockers.append(f"{label}_preview_identity")
    if baseline["build_scope"] != "production" or candidate["build_scope"] not in {"production", "production_candidate"}:
        blockers.append("nonproduction_artifact")
    if baseline["signer_sha256"] != candidate["signer_sha256"]:
        blockers.append("signing_lineage_changed")
    if baseline["sha256"] != candidate["sha256"] and candidate["version_code"] <= baseline["version_code"]:
        blockers.append("version_code_not_increased")

    for feature_id in FEATURE_MINIMUM:
        if feature_id not in specs:
            blockers.append(f"required_baseline_feature_missing:{feature_id}")
    for feature_id, spec in specs.items():
        feature = features.get(feature_id)
        if feature is None:
            blockers.append(f"baseline_feature_dropped:{feature_id}")
            continue
        required = spec["required"] or feature_id in FEATURE_MINIMUM
        problems = []
        if feature["result"] != "pass":
            problems.append(f"feature_{feature['result']}:{feature_id}")
        else:
            minimum = max(LEVELS[spec["minimum_evidence"]], LEVELS[FEATURE_MINIMUM.get(feature_id, "source")])
            acceptable = False
            for proof in feature["evidence"]:
                if proof["source_sha256"] != candidate["source_sha256"] or proof["sha256"] != candidate["sha256"]:
                    problems.append(f"stale_evidence:{feature_id}")
                elif proof["result"] != "pass":
                    problems.append(f"evidence_{proof['result']}:{feature_id}")
                elif LEVELS[proof["kind"]] >= minimum:
                    acceptable = True
            if not acceptable:
                problems.append(f"insufficient_evidence:{feature_id}")
        (blockers if required else warnings).extend(problems)
    for feature_id in features.keys() - specs.keys():
        blockers.append(f"feature_not_in_baseline:{feature_id}")
    for name in required_tools - tools.keys():
        blockers.append(f"required_tool_missing:{name}")
    for name, tool in tools.items():
        required = tool["required"] or name in required_tools
        current = tool["state"] == "currently_verified"
        if current:
            current = _time(tool["verified_at"]) <= now <= _time(tool["valid_until"])
        if required and not current:
            blockers.append(f"required_tool_unverified:{name}")
        elif not required and not current:
            state = "verification_not_current" if tool["state"] == "currently_verified" else tool["state"]
            warnings.append(f"optional_tool_{state}:{name}")
    return {"schema": SCHEMA, "status": "BLOCKED" if blockers else "READY_FOR_RELEASE", "authorization": "none",
            "source_sha256": candidate["source_sha256"], "sha256": candidate["sha256"],
            "blockers": sorted(set(blockers)), "warnings": sorted(set(warnings))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        # Reject duplicate JSON keys before a decoder can silently overwrite evidence.
        def pairs(items: list[tuple[str, Any]]) -> dict:
            result = {}
            for key, value in items:
                _check(key not in result, "duplicate_json_key")
                result[key] = value
            return result
        _check(args.manifest.stat().st_size <= 2_000_000, "manifest_too_large")
        report = evaluate(json.loads(args.manifest.read_text(encoding="utf-8"), object_pairs_hook=pairs))
    except (OSError, ValueError, UnicodeError, RecursionError):
        report = {"schema": SCHEMA, "status": "INVALID_INPUT", "authorization": "none", "blockers": ["manifest_unreadable_or_invalid"], "warnings": []}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_RELEASE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
