"""In-memory checks only: never invoke a gateway or installer."""
import hashlib
from pathlib import Path
import subprocess
from dataclasses import replace

import pytest

from core import runner_controller_privileged_gateway as gateway
from core import runner_media_snapshot_signer_gateway as signer


@pytest.fixture
def evidence(monkeypatch):
    # Substitute test blobs only, preserving the reviewed paths and Git modes.
    artifacts = {}
    pins = []
    for path, mode, _ in signer.ARTIFACTS:
        content = (path + " synthetic\n").encode()
        blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        pins.append((path, mode, blob))
        artifacts[path] = signer.SyntheticArtifact(mode, content)
    monkeypatch.setattr(signer, "ARTIFACTS", tuple(pins))
    return dict(
        action_id=signer.ACTION_ID,
        operator_approval=signer.OPERATOR_APPROVAL,
        skeleton_main_sha=signer.SKELETON_MAIN_SHA,
        source_repository=signer.SOURCE_REPOSITORY,
        source_main_sha=signer.SOURCE_MAIN_SHA,
        source_clean=True,
        artifacts=artifacts,
    )


def check(evidence, reason):
    result = signer.validate_synthetic_action(**evidence)
    assert result["reason"] == reason
    assert result["status"] == "NEEDS_OPERATOR"
    assert result["activation_blocker"] == "gateway_bootstrap_required"
    assert result["synthetic_only"] is True
    for field in ("registered", "mutation_started", "mutation_performed",
                  "external_side_effects_executed"):
        assert result[field] is False


def test_matching_evidence_is_still_blocked(evidence, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("synthetic helper attempted IO or execution")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    check(evidence, "gateway_bootstrap_required")


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("action_id", "other", "action_not_registered"),
    ("operator_approval", "", "operator_approval_mismatch"),
    ("skeleton_main_sha", signer.SOURCE_MAIN_SHA, "skeleton_main_sha_mismatch"),
    ("source_repository", "alanua/Skeleton", "source_repository_mismatch"),
    ("source_main_sha", signer.SKELETON_MAIN_SHA, "source_main_sha_mismatch"),
    ("source_main_sha", "475df11fa38f910cc4f81a4dfbf535d04b7ad092",
     "source_main_sha_mismatch"),
    ("source_main_sha", "0" * 40, "source_main_sha_mismatch"),
    ("source_clean", False, "source_not_clean"),
    ("source_clean", 1, "source_not_clean"),
])
def test_authority_rejections(evidence, field, value, reason):
    evidence[field] = value
    check(evidence, reason)


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize(("change", "reason"), [
    ({"symlink": True}, "source_type_or_mode_mismatch"),
    ({"tree_mode": "120000"}, "source_type_or_mode_mismatch"),
    ({"tree_mode": "0600"}, "source_type_or_mode_mismatch"),
    ({"content": b"tampered"}, "source_blob_mismatch"),
    ({"content": b""}, "source_size_invalid"),
    ({"content": b"x" * (signer.MAX_ARTIFACT_BYTES + 1)}, "source_size_invalid"),
])
def test_artifact_rejections(evidence, index, change, reason):
    path = signer.ARTIFACTS[index][0]
    evidence["artifacts"][path] = replace(evidence["artifacts"][path], **change)
    check(evidence, reason)


def test_missing_or_extra_artifacts(evidence):
    path = signer.ARTIFACTS[0][0]
    evidence["artifacts"].pop(path)
    check(evidence, "source_artifact_set_mismatch")
    evidence["artifacts"]["arbitrary/path"] = signer.SyntheticArtifact("100755", b"x")
    check(evidence, "source_artifact_set_mismatch")


def test_no_source_access_before_authority(evidence):
    class Unreadable(dict):
        def __iter__(self):
            pytest.fail("source accessed before approval")
    evidence["artifacts"] = Unreadable()
    evidence["operator_approval"] = "unapproved"
    check(evidence, "operator_approval_mismatch")


def test_fixed_pins_and_blocked_gateway_registration():
    assert [mode for _, mode, _ in signer.ARTIFACTS] == [
        "100755", "100644", "100644", "100644",
    ]
    assert [blob for _, _, blob in signer.ARTIFACTS] == [
        "6d596a70dc010393c3d83a3626170017cb88ee02",
        "71bd0a237f4180fc52694a86c596fadf0b2ed687",
        "24620d9e9fe4f62c055113e6aeefb2d0984be2d5",
        "3e0a27bdb229db594813c2ea7bd8af035625e565",
    ]
    actions = gateway._embedded_action_registry()["actions"]
    assert [action["action_id"] for action in actions] == [
        gateway.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
        gateway.RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_TASK_ID,
        gateway.SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID,
        gateway.RUNNER_CONTROLLER_REFRESH_TRUST_ANCHOR_BUNDLE_TASK_ID,
        gateway.MEDIA_SNAPSHOT_SIGNER_ACTION_ID,
    ]
    assert gateway.load_action_registry()[signer.ACTION_ID].operator_approval == signer.OPERATOR_APPROVAL


@pytest.fixture
def pinned_checkout_evidence():
    # Optional read-only Git object access is confined to tests. Never run source.
    checkout = Path("/home/agent/agent-dev/repos/skeleton-media")
    source_sha = "3824df3b6de17ac259504b75c26c0d028642f9e4"
    if not checkout.is_dir():
        pytest.skip("fixed media checkout is unavailable")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(checkout), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout

    try:
        git("cat-file", "-e", source_sha + "^{commit}")
    except subprocess.CalledProcessError:
        pytest.skip("fixed media commit is unavailable")
    return git, source_sha


def test_independent_pinned_tree_and_blob_bytes(pinned_checkout_evidence):
    git, source_sha = pinned_checkout_evidence
    assert signer.SOURCE_MAIN_SHA == source_sha
    entries = {}
    for line in git("ls-tree", "-r", source_sha).decode().splitlines():
        metadata, path = line.split("\t", 1)
        mode, kind, blob = metadata.split()
        entries[path] = (mode, kind, blob)
    artifacts = {}
    for path, mode, blob in signer.ARTIFACTS:
        assert entries[path] == (mode, "blob", blob)
        content = git("cat-file", "blob", blob)
        assert hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest() == blob
        artifacts[path] = signer.SyntheticArtifact(mode, content)
    evidence = dict(
        action_id=signer.ACTION_ID,
        operator_approval=signer.OPERATOR_APPROVAL,
        skeleton_main_sha=signer.SKELETON_MAIN_SHA,
        source_repository=signer.SOURCE_REPOSITORY,
        source_main_sha=source_sha,
        source_clean=True,
        artifacts=artifacts,
    )
    check(evidence, "gateway_bootstrap_required")
    stale_blob = "b26493da46686a96d0edc5c79efb6ecba6a0045c"
    stale_content = git("cat-file", "blob", stale_blob)
    assert hashlib.sha1(
        f"blob {len(stale_content)}\0".encode() + stale_content
    ).hexdigest() == stale_blob
    path, mode, _ = signer.ARTIFACTS[0]
    artifacts[path] = signer.SyntheticArtifact(mode, stale_content)
    check(evidence, "source_blob_mismatch")
