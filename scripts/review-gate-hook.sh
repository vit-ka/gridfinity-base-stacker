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
# The gate runs only while ARMED for a workstream: `review-gate start-auto-review`
# (run when you begin implementation, e.g. from /openspec-apply-change) writes the
# marker; `review-gate stop-auto-review` or archiving the change clears it. (For an
# on-demand review outside a stop, run `review-gate start`.) Steps 1–3 short-circuit
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
# Managed skills kept in sync too — the review-gate skill (.claude/skills/<name>/SKILL.md).
MANAGED_SKILLS=(review-gate)

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
  local name src dst tmp
  for name in "${MANAGED_SCRIPTS[@]}"; do
    src="$canon_root/scripts/$name"
    dst="$ROOT/scripts/$name"
    [ -f "$src" ] || continue
    if ! cmp -s "$src" "$dst"; then
      # Replace via a same-dir temp + atomic rename — NEVER an in-place cp. This
      # hook syncs ITSELF (review-gate-hook.sh is a managed script), and rewriting
      # the running script's inode mid-execution corrupts bash's byte-offset read of
      # it (a syntax error partway through) and skips the review that run. A rename
      # swaps the directory entry to a NEW inode, so the already-running process
      # keeps reading the old (now-unlinked) inode to completion; the update takes
      # effect on the next run. cp -p carries canonical's content + mode onto the
      # temp, and the temp shares dst's directory so the rename stays on one
      # filesystem (a precondition for atomicity).
      tmp="$dst.tmp.$$"
      cp -p "$src" "$tmp" && mv -f "$tmp" "$dst" || { rm -f "$tmp"; continue; }
      echo "review-gate: synced scripts/$name from canonical ($canon_root)." >&2
    fi
  done
  # Managed skills: same canonical-wins refresh for .claude/skills/<name>/SKILL.md.
  # The skill is a non-executing markdown file, but we reuse the same-dir temp +
  # atomic-rename path for uniformity. A consumer missing the skill (cmp fails on a
  # nonexistent dst) has it created; the dir is made if absent.
  local sdir
  for name in "${MANAGED_SKILLS[@]}"; do
    src="$canon_root/.claude/skills/$name/SKILL.md"
    dst="$ROOT/.claude/skills/$name/SKILL.md"
    [ -f "$src" ] || continue
    if ! cmp -s "$src" "$dst" 2>/dev/null; then
      sdir="$(dirname "$dst")"; mkdir -p "$sdir" || continue
      tmp="$dst.tmp.$$"
      cp -p "$src" "$tmp" && mv -f "$tmp" "$dst" || { rm -f "$tmp"; continue; }
      echo "review-gate: synced .claude/skills/$name/SKILL.md from canonical ($canon_root)." >&2
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
