from __future__ import annotations

from core.change_classifier import ChangeClassifier, ChangeDecision


def test_safe_for_docs_tests_and_readme_changes() -> None:
    decision = ChangeClassifier().check(
        {
            "files": [
                "README.md",
                "docs/RUNNER_QUEUE_STATUS.md",
                "tests/test_change_classifier.py",
            ]
        }
    )

    assert decision is ChangeDecision.SAFE
    assert decision.value == "SAFE"


def test_review_for_ordinary_code_changes() -> None:
    decision = ChangeClassifier().check({"files": ["core/change_classifier.py"]})

    assert decision is ChangeDecision.REVIEW


def test_review_when_safe_paths_require_human_review() -> None:
    decision = ChangeClassifier().check(
        {
            "files": ["docs/ACTION_GATE.md"],
            "requires_review": True,
        }
    )

    assert decision is ChangeDecision.REVIEW


def test_stop_for_runtime_secret_or_network_signals() -> None:
    classifier = ChangeClassifier()

    assert classifier.check({"files": ["scripts/runner_poll_github_tasks.py"]}) is ChangeDecision.STOP
    assert classifier.check({"files": [".env.production"]}) is ChangeDecision.STOP
    assert classifier.check({"files": ["docs/NETWORK.md"], "network_change": True}) is ChangeDecision.STOP


def test_blocked_for_malformed_change_data() -> None:
    classifier = ChangeClassifier()

    assert classifier.check({}) is ChangeDecision.BLOCKED
    assert classifier.check({"files": "README.md"}) is ChangeDecision.BLOCKED
    assert classifier.check({"files": ["README.md", 7]}) is ChangeDecision.BLOCKED


def test_blocked_for_unsafe_paths() -> None:
    classifier = ChangeClassifier()

    assert classifier.check({"files": ["/tmp/file.txt"]}) is ChangeDecision.BLOCKED
    assert classifier.check({"files": ["../outside.txt"]}) is ChangeDecision.BLOCKED
    assert classifier.check({"files": ["docs\\ACTION_GATE.md"]}) is ChangeDecision.BLOCKED
