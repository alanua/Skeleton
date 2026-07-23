from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from core.memory_bootstrap import (
    PRIVATE_CONTEXT_ENV,
    _specific_strings,
    build_private_context_payload,
    private_echo_detected,
    retained_memory_bootstrap,
    safe_reason,
    write_private_context_payload,
)
from scripts import runner_poll_github_tasks as runner


HASH = "0" * 64


class FakeGateway:
    def __init__(self) -> None:
        self.exact_reads = 0

    def lookup_exact(self, *, namespace: str, project_id: str, key: str) -> dict[str, object]:
        self.exact_reads += 1
        return {
            "payload": {
                "namespace": namespace,
                "project_id": project_id,
                "canonical_ref": "canon-aufmass-aufmass-primary",
                "canonical_revision": 3,
                "value_hash": HASH,
                "provenance_refs": [
                    {"ref": "exact-aufmass-aufmass-primary", "kind": "exact_source", "evidence_hash": HASH}
                ],
            }
        }


class FakeCognee:
    def __init__(self, *, revision: int = 3, project_id: str = "aufmass", content_hash: str = HASH) -> None:
        self.revision = revision
        self.project_id = project_id
        self.content_hash = content_hash

    def health(self, *, project_id: str, dataset_id: str, current_canonical_revision: int) -> dict[str, object]:
        return {
            "status": "READY",
            "canonical_revisions": {
                "indexed_canonical_revision": self.revision,
                "current_canonical_revision": current_canonical_revision,
            },
        }

    def recall(self, request: dict[str, object]) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "dataset_id": request["dataset_id"],
            "current_canonical_revision": request["current_canonical_revision"],
            "indexed_canonical_revision": self.revision,
            "results": [
                {
                    "canonical_ref": "canon-aufmass-aufmass-primary",
                    "canonical_revision": self.revision,
                    "content_hash": self.content_hash,
                    "projection_text_hash": "1" * 64,
                    "metadata": {"project_id": self.project_id, "dataset_id": request["dataset_id"]},
                    "source_attribution": [
                        {"canonical_ref": "canon-aufmass-aufmass-primary", "value_hash": self.content_hash}
                    ],
                    "provenance_refs": [
                        {"ref": "exact-aufmass-aufmass-primary", "evidence_hash": self.content_hash}
                    ],
                }
            ],
        }


class FakeMemPalace:
    def search_semantic(self, **_: object) -> dict[str, object]:
        return {
            "project_id": "aufmass",
            "current_canonical_revision": 3,
            "indexed_canonical_revision": 3,
            "results": [
                {
                    "canonical_ref": "canon-aufmass-aufmass-primary",
                    "canonical_revision": 3,
                    "content_hash": HASH,
                    "source_attribution": [
                        {"canonical_ref": "canon-aufmass-aufmass-primary", "value_hash": HASH}
                    ],
                    "provenance_refs": [{"ref": "exact-aufmass-aufmass-primary", "evidence_hash": HASH}],
                }
            ],
        }


class FakeGraphify:
    def query_code(self, **_: object) -> dict[str, object]:
        return {
            "project_id": "aufmass",
            "current_canonical_revision": 3,
            "indexed_canonical_revision": 3,
            "results": [
                {
                    "canonical_ref": "canon-aufmass-aufmass-primary",
                    "canonical_revision": 3,
                    "content_hash": HASH,
                    "source_attribution": [
                        {"canonical_ref": "canon-aufmass-aufmass-primary", "value_hash": HASH}
                    ],
                    "provenance_refs": [{"ref": "exact-aufmass-aufmass-primary", "evidence_hash": HASH}],
                }
            ],
        }


def test_retained_factory_injects_one_cognee_adapter_and_reuses_identical_config() -> None:
    first = retained_memory_bootstrap({"root": "/synthetic/private"}, current_canonical_revision=3)
    second = retained_memory_bootstrap({"root": "/synthetic/private"}, current_canonical_revision=3)

    assert first is second
    assert first.primary_semantic_layer == "cognee"
    assert len(first.semantic_candidates) == 1
    assert first.semantic_candidates[0].__class__.__name__ == "CogneeProjectionAdapter"


def test_fresh_cognee_selected_first_and_written_inside_0600_payload(tmp_path: Path) -> None:
    payload = build_private_context_payload(
        gateway=FakeGateway(),
        project_id="aufmass",
        dataset_id="aufmass",
        query="primary",
        canonical_keys=("primary_fact",),
        current_canonical_revision=3,
        cognee_adapter=FakeCognee(),
        mempalace_adapter=FakeMemPalace(),
        mempalace_status={"state": "READY", "indexed_canonical_revision": 3, "current_canonical_revision": 3},
    )
    path = write_private_context_payload(payload, tmp_path)

    assert path.stat().st_mode & 0o777 == 0o600
    assert payload["selected_context"]["semantic_layer"]["layer"] == "cognee"
    assert "semantic_layers" not in payload
    assert json.loads(path.read_text(encoding="utf-8"))["selected_context"]["semantic_layer"]["layer"] == "cognee"


def test_bad_cognee_rejected_and_fresh_mempalace_fallback_selected() -> None:
    payload = build_private_context_payload(
        gateway=FakeGateway(),
        project_id="aufmass",
        dataset_id="aufmass",
        query="primary",
        canonical_keys=("primary_fact",),
        current_canonical_revision=3,
        cognee_adapter=FakeCognee(content_hash="2" * 64),
        mempalace_adapter=FakeMemPalace(),
        mempalace_status={"state": "READY", "indexed_canonical_revision": 3, "current_canonical_revision": 3},
    )

    assert payload["selected_context"]["semantic_layer"]["layer"] == "mempalace"


def test_stale_mempalace_omitted() -> None:
    payload = build_private_context_payload(
        gateway=FakeGateway(),
        project_id="aufmass",
        dataset_id="aufmass",
        query="primary",
        canonical_keys=("primary_fact",),
        current_canonical_revision=3,
        mempalace_adapter=FakeMemPalace(),
        mempalace_status={"state": "READY", "indexed_canonical_revision": 2, "current_canonical_revision": 3},
    )

    assert payload["selected_context"]["semantic_layer"] is None


def test_graphify_fresh_included_independently_and_stale_omitted() -> None:
    fresh = build_private_context_payload(
        gateway=FakeGateway(),
        project_id="aufmass",
        dataset_id="aufmass",
        query="primary",
        canonical_keys=("primary_fact",),
        current_canonical_revision=3,
        graphify_adapter=FakeGraphify(),
        graphify_status={"state": "READY", "indexed_canonical_revision": 3, "current_canonical_revision": 3},
    )
    stale = build_private_context_payload(
        gateway=FakeGateway(),
        project_id="aufmass",
        dataset_id="aufmass",
        query="primary",
        canonical_keys=("primary_fact",),
        current_canonical_revision=3,
        graphify_adapter=FakeGraphify(),
        graphify_status={"state": "READY", "indexed_canonical_revision": 2, "current_canonical_revision": 3},
    )

    assert fresh["selected_context"]["graph_layer"]["layer"] == "graphify"
    assert stale["selected_context"]["graph_layer"] is None


def test_executor_receives_task_once_by_stdin_and_private_context_by_env(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_run_command(args, cwd=None, *, timeout=None, input_text=None):
        calls.append((args, cwd, timeout, input_text, dict(runner._RUN_COMMAND_ENV_OVERRIDE.get() or {})))
        return 0, "DONE"

    monkeypatch.setattr(runner, "run_command", fake_run_command)
    code, _output = runner.run_codex_task("Do exact task", str(tmp_path), runner.RunnerTask(content="Do exact task"))

    assert code == 0
    args, _cwd, _timeout, input_text, env = calls[0]
    assert "Do exact task" not in args
    assert input_text.count("Do exact task") == 1
    assert PRIVATE_CONTEXT_ENV in env
    context_path = Path(env[PRIVATE_CONTEXT_ENV])
    assert context_path.stat().st_mode & 0o777 == stat.S_IRUSR | stat.S_IWUSR
    assert "canonical_exact_records" in context_path.read_text(encoding="utf-8")


def test_distinctive_alphabetic_private_echo_blocked_but_generic_summary_allowed() -> None:
    private_values = {"customer": {"name": "Ariadne Montgomery", "note": "summary"}}

    assert "Ariadne Montgomery" in _specific_strings(private_values)
    assert private_echo_detected("Report for Ariadne Montgomery", private_values)
    assert not private_echo_detected("The summary is complete", private_values)


def test_unknown_internal_reason_maps_to_generic_allowlisted_public_code() -> None:
    assert safe_reason("COGNEE_BACKEND_STACKTRACE") == "internal_error"
    assert safe_reason("ok") == "ok"
