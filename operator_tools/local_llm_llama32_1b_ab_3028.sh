#!/usr/bin/env bash
set -euo pipefail

: "${SKELETON_TARGET:?Set SKELETON_TARGET to the SSH target for the local Ollama host}"

HARNESS_URL="https://raw.githubusercontent.com/alanua/Skeleton/cdf6fc76e35742c8a305dbc6d0d45de8120f2476/operator_tools/local_llm_broad_ab_3024.sh"

ssh -o BatchMode=yes "$SKELETON_TARGET" 'bash -s' <<'EOF'
set -euo pipefail
BASELINE="qwen2.5:1.5b"
EMBED="nomic-embed-text:latest"
REJECTED="smollm2:1.7b-instruct-q4_0"
CANDIDATE="llama3.2:1b"

ollama list | grep -Fq "$BASELINE" || { echo "BLOCKED_BASELINE_MISSING"; exit 10; }
ollama list | grep -Fq "$EMBED" || { echo "BLOCKED_EMBED_MISSING"; exit 11; }

if ollama list | grep -Fq "$REJECTED"; then
  ollama rm "$REJECTED" >/dev/null
fi

FREE_BEFORE="$(df -PB1 / | awk 'NR==2{print $4}')"
MEM_AVAILABLE_KIB="$(awk '/MemAvailable:/{print $2}' /proc/meminfo)"
[ "$FREE_BEFORE" -ge 3006477107 ] || { echo "BLOCKED_DISK_BEFORE_PULL"; exit 12; }
[ "$MEM_AVAILABLE_KIB" -ge 2097152 ] || { echo "BLOCKED_MEMORY_BEFORE_PULL"; exit 13; }

if ! ollama list | grep -Fq "$CANDIDATE"; then
  timeout 900 ollama pull "$CANDIDATE"
fi

FREE_AFTER="$(df -PB1 / | awk 'NR==2{print $4}')"
[ "$FREE_AFTER" -ge 1288490188 ] || { echo "BLOCKED_DISK_AFTER_PULL"; exit 14; }

ollama list | grep -Fq "$BASELINE" || exit 15
ollama list | grep -Fq "$EMBED" || exit 16
ollama list | grep -Fq "$CANDIDATE" || exit 17
EOF

curl -fsSL "$HARNESS_URL" \
  | sed \
      -e 's/ISSUE="3024"/ISSUE="3028"/' \
      -e 's/local-llm-broad-ab-v1/local-llm-llama32-1b-broad-ab-v1/g' \
      -e 's/CANDIDATE="qwen3.5:0.8b"/CANDIDATE="llama3.2:1b"/' \
      -e 's/c,cmal,ct=run(candidate,True)/c,cmal,ct=run(candidate,False)/' \
      -e 's/model_mutations: zero/model_mutations: llama32_pull_by_wrapper/' \
      -e 's/RESULT=LOCAL_LLM_BROAD_AB_PUBLISHED/RESULT=LOCAL_LLM_LLAMA32_AB_PUBLISHED/g' \
  | SKELETON_TARGET="$SKELETON_TARGET" bash
