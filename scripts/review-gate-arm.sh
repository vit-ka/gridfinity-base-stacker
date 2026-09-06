#!/usr/bin/env bash
#
# review-gate-arm.sh — UserPromptSubmit hook that arms the review gate when an
# implementation workstream begins.
#
# Wired into .claude/settings.json (UserPromptSubmit) by bootstrap-repo.sh. It
# reads the hook JSON on stdin and toggles the armed state from the prompt's
# leading OpenSpec workflow slash-command: `/openspec-apply-change` arms the gate
# for the current change (so the Stop-hook gate reviews the implementation), while
# any OTHER OpenSpec workflow command (`/openspec-explore`, `/openspec-propose`,
# `/openspec-archive-change`, `/openspec-update-change`, `/openspec-sync-specs`)
# disarms it — so activity that is not implementation work does not trigger reviews.
# A prompt that is neither leaves the armed state as-is. Disarming is also still
# automatic as a backstop: the Stop-hook guard clears the marker once the armed
# change is archived or removed.
#
# It never blocks the prompt (always exits 0) and is silent — arming/disarming is
# idempotent and harmless if there is nothing to arm or the gate is already idle.
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

# Toggle the armed state from the prompt's LEADING OpenSpec workflow slash-command
# (the command must be the first token — a mid-prose mention never toggles):
#   /openspec-apply-change            -> arm    (start-auto-review)
#   any other OpenSpec workflow cmd   -> disarm (stop-auto-review)
#   anything else / plain prompt      -> leave the armed state unchanged
# So the gate is armed only while the most recent workflow command was apply-change;
# switching to exploring, planning, or finalizing disarms it, and resuming
# implementation via /openspec-apply-change re-arms. `start-auto-review` (no arg)
# targets the most recently modified change (the one being applied). The arm
# branch's stdout (the arm notice + useful-commands cheat sheet) is forwarded so the
# UserPromptSubmit hook surfaces it to the session; stderr is suppressed and the hook
# never blocks (always exit 0). stop-auto-review on an already-idle gate is a
# harmless no-op, so a disarm on an unarmed repo is safe.
trimmed="${prompt#"${prompt%%[![:space:]]*}"}"   # strip leading whitespace
case "$trimmed" in
  /openspec-apply-change|/openspec-apply-change[[:space:]]*)
    [ -x "$ROOT/scripts/review-gate" ] && bash "$ROOT/scripts/review-gate" start-auto-review 2>/dev/null || true
    ;;
  /openspec-explore|/openspec-explore[[:space:]]*|\
  /openspec-propose|/openspec-propose[[:space:]]*|\
  /openspec-archive-change|/openspec-archive-change[[:space:]]*|\
  /openspec-update-change|/openspec-update-change[[:space:]]*|\
  /openspec-sync-specs|/openspec-sync-specs[[:space:]]*)
    [ -x "$ROOT/scripts/review-gate" ] && bash "$ROOT/scripts/review-gate" stop-auto-review 2>/dev/null || true
    ;;
esac

exit 0
