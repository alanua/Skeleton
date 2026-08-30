#!/usr/bin/env bash
set -euo pipefail

# Issue #3591 P0 read-only localization helper.
# Purpose: capture the exact filesystem syscall/path around a minimal real Codex provider invocation.
# No repository, runtime, systemd, permissions, secrets, or preserved worktree mutation is intended.

REPO=/home/agent/agent-dev/repos/Skeleton
WORK=/home/agent/agent-dev/worktrees/skeleton/issue-3591
WRAPPER=/home/agent/.local/state/skeleton-runner/codegen-fallback-bin/codex
TRACE=/tmp/skeleton-3591-codex.strace
OUT=/tmp/skeleton-3591-codex.out
ERR=/tmp/skeleton-3591-codex.err

printf '%s\n' '=== PRECHECK ==='
id
printf 'repo='; git -C "$REPO" rev-parse HEAD
printf 'worktree='; git -C "$WORK" rev-parse --show-toplevel
printf 'wrapper='; stat -c '%A %U:%G %n' "$WRAPPER"
printf 'codex='; /usr/local/bin/codex --version </dev/null
command -v strace

rm -f "$TRACE" "$OUT" "$ERR"

printf '%s\n' '=== REAL MINIMAL PROVIDER TRACE ==='
# The wrapper consumes stdin until EOF, so stdin is explicitly closed by printf.
# Trace only filesystem/process syscalls. String payload is deliberately innocuous.
set +e
printf '%s\n' 'Reply with exactly OK. Do not inspect or modify files.' | timeout 90s strace -ff -s 512 -yy -o "$TRACE" -e trace=%file,process "$WRAPPER" exec --cd "$WORK" --sandbox read-only --skip-git-repo-check - 1>"$OUT" 2>"$ERR"
RC=$?
set -e
printf 'rc=%s\n' "$RC"

printf '%s\n' '=== STDERR TAIL ==='
tail -n 80 "$ERR" 2>/dev/null || true
printf '%s\n' '=== PROVIDER OUTPUT TAIL ==='
tail -n 40 "$OUT" 2>/dev/null || true
printf '%s\n' '=== DENIED/FFFF SYSCALLS ==='
grep -HEn "EACCES|EPERM|ffffffffffffffffffffffffffffffffffffffff" "${TRACE}"* 2>/dev/null | tail -n 120 || true
printf '%s\n' '=== CODEX STATE FILE ACTIVITY ==='
grep -HEn '/home/agent/\.codex|runner-codex-state|ffffffffffffffffffffffffffffffffffffffff' "${TRACE}"* 2>/dev/null | tail -n 200 || true
printf '%s\n' '=== RESULT FILES ==='
ls -l "${TRACE}"* "$OUT" "$ERR" 2>/dev/null || true
printf '%s\n' '=== DONE ==='
