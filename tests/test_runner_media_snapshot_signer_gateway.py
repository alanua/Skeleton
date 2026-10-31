"""In-memory checks only: never invoke a gateway or installer."""
import hashlib
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


def test_fixed_pins_and_legacy_registry_unchanged():
    assert [mode for _, mode, _ in signer.ARTIFACTS] == [
        "100755", "100644", "100644", "100644",
    ]
    assert [blob for _, _, blob in signer.ARTIFACTS] == [
        "b26493da46686a96d0edc5c79efb6ecba6a0045c",
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
    ]
    assert signer.ACTION_ID not in gateway.load_action_registry()
