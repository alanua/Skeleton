#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="agent@49.12.76.236"
REPO="alanua/Skeleton"
ISSUE="2998"
MARKER="<!-- skeleton-queue-selector-diag-v3 -->"

ssh "$SSH_TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
REPO="alanua/Skeleton"
ISSUE="2998"
MARKER="<!-- skeleton-queue-selector-diag-v3 -->"

WD="$(systemctl show skeleton-runner-poll.service -p WorkingDirectory --value 2>/dev/null || true)"
if [ -z "$WD" ] || [ ! -f "$WD/scripts/runner_poll_github_tasks.py" ]; then
  echo "RESULT=BLOCKED_RUNNER_CHECKOUT_NOT_FOUND"
  exit 1
fi

REPORT="$(cd "$WD" && python3 - <<'PY'
import importlib.util
import subprocess
import sys
from pathlib import Path

MARKER = "<!-- skeleton-queue-selector-diag-v3 -->"
runner_file = Path("scripts/runner_poll_github_tasks.py")
spec = importlib.util.spec_from_file_location("queue_diag_v3", runner_file)
if spec is None or spec.loader is None:
    raise SystemExit("IMPORT_SPEC_FAILED")
mod = importlib.util.module_from_spec(spec)
sys.modules["queue_diag_v3"] = mod
spec.loader.exec_module(mod)

def num(item):
    try:
        return mod._queue_replenisher_issue_number(item)
    except Exception:
        return None

head = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False).stdout.strip()
ready = mod.get_ready_issues()
candidates = mod.get_run_now_queue_intake_candidate_issues()
target = next((i for i in candidates if num(i) == 2997), None)

print(MARKER)
print("### Queue selector diagnostic v3")
print(f"- runtime_head: `{head}`")
print(f"- target_present: `{str(target is not None).lower()}`")
if target is None:
    print("**DIAGNOSTIC_CONCLUSION:** `TARGET_MISSING`")
    raise SystemExit(0)

labels = mod._issue_label_names(target)
visible_gate = (
    mod.LABEL_RUN_NOW in labels
    and mod.LABEL_AGENT_TASK in labels
    and not (labels & mod.TERMINAL_RUNNER_LABELS)
    and mod.LABEL_READY not in labels
    and mod.LABEL_WAITING_DEPENDENCY not in labels
)
print(f"- visible_gate_pass: `{str(visible_gate).lower()}`")

try:
    discoverable = bool(mod._queue_replenisher_issue_is_discoverable(target))
except Exception:
    discoverable = False
print(f"- generic_discoverable: `{str(discoverable).lower()}`")

try:
    candidate = mod._queue_replenisher_candidate(target)
except Exception:
    candidate = None
print(f"- generic_candidate_present: `{str(candidate is not None).lower()}`")

if candidate is not None:
    allowed = set(getattr(candidate, "allowed_files", ()) or ())
    intent_key = str(getattr(candidate, "intent_key", ""))
    print(f"- generic_allowed_files_count: `{len(allowed)}`")
    print(f"- generic_intent_key_present: `{str(bool(intent_key)).lower()}`")
    try:
        protected = set(mod._queue_replenisher_protected_files(target) or ())
    except Exception:
        protected = set()
    print(f"- protected_overlap_count: `{len(allowed & protected)}`")
else:
    print("- generic_allowed_files_count: `unknown`")
    print("- generic_intent_key_present: `unknown`")
    print("- protected_overlap_count: `unknown`")

single = mod._runner_queue_replenishment_selection(
    ready,
    [target],
    target_min_depth=len(ready) + 1,
    target_max_depth=len(ready) + 1,
)
selected = [num(i) for i in getattr(single, "selected", ()) if num(i) is not None]
waiting = [num(i) for i in getattr(single, "waiting_dependency", ()) if num(i) is not None]
print(f"- singleton_selected: `{','.join(map(str, selected)) or 'none'}`")
print(f"- singleton_waiting: `{','.join(map(str, waiting)) or 'none'}`")

if visible_gate and not discoverable:
    conclusion = "GENERIC_DISCOVERABLE_GATE_DROPS_VALID_RUN_NOW"
elif visible_gate and discoverable and candidate is None:
    conclusion = "GENERIC_CANDIDATE_PARSER_DROPS_VALID_RUN_NOW"
elif visible_gate and candidate is not None and not (set(getattr(candidate, 'allowed_files', ()) or ())):
    conclusion = "GENERIC_ALLOWED_FILES_GATE_DROPS_NO_MODEL_MAINTENANCE"
elif visible_gate and candidate is not None and len(set(getattr(candidate, 'allowed_files', ()) or ()) & set(mod._queue_replenisher_protected_files(target) or ())) > 0:
    conclusion = "GENERIC_PROTECTED_OVERLAP_GATE_DROPS_TARGET"
elif selected:
    conclusion = "GENERIC_SINGLETON_NOW_ACCEPTS_TARGET"
else:
    conclusion = "GENERIC_SELECTION_DROP_OTHER"

print(f"**DIAGNOSTIC_CONCLUSION:** `{conclusion}`")
print("Privacy: public issue/control metadata only; no secrets, private paths, task bodies or provider payloads.")
PY
)"

COMMENT_ID="$(gh api "repos/$REPO/issues/$ISSUE/comments" --paginate --jq ".[] | select(.body | contains(\"$MARKER\")) | .id" | tail -n 1)"
if [ -n "$COMMENT_ID" ]; then
  gh api --method PATCH "repos/$REPO/issues/comments/$COMMENT_ID" -f body="$REPORT" >/dev/null
  echo "GITHUB_SELECTOR_DIAG=UPDATED"
else
  gh api --method POST "repos/$REPO/issues/$ISSUE/comments" -f body="$REPORT" >/dev/null
  echo "GITHUB_SELECTOR_DIAG=CREATED"
fi

echo "RESULT=SELECTOR_DIAGNOSTIC_V3_PUBLISHED"
REMOTE
