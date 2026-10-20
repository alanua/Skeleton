from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema

from core.self_knowledge_resolver import NEEDS_VERIFICATION, RESOLVED, STALE, SUPERSEDED, SelfKnowledgeResolver
from core.topology_fact import TOPOLOGY_FACT_SCHEMA, TopologyFact


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _fact(**overrides: object) -> TopologyFact:
    values = {
        "schema": TOPOLOGY_FACT_SCHEMA,
        "fact_id": "topology:repository:skeleton",
        "fact_type": "repository",
        "lookup_key": "skeleton",
        "value_class": "PROJECT_ID",
        "value_ref": "repo:alanua/Skeleton",
        "source": "runner-contract",
        "provenance_ref": "receipt:runner:self-knowledge",
        "verified_revision": 1,
        "verified_at": NOW,
        "freshness_class": "CURRENT",
        "fresh_until": datetime(2026, 9, 21, tzinfo=timezone.utc),
        "authority": 10,
        "status": "VERIFIED",
        "roles": frozenset({"repository"}),
        "public_fingerprints": frozenset(),
        "supersedes": (),
        "superseded_by": None,
    }
    values.update(overrides)
    return TopologyFact(**values)


def test_fresh_fact_resolution_picks_newest_verified_current_fact() -> None:
    old = _fact(fact_id="topology:repository:skeleton:old", verified_revision=1, value_ref="repo:alanua/Skeleton-old")
    new = _fact(fact_id="topology:repository:skeleton:new", verified_revision=2)

    receipt = SelfKnowledgeResolver([old, new], now=NOW).resolve_repository("skeleton").public_receipt()

    assert receipt["status"] == RESOLVED
    assert receipt["resolved_ref"] == "repo:alanua/Skeleton"
    assert receipt["selected_fact_ref"] == "topology:repository:skeleton:new"
    assert receipt["freshness_class"] == "CURRENT"


def test_stale_facts_never_masquerade_as_current() -> None:
    stale = _fact(
        fact_id="topology:runtime:stale",
        fact_type="runtime",
        lookup_key="codex-runtime",
        value_class="RUNTIME_ID",
        value_ref="runtime:codex",
        verified_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        freshness_class="CURRENT",
        fresh_until=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    receipt = SelfKnowledgeResolver([stale], now=NOW).resolve_runtime("codex-runtime").public_receipt()

    assert receipt["status"] == STALE
    assert receipt["resolved_ref"] is None
    assert receipt["freshness_class"] == "STALE"
    assert receipt["stale_fact_refs"] == ["topology:runtime:stale"]


def test_conflicting_equal_authority_facts_fail_closed_as_needs_verification() -> None:
    first = _fact(fact_id="topology:host:a", fact_type="host", lookup_key="active-host", value_class="MACHINE_ID", value_ref="machine:alpha")
    second = _fact(fact_id="topology:host:b", fact_type="host", lookup_key="active-host", value_class="MACHINE_ID", value_ref="machine:beta")

    receipt = SelfKnowledgeResolver([first, second], now=NOW).resolve_host("active-host").public_receipt()

    assert receipt["status"] == NEEDS_VERIFICATION
    assert receipt["resolved_ref"] is None
    assert set(receipt["candidate_fact_refs"]) == {"topology:host:a", "topology:host:b"}


def test_superseded_aliases_remain_historical_but_cannot_route_new_actions() -> None:
    old = _fact(
        fact_id="topology:entrypoint:old",
        fact_type="entrypoint",
        lookup_key="skeleton-root",
        value_class="OPAQUE_PRIVATE_REF",
        value_ref="private-ref:workspace-root:old",
        superseded_by="topology:entrypoint:new",
    )
    new = _fact(
        fact_id="topology:entrypoint:new",
        fact_type="entrypoint",
        lookup_key="skeleton-root",
        value_class="OPAQUE_PRIVATE_REF",
        value_ref="private-ref:workspace-root:v2",
        verified_revision=2,
        supersedes=("topology:entrypoint:old",),
    )

    receipt = SelfKnowledgeResolver([old, new], now=NOW).resolve_entrypoint("skeleton-root").public_receipt()

    assert receipt["status"] == RESOLVED
    assert receipt["resolved_ref"] == "private-ref:workspace-root:v2"
    assert receipt["superseded_fact_refs"] == ["topology:entrypoint:old"]


def test_only_superseded_fact_fails_closed() -> None:
    old = _fact(fact_id="topology:host:old", fact_type="host", lookup_key="active-host", superseded_by="topology:host:new")

    receipt = SelfKnowledgeResolver([old], now=NOW).resolve_host("active-host").public_receipt()

    assert receipt["status"] == SUPERSEDED
    assert receipt["resolved_ref"] is None


def test_opaque_private_reference_is_preserved_without_value_leakage() -> None:
    fact = _fact(
        fact_id="topology:entrypoint:root",
        fact_type="entrypoint",
        lookup_key="skeleton-root",
        value_class="OPAQUE_PRIVATE_REF",
        value_ref="private-ref:workspace-root:v1",
        public_fingerprints=frozenset({"SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}),
    )

    receipt = SelfKnowledgeResolver([fact], now=NOW).resolve_entrypoint("skeleton-root").public_receipt()
    serialized = json.dumps(receipt, sort_keys=True).lower()

    assert receipt["status"] == RESOLVED
    assert receipt["resolved_ref"] == "private-ref:workspace-root:v1"
    assert "/home/" not in serialized
    assert "private-ref:workspace-root:v1" in serialized


def test_lookup_helpers_cover_host_repository_runtime_and_entrypoint() -> None:
    facts = [
        _fact(fact_id="topology:host:active", fact_type="host", lookup_key="active-host", value_class="MACHINE_ID", value_ref="machine:home-edge-01"),
        _fact(fact_id="topology:repository:skeleton", fact_type="repository", lookup_key="skeleton", value_class="PROJECT_ID", value_ref="repo:alanua/Skeleton"),
        _fact(fact_id="topology:runtime:codex", fact_type="runtime", lookup_key="codex", value_class="RUNTIME_ID", value_ref="runtime:codex"),
        _fact(fact_id="topology:entrypoint:root", fact_type="entrypoint", lookup_key="root", value_class="OPAQUE_PRIVATE_REF", value_ref="private-ref:workspace-root:v1"),
    ]
    resolver = SelfKnowledgeResolver(facts, now=NOW)

    assert resolver.resolve_host("active-host").public_receipt()["status"] == RESOLVED
    assert resolver.resolve_repository("skeleton").public_receipt()["status"] == RESOLVED
    assert resolver.resolve_runtime("codex").public_receipt()["status"] == RESOLVED
    assert resolver.resolve_entrypoint("root").public_receipt()["status"] == RESOLVED


def test_self_knowledge_receipt_schema_parse_and_validate() -> None:
    schema = json.loads((ROOT / "schemas/self_knowledge_receipt.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    receipt = SelfKnowledgeResolver([_fact()], now=NOW).resolve_repository("skeleton").public_receipt()

    validator.validate(receipt)
    invalid = dict(receipt)
    invalid["private_path"] = "PRIVATE_ROOT_VALUE"
    assert list(validator.iter_errors(invalid))
