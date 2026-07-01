from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = ROOT / "policies" / "RULE_ENFORCEMENT_REGISTRY.yaml"
DEFAULT_SCHEMA_PATH = ROOT / "schemas" / "rule_enforcement_registry.schema.json"

EFFECTIVE_ENFORCEMENTS = frozenset({"runtime_gate", "preflight_gate", "route_validation", "schema_validation"})
ACTION_APPROVAL_PREFIX = "exact_"
CONFIG_SUFFIXES = {".yaml", ".yml", ".json"}
PRIVATE_REPORT_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9_.-]+/"),
    re.compile(r"/(?:root|etc|var|mnt|media|run/secrets)(?:/|\\b)"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"\b[A-Za-z0-9_]*(?:token|secret|password|api[_-]?key|credential)[A-Za-z0-9_]*\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|10\.|172\.(?:1[6-9]|2[0-9]|3[0-1])\.|192\.168\.|[^/\s]*(?:private|internal|corp|vpn|customer|client|secret)[^/\s]*)\S*", re.IGNORECASE),
    re.compile(r"\b(?:private|customer|client|tenant|account|ticket|request)[_-]?(?:id|key|ref)\s*[:=]\s*[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\b(?:owner|next-stage goal|goal)\s*[:=]\s*[^`\n]*(?:/home/|@|Bearer\s+|api[_-]?key|secret|token|customer|client|tenant|private)", re.IGNORECASE),
    re.compile(r"SKELETON_TG_[A-Z0-9_]+"),
    re.compile(r"raw payload", re.IGNORECASE),
    re.compile(r"environment value", re.IGNORECASE),
)


@dataclass(frozen=True)
class RegistryValidationResult:
    errors: tuple[str, ...]
    lifecycle_counts: tuple[tuple[str, int], ...]
    enforcement_counts: tuple[tuple[str, int], ...]
    coverage_counts: tuple[tuple[str, int], ...]
    effective_enforcement_gaps: tuple[str, ...]
    duplicate_rule_ids: tuple[str, ...]
    duplicate_source_files: tuple[str, ...]
    contradictory_rules: tuple[tuple[str, str], ...]
    superseded_rules: tuple[str, ...]
    duplicated_sources: tuple[str, ...]
    contradictory_sources: tuple[str, ...]
    dead_sources: tuple[str, ...]
    needs_review_sources: tuple[str, ...]
    needs_review_rules: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_registry(path: Path | str = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("rule enforcement registry must be a mapping")
    return data


def load_schema(path: Path | str = DEFAULT_SCHEMA_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("rule enforcement registry schema must be a mapping")
    return data


def required_source_universe(root: Path | str = ROOT) -> tuple[str, ...]:
    root_path = Path(root)
    fixed = {
        "BOOT_MANIFEST.yaml",
        "COMMANDS.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "HELPER_REGISTRY.yaml",
        "PROVIDER_ROUTING.yaml",
        "MEMORY_ROUTING.yaml",
        "PROJECT_TREE.yaml",
        "core/action_gate.py",
        "core/audit_ledger.py",
        "core/gate_engine.py",
        "core/memory_manager.py",
        "core/merge_policy_checker.py",
        "core/project_tree.py",
        "scripts/runner_poll_github_tasks.py",
        "docs/ACTION_GATE.md",
        "docs/JEEVES_BRIDGE.md",
        "docs/RUNNER_MAINTENANCE_TASKS.md",
        "docs/RUNNER_QUEUE_STATUS.md",
    }
    discovered = {
        path.relative_to(root_path).as_posix()
        for pattern in ("policies/*.yaml", "schemas/*.json")
        for path in root_path.glob(pattern)
    }
    return tuple(sorted(fixed | discovered))


def validate_registry(
    registry: Mapping[str, Any] | None = None,
    *,
    root: Path | str = ROOT,
    schema: Mapping[str, Any] | None = None,
) -> RegistryValidationResult:
    root_path = Path(root)
    registry_source = load_registry(root_path / "policies" / "RULE_ENFORCEMENT_REGISTRY.yaml") if registry is None else registry
    schema_source = load_schema(root_path / "schemas" / "rule_enforcement_registry.schema.json") if schema is None else schema
    data = dict(registry_source)
    schema_data = dict(schema_source)

    errors: list[str] = []
    validator = Draft202012Validator(schema_data)
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")

    rules = _mapping_items(data.get("rules"))
    source_coverage = _mapping_items(data.get("source_coverage"))
    rule_ids = tuple(str(rule.get("rule_id", "")) for rule in rules)
    duplicate_ids = _duplicates(rule_ids)
    duplicate_sources = _duplicates(str(item.get("source_file", "")) for item in source_coverage)

    errors.extend(f"duplicate rule_id: {rule_id}" for rule_id in duplicate_ids)
    errors.extend(f"duplicate source_coverage entry: {source_file}" for source_file in duplicate_sources)
    errors.extend(_required_coverage_errors(root_path, source_coverage))
    errors.extend(_missing_file_errors(root_path, source_coverage, "source_coverage"))
    errors.extend(_missing_file_errors(root_path, rules, "rule"))
    errors.extend(_coverage_errors(rules, source_coverage))
    evidence_errors, evidence_rule_ids = _evidence_errors(root_path, rules)
    errors.extend(evidence_errors)
    errors.extend(_approval_scope_errors(rules))

    gaps = _effective_enforcement_gaps(rules, evidence_rule_ids)
    errors.extend(f"active rule lacks verifiable enforcement: {rule_id}" for rule_id in gaps)

    contradictions = _contradictory_active_rules(rules)
    errors.extend(f"contradictory active rules: {left} vs {right}" for left, right in contradictions)
    errors.extend(_supersession_reference_errors(rules))

    return RegistryValidationResult(
        errors=tuple(errors),
        lifecycle_counts=_sorted_counts(rule.get("lifecycle") for rule in rules),
        enforcement_counts=_sorted_counts(rule.get("enforcement") for rule in rules),
        coverage_counts=_sorted_counts(item.get("classification") for item in source_coverage),
        effective_enforcement_gaps=gaps,
        duplicate_rule_ids=duplicate_ids,
        duplicate_source_files=duplicate_sources,
        contradictory_rules=contradictions,
        superseded_rules=tuple(sorted(str(rule["rule_id"]) for rule in rules if rule.get("lifecycle") == "superseded")),
        duplicated_sources=_source_ids_by_classification(data, "duplicated"),
        contradictory_sources=_source_ids_by_classification(data, "contradictory"),
        dead_sources=_source_ids_by_classification(data, "dead"),
        needs_review_sources=_source_ids_by_classification(data, "needs_review"),
        needs_review_rules=tuple(sorted(str(rule["rule_id"]) for rule in rules if rule.get("lifecycle") == "needs_review")),
    )


def build_report(registry: Mapping[str, Any] | None = None, *, root: Path | str = ROOT) -> str:
    root_path = Path(root)
    data = dict(load_registry(root_path / "policies" / "RULE_ENFORCEMENT_REGISTRY.yaml") if registry is None else registry)
    result = validate_registry(data, root=root_path)
    rules = _mapping_items(data.get("rules"))

    lines = [
        "# Rule Enforcement Matrix",
        "",
        "Generated by `scripts/generate_rule_enforcement_report.py`. Do not hand-edit.",
        "",
        "Stage 0 is inventory-only: no merge, runtime activation, canon promotion, provider call, deployment, service restart, or Skeleton/Jeeves boundary change is enabled by this report.",
        "",
        "References: " + ", ".join(sorted(data.get("issue_refs", []), key=_issue_sort_key)),
        "",
        "## Counts",
        "",
        "Lifecycle counts:",
        "",
    ]
    lines.extend(_count_lines(result.lifecycle_counts))
    lines.extend(["", "Enforcement counts:", ""])
    lines.extend(_count_lines(result.enforcement_counts))
    lines.extend(["", "Source coverage counts:", ""])
    lines.extend(_count_lines(result.coverage_counts))
    lines.extend(["", "## Effective Enforcement Gaps", ""])
    if result.effective_enforcement_gaps:
        lines.extend(f"- `{value}`" for value in result.effective_enforcement_gaps)
    else:
        lines.append("- No verified gaps in the declared active inventory.")
    lines.extend(["", "## Source Review Lists", ""])
    lines.append("- duplicated_sources: " + _inline_values(result.duplicated_sources))
    lines.append("- contradictory_sources: " + _inline_values(result.contradictory_sources))
    lines.append("- dead_sources: " + _inline_values(result.dead_sources))
    lines.append("- needs_review_sources: " + _inline_values(result.needs_review_sources))
    lines.extend(["", "## Rule Review Lists", ""])
    lines.append("- duplicate_rule_ids: " + _inline_values(result.duplicate_rule_ids))
    lines.append("- duplicate_source_coverage_entries: " + _inline_values(result.duplicate_source_files))
    lines.append("- contradictory_active_rule_pairs: " + _inline_pairs(result.contradictory_rules))
    lines.append("- needs_review_rule_ids: " + _inline_values(result.needs_review_rules))
    lines.append("- superseded_rule_ids: " + _inline_values(result.superseded_rules))
    lines.extend(["", "## Rule Inventory", ""])
    for rule in sorted(rules, key=lambda item: str(item["rule_id"])):
        lines.append(
            "- `{rule_id}` lifecycle={lifecycle} enforcement={enforcement} owner={owner}".format(
                rule_id=rule["rule_id"],
                lifecycle=rule["lifecycle"],
                enforcement=rule["enforcement"],
                owner=rule["owner_component"],
            )
        )
    lines.extend(["", "## Next Stages", ""])
    for stage in sorted(_mapping_items(data.get("next_stages")), key=lambda item: str(item["stage"])):
        lines.append(f"- `{stage['stage']}`: {stage['goal']}")
    lines.extend(["", "## Validation", ""])
    lines.append("- status: ok" if result.ok else "- status: failed")
    if not result.ok:
        lines.append("- error_count: " + str(len(result.errors)))

    report = "\n".join(lines) + "\n"
    leaked = public_report_violations(report)
    if leaked:
        raise ValueError("generated rule enforcement report contains private or unsafe terms: " + ", ".join(leaked))
    return report


def public_report_violations(report: str) -> tuple[str, ...]:
    return tuple(pattern.pattern for pattern in PRIVATE_REPORT_PATTERNS if pattern.search(report))


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _duplicates(values: Iterable[str]) -> tuple[str, ...]:
    counts = Counter(values)
    return tuple(sorted(value for value, count in counts.items() if value and count > 1))


def _required_coverage_errors(root: Path, source_coverage: Iterable[Mapping[str, Any]]) -> list[str]:
    expected = set(required_source_universe(root))
    actual = {str(item.get("source_file")) for item in source_coverage if isinstance(item.get("source_file"), str)}
    errors = [f"required source lacks source_coverage entry: {source}" for source in sorted(expected - actual)]
    errors.extend(f"source_coverage entry outside required universe: {source}" for source in sorted(actual - expected))
    return errors


def _missing_file_errors(root: Path, items: Iterable[Mapping[str, Any]], label: str) -> list[str]:
    errors: list[str] = []
    for item in items:
        source_file = item.get("source_file")
        if isinstance(source_file, str) and not (root / source_file).exists():
            item_id = item.get("rule_id", source_file)
            errors.append(f"{label} references missing file for {item_id}: {source_file}")
    return errors


def _coverage_errors(rules: Iterable[Mapping[str, Any]], source_coverage: Iterable[Mapping[str, Any]]) -> list[str]:
    rule_sources = {rule.get("source_file") for rule in rules if isinstance(rule.get("source_file"), str)}
    covered = {item.get("source_file") for item in source_coverage}
    missing = sorted(
        str(rule.get("source_file"))
        for rule in rules
        if isinstance(rule.get("source_file"), str) and rule.get("source_file") not in covered
    )
    errors = [f"rule source lacks source_coverage entry: {source_file}" for source_file in missing]
    for item in source_coverage:
        source_file = item.get("source_file")
        if item.get("classification") != "covered" or source_file in rule_sources:
            continue
        rationale = item.get("non_rule_rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"covered source lacks linked rule or non_rule_rationale: {source_file}")
    return errors


def _evidence_errors(root: Path, rules: Iterable[Mapping[str, Any]]) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    evidence_rule_ids: set[str] = set()
    for rule in rules:
        rule_id = str(rule.get("rule_id", "<unknown>"))
        rule_errors: list[str] = []
        source_file = rule.get("source_file")
        source_locator = rule.get("source_locator")
        if isinstance(source_file, str) and isinstance(source_locator, str):
            rule_errors.extend(_locator_errors(root, source_file, source_locator, rule_id, "source_locator"))
        for field in ("test_evidence", "audit_evidence"):
            values = rule.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                if ":" in value:
                    path_text, locator = value.split(":", 1)
                    if field == "test_evidence":
                        rule_errors.extend(_test_callable_errors(root, path_text, locator, rule_id))
                    else:
                        rule_errors.extend(_locator_errors(root, path_text, locator, rule_id, field))
                elif field == "test_evidence":
                    rule_errors.append(f"{rule_id} test_evidence must reference a test callable: {value}")
                elif not (root / value).exists():
                    rule_errors.append(f"{rule_id} {field} references missing path: {value}")
        gate_entrypoint = str(rule.get("gate_entrypoint", "")).strip()
        if rule.get("enforcement") in EFFECTIVE_ENFORCEMENTS:
            if not gate_entrypoint:
                rule_errors.append(f"{rule_id} verifiable rule lacks gate_entrypoint")
            elif rule.get("enforcement") == "route_validation" and _is_passive_entrypoint(gate_entrypoint):
                rule_errors.append(f"{rule_id} route_validation lacks callable or route binding gate_entrypoint")
            elif ":" in gate_entrypoint:
                path_text, locator = gate_entrypoint.split(":", 1)
                rule_errors.extend(_locator_errors(root, path_text, locator, rule_id, "gate_entrypoint"))
            else:
                rule_errors.extend(_python_callable_errors(root, gate_entrypoint, rule_id))
            reason_tokens = rule.get("reason_tokens")
            if not isinstance(reason_tokens, list) or not reason_tokens:
                rule_errors.append(f"{rule_id} verifiable rule lacks stable reason token")
            if not isinstance(rule.get("test_evidence"), list) or not rule.get("test_evidence"):
                rule_errors.append(f"{rule_id} verifiable rule lacks test evidence")
            if not isinstance(rule.get("audit_evidence"), list) or not rule.get("audit_evidence"):
                rule_errors.append(f"{rule_id} verifiable rule lacks audit evidence")
        if not rule_errors:
            evidence_rule_ids.add(rule_id)
        errors.extend(rule_errors)
    return errors, evidence_rule_ids


def _test_callable_errors(root: Path, source_file: str, locator: str, rule_id: str) -> list[str]:
    path = root / source_file
    if not path.exists():
        return [f"{rule_id} test_evidence references missing path: {source_file}"]
    if path.suffix != ".py":
        return [f"{rule_id} test_evidence must reference a Python test callable: {source_file}:{locator}"]
    if not locator.startswith("test_") and ".test_" not in locator:
        return [f"{rule_id} test_evidence locator is not a test callable: {source_file}:{locator}"]
    return _python_member_errors(path, locator, rule_id, "test_evidence")


def _is_passive_entrypoint(entrypoint: str) -> bool:
    if ":" not in entrypoint:
        return entrypoint.endswith((".yaml", ".yml", ".json", ".md"))
    path_text, _locator = entrypoint.split(":", 1)
    return Path(path_text).suffix in CONFIG_SUFFIXES | {".md"}


def _locator_errors(root: Path, source_file: str, locator: str, rule_id: str, field: str) -> list[str]:
    path = root / source_file
    if not path.exists():
        return [f"{rule_id} {field} references missing path: {source_file}"]
    suffix = path.suffix
    if suffix == ".py":
        return _python_member_errors(path, locator, rule_id, field)
    if suffix in CONFIG_SUFFIXES:
        return [] if _config_locator_exists(path, locator) else [f"{rule_id} {field} locator not found: {source_file}:{locator}"]
    text = path.read_text(encoding="utf-8")
    return [] if locator in text else [f"{rule_id} {field} text not found: {source_file}:{locator}"]


def _python_callable_errors(root: Path, entrypoint: str, rule_id: str) -> list[str]:
    parts = entrypoint.split(".")
    if len(parts) < 3:
        return [f"{rule_id} gate_entrypoint is not module callable: {entrypoint}"]
    module_path = root.joinpath(*parts[:-1]).with_suffix(".py")
    member_parts = [parts[-1]]
    if not module_path.exists() and len(parts) >= 4:
        module_path = root.joinpath(*parts[:-2]).with_suffix(".py")
        member_parts = parts[-2:]
    if not module_path.exists():
        return [f"{rule_id} gate_entrypoint module not found: {entrypoint}"]
    return _python_member_errors(module_path, ".".join(member_parts), rule_id, "gate_entrypoint")


def _python_member_errors(path: Path, locator: str, rule_id: str, field: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = locator.split(".")
    if len(parts) == 1:
        found = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == parts[0] for node in tree.body)
    elif len(parts) == 2:
        found = False
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == parts[0]:
                found = any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == parts[1] for child in node.body)
                break
    else:
        found = False
    if found:
        return []
    relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
    return [f"{rule_id} {field} Python member not found: {relative}:{locator}"]


def _config_locator_exists(path: Path, locator: str) -> bool:
    data = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else yaml.safe_load(path.read_text(encoding="utf-8"))
    current: Any = data
    for part in locator.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return False
            current = current[part]
        elif isinstance(current, list):
            match = next((item for item in current if isinstance(item, Mapping) and item.get("id") == part), None)
            if match is None:
                return False
            current = match
        else:
            return False
    return True


def _effective_enforcement_gaps(rules: Iterable[Mapping[str, Any]], evidence_rule_ids: set[str]) -> tuple[str, ...]:
    gaps = []
    for rule in rules:
        if rule.get("lifecycle") != "active":
            continue
        rule_id = str(rule.get("rule_id"))
        if rule.get("enforcement") not in EFFECTIVE_ENFORCEMENTS or rule_id not in evidence_rule_ids:
            gaps.append(str(rule.get("rule_id")))
    return tuple(sorted(gaps))


def _approval_scope_errors(rules: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    for rule in rules:
        actions = rule.get("protected_actions")
        if not isinstance(actions, list) or not actions:
            continue
        approval_scope = rule.get("approval_scope")
        if not isinstance(approval_scope, str) or not approval_scope.startswith(ACTION_APPROVAL_PREFIX):
            errors.append(f"{rule.get('rule_id', '<unknown>')} protected action lacks exact approval_scope")
    return errors


def _contradictory_active_rules(rules: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    active = [rule for rule in rules if rule.get("lifecycle") == "active"]
    pairs: set[tuple[str, str]] = set()
    for index, left in enumerate(active):
        left_actions = _actions(left)
        if not left_actions:
            continue
        for right in active[index + 1:]:
            right_actions = _actions(right)
            if not left_actions.intersection(right_actions) or _related_by_supersession(left, right):
                continue
            if (
                left_actions != right_actions
                or left.get("approval_scope") != right.get("approval_scope")
                or left.get("owner_component") != right.get("owner_component")
                or left.get("enforcement") != right.get("enforcement")
                or left.get("gate_entrypoint") != right.get("gate_entrypoint")
                or set(left.get("reason_tokens", [])) != set(right.get("reason_tokens", []))
                or left.get("public_private_boundary") != right.get("public_private_boundary")
            ):
                pairs.add(tuple(sorted((str(left.get("rule_id")), str(right.get("rule_id"))))))
    return tuple(sorted(pairs))


def _actions(rule: Mapping[str, Any]) -> set[str]:
    return {str(action) for action in rule.get("protected_actions", []) if isinstance(action, str)}


def _related_by_supersession(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_id = str(left.get("rule_id"))
    right_id = str(right.get("rule_id"))
    left_related = set(left.get("supersedes", []) if isinstance(left.get("supersedes"), list) else [])
    left_related.update(left.get("superseded_by", []) if isinstance(left.get("superseded_by"), list) else [])
    right_related = set(right.get("supersedes", []) if isinstance(right.get("supersedes"), list) else [])
    right_related.update(right.get("superseded_by", []) if isinstance(right.get("superseded_by"), list) else [])
    return right_id in left_related or left_id in right_related


def _supersession_reference_errors(rules: Iterable[Mapping[str, Any]]) -> list[str]:
    rule_list = list(rules)
    ids = {str(rule.get("rule_id")) for rule in rule_list}
    errors: list[str] = []
    for rule in rule_list:
        rule_id = str(rule.get("rule_id", "<unknown>"))
        for field in ("supersedes", "superseded_by"):
            values = rule.get(field)
            if isinstance(values, list):
                errors.extend(f"{rule_id} {field} unknown rule_id: {value}" for value in values if value not in ids)
    return errors


def _sorted_counts(values: Iterable[object]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(str(value) for value in values if value is not None).items()))


def _count_lines(counts: Iterable[tuple[str, int]]) -> list[str]:
    return [f"- {key}: {value}" for key, value in counts]


def _id_lines(values: Iterable[str]) -> list[str]:
    value_tuple = tuple(values)
    return ["- none"] if not value_tuple else [f"- `{value}`" for value in value_tuple]


def _inline_values(values: Iterable[str]) -> str:
    value_tuple = tuple(values)
    return "none" if not value_tuple else ", ".join(f"`{value}`" for value in value_tuple)


def _inline_pairs(values: Iterable[tuple[str, str]]) -> str:
    value_tuple = tuple(values)
    return "none" if not value_tuple else ", ".join(f"`{left}`/`{right}`" for left, right in value_tuple)


def _source_ids_by_classification(data: Mapping[str, Any], classification: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(item["source_file"])
            for item in _mapping_items(data.get("source_coverage"))
            if item.get("classification") == classification
        )
    )


def _issue_sort_key(value: object) -> int:
    text = str(value).lstrip("#")
    return int(text) if text.isdigit() else 0
