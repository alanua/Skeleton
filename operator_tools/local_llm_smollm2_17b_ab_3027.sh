#!/usr/bin/env bash
set -euo pipefail

: "${SKELETON_TARGET:?Set SKELETON_TARGET to the SSH target for the local Ollama host}"

ssh -o BatchMode=yes "$SKELETON_TARGET" 'bash -s' <<'EOF'
set -euo pipefail
BASELINE="qwen2.5:1.5b"
EMBED="nomic-embed-text:latest"
CANDIDATE="smollm2:1.7b-instruct-q4_0"

command -v ollama >/dev/null 2>&1 || { echo BLOCKED_OLLAMA_MISSING; exit 20; }
ollama list | grep -q "$BASELINE" || { echo BLOCKED_BASELINE_MISSING; exit 21; }
ollama list | grep -q "$EMBED" || { echo BLOCKED_EMBED_MISSING; exit 22; }

FREE="$(df -PB1 / | awk 'NR==2{print $4}')"
MEM="$(awk '/MemAvailable:/{print $2}' /proc/meminfo)"
[ "$FREE" -ge 2684354560 ] || { echo BLOCKED_DISK_PREFLIGHT; exit 23; }
[ "$MEM" -ge 2097152 ] || { echo BLOCKED_RAM_PREFLIGHT; exit 24; }

if ! ollama list | grep -q "$CANDIDATE"; then
  timeout 900 ollama pull "$CANDIDATE"
fi

FREE_AFTER="$(df -PB1 / | awk 'NR==2{print $4}')"
[ "$FREE_AFTER" -ge 1395864371 ] || { echo BLOCKED_DISK_AFTER_PULL; exit 25; }
EOF

curl -fsSL https://raw.githubusercontent.com/alanua/Skeleton/cdf6fc76e35742c8a305dbc6d0d45de8120f2476/operator_tools/local_llm_broad_ab_3024.sh \
  | sed \
      -e 's/ISSUE="3024"/ISSUE="3027"/' \
      -e 's/local-llm-broad-ab-v1/local-llm-smollm2-17b-broad-ab-v1/g' \
      -e 's/CANDIDATE="qwen3.5:0.8b"/CANDIDATE="smollm2:1.7b-instruct-q4_0"/' \
      -e 's/c,c​mal,ct=run(candidate,True)/c,cmal,ct=run(candidate,False)/' \
      -e 's/c,cmal,ct=run(candidate,True)/c,cmal,ct=run(candidate,False)/' \
      -e 's/model_mutations: zero/model_mutations: smollm2_pull_only/' \
      -e 's/RESULT=LOCAL_LLM_BROAD_AB_PUBLISHED/RESULT=LOCAL_LLM_SMOLLM2_AB_PUBLISHED/g' \
  | SKELETON_TARGET="$SKELETON_TARGET" bash
