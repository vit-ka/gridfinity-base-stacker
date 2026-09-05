#!/usr/bin/env bash
#
# codex-gate-hook.sh — Stop-hook guard for the Codex review gate.
#
# This is the single command wired into the Claude Code `Stop` hook. It runs,
# in order:
#   1. one-shot bypass    — SKIP_CODEX_GATE=1 → exit 0
#   2. persistent disable — .claude/.codex-gate-disabled present → exit 0
#   3. sync               — refresh managed scripts from the canonical checkout
#   4. dirty-tree check   — clean working tree → exit 0
#   5. active-change check — no non-archived openspec change with tasks → exit 0
#   6. run the reviewer   — exec scripts/codex-gate.sh (exit code propagates)
#
# Steps 1 and 2 short-circuit BEFORE sync, so manual work (disabled/bypassed)
# never mutates the working tree.
#
# The canonical checkout lives at ${CODEX_GATE_HOME:-$HOME/Projects/openspec-codex-gate}.
# When it is absent, sync is skipped silently and the committed copies are used,
# so a fresh clone works standalone.
#
# This file is part of the shared gate at vit-ka/openspec-codex-gate and is
# kept in sync from the canonical checkout — edit it THERE, not here.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The scripts kept in sync from the canonical checkout.
MANAGED_SCRIPTS=(codex-gate.sh codex-gate-hook.sh codex-gate)

# 1. One-shot bypass.
if [ "${SKIP_CODEX_GATE:-0}" = "1" ]; then
  exit 0
fi

# 2. Persistent disable.
if [ -e "$ROOT/.claude/.codex-gate-disabled" ]; then
  exit 0
fi

# 3. Sync managed scripts from the canonical checkout (canonical wins).
sync_from_canonical() {
  local canon="${CODEX_GATE_HOME:-$HOME/Projects/openspec-codex-gate}"
  [ -d "$canon" ] || return 0                      # canonical absent → standalone
  local canon_root repo_root
  canon_root="$(cd "$canon" && pwd -P)"
  repo_root="$(pwd -P)"
  [ "$canon_root" != "$repo_root" ] || return 0    # don't sync the canonical repo onto itself
  local name src dst
  for name in "${MANAGED_SCRIPTS[@]}"; do
    src="$canon_root/scripts/$name"
    dst="$ROOT/scripts/$name"
    [ -f "$src" ] || continue
    if ! cmp -s "$src" "$dst"; then
      cp -p "$src" "$dst"                           # canonical content + mode wins
      echo "codex-gate: synced scripts/$name from canonical ($canon_root)." >&2
    fi
  done
}
sync_from_canonical

# 4. Nothing changed → nothing to review.
if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
  exit 0
fi

# 5. No active (non-archived) change with tasks → not OpenSpec work in progress.
active_tasks="$(find openspec/changes -maxdepth 2 -name tasks.md -not -path '*/archive/*' -print -quit 2>/dev/null || true)"
if [ -z "$active_tasks" ]; then
  exit 0
fi

# 6. Run the reviewer; its exit code (notably 2 = blocking) propagates.
exec bash "$ROOT/scripts/codex-gate.sh"
