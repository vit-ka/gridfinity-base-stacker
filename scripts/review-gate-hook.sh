#!/usr/bin/env bash
#
# review-gate-hook.sh — Stop-hook guard for the review gate.
#
# This is the single command wired into the Claude Code `Stop` hook. It runs,
# in order:
#   1. one-shot bypass    — SKIP_REVIEW_GATE=1 → exit 0
#   2. persistent disable — .claude/.review-gate-disabled present → exit 0
#   3. armed check        — no .claude/.review-gate-active marker → exit 0
#   4. sync               — refresh managed scripts from the canonical checkout
#   5. run the reviewer   — exec scripts/review-gate.sh (exit code propagates)
#
# The gate runs only while ARMED for a workstream: `review-gate start` (run when
# you begin implementation, e.g. from /openspec-apply-change) writes the marker;
# `review-gate stop` or archiving the change clears it. Steps 1–3 short-circuit
# BEFORE sync, so an idle, disabled, or bypassed repo never reviews or mutates
# the working tree.
#
# There is deliberately NO dirty-tree gate: whether the change is committed or
# not does not tell us whether it has been reviewed. The reviewer's own content
# fingerprint (a git tree hash of the working state, committed + uncommitted)
# decides whether anything actually needs (re-)reviewing, so an unchanged,
# already-passed state returns instantly from cache while committed-but-unreviewed
# code is still reviewed.
#
# The canonical checkout lives at ${REVIEW_GATE_HOME:-$HOME/Projects/openspec-codex-gate}.
# When it is absent, sync is skipped silently and the committed copies are used,
# so a fresh clone works standalone.
#
# This file is part of the shared gate at vit-ka/openspec-codex-gate and is
# kept in sync from the canonical checkout — edit it THERE, not here.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The scripts kept in sync from the canonical checkout.
MANAGED_SCRIPTS=(review-gate.sh review-gate-hook.sh review-gate review-gate-arm.sh)

# 1. One-shot bypass.
if [ "${SKIP_REVIEW_GATE:-0}" = "1" ]; then
  exit 0
fi

# 2. Persistent disable.
if [ -e "$ROOT/.claude/.review-gate-disabled" ]; then
  exit 0
fi

# 3. Armed check — the gate runs only during an implementation workstream.
ACTIVE="$ROOT/.claude/.review-gate-active"
[ -e "$ACTIVE" ] || exit 0
armed_change="$(tr -d '\n' < "$ACTIVE" 2>/dev/null || true)"
# Auto-disarm once the armed change is gone (archived or removed).
if [ -n "$armed_change" ] && [ ! -d "$ROOT/openspec/changes/$armed_change" ]; then
  rm -f "$ACTIVE"
  exit 0
fi

# 4. Sync managed scripts from the canonical checkout (canonical wins).
sync_from_canonical() {
  local canon="${REVIEW_GATE_HOME:-$HOME/Projects/openspec-codex-gate}"
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
      echo "review-gate: synced scripts/$name from canonical ($canon_root)." >&2
    fi
  done
}
sync_from_canonical

# 5. Run the reviewer; its exit code (notably 2 = blocking) propagates.
# We deliberately do NOT gate on a dirty working tree: whether the change is
# committed or not says nothing about whether it has been reviewed. The reviewer
# verifies the armed change against its specs (reading committed code too), and
# its own content-fingerprint cache decides whether anything actually needs
# re-reviewing — a clean, unchanged, already-passed state returns instantly from
# cache, while committed-but-unreviewed code still gets reviewed.
exec bash "$ROOT/scripts/review-gate.sh"
