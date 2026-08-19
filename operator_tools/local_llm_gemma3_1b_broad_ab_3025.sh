#!/usr/bin/env bash
set -euo pipefail
: "${SKELETON_TARGET:?Set SKELETON_TARGET to the SSH target for the local Ollama host}"

ssh -o BatchMode=yes "$SKELETON_TARGET" 'bash -s' <<'EOF'
set -euo pipefail
for cmd in ollama; do command -v "$cmd" >/dev/null 2>&1 || exit 2; done
FREE_BEFORE="$(df -PB1 / | awk 'NR==2 {print $4}')"
if ! ollama list | awk 'NR>1 {print $1}' | grep -Fxq 'gemma3:1b'; then
  timeout 600 ollama pull gemma3:1b >/dev/null 2>&1
fi
for m in 'qwen2.5:1.5b' 'nomic-embed-text:latest' 'qwen3.5:0.8b' 'gemma3:1b'; do
  ollama list | awk 'NR>1 {print $1}' | grep -Fxq "$m" || exit 3
done
FREE_AFTER="$(df -PB1 / | awk 'NR==2 {print $4}')"
[ "$FREE_AFTER" -ge $((1536*1024*1024)) ] || exit 4
EOF

curl -fsSL 'https://raw.githubusercontent.com/alanua/Skeleton/cdf6fc76e35742c8a305dbc6d0d45de8120f2476/operator_tools/local_llm_broad_ab_3024.sh' \
  | sed \
      -e 's/ISSUE="3024"/ISSUE="3025"/' \
      -e 's/local-llm-broad-ab-v1/local-llm-gemma3-1b-broad-ab-v1/g' \
      -e 's/CANDIDATE="qwen3.5:0.8b"/CANDIDATE="gemma3:1b"/' \
      -e 's/c,cmal,ct=run(candidate,True)/c,cmal,ct=run(candidate,False)/' \
      -e 's/model_mutations: zero/model_mutations: gemma3_pull_only/' \
      -e 's/LOCAL_LLM_BROAD_AB_PUBLISHED/LOCAL_LLM_GEMMA_BROAD_AB_PUBLISHED/g' \
  | SKELETON_TARGET="$SKELETON_TARGET" bash
