#!/usr/bin/env bash
#
# review-gate.sh — two-stage code-review gate for an OpenSpec project.
#
# Each gated run reviews the active change in two stages:
#   1. Cheap Claude (Sonnet) — a cheap pass via the `claude` CLI headless
#                (`claude -p --model "$CHEAP_REVIEW_MODEL"`, fresh session each
#                round). Given the change name, it locates/reads the related code
#                itself with a read-only tool allow-list. Blocks (exit 2) until
#                it comes back clean.
#   2. Expensive final reviewer — run only once stage 1 is clean: Codex
#                (`$REVIEW_MODEL`) normally, or Claude `$FINAL_CLAUDE_MODEL` only
#                when Codex is rate-limited.
#
# A clean final pass is cached against a fingerprint of the reviewed diff, so a
# converged change is not re-reviewed on every subsequent stop. When Codex
# reports a usage/subscription limit the gate does NOT fail closed: it records a
# git-ignored Codex-cooldown sentinel and uses Claude `$FINAL_CLAUDE_MODEL` as
# the final reviewer until the limit resets. The final reviewers reuse their
# session across rounds; the cheap stage does not.
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
#   CHEAP_REVIEW_MODEL  (claude-sonnet-5)  Stage-1 cheap Claude model.
#   REVIEW_MODEL        (gpt-5.6-sol)      Stage-2 Codex model (`codex exec -m`).
#   FINAL_CLAUDE_MODEL  (claude-opus-4-8)  Stage-2 fallback Claude model (Codex-limited).
#   REVIEW_EFFORT       (high)             Codex model_reasoning_effort.
#   REVIEW_MAX_ROUNDS   (5)                Per stage: after this many blocking
#                                          rounds, bail to human review.
#   CODEX_LIMIT_COOLDOWN(60m)              Codex-cooldown length when no reset
#                                          time is parseable (Ns/Nm/Nh or N).
#   REVIEW_GATE_NOTIFY   (1)                0 disables the macOS notification.
#
# All of the above can also be set in a committed ./.review-gate.conf (a shell
# fragment; override its path with REVIEW_GATE_CONF). Precedence: environment
# variable > .review-gate.conf > built-in default.
#
# Exit codes: 0 = clean / cached / no reviewer available (session may stop),
#             2 = blocking findings, 1 = misconfiguration / unparseable output
#             (never a silent pass).

set -euo pipefail

# Resolve repo root from this script's location so paths and `git diff` work
# regardless of the caller's working directory.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Configuration. Precedence: environment variable > repo config file > default.
# The config file (default ./.review-gate.conf, override with $REVIEW_GATE_CONF)
# is an optional committed shell fragment — the easy way to set the review models
# and other knobs per repo, e.g.:  CHEAP_REVIEW_MODEL=claude-sonnet-5
#
# Two stages; the final stage's reviewer depends on Codex availability:
#   CHEAP_REVIEW_MODEL  stage 1 — cheap Claude pass (fresh session each round)
#   REVIEW_MODEL        stage 2 — Codex, the normal final reviewer (session reused)
#   FINAL_CLAUDE_MODEL  stage 2 fallback — Claude, used only when Codex is limited
_env_REVIEW_THRESHOLD="${REVIEW_THRESHOLD:-}"
_env_REVIEW_MODEL="${REVIEW_MODEL:-}"
_env_REVIEW_EFFORT="${REVIEW_EFFORT:-}"
_env_REVIEW_MAX_ROUNDS="${REVIEW_MAX_ROUNDS:-}"
_env_CHEAP_REVIEW_MODEL="${CHEAP_REVIEW_MODEL:-}"
_env_FINAL_CLAUDE_MODEL="${FINAL_CLAUDE_MODEL:-}"
_env_CODEX_LIMIT_COOLDOWN="${CODEX_LIMIT_COOLDOWN:-}"
_env_REVIEW_GATE_NOTIFY="${REVIEW_GATE_NOTIFY:-}"
REVIEW_GATE_CONF="${REVIEW_GATE_CONF:-$ROOT/.review-gate.conf}"
# shellcheck disable=SC1090
[ -f "$REVIEW_GATE_CONF" ] && . "$REVIEW_GATE_CONF"
REVIEW_THRESHOLD="${_env_REVIEW_THRESHOLD:-${REVIEW_THRESHOLD:-2}}"
REVIEW_MODEL="${_env_REVIEW_MODEL:-${REVIEW_MODEL:-gpt-5.6-sol}}"
REVIEW_EFFORT="${_env_REVIEW_EFFORT:-${REVIEW_EFFORT:-high}}"
REVIEW_MAX_ROUNDS="${_env_REVIEW_MAX_ROUNDS:-${REVIEW_MAX_ROUNDS:-5}}"
CHEAP_REVIEW_MODEL="${_env_CHEAP_REVIEW_MODEL:-${CHEAP_REVIEW_MODEL:-claude-sonnet-5}}"
FINAL_CLAUDE_MODEL="${_env_FINAL_CLAUDE_MODEL:-${FINAL_CLAUDE_MODEL:-claude-opus-4-8}}"
CODEX_LIMIT_COOLDOWN="${_env_CODEX_LIMIT_COOLDOWN:-${CODEX_LIMIT_COOLDOWN:-60m}}"
REVIEW_GATE_NOTIFY="${_env_REVIEW_GATE_NOTIFY:-${REVIEW_GATE_NOTIFY:-1}}"
unset _env_REVIEW_THRESHOLD _env_REVIEW_MODEL _env_REVIEW_EFFORT _env_REVIEW_MAX_ROUNDS \
      _env_CHEAP_REVIEW_MODEL _env_FINAL_CLAUDE_MODEL _env_CODEX_LIMIT_COOLDOWN _env_REVIEW_GATE_NOTIFY

CLAUDE_DIR="$ROOT/.claude"
CODEX_COOLDOWN="$CLAUDE_DIR/.review-gate-codex-cooldown" # Codex limited until this epoch → use final Claude
FINAL_PASS="$CLAUDE_DIR/.review-gate-final-pass"         # diff hash of the last clean FINAL review
ACTIVE_MARKER="$CLAUDE_DIR/.review-gate-active"          # armed workstream: the change under review
# Per-stage reused review sessions live in .review-gate-<stage>-session as
# "<change>\t<session-id>" (see session_file/session_id_for/save_session).
CHANGE=""                                            # the single change under review; set in main
CLAUDE_RESET_EPOCH=""                                # set by a Claude stage when rate-limited
RESET_EPOCH=""                                        # set by run_codex on a Codex limit

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
  if have shasum; then shasum -a 256 | awk '{print $1}'
  elif have sha256sum; then sha256sum | awk '{print $1}'
  else cat  # no hasher: pass the content through unchanged (true identity) — a
            # longer cache key, but correct; never collapses distinct diffs
  fi
}

# The uncommitted changes, used to compute the Codex-pass cache fingerprint. The
# gate's own transient files (.claude/.review-gate-*) are skipped so they cannot
# perturb the fingerprint — otherwise writing the cache would change the next
# run's fingerprint and the cache would never hit. They are excluded from BOTH the
# tracked diff and the untracked listing: if a repo committed them before adopting
# the gitignore they show up in `git diff HEAD`, and their churn every run would
# otherwise defeat the cache entirely.
diff_content() {
  git diff HEAD -- ':(exclude,glob).claude/.review-gate-*' 2>/dev/null || git diff HEAD 2>/dev/null || true
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

# Claude review session reuse: keep one session per change so rounds 2..N of the
# same change resume prior context instead of re-inferring from scratch; a new
# change gets a new session.
gen_uuid() { uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())' 2>/dev/null; }
session_file() { echo "$CLAUDE_DIR/.review-gate-$1-session"; }   # $1 = claude|codex
session_id_for() {  # $1 = stage, $2 = change; echoes the stored session id iff it is for this change
  local f; f="$(session_file "$1")"; [ -f "$f" ] || return 0
  local sc su tab; tab="$(printf '\t')"
  IFS="$tab" read -r sc su < "$f" 2>/dev/null || return 0
  [ "$sc" = "$2" ] && [ -n "$su" ] && printf '%s' "$su"
}
save_session() { mkdir -p "$CLAUDE_DIR"; printf '%s\t%s\n' "$2" "$3" > "$(session_file "$1")"; }  # $1 stage $2 change $3 id

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
    printf 'You are the code-review gate for this repository. Verify that the OpenSpec change "%s" is implemented correctly.\n\n' "$change"
    printf 'First read the artifacts for this change under openspec/changes/%s/ — proposal.md, design.md, tasks.md, and specs/**/*.md — to understand exactly what it is supposed to do. Then review this repository and judge whether the implementation of that change is correct, complete, and faithful to those specs. Whether the relevant code is already committed or still uncommitted does not matter — review the implementation as it now stands.\n\n' "$change"
    printf 'Stay scoped to THIS change: report defects only in the code that implements it. Do not audit the repository at large and do not flag pre-existing issues unrelated to this change.\n\n'
  else
    printf 'You are the code-review gate for this repository. Review the uncommitted changes for actionable defects.\n\n'
  fi
  cat <<'EOF'
Do NOT modify anything in the repository, and do NOT run tests, builds, formatters, or other commands — review by reading the code only (this keeps the review fast and side-effect-free). Report ONLY actionable defects — correctness, security, reliability, or violations of the change's specifications under openspec/. Assign each finding a priority: P0 (critical), P1 (high), P2 (medium), P3 (minor). Ignore style nits and formatting preferences. Output ONLY a single JSON object, no prose and no markdown fences, of exactly this shape: {"findings":[{"priority":"P1","file":"path/to/file","line":0,"issue":"what is wrong","fix":"how to fix it"}]}. Use an empty array when there are no defects: {"findings":[]}.
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
    echo "review-gate: [$label] raw output follows (truncated):" >&2
    { { [ -s "$msg" ] && cat "$msg"; printf '%s' "$raw"; } | head -c 2000; echo; } | sed "s/^/  $label> /" >&2
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
# Run a Claude review stage. $1 = model, $2 = stage label (also the session key and
# round-counter key), $3 = reuse-session (1 = reuse across rounds of this change,
# 0 = fresh session every round). Returns: 0 clean, 2 blocking (BLOCKING set),
# 4 usage/rate limit (CLAUDE_RESET_EPOCH set); genuine failure exits 1.
run_claude_stage() {
  local model="$1" stage="$2" reuse="$3"
  have claude || die "'claude' CLI not found on PATH — cannot run the $stage review stage."
  local err msg raw rc sid
  err="$(mktemp)"; msg="$(mktemp)"; : > "$msg"
  # The agent locates and reads the code itself. `--setting-sources ''` isolates
  # the review from the repo's own settings so its allow-list can't inherit the
  # project/user permissions (which may permit mutating Bash/cp/etc.) — the review
  # is genuinely read-only, granting only the explicit read-only allow-list, with
  # writers denied. No --permission-mode (its values vary across builds).
  # SKIP_REVIEW_GATE=1 guards against Stop-hook recursion.
  local -a cflags=(-p --model "$model" --setting-sources '' \
    --allowedTools "Read Grep Glob Bash(git diff:*) Bash(git status:*) Bash(git log:*) Bash(git show:*) Bash(git ls-files:*)" \
    --disallowedTools "Write Edit NotebookEdit")
  local -a sflag=(); local use_session=0
  if [ "$reuse" = "1" ]; then
    use_session=1
    sid="$(session_id_for "$stage" "$CHANGE")"
    if [ -n "$sid" ]; then sflag=(--resume "$sid"); else sid="$(gen_uuid)"; sflag=(--session-id "$sid"); fi
  fi
  set +e
  raw="$(review_prompt "$CHANGE" | SKIP_REVIEW_GATE=1 claude "${cflags[@]}" ${sflag[@]+"${sflag[@]}"} 2>"$err")"
  rc=$?
  # Fail-safe: if a session-flagged call fails for a non-limit reason — a stale
  # session, or a `claude` build that doesn't accept --session-id/--resume — retry
  # once WITHOUT session flags so a session-feature problem never fails the review
  # closed (mirrors why we avoid --permission-mode). Only persist a session when a
  # session-flagged call actually succeeds.
  if [ "$rc" -ne 0 ] && [ "$use_session" -eq 1 ]; then
    # Limit detection scans ONLY stderr: claude prints usage limits there, while
    # the review's own findings (stdout) can legitimately contain words like
    # 'rate limit' or '429' and must not be mistaken for a limit.
    if ! is_limit "$err"; then
      echo "review-gate: [$stage] session call failed — retrying without a session." >&2
      : > "$err"
      raw="$(review_prompt "$CHANGE" | SKIP_REVIEW_GATE=1 claude "${cflags[@]}" 2>"$err")"
      rc=$?; use_session=0
    fi
  fi
  set -e
  if [ "$rc" -ne 0 ]; then
    # A Claude usage/session/rate limit is not a review failure — surface it so the
    # gate degrades with a clear message instead of a scary "failed review".
    # Scan ONLY stderr (see the retry note above): the review's own findings on
    # stdout can contain limit-ish words and must not trip this.
    if is_limit "$err"; then
      CLAUDE_RESET_EPOCH="$(parse_reset_epoch "$err")"
      rm -f "$err" "$msg"; return 4
    fi
    echo "review-gate: [$stage] 'claude -p' exited $rc — treating as a failed review (fail-closed)." >&2
    sed "s/^/  $stage: /" "$err" >&2 2>/dev/null || true
    rm -f "$err" "$msg"; exit 1
  fi
  parse_verdict "$stage" "$msg" "$raw" "$err"
  rm -f "$err" "$msg"
  [ "$use_session" -eq 1 ] && save_session "$stage" "$CHANGE" "$sid"
  report_findings "$stage" "$ALL"
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

# Run Codex over the working tree. A fresh session runs read-only and records its
# session id (from the `session id:` startup line) so rounds 2..N of the same
# change resume it instead of re-inferring. `codex exec resume` continues the
# original read-only session; a failed resume (non-limit) falls back to a fresh one.
run_codex() {
  have codex || die "'codex' CLI not found on PATH — cannot run the Codex review stage."
  local err msg raw rc csid resuming=0
  err="$(mktemp)"; msg="$(mktemp)"
  csid="$(session_id_for codex "$CHANGE")"
  _codex_fresh() { codex exec -m "$REVIEW_MODEL" -c model_reasoning_effort="\"$REVIEW_EFFORT\"" \
    --sandbox read-only --skip-git-repo-check -o "$msg" "$(review_prompt "$CHANGE")" 2>"$err"; }
  # `codex exec resume` takes no --sandbox flag, so force read-only via config —
  # otherwise a resumed review could inherit a workspace-write policy and mutate the repo.
  _codex_resume() { codex exec resume "$1" -m "$REVIEW_MODEL" -c sandbox_mode='"read-only"' \
    --skip-git-repo-check -o "$msg" "$(review_prompt "$CHANGE")" 2>"$err"; }
  set +e
  if [ -n "$csid" ]; then resuming=1; raw="$(_codex_resume "$csid")"; else raw="$(_codex_fresh)"; fi
  rc=$?
  # Fail-safe: a failed resume that is not a usage limit → retry once fresh.
  # Limit detection scans ONLY stderr: Codex prints usage limits there, while the
  # review's own findings (stdout / -o file) can legitimately contain words like
  # "rate limit" or "429" and must not be mistaken for a limit.
  if [ "$rc" -ne 0 ] && [ "$resuming" -eq 1 ]; then
    if ! is_limit "$err"; then
      echo "review-gate: [codex] resume failed — starting a fresh review session." >&2
      : > "$err"; raw="$(_codex_fresh)"; rc=$?; resuming=0
    fi
  fi
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
  # Record a fresh session's id so the next round of this change resumes it. Scan
  # stderr + stdout + the -o file (the "session id:" channel is not guaranteed),
  # matching how limit detection scans all three.
  if [ "$resuming" -eq 0 ]; then
    local sidscan newid; sidscan="$(mktemp)"; { cat "$err" 2>/dev/null; printf '\n%s\n' "$raw"; cat "$msg" 2>/dev/null; } > "$sidscan"
    newid="$(grep -ioE 'session id:[[:space:]]*[0-9a-fA-F-]{8,}' "$sidscan" | head -1 | grep -oE '[0-9a-fA-F-]{8,}' || true)"
    rm -f "$sidscan"
    [ -n "$newid" ] && save_session codex "$CHANGE" "$newid"
  fi
  rm -f "$err" "$msg"
  report_findings codex "$ALL"
  [ "$(blocking_count "$BLOCKING")" -eq 0 ] && return 0 || return 2
}

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
# A blocking FINAL review ($1 = reviewer label): bump the shared "final" round
# counter, bail to human review after the max, else exit 2. Always terminal.
final_block() {
  local n; n="$(read_counter final)"; n=$(( n + 1 )); write_counter final "$n"
  if [ "$n" -gt "$REVIEW_MAX_ROUNDS" ]; then
    write_counter final 0
    loud_banner "FINAL ($1) STAGE exceeded max rounds ($REVIEW_MAX_ROUNDS) — the [BLOCK] findings above need human review."
    exit 0
  fi
  echo "review-gate: [$1] BLOCKING (round $n/$REVIEW_MAX_ROUNDS) — fix the [BLOCK] findings above; exiting 2." >&2
  exit 2
}

main() {
  # Select the single change under review: the armed workstream marker (set by
  # `review-gate start` / the apply workflow) when present; else, for ad-hoc manual
  # runs, the most recently modified change. Empty → generic "uncommitted changes".
  CHANGE=""
  [ -f "$ACTIVE_MARKER" ] && CHANGE="$(tr -d '\n' < "$ACTIVE_MARKER" 2>/dev/null || true)"
  if [ -z "$CHANGE" ]; then
    CHANGE="$(active_change)"
    local nchanges; nchanges="$(active_changes | grep -c . || true)"
    [ "${nchanges:-0}" -gt 1 ] && echo "review-gate: $nchanges non-archived changes found — reviewing the most recently modified: ${CHANGE:-<none>}." >&2
  fi

  # ---- Full-review cache: if nothing has changed since the last clean pass (both
  # stages passed on this exact diff), skip the ENTIRE review — no reviewer runs. ----
  if [ -f "$FINAL_PASS" ]; then
    local fp cur; fp="$(cat "$FINAL_PASS" 2>/dev/null || true)"; cur="$(diff_fingerprint)"
    if [ -n "$fp" ] && [ "$fp" = "$cur" ]; then
      echo "review-gate: review cached (nothing changed since the last clean pass) — skipping the review."
      exit 0
    fi
  fi

  # Is Codex on cooldown from an earlier limit? While so, the FINAL reviewer is
  # Claude ($FINAL_CLAUDE_MODEL) instead of Codex.
  local codex_ok=1
  if [ -f "$CODEX_COOLDOWN" ]; then
    local resume now; resume="$(tr -dc '0-9' < "$CODEX_COOLDOWN")"; resume="${resume:-0}"; now="$(now_epoch)"
    if [ "$now" -ge "$resume" ]; then rm -f "$CODEX_COOLDOWN"; echo "review-gate: Codex cooldown elapsed — re-probing Codex." >&2
    else codex_ok=0; fi
  fi

  # ---- Stage 1: cheap Sonnet pass (fresh session every round) ----
  local rc=0 cheap_desc="cheap $CHEAP_REVIEW_MODEL"
  run_claude_stage "$CHEAP_REVIEW_MODEL" cheap 0 || rc=$?
  if [ "$rc" -eq 4 ]; then
    cheap_desc="cheap stage skipped (rate-limited)"
    loud_banner \
      "CHEAP ($CHEAP_REVIEW_MODEL) STAGE RATE-LIMITED — skipping it this stop." \
      "Falling through to the final reviewer; Claude should recover by $(human_time "$CLAUDE_RESET_EPOCH")."
  elif [ "$rc" -eq 2 ]; then
    local n; n="$(read_counter cheap)"; n=$(( n + 1 )); write_counter cheap "$n"
    if [ "$n" -gt "$REVIEW_MAX_ROUNDS" ]; then
      write_counter cheap 0
      loud_banner "CHEAP ($CHEAP_REVIEW_MODEL) STAGE exceeded max rounds ($REVIEW_MAX_ROUNDS) — the [BLOCK] findings above need human review."
      # bail → fall through to the final reviewer
    else
      echo "review-gate: [cheap] BLOCKING (round $n/$REVIEW_MAX_ROUNDS) — fix the [BLOCK] findings above; exiting 2." >&2
      exit 2
    fi
  else
    write_counter cheap 0
  fi

  # ---- Stage 2: expensive final reviewer — Codex if it has tokens, else Claude Opus ----
  if [ "$codex_ok" -eq 1 ]; then
    local crc=0; run_codex || crc=$?
    case "$crc" in
      0) write_counter final 0; diff_fingerprint > "$FINAL_PASS"; echo "Review clean ($cheap_desc + Codex)."; exit 0 ;;
      2) final_block codex ;;
      3) mkdir -p "$CLAUDE_DIR"; printf '%s\n' "$RESET_EPOCH" > "$CODEX_COOLDOWN"
         loud_banner \
           "CODEX USAGE LIMIT — final review falls back to $FINAL_CLAUDE_MODEL." \
           "Codex resumes automatically after $(human_time "$RESET_EPOCH")." \
           "Force a Codex retry sooner with:  scripts/review-gate resume"
         notify "Codex limited — final reviewer is $FINAL_CLAUDE_MODEL until $(human_time "$RESET_EPOCH")"
         codex_ok=0 ;;
    esac
  fi

  # Codex unavailable (cooldown or just limited) → final reviewer is Claude Opus.
  if [ "$codex_ok" -eq 0 ]; then
    local orc=0; run_claude_stage "$FINAL_CLAUDE_MODEL" final 1 || orc=$?
    case "$orc" in
      0) write_counter final 0; diff_fingerprint > "$FINAL_PASS"; echo "Review clean ($cheap_desc + $FINAL_CLAUDE_MODEL)."; exit 0 ;;
      2) final_block "$FINAL_CLAUDE_MODEL" ;;
      4) loud_banner \
           "NO FINAL REVIEW PERFORMED — Codex and Claude are both rate-limited." \
           "The cheap stage ran, but no expensive reviewer is available this stop." \
           "This stop is allowed to proceed; please review the change by hand before merging."
         notify "All reviewers rate-limited — no final review this stop"
         exit 0 ;;
    esac
  fi
}

main "$@"
