#!/usr/bin/env bash
set -euo pipefail
: "${SKELETON_TARGET:?Set SKELETON_TARGET}"
HARNESS_URL="https://raw.githubusercontent.com/alanua/Skeleton/cdf6fc76e35742c8a305dbc6d0d45de8120f2476/operator_tools/local_llm_broad_ab_3024.sh"

ssh -o BatchMode=yes "$SKELETON_TARGET" 'bash -s' <<'EOF'
set -euo pipefail
for m in 'qwen2.5:1.5b' 'nomic-embed-text:latest' 'llama3.2:1b'; do
  ollama show "$m" >/dev/null 2>&1 || { echo "BLOCKED_MODEL_MISSING:$m"; exit 20; }
done
FREE="$(df -PB1 / | awk 'NR==2{print $4}')"
MEM="$(awk '/MemAvailable:/{print $2}' /proc/meminfo)"
[ "$FREE" -ge 1288490188 ] || { echo BLOCKED_DISK_AFTER_PULL; exit 21; }
[ "$MEM" -ge 1572864 ] || { echo BLOCKED_MEMORY_FOR_BENCHMARK; exit 22; }
echo LLAMA32_PREFLIGHT=PASS
EOF

set +e
curl -fsSL "$HARNESS_URL" \
  | sed \
      -e 's/ISSUE="3024"/ISSUE="3028"/' \
      -e 's/local-llm-broad-ab-v1/local-llm-llama32-1b-broad-ab-v1/g' \
      -e 's/CANDIDATE="qwen3.5:0.8b"/CANDIDATE="llama3.2:1b"/' \
      -e 's/c,cmal,ct=run(candidate,True)/c,cmal,ct=run(candidate,False)/' \
      -e 's/model_mutations: zero/model_mutations: llama32_already_installed/' \
      -e 's/RESULT=LOCAL_LLM_BROAD_AB_PUBLISHED/RESULT=LOCAL_LLM_LLAMA32_AB_PUBLISHED/g' \
  | SKELETON_TARGET="$SKELETON_TARGET" bash
RC=$?
set -e
[ "$RC" -eq 0 ] || { echo "BLOCKED_HARNESS_OR_PUBLISH_RC=$RC"; exit "$RC"; }
