#!/usr/bin/env bash
set -euo pipefail

REPO=/home/agent/agent-dev/repos/Skeleton
WT=/home/agent/agent-dev/worktrees/openhands-explicit-route-v1
TARGET_BRANCH=runner/temporary-explicit-openhands-route-v1
EXPECTED_MAIN=21e023ae92a78477231ff9b9139980e1e814ce15
EXPECTED_TARGET_HEAD=bbbd15172644749835a7b1bb7c738e7764ebd100

cd "$REPO"

echo "=== SAFETY ==="
test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$EXPECTED_MAIN"
test -z "$(git status --porcelain --untracked-files=all)"
git fetch origin main "$TARGET_BRANCH"
test "$(git rev-parse origin/main)" = "$EXPECTED_MAIN"
test "$(git rev-parse origin/$TARGET_BRANCH)" = "$EXPECTED_TARGET_HEAD"
test -d "$WT"
test "$(git -C "$WT" rev-parse HEAD)" = "$EXPECTED_TARGET_HEAD"

BEFORE_CHANGED="$(git -C "$WT" status --porcelain --untracked-files=all | sed 's/^...//' | sort -u)"
for f in $BEFORE_CHANGED; do
  case "$f" in
    MODEL_REGISTRY.yaml|\
    core/runner_codegen_router.py|\
    scripts/runner_poll_github_tasks.py|\
    tests/test_model_registry.py|\
    tests/test_model_selector.py|\
    tests/test_runner_codegen_router.py|\
    tests/test_runner_poll_github_tasks.py)
      ;;
    *)
      echo "BLOCKED: unexpected pre-existing file $f"
      exit 1
      ;;
  esac
done

cd "$WT"

python3 - <<'PY'
from pathlib import Path

path = Path("tests/test_execution_fabric.py")
text = path.read_text(encoding="utf-8")
old = '''def test_kimi_eligible_is_evaluation_only_not_production() -> None:\n    executors, models = registries()\n    prod = build_execution_bindings(profile(), executors, models, production=True)\n    evaluation = build_execution_bindings(profile(), executors, models, production=False)\n    assert all(binding.model_id != "openrouter-kimi-k2-challenger" for binding in prod)\n    assert any(binding.model_id == "openrouter-kimi-k2-challenger" for binding in evaluation)\n    assert any(binding.model_binding_kind == "EMBEDDED_MODEL" and binding.executor_id == "codex-embedded" for binding in prod)\n'''
new = '''def test_kimi_live_is_production_eligible_for_openhands() -> None:\n    executors, models = registries()\n    prod = build_execution_bindings(profile(), executors, models, production=True)\n    evaluation = build_execution_bindings(profile(), executors, models, production=False)\n    assert any(\n        binding.executor_id == "openhands-external"\n        and binding.model_binding_kind == "EXTERNAL_MODEL"\n        and binding.model_id == "openrouter-kimi-k2-challenger"\n        for binding in prod\n    )\n    assert any(\n        binding.executor_id == "openhands-external"\n        and binding.model_id == "openrouter-kimi-k2-challenger"\n        for binding in evaluation\n    )\n    assert any(\n        binding.model_binding_kind == "EMBEDDED_MODEL"\n        and binding.executor_id == "codex-embedded"\n        for binding in prod\n    )\n'''
if text.count(old) != 1:
    raise SystemExit("BLOCKED: stale Kimi production test anchor mismatch")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

echo "=== ALLOWLIST ==="
CHANGED="$(git diff --name-only origin/main...HEAD; git diff --name-only)"
CHANGED="$(printf '%s\n' "$CHANGED" | sed '/^$/d' | sort -u)"
printf '%s\n' "$CHANGED"
for f in $CHANGED; do
  case "$f" in
    MODEL_REGISTRY.yaml|\
    core/runner_codegen_router.py|\
    scripts/runner_poll_github_tasks.py|\
    tests/test_execution_fabric.py|\
    tests/test_model_registry.py|\
    tests/test_model_selector.py|\
    tests/test_runner_codegen_router.py|\
    tests/test_runner_poll_github_tasks.py)
      ;;
    *)
      echo "BLOCKED: unexpected file $f"
      exit 1
      ;;
  esac
done

echo "=== FOCUSED ==="
python3 -m pytest -q \
  tests/test_runner_codegen_router.py \
  tests/test_execution_fabric.py \
  tests/test_model_registry.py \
  tests/test_model_selector.py
python3 -m pytest -q tests/test_runner_poll_github_tasks.py

echo "=== STATIC ==="
python3 -m py_compile \
  core/runner_codegen_router.py \
  scripts/runner_poll_github_tasks.py \
  tests/test_execution_fabric.py \
  tests/test_runner_codegen_router.py \
  tests/test_model_registry.py \
  tests/test_model_selector.py \
  tests/test_runner_poll_github_tasks.py
python3 - <<'PY'
import json
for path in ("MODEL_REGISTRY.yaml", "EXECUTOR_REGISTRY.yaml"):
    with open(path, encoding="utf-8") as handle:
        json.load(handle)
print("REGISTRIES=PASS")
PY
git diff --check

echo "=== FULL ==="
python3 -m pytest -q

echo "=== COMMIT ==="
git add \
  MODEL_REGISTRY.yaml \
  core/runner_codegen_router.py \
  scripts/runner_poll_github_tasks.py \
  tests/test_execution_fabric.py \
  tests/test_model_registry.py \
  tests/test_model_selector.py \
  tests/test_runner_codegen_router.py \
  tests/test_runner_poll_github_tasks.py

git diff --cached --check
git commit -m "P0 wire explicit OpenHands task-fit secondary route"
NEW_HEAD="$(git rev-parse HEAD)"

git diff --check origin/main...HEAD

echo "=== PUSH SAME BRANCH ==="
git push \
  --force-with-lease="refs/heads/$TARGET_BRANCH:$EXPECTED_TARGET_HEAD" \
  origin "HEAD:refs/heads/$TARGET_BRANCH"

cd "$REPO"
git worktree remove "$WT"

test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$EXPECTED_MAIN"
test -z "$(git status --porcelain --untracked-files=all)"

echo "RESULT=DONE"
echo "HEAD=$NEW_HEAD"
echo "CANONICAL_RUNTIME_HEAD=$(git rev-parse HEAD)"
