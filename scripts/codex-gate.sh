#!/usr/bin/env bash
#
# codex-gate.sh — Codex-based code-review gate for an OpenSpec project.
#
# Runs Codex read-only over the uncommitted changes, asks for actionable
# defects as strict JSON, and blocks (exit 2) when any finding is at or above
# the configured priority threshold. Designed to be driven in a loop from a
# Claude Code Stop hook (see codex-gate-hook.sh); a round counter prevents it
# from spinning forever. Safe to run by hand at any time.
#
# This file is part of the shared gate at vit-ka/openspec-codex-gate and is
# kept in sync from the canonical checkout — edit it THERE, not here.
#
# Env vars (defaults in parentheses):
#   REVIEW_THRESHOLD   (2)           Fail on findings P0..P<THRESHOLD>.
#   REVIEW_MODEL       (gpt-5.6-sol) Model passed to `codex exec -m`.
#   REVIEW_EFFORT      (high)        model_reasoning_effort.
#   REVIEW_MAX_ROUNDS  (5)           After this many blocking rounds, bail to
#                                    human review (exit 0, loud warning).
#
# Exit codes: 0 = clean (or bailed to human review), 2 = blocking findings,
#             1 = misconfiguration / unparseable output (never a silent pass).

set -euo pipefail

REVIEW_THRESHOLD="${REVIEW_THRESHOLD:-2}"
REVIEW_MODEL="${REVIEW_MODEL:-gpt-5.6-sol}"
REVIEW_EFFORT="${REVIEW_EFFORT:-high}"
REVIEW_MAX_ROUNDS="${REVIEW_MAX_ROUNDS:-5}"

# Resolve repo root from this script's location so paths and `git diff` work
# regardless of the caller's working directory.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COUNTER_FILE="$ROOT/.claude/.codex-gate-rounds"

die() { echo "codex-gate: $*" >&2; exit 1; }

command -v codex >/dev/null 2>&1 || die "'codex' CLI not found on PATH — cannot run review gate."
command -v jq    >/dev/null 2>&1 || die "'jq' not found on PATH — cannot parse review output."

read_counter() {
  if [ -f "$COUNTER_FILE" ]; then
    local v
    v="$(tr -dc '0-9' < "$COUNTER_FILE")"
    echo "${v:-0}"
  else
    echo 0
  fi
}

write_counter() {
  mkdir -p "$(dirname "$COUNTER_FILE")"
  printf '%s\n' "$1" > "$COUNTER_FILE"
}

PROMPT='Review the uncommitted changes in this repository: everything in `git diff HEAD` plus any untracked files. Report ONLY actionable defects — correctness, security, reliability, or violations of the specifications under openspec/. Assign each finding a priority: P0 (critical), P1 (high), P2 (medium), P3 (minor). Ignore style nits and formatting preferences. Output ONLY a single JSON object, no prose and no markdown fences, of exactly this shape: {"findings":[{"priority":"P1","file":"path/to/file","line":0,"issue":"what is wrong","fix":"how to fix it"}]}. Use an empty array when there are no defects: {"findings":[]}.'

# Extract the last complete top-level JSON object from a stream. Scans
# backward from the final '}' to its matching '{', tracking JSON string/escape
# context so braces inside strings (and stray unbalanced braces in any Codex
# reasoning preamble) do not confuse the match.
extract_json() {
  awk '
    { s = s $0 "\n" }
    END {
      n = length(s); endpos = 0;
      for (i = n; i >= 1; i--) { if (substr(s, i, 1) == "}") { endpos = i; break } }
      if (endpos == 0) { exit }
      depth = 0; instr = 0; start = 0;
      for (i = endpos; i >= 1; i--) {
        c = substr(s, i, 1);
        if (c == "\"") {
          bs = 0; j = i - 1;
          while (j >= 1 && substr(s, j, 1) == "\\") { bs++; j-- }
          if (bs % 2 == 0) { instr = !instr }
          continue;
        }
        if (instr) continue;
        if (c == "}") depth++;
        else if (c == "{") { depth--; if (depth == 0) { start = i; break } }
      }
      if (start == 0) { exit }
      printf "%s", substr(s, start, endpos - start + 1);
    }'
}

# Run Codex read-only over the working tree. `-o` writes ONLY the agent's final
# message (the JSON verdict) to a file, which is the reliable source; stdout is
# a fallback. Keep stderr for diagnostics.
CODEX_ERR="$(mktemp)"
LAST_MSG="$(mktemp)"
trap 'rm -f "$CODEX_ERR" "$LAST_MSG"' EXIT

set +e
RAW="$(codex exec -m "$REVIEW_MODEL" \
  -c model_reasoning_effort="\"$REVIEW_EFFORT\"" \
  --sandbox read-only \
  --skip-git-repo-check \
  -o "$LAST_MSG" \
  "$PROMPT" 2>"$CODEX_ERR")"
CODEX_RC=$?
set -e

# Prefer the clean last-message file; fall back to extracting from stdout.
JSON="$(extract_json < "$LAST_MSG")"
if [ -z "$JSON" ]; then
  JSON="$(printf '%s' "$RAW" | extract_json)"
fi

if [ -z "$JSON" ] || ! printf '%s' "$JSON" | jq -e 'has("findings") and (.findings | type == "array")' >/dev/null 2>&1; then
  echo "codex-gate: could not parse a JSON review result from Codex output (rc=$CODEX_RC)." >&2
  echo "codex-gate: Codex stderr follows:" >&2
  sed 's/^/  codex: /' "$CODEX_ERR" >&2 || true
  exit 1
fi

# Findings whose numeric priority is at or below the threshold are blocking.
BLOCKING="$(printf '%s' "$JSON" | jq -c --argjson t "$REVIEW_THRESHOLD" \
  '[.findings[] | select((.priority | ltrimstr("P") | tonumber) <= $t)]')"
COUNT="$(printf '%s' "$BLOCKING" | jq 'length')"

format_findings() {
  printf '%s' "$1" | jq -r '.[] | "- [\(.priority)] \(.file):\(.line) \(.issue) → \(.fix)"'
}

if [ "$COUNT" -eq 0 ]; then
  write_counter 0
  echo "Codex review clean."
  exit 0
fi

# Blocking findings: bump the round counter.
ROUNDS="$(read_counter)"
ROUNDS=$((ROUNDS + 1))
write_counter "$ROUNDS"

if [ "$ROUNDS" -gt "$REVIEW_MAX_ROUNDS" ]; then
  write_counter 0
  echo "codex-gate: WARNING — gate exceeded max rounds ($REVIEW_MAX_ROUNDS) — remaining findings need human review." >&2
  format_findings "$BLOCKING" >&2
  exit 0
fi

echo "codex-gate: blocking findings (round $ROUNDS/$REVIEW_MAX_ROUNDS), priority <= P$REVIEW_THRESHOLD:" >&2
format_findings "$BLOCKING" >&2
exit 2
