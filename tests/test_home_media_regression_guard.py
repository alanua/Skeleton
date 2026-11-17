from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import tempfile
import unittest

from core.home_media_regression_guard import FEATURE_MINIMUM, SCHEMA, evaluate, evaluate_bound_candidate

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def manifest():
    baseline = dict(version_name="1.0.0", version_code=1, source_sha256="a" * 64,
                    source_commit=None, sha256="b" * 64, signer_sha256="c" * 64,
                    application_id="com.skeleton.home", build_scope="production")
    candidate = dict(baseline, version_name="1.0.1", version_code=2,
                     source_sha256="d" * 64, sha256="e" * 64, build_scope="production_candidate")
    return dict(schema=SCHEMA, baseline=baseline, candidate=candidate,
                required_tools=["git"],
                baseline_features=[dict(id=f, required=True, minimum_evidence=k) for f, k in FEATURE_MINIMUM.items()],
                features=[dict(id=f, result="pass", reason="Synthetic fixture only", evidence=[
                    dict(report_id=f, report_sha256="f" * 64, kind="device", result="pass",
                         source_sha256=candidate["source_sha256"], sha256=candidate["sha256"])]) for f in FEATURE_MINIMUM],
                tools=[dict(name="git", required=True, state="currently_verified", version="fixture-1",
                            provenance="synthetic-fixture", verified_at="2026-11-12T11:00:00Z",
                            valid_until="2026-11-12T13:00:00Z", reason="Synthetic fixture only")])


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.doc = manifest()

    def review(self):
        return evaluate(self.doc, now=NOW)

    def cast(self):
        return next(f for f in self.doc["features"] if f["id"] == "incoming_youtube_cast")


    def test_all_accumulated_regression_contracts_are_mandatory(self):
        cases=['youtube_current_metadata','games_controls','foreground_handoff','hls_recovery',
               'boot_state_recovery','responsive_remote_geometry','provider_release_monitor',
               'display_cadence','hyperhdr_stability','provider_source_identity']
        for feature_id in cases:
            with self.subTest(feature=feature_id):
                doc=manifest()
                doc['baseline_features']=[s for s in doc['baseline_features'] if s['id']!=feature_id]
                self.assertIn('required_baseline_feature_missing:'+feature_id,evaluate(doc,now=NOW)['blockers'])

    def test_atomic_writer_unit_tests_are_not_a_boot_acceptance_pass(self):
        f=next(f for f in self.doc['features'] if f['id']=='boot_state_recovery')
        f['evidence'][0]['kind']='unit'
        self.assertIn('insufficient_evidence:boot_state_recovery',self.review()['blockers'])

    def test_title_source_checks_are_not_real_current_metadata_acceptance(self):
        f=next(f for f in self.doc['features'] if f['id']=='youtube_current_metadata')
        f['evidence'][0]['kind']='unit'
        self.assertIn('insufficient_evidence:youtube_current_metadata',self.review()['blockers'])


    def test_current_artifact_bindings_are_required(self):
        report=evaluate_bound_candidate(self.doc, observed_baseline=copy.deepcopy(self.doc["baseline"]),
                                        observed_candidate=copy.deepcopy(self.doc["candidate"]),now=NOW)
        self.assertEqual(report["status"],"READY_FOR_RELEASE")
        self.assertEqual(report["authorization"],"none")

    def test_actual_backend_or_apk_drift_blocks_a_self_consistent_old_manifest(self):
        for key in ["source_sha256","sha256","signer_sha256"]:
            with self.subTest(field=key):
                observed=copy.deepcopy(self.doc["candidate"]);observed[key]="0"*64
                report=evaluate_bound_candidate(self.doc,observed_baseline=self.doc["baseline"],
                                                observed_candidate=observed,now=NOW)
                self.assertIn("observed_candidate_"+key+"_mismatch",report["blockers"])

    def test_wrong_observed_baseline_is_not_accepted(self):
        observed=copy.deepcopy(self.doc["baseline"]);observed["sha256"]="0"*64
        result=evaluate_bound_candidate(self.doc,observed_baseline=observed,
                                        observed_candidate=self.doc["candidate"],now=NOW)
        self.assertIn("observed_baseline_sha256_mismatch",result["blockers"])

    def test_unknown_observed_provenance_cannot_be_ready(self):
        observed=copy.deepcopy(self.doc["candidate"]);observed["source_sha256"]=None
        result=evaluate_bound_candidate(self.doc,observed_baseline=self.doc["baseline"],
                                        observed_candidate=observed,now=NOW)
        self.assertIn("observed_candidate_source_sha256_unknown",result["blockers"])

    def test_invalid_observed_input_is_sanitized(self):
        result=evaluate_bound_candidate(self.doc,observed_baseline={"PRIVATE_SENTINEL":"value"},
                                        observed_candidate=self.doc["candidate"],now=NOW)
        self.assertEqual(result["status"],"INVALID_INPUT")
        self.assertNotIn("PRIVATE_SENTINEL",json.dumps(result))

    def test_new_version_claim_must_match_actual_artifact(self):
        observed=copy.deepcopy(self.doc["candidate"]);observed["version_code"]+=1
        result=evaluate_bound_candidate(self.doc,observed_baseline=self.doc["baseline"],
                                        observed_candidate=observed,now=NOW)
        self.assertIn("observed_candidate_version_code_mismatch",result["blockers"])

    def test_complete_evidence_grants_no_authority_or_input_mutation(self):
        before = copy.deepcopy(self.doc)
        result = self.review()
        self.assertEqual(result["status"], "READY_FOR_RELEASE")
        self.assertEqual(result["authorization"], "none")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(self.doc, before)

    def test_feature_cannot_disappear_from_candidate_or_baseline(self):
        self.doc["features"] = [f for f in self.doc["features"] if f["id"] != "episode_arrows"]
        self.assertIn("baseline_feature_dropped:episode_arrows", self.review()["blockers"])
        self.doc["baseline_features"] = [f for f in self.doc["baseline_features"] if f["id"] != "episode_arrows"]
        self.assertIn("required_baseline_feature_missing:episode_arrows", self.review()["blockers"])

    def test_required_feature_must_pass(self):
        for state in ["fail", "unverified", "not_applicable"]:
            with self.subTest(state=state):
                self.cast()["result"] = state
                self.assertEqual(self.review()["status"], "BLOCKED")

    def test_evidence_for_another_artifact_is_stale(self):
        for field in ["source_sha256", "sha256"]:
            with self.subTest(field=field):
                self.doc = manifest()
                self.cast()["evidence"][0][field] = "0" * 64
                self.assertIn("stale_evidence:incoming_youtube_cast", self.review()["blockers"])

    def test_unit_checks_cannot_replace_real_cast_even_if_scope_is_downgraded(self):
        for f in self.doc["features"]:
            f["evidence"][0]["kind"] = "unit"
        self.assertIn("insufficient_evidence:incoming_youtube_cast", self.review()["blockers"])
        for spec in self.doc["baseline_features"]:
            spec.update(required=False, minimum_evidence="source")
        self.assertIn("insufficient_evidence:incoming_youtube_cast", self.review()["blockers"])

    def test_failed_check_not_hidden_by_another_passing_report(self):
        f = self.cast()
        f["evidence"].append(dict(f["evidence"][0], report_id="failed-report", result="fail"))
        self.assertIn("evidence_fail:incoming_youtube_cast", self.review()["blockers"])

    def test_unknown_provenance_blocks_readiness(self):
        for field in ["source_sha256", "sha256", "signer_sha256"]:
            with self.subTest(field=field):
                self.doc = manifest()
                self.doc["candidate"][field] = None
                self.assertIn(f"candidate_{field}_unknown", self.review()["blockers"])

    def test_new_apk_requires_new_version_and_same_signer(self):
        self.doc["candidate"].update(version_code=1, signer_sha256="0" * 64)
        self.assertTrue({"version_code_not_increased", "signing_lineage_changed"} <= set(self.review()["blockers"]))

    def test_debug_or_preview_cannot_be_release_ready(self):
        for change in [{"build_scope": "debug"}, {"application_id": "com.skeleton.home.preview"}]:
            with self.subTest(change=change):
                self.doc = manifest()
                self.doc["candidate"].update(change)
                self.assertEqual(self.review()["status"], "BLOCKED")

    def test_required_tool_cannot_be_dropped(self):
        self.doc["tools"].clear()
        self.assertIn("required_tool_missing:git", self.review()["blockers"])

    def test_required_tool_cannot_be_marked_optional_or_historical(self):
        for state in ["historically_verified", "unavailable", "not_required"]:
            with self.subTest(state=state):
                self.doc["tools"][0].update(state=state, required=False)
                self.assertIn("required_tool_unverified:git", self.review()["blockers"])

    def test_expired_or_future_verification_is_not_current(self):
        self.doc["tools"][0]["valid_until"] = "2026-11-12T11:30:00Z"
        self.assertIn("required_tool_unverified:git", self.review()["blockers"])
        self.doc["tools"][0].update(valid_until="2026-11-12T14:00:00Z", verified_at="2026-11-12T13:00:00Z")
        self.assertIn("required_tool_unverified:git", self.review()["blockers"])

    def test_optional_unnecessary_binary_tool_is_visible_without_blocking(self):
        self.doc["tools"].append(dict(name="bindiff", required=False, state="not_required", version=None,
                                    provenance=None, verified_at=None, valid_until=None,
                                    reason="Source review does not compare closed binaries"))
        result = self.review()
        self.assertEqual(result["status"], "READY_FOR_RELEASE")
        self.assertEqual(result["warnings"], ["optional_tool_not_required:bindiff"])

    def test_duplicate_identities_invalid(self):
        for where in ["features", "baseline_features", "tools"]:
            with self.subTest(where=where):
                self.doc = manifest()
                self.doc[where].append(copy.deepcopy(self.doc[where][0]))
                self.assertEqual(self.review()["status"], "INVALID_INPUT")

    def test_unexpected_private_fields_not_echoed(self):
        self.doc["candidate"]["private_url"] = "PRIVATE_SENTINEL_DO_NOT_ECHO"
        result = self.review()
        self.assertEqual(result["status"], "INVALID_INPUT")
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_strict_version_code(self):
        for value in [True, 1.0, "2", 0]:
            with self.subTest(value=value):
                self.doc["candidate"]["version_code"] = value
                self.assertEqual(self.review()["status"], "INVALID_INPUT")

    def test_invalid_timestamp_and_duplicate_evidence_are_invalid(self):
        self.doc["tools"][0]["verified_at"] = "2026-11-12T11:00:00"
        self.assertEqual(self.review()["status"], "INVALID_INPUT")
        self.doc = manifest()
        f = self.cast()
        f["evidence"].append(copy.deepcopy(f["evidence"][0]))
        self.assertEqual(self.review()["status"], "INVALID_INPUT")

    def test_unverified_optional_feature_stays_visible(self):
        self.doc["baseline_features"].append(dict(id="extra_feature", required=False, minimum_evidence="unit"))
        self.doc["features"].append(dict(id="extra_feature", result="not_applicable", evidence=[], reason="Optional fixture"))
        self.assertEqual(self.review()["status"], "READY_FOR_RELEASE")
        self.assertIn("feature_not_applicable:extra_feature", self.review()["warnings"])

    def test_cli_duplicate_json_does_not_echo_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"schema":"PRIVATE_SENTINEL","schema":"duplicate"}')
            script = Path(__file__).parents[1] / "core/home_media_regression_guard.py"
            result = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "INVALID_INPUT")
            self.assertNotIn("PRIVATE_SENTINEL", result.stdout + result.stderr)

    def test_passing_feature_without_evidence_is_blocked(self):
        self.cast()["evidence"].clear()
        self.assertIn("insufficient_evidence:incoming_youtube_cast", self.review()["blockers"])

    def test_expired_optional_tool_is_not_reported_as_current(self):
        self.doc["required_tools"].clear()
        self.doc["tools"][0].update(required=False, valid_until="2026-11-12T11:30:00Z")
        result = self.review()
        self.assertEqual(result["status"], "READY_FOR_RELEASE")
        self.assertEqual(result["warnings"], ["optional_tool_verification_not_current:git"])

    def test_unhashable_malformed_json_value_returns_invalid_not_traceback(self):
        self.doc["candidate"]["build_scope"] = {"malformed": True}
        self.assertEqual(self.review()["status"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
