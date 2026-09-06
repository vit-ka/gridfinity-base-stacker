#!/usr/bin/env bash
#
# review-gate-arm.sh — UserPromptSubmit hook that arms the review gate when an
# implementation workstream begins.
#
# Wired into .claude/settings.json (UserPromptSubmit) by bootstrap-repo.sh. It
# reads the hook JSON on stdin; when the user's prompt invokes
# `/openspec-apply-change`, it arms the gate for the current change so the
# Stop-hook gate reviews the implementation. Disarming is automatic: the guard
# clears the marker once the change is archived.
#
# It never blocks the prompt (always exits 0) and is silent — arming is
# idempotent and harmless if there is no change to arm.
#
# This file is part of the shared gate at vit-ka/openspec-codex-gate and is
# kept in sync from the canonical checkout — edit it THERE, not here.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

payload="$(cat 2>/dev/null || true)"

# The user's prompt text: the JSON `prompt` field when present, else the raw
# payload (so the hook still works if the format is plain text).
prompt="$payload"
if command -v jq >/dev/null 2>&1; then
  p="$(printf '%s' "$payload" | jq -r '.prompt // empty' 2>/dev/null || true)"
  [ -n "$p" ] && prompt="$p"
fi

# Arm only on an actual /openspec-apply-change slash-command invocation — the
# command as the first token of the prompt — so a prompt that merely mentions it
# in prose does not arm. `review-gate start` (no arg) targets the most recently
# modified change (the one being applied).
trimmed="${prompt#"${prompt%%[![:space:]]*}"}"   # strip leading whitespace
case "$trimmed" in
  /openspec-apply-change|/openspec-apply-change[[:space:]]*)
    [ -x "$ROOT/scripts/review-gate" ] && bash "$ROOT/scripts/review-gate" start >/dev/null 2>&1 || true
    ;;
esac

exit 0
