#!/usr/bin/env bash
#
# review-gate.sh — two-stage code-review gate for an OpenSpec project.
#
# Each gated run reviews the uncommitted changes in two stages:
#   1. Claude  — a cheap pass via the `claude` CLI headless
#                (`claude -p --model "$CLAUDE_REVIEW_MODEL"`), the diff fed
#                in-prompt. Blocks (exit 2) until it comes back clean.
#   2. Codex   — the costly full pass, run only once the Claude stage is clean.
#
# A clean Codex pass is cached against a fingerprint of the reviewed diff, so a
# converged change is not re-billed to Codex on every subsequent stop. When
# Codex reports a usage/subscription limit the gate does NOT fail closed: it
# enters an announced, git-ignored Claude-only mode until the limit resets.
#
# Designed to be driven in a loop from a Claude Code Stop hook (see
# review-gate-hook.sh); per-stage round counters prevent it spinning forever.
# Safe to run by hand at any time.
#
# This file is part of the shared gate at vit-ka/openspec-codex-gate and is
# kept in sync from the canonical checkout — edit it THERE, not here.
#
# Env vars (defaults in parentheses):
#   REVIEW_THRESHOLD    (2)                Block on findings P0..P<THRESHOLD>.
#   REVIEW_MODEL        (gpt-5.6-sol)      Model passed to `codex exec -m`.
#   REVIEW_EFFORT       (high)             Codex model_reasoning_effort.
#   REVIEW_MAX_ROUNDS   (5)                Per stage: after this many blocking
#                                          rounds, bail to human review.
#   CLAUDE_REVIEW_MODEL (claude-opus-4-8)  Model for the Claude stage.
#   CODEX_LIMIT_COOLDOWN(60m)              Claude-only fallback when no reset
#                                          time is parseable (Ns/Nm/Nh or N).
#   REVIEW_GATE_NOTIFY   (1)                0 disables the macOS notification.
#
# Exit codes: 0 = clean / cached / degraded-to-Claude-only (session may stop),
#             2 = blocking findings, 1 = misconfiguration / unparseable output
#             (never a silent pass).

set -euo pipefail

REVIEW_THRESHOLD="${REVIEW_THRESHOLD:-2}"
REVIEW_MODEL="${REVIEW_MODEL:-gpt-5.6-sol}"
REVIEW_EFFORT="${REVIEW_EFFORT:-high}"
REVIEW_MAX_ROUNDS="${REVIEW_MAX_ROUNDS:-5}"
CLAUDE_REVIEW_MODEL="${CLAUDE_REVIEW_MODEL:-claude-opus-4-8}"
CODEX_LIMIT_COOLDOWN="${CODEX_LIMIT_COOLDOWN:-60m}"
REVIEW_GATE_NOTIFY="${REVIEW_GATE_NOTIFY:-1}"

# Resolve repo root from this script's location so paths and `git diff` work
# regardless of the caller's working directory.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CLAUDE_DIR="$ROOT/.claude"
CLAUDE_ONLY="$CLAUDE_DIR/.review-gate-claude-only"   # holds resume epoch
CODEX_PASS="$CLAUDE_DIR/.review-gate-codex-pass"      # holds last clean diff hash
ACTIVE_MARKER="$CLAUDE_DIR/.review-gate-active"       # armed workstream: the change under review
CHANGE=""                                            # the single change under review; set in main

die()  { echo "review-gate: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have jq || die "'jq' not found on PATH — cannot parse review output."

# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
now_epoch() { date +%s; }

# Convert a duration (Ns / Nm / Nh / bare N seconds) to seconds; 1h on garbage.
to_seconds() {
  local v="${1:-}"
  if [[ "$v" =~ ^([0-9]+)([sSmMhH]?)$ ]]; then
    local n="${BASH_REMATCH[1]}" u="${BASH_REMATCH[2]}"
    case "$u" in
      [sS]|"") echo "$n" ;;
      [mM])    echo $(( n * 60 )) ;;
      [hH])    echo $(( n * 3600 )) ;;
    esac
  else
    echo 3600
  fi
}

human_time() {
  date -r "$1" '+%Y-%m-%d %H:%M' 2>/dev/null \
    || date -d "@$1" '+%Y-%m-%d %H:%M' 2>/dev/null \
    || echo "epoch $1"
}

sha256() {
  if have shasum; then shasum -a 256
  elif have sha256sum; then sha256sum
  else cat  # degraded: identity, still stable for cache comparison within a run
  fi | awk '{print $1}'
}

# The uncommitted changes, used to compute the Codex-pass cache fingerprint. The
# gate's own transient files (.claude/.review-gate-*) are skipped so they cannot
# perturb the fingerprint — otherwise writing the cache would change the next
# run's fingerprint and the cache would never hit in a repo that does not
# git-ignore them.
diff_content() {
  git diff HEAD 2>/dev/null || true
  git ls-files --others --exclude-standard -z 2>/dev/null | while IFS= read -r -d '' f; do
    case "$f" in .claude/.review-gate-*) continue ;; esac
    printf '\n===== untracked: %s =====\n' "$f"
    cat -- "$f" 2>/dev/null || true
  done
}

# A stable fingerprint of the uncommitted changes, for the Codex-pass cache.
diff_fingerprint() { diff_content | sha256; }

# ---------------------------------------------------------------------------
# Round counters (per stage: claude|codex)
# ---------------------------------------------------------------------------
counter_file() { echo "$CLAUDE_DIR/.review-gate-$1-rounds"; }
read_counter() {
  local f; f="$(counter_file "$1")"
  if [ -f "$f" ]; then local v; v="$(tr -dc '0-9' < "$f")"; echo "${v:-0}"; else echo 0; fi
}
write_counter() { mkdir -p "$CLAUDE_DIR"; printf '%s\n' "$2" > "$(counter_file "$1")"; }

# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------
loud_banner() {
  {
    echo "############################################################"
    local line
    for line in "$@"; do echo "# $line"; done
    echo "############################################################"
  } >&2
}
notify() {
  [ "$REVIEW_GATE_NOTIFY" != "0" ] || return 0
  [ "$(uname)" = "Darwin" ] || return 0
  have osascript || return 0
  osascript -e "display notification \"$1\" with title \"review-gate\"" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Review prompt + JSON extraction (shared by both stages)
# ---------------------------------------------------------------------------
# All non-archived change names (each openspec/changes/<name>/ with a tasks.md).
active_changes() {
  find openspec/changes -maxdepth 2 -name tasks.md -not -path '*/archive/*' 2>/dev/null \
    | sed -E 's#.*/changes/([^/]+)/tasks\.md$#\1#' | sort -u
}

# The single change under review. The OpenSpec workflow keeps one non-archived
# change at a time, so normally there is exactly one; if several exist, pick the
# most recently modified (by tasks.md mtime) — the one being worked on.
active_change() {
  ls -t openspec/changes/*/tasks.md 2>/dev/null \
    | grep -v '/archive/' \
    | head -1 \
    | sed -E 's#.*/changes/([^/]+)/tasks\.md$#\1#'
}

# The shared review prompt. Names the single change under review so each agent
# reads openspec/changes/<name>/ and locates the related code itself, rather than
# being handed a precomputed diff. $1 is the change name (may be empty).
review_prompt() {
  local change="$1"
  if [ -n "$change" ]; then
    printf 'You are the code-review gate for this repository. The OpenSpec change under review: %s.\n\n' "$change"
    printf 'Read its planning artifacts under openspec/changes/%s/ (proposal.md, design.md, tasks.md, specs/**/*.md) to understand the intended behavior, then find and review the related implementation in this repository yourself.\n\n' "$change"
  else
    printf 'You are the code-review gate for this repository. Review the uncommitted changes in this repository.\n\n'
  fi
  cat <<'EOF'
Focus on the uncommitted work (everything in `git diff HEAD` plus any untracked files). Report ONLY actionable defects — correctness, security, reliability, or violations of the specifications under openspec/. Assign each finding a priority: P0 (critical), P1 (high), P2 (medium), P3 (minor). Ignore style nits and formatting preferences. Output ONLY a single JSON object, no prose and no markdown fences, of exactly this shape: {"findings":[{"priority":"P1","file":"path/to/file","line":0,"issue":"what is wrong","fix":"how to fix it"}]}. Use an empty array when there are no defects: {"findings":[]}.
EOF
}

# Extract the last complete top-level JSON object from a stream. Scans backward
# from the final '}' to its matching '{', tracking JSON string/escape context so
# braces inside strings (and stray braces in any reasoning preamble) do not
# confuse the match.
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

# Parse a stage's output into a blocking-findings array (global BLOCKING).
# Fails closed (exit 1) on unparseable or malformed output — never a silent pass.
#   $1 label   $2 last-message file (may be empty)   $3 raw stdout   $4 stderr file
parse_verdict() {
  local label="$1" msg="$2" raw="$3" err="$4" json
  json="$(extract_json < "$msg")"
  [ -n "$json" ] || json="$(printf '%s' "$raw" | extract_json)"
  if [ -z "$json" ] || ! printf '%s' "$json" | jq -e 'has("findings") and (.findings | type == "array")' >/dev/null 2>&1; then
    echo "review-gate: [$label] could not parse a JSON review result." >&2
    echo "review-gate: [$label] stderr follows:" >&2
    sed "s/^/  $label: /" "$err" >&2 2>/dev/null || true
    exit 1
  fi
  if ! printf '%s' "$json" | jq -e '.findings | all(
        (.priority? | type == "string" and test("^P[0-3]$")) and
        (.file?     | type == "string") and
        (.line?     | type == "number") and
        (.issue?    | type == "string") and
        (.fix?      | type == "string"))' >/dev/null 2>&1; then
    echo "review-gate: [$label] review JSON has malformed findings (need priority P0-P3, string file/issue/fix, numeric line)." >&2
    printf '%s\n' "$json" | sed 's/^/  json: /' >&2
    exit 1
  fi
  ALL="$(printf '%s' "$json" | jq -c '.findings')"
  BLOCKING="$(printf '%s' "$json" | jq -c --argjson t "$REVIEW_THRESHOLD" \
    '[.findings[] | select((.priority | ltrimstr("P") | tonumber) <= $t)]')"
}

blocking_count() { printf '%s' "${1:-[]}" | jq 'length'; }

# Print EVERY finding from a stage (blocking and sub-threshold) to stderr so the
# full review stays in the session log. $1 stage label, $2 all-findings JSON.
report_findings() {
  local n; n="$(blocking_count "$2")"
  if [ "$n" -eq 0 ]; then
    echo "review-gate: [$1] review returned no findings." >&2
    return 0
  fi
  echo "review-gate: [$1] review findings ($n) — [BLOCK] = priority at/above P$REVIEW_THRESHOLD:" >&2
  printf '%s' "$2" | jq -r --argjson t "$REVIEW_THRESHOLD" \
    '.[] | "  \(if (.priority | ltrimstr("P") | tonumber) <= $t then "[BLOCK]" else "[note] " end) [\(.priority)] \(.file):\(.line) — \(.issue) → \(.fix)"' >&2
}

# ---------------------------------------------------------------------------
# Stage runners. Return: 0 clean, 2 blocking (BLOCKING set), 3 usage-limit
# (codex only; RESET_EPOCH set). Genuine failures exit 1 via die/parse_verdict.
# ---------------------------------------------------------------------------
run_claude() {
  have claude || die "'claude' CLI not found on PATH — cannot run the Claude review stage."
  local err msg raw rc
  err="$(mktemp)"; msg="$(mktemp)"; : > "$msg"
  # The agent locates and reads the code itself with read-only tools; writers are
  # denied and dontAsk means it neither prompts nor edits. SKIP_REVIEW_GATE=1
  # guards against any Stop hook this nested `claude` might fire.
  set +e
  raw="$(review_prompt "$CHANGE" | SKIP_REVIEW_GATE=1 claude -p --model "$CLAUDE_REVIEW_MODEL" \
    --permission-mode dontAsk \
    --allowedTools "Read Grep Glob Bash(git diff:*) Bash(git status:*) Bash(git log:*) Bash(git show:*) Bash(git ls-files:*)" \
    --disallowedTools "Write Edit NotebookEdit" \
    2>"$err")"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    echo "review-gate: [claude] 'claude -p' exited $rc — treating as a failed review (fail-closed)." >&2
    sed 's/^/  claude: /' "$err" >&2 2>/dev/null || true
    rm -f "$err" "$msg"; exit 1
  fi
  parse_verdict claude "$msg" "$raw" "$err"
  rm -f "$err" "$msg"
  report_findings claude "$ALL"
  [ "$(blocking_count "$BLOCKING")" -eq 0 ] && return 0 || return 2
}

# Distinguish a usage/rate/subscription limit from a genuine failure. Broad on
# purpose: the exact wording of the weekly limit is not yet known, so we match a
# range of limit phrasings. A false match at worst re-probes after the cooldown;
# a genuine failure that matches none of these still fails closed.
is_limit() {
  grep -qiE 'usage limit|weekly|rate ?limit|rate.?limited|quota|429|too many requests|out of (credits|usage)|resets? (at|in|on)|try again (in|later|at|on)' "$1"
}

# Best-effort resume epoch from a Codex limit message; else now + cooldown.
# Handles a relative duration ("try again in 45 minutes") and an absolute clock
# time ("try again at 7:42 PM" / "at 19:42"); anything else falls back to cooldown.
parse_reset_epoch() {
  local f="$1" now m c e; now="$(now_epoch)"
  # Relative: "in N seconds/minutes/hours".
  m="$(grep -ioE 'in [0-9]+ ?(second|minute|hour)' "$f" 2>/dev/null | head -1 || true)"
  if [ -n "$m" ]; then
    local num unit
    num="$(printf '%s' "$m" | grep -oE '[0-9]+')"
    unit="$(printf '%s' "$m" | grep -ioE '(second|minute|hour)' | tr 'A-Z' 'a-z')"
    case "$unit" in
      second) echo $(( now + num )); return ;;
      minute) echo $(( now + num * 60 )); return ;;
      hour)   echo $(( now + num * 3600 )); return ;;
    esac
  fi
  # Absolute clock time: "at 7:42 PM" or "at 19:42". Try GNU then BSD date; roll
  # to tomorrow if the parsed time already passed today.
  c="$(grep -ioE 'at [0-9]{1,2}(:[0-9]{2})? ?(am|pm)' "$f" 2>/dev/null | head -1 | sed -E 's/^at //I' || true)"
  [ -n "$c" ] || c="$(grep -ioE 'at [0-9]{1,2}:[0-9]{2}' "$f" 2>/dev/null | head -1 | sed -E 's/^at //I' || true)"
  if [ -n "$c" ]; then
    e="$(date -d "$c" +%s 2>/dev/null || true)"                              # GNU
    [ -n "$e" ] || e="$(date -j -f '%I:%M %p' "$c" +%s 2>/dev/null || true)" # BSD 12h
    [ -n "$e" ] || e="$(date -j -f '%H:%M' "$c" +%s 2>/dev/null || true)"    # BSD 24h
    if [ -n "$e" ]; then
      e=$(( e - e % 60 ))                    # normalize to the top of the minute
      [ "$e" -le "$now" ] && e=$(( e + 86400 ))
      echo "$e"; return
    fi
  fi
  echo $(( now + $(to_seconds "$CODEX_LIMIT_COOLDOWN") ))
}

run_codex() {
  have codex || die "'codex' CLI not found on PATH — cannot run the Codex review stage."
  local err msg raw rc
  err="$(mktemp)"; msg="$(mktemp)"
  set +e
  raw="$(codex exec -m "$REVIEW_MODEL" \
    -c model_reasoning_effort="\"$REVIEW_EFFORT\"" \
    --sandbox read-only \
    --skip-git-repo-check \
    -o "$msg" \
    "$(review_prompt "$CHANGE")" 2>"$err")"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    if is_limit "$err"; then
      RESET_EPOCH="$(parse_reset_epoch "$err")"
      rm -f "$err" "$msg"; return 3
    fi
    echo "review-gate: [codex] 'codex exec' exited $rc — treating as a failed review (fail-closed)." >&2
    sed 's/^/  codex: /' "$err" >&2 2>/dev/null || true
    rm -f "$err" "$msg"; exit 1
  fi
  parse_verdict codex "$msg" "$raw" "$err"
  rm -f "$err" "$msg"
  report_findings codex "$ALL"
  [ "$(blocking_count "$BLOCKING")" -eq 0 ] && return 0 || return 2
}

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
main() {
  local codex_allowed=1

  # Select the single change under review for both agents: the armed workstream
  # marker (set by `review-gate start` / the apply workflow) when present; else, for
  # ad-hoc manual runs, the most recently modified change. Empty falls back to a
  # generic "review the uncommitted changes" prompt.
  CHANGE="$(tr -d '\n' < "$ACTIVE_MARKER" 2>/dev/null || true)"
  if [ -z "$CHANGE" ]; then
    CHANGE="$(active_change)"
    local nchanges; nchanges="$(active_changes | grep -c . || true)"
    if [ "${nchanges:-0}" -gt 1 ]; then
      echo "review-gate: $nchanges non-archived changes found — reviewing the most recently modified: ${CHANGE:-<none>}." >&2
    fi
  fi

  # Claude-only mode: skip Codex until the recorded resume epoch has passed.
  if [ -f "$CLAUDE_ONLY" ]; then
    local resume now; resume="$(tr -dc '0-9' < "$CLAUDE_ONLY")"; resume="${resume:-0}"; now="$(now_epoch)"
    if [ "$now" -ge "$resume" ]; then
      rm -f "$CLAUDE_ONLY"
      echo "review-gate: Claude-only cooldown elapsed — re-probing Codex." >&2
    else
      codex_allowed=0
      loud_banner \
        "CODEX REVIEW GATE — CLAUDE-ONLY MODE (Codex usage limit reached)" \
        "Codex is temporarily disabled; reviewing with $CLAUDE_REVIEW_MODEL only." \
        "Codex resumes automatically after $(human_time "$resume")." \
        "Force a full review sooner with:  scripts/review-gate resume"
      notify "Claude-only mode — Codex limited until $(human_time "$resume")"
    fi
  fi

  # Stage 1 — Claude.
  local rc=0; run_claude || rc=$?
  if [ "$rc" -eq 2 ]; then
    local n; n="$(read_counter claude)"; n=$(( n + 1 )); write_counter claude "$n"
    if [ "$n" -gt "$REVIEW_MAX_ROUNDS" ]; then
      write_counter claude 0
      loud_banner "CLAUDE STAGE exceeded max rounds ($REVIEW_MAX_ROUNDS) — the [BLOCK] findings above need human review."
      # Bail: treat the Claude stage as passed and fall through to Codex.
    else
      echo "review-gate: [claude] BLOCKING (round $n/$REVIEW_MAX_ROUNDS) — fix the [BLOCK] findings above; exiting 2." >&2
      exit 2
    fi
  else
    write_counter claude 0
  fi

  # Claude stage passed (clean or bailed to human review).
  if [ "$codex_allowed" -eq 0 ]; then
    echo "review-gate: Claude stage clean; Codex skipped (Claude-only mode)." >&2
    exit 0
  fi

  # Codex-pass cache: skip Codex while the reviewed diff is unchanged.
  if [ -f "$CODEX_PASS" ]; then
    local fp cur; fp="$(cat "$CODEX_PASS" 2>/dev/null || true)"; cur="$(diff_fingerprint)"
    if [ -n "$fp" ] && [ "$fp" = "$cur" ]; then
      echo "review-gate: Codex review cached (diff unchanged since last clean pass) — skipping Codex."
      exit 0
    fi
  fi

  # Stage 2 — Codex.
  local crc=0; run_codex || crc=$?
  case "$crc" in
    0)
      write_counter codex 0
      diff_fingerprint > "$CODEX_PASS"
      echo "Two-stage review clean (Claude + Codex)."
      exit 0
      ;;
    2)
      local n; n="$(read_counter codex)"; n=$(( n + 1 )); write_counter codex "$n"
      if [ "$n" -gt "$REVIEW_MAX_ROUNDS" ]; then
        write_counter codex 0
        loud_banner "CODEX STAGE exceeded max rounds ($REVIEW_MAX_ROUNDS) — the [BLOCK] findings above need human review."
        exit 0
      fi
      echo "review-gate: [codex] BLOCKING (round $n/$REVIEW_MAX_ROUNDS) — fix the [BLOCK] findings above; exiting 2." >&2
      exit 2
      ;;
    3)
      mkdir -p "$CLAUDE_DIR"
      printf '%s\n' "$RESET_EPOCH" > "$CLAUDE_ONLY"
      loud_banner \
        "CODEX USAGE LIMIT REACHED — entering CLAUDE-ONLY MODE." \
        "The Claude stage already passed, so this stop is allowed to proceed." \
        "Codex will be skipped until $(human_time "$RESET_EPOCH")." \
        "Force a retry sooner with:  scripts/review-gate resume"
      notify "Codex limit reached — Claude-only until $(human_time "$RESET_EPOCH")"
      exit 0
      ;;
  esac
}

main "$@"
