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
# A clean final pass is cached against a content fingerprint (a git tree hash of
# the working state, committed + uncommitted) keyed by the change under review, so
# a converged change is not re-reviewed on every subsequent stop. When Codex
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
FINAL_PASS="$CLAUDE_DIR/.review-gate-final-pass"         # "<change>\t<content-fingerprint>" of the last clean FULL review
ACTIVE_MARKER="$CLAUDE_DIR/.review-gate-active"          # armed workstream: the change under review
# Committable, human-readable review history — one file per stage verdict. Lives at
# the repo ROOT (deliberately NOT under the git-ignored .claude/.review-gate-* glob)
# so entries are tracked and committed. Excluded from the content fingerprint (see
# diff_fingerprint) so appending/committing a verdict never busts the pass cache.
HISTORY_DIR="$ROOT/.review-gate-history"
# Per-stage reused review sessions live in .review-gate-<stage>-session as
# "<change>\t<session-id>" (see session_file/session_id_for/save_session).
CHANGE=""                                            # the single change under review; set in main
CHANGE_DIR=""                                         # its resolved artifacts dir (active or archived); set in main
CLAUDE_RESET_EPOCH=""                                # set by a Claude stage when rate-limited
RESET_EPOCH=""                                        # set by run_codex on a Codex limit

# Manual-run inputs, set by `review-gate start` (see scripts/review-gate). They do
# not apply to the automatic Stop-hook path, which leaves them unset:
#   REVIEW_START_STAGE  (1)  first stage to run: 1 = cheap+final round, 2 = final only
#   REVIEW_FORCE        ()   non-empty = skip the pre-cheap pass-cache short-circuit
#   REVIEW_CHANGE_OVERRIDE () one-shot change target; does NOT read/write the armed marker
REVIEW_START_STAGE="${REVIEW_START_STAGE:-1}"
REVIEW_FORCE="${REVIEW_FORCE:-}"
REVIEW_CHANGE_OVERRIDE="${REVIEW_CHANGE_OVERRIDE:-}"

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

# Fallback content of the working tree (committed + uncommitted), used only when a
# git tree hash can't be produced. The gate's own transient files
# (.claude/.review-gate-*) AND the committable review-history store
# (.review-gate-history/) are skipped so neither can perturb the fingerprint.
diff_content() {
  git rev-parse HEAD 2>/dev/null || true
  git diff HEAD -- ':(exclude,glob).claude/.review-gate-*' ':(exclude,glob).review-gate-history/**' 2>/dev/null || git diff HEAD 2>/dev/null || true
  git ls-files --others --exclude-standard -z 2>/dev/null | while IFS= read -r -d '' f; do
    case "$f" in .claude/.review-gate-*|.review-gate-history/*) continue ;; esac
    printf '\n===== untracked: %s =====\n' "$f"
    cat -- "$f" 2>/dev/null || true
  done
}

# A CONTENT fingerprint of the current working tree, for the final-pass cache. It
# hashes the actual code as it stands — committed AND uncommitted alike — so the
# cache tracks the state of the change's implementation, NOT how clean the
# checkout happens to be. We build a throwaway index seeded from HEAD, stage every
# working-tree change into it, and take git's own tree object id:
#   - identical content hashes identically regardless of commit history, so
#     committing already-reviewed work does NOT force a needless re-review;
#   - any real edit (committed or not) changes the tree id → the cache invalidates
#     and the reviewer runs again;
#   - the gate's own .claude/.review-gate-* markers are excluded (and .gitignored
#     files are ignored by `git add` anyway), so their per-run churn never counts;
#   - the committable review-history store (.review-gate-history/) is excluded the
#     same way — it IS tracked, so appending or committing a verdict there must not
#     change the tree id and re-trigger a review of otherwise-unchanged code.
# The real index and working tree are untouched. Falls back to hashing
# diff_content when no HEAD/tree is available (e.g. a repo with no commits).
diff_fingerprint() {
  local idxdir idx tree
  # Seed the throwaway index at a guaranteed-NONEXISTENT path inside a private temp
  # dir (not an existing empty file): some git builds reject an existing file as an
  # alternate index. The dir is removed regardless of outcome.
  idxdir="$(mktemp -d)"; idx="$idxdir/index"
  # Clean the scratch dir even if a signal interrupts the git commands below
  # (the normal path clears the trap and removes it explicitly). No other EXIT
  # trap exists in this script.
  trap 'rm -rf "$idxdir"' EXIT
  if GIT_INDEX_FILE="$idx" git read-tree HEAD 2>/dev/null \
     && GIT_INDEX_FILE="$idx" git add -A -- ':(exclude,glob).claude/.review-gate-*' ':(exclude).review-gate-history' ':(exclude,glob).review-gate-history/**' 2>/dev/null; then
    # Drop the excluded paths from the index — both any that `git add` still let
    # through and, crucially, any that a repo committed before adopting the
    # gitignore/exclusion (those arrive via read-tree HEAD, so excluding them from
    # `add` alone would leave their per-run churn in the tree id and defeat the
    # cache). The history store is tracked on purpose, so it MUST be removed here.
    GIT_INDEX_FILE="$idx" git rm --cached -q --ignore-unmatch -- ':(glob).claude/.review-gate-*' ':(glob).review-gate-history/**' 2>/dev/null || true
    tree="$(GIT_INDEX_FILE="$idx" git write-tree 2>/dev/null || true)"
  fi
  rm -rf "$idxdir"; trap - EXIT
  if [ -n "${tree:-}" ]; then printf '%s\n' "$tree"; else diff_content | sha256; fi
}

# The full-pass cache is keyed by BOTH the change under review and the content
# fingerprint, stored as one "<change>\t<fingerprint>" line. A cached pass may
# short-circuit the review ONLY for the same change on identical content — arming a
# different change whose repository content is byte-identical must NOT reuse another
# change's approval. An empty/ad-hoc CHANGE uses the '-' sentinel so it only matches
# another generic run.
# The ad-hoc sentinel contains '/', which arming forbids in a change name, so it can
# never collide with a real change (even one literally named '-').
cache_change_key() { if [ -n "${CHANGE:-}" ]; then printf '%s' "$CHANGE"; else printf '%s' '/ad-hoc/'; fi; }
write_final_pass() {
  mkdir -p "$CLAUDE_DIR"
  printf '%s\t%s\n' "$(cache_change_key)" "$(diff_fingerprint)" > "$FINAL_PASS"
}
# 0 iff the stored cache is for THIS change AND the current fingerprint matches.
final_pass_matches() {
  [ -f "$FINAL_PASS" ] || return 1
  local sc sf tab; tab="$(printf '\t')"
  IFS="$tab" read -r sc sf < "$FINAL_PASS" 2>/dev/null || true
  [ -n "${sf:-}" ] && [ "$sc" = "$(cache_change_key)" ] && [ "$sf" = "$(diff_fingerprint)" ]
}

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

# Announce the Codex→Claude fallback loudly. Called on EVERY run that uses the
# Claude final reviewer because Codex is unavailable — both when the limit is first
# hit and on later runs while the cooldown sentinel is still active — so the
# degraded mode is never silent. Reads the resume epoch from the cooldown sentinel.
announce_codex_fallback() {
  local ep; ep="$(tr -dc '0-9' < "$CODEX_COOLDOWN" 2>/dev/null || true)"; ep="${ep:-0}"
  loud_banner \
    "CODEX USAGE LIMIT — final review falls back to $FINAL_CLAUDE_MODEL." \
    "Codex resumes automatically after $(human_time "$ep")." \
    "Force a Codex retry sooner with:  scripts/review-gate reset-cooldown"
  notify "Codex limited — final reviewer is $FINAL_CLAUDE_MODEL until $(human_time "$ep")"
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

# Resolve a change NAME to its artifacts directory: the active dir
# openspec/changes/<name>/ first, else an archived dir openspec/changes/archive/*/
# whose basename is <name> (exact) or *-<name> (a dated archive prefix). Echoes the
# resolved dir and returns 0; returns 1 (nothing echoed) when nothing matches;
# returns 2 and lists candidates on stderr when the archived match is ambiguous.
# Exact-basename matches win over dated-suffix matches.
resolve_change_dir() {
  local name="$1"
  [ -n "$name" ] || return 1
  if [ -d "openspec/changes/$name" ]; then
    printf '%s' "openspec/changes/$name"; return 0
  fi
  local d base; local -a exact=() suffix=()
  for d in openspec/changes/archive/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"; base="$(basename "$d")"
    case "$base" in
      "$name")   exact+=("$d") ;;
      *-"$name") suffix+=("$d") ;;
    esac
  done
  if [ "${#exact[@]}" -gt 0 ]; then
    if [ "${#exact[@]}" -gt 1 ]; then
      echo "review-gate: ambiguous archived change '$name' — candidates: ${exact[*]}" >&2; return 2
    fi
    printf '%s' "${exact[0]}"; return 0
  fi
  if [ "${#suffix[@]}" -gt 0 ]; then
    if [ "${#suffix[@]}" -gt 1 ]; then
      echo "review-gate: ambiguous archived change '$name' — candidates: ${suffix[*]}" >&2; return 2
    fi
    printf '%s' "${suffix[0]}"; return 0
  fi
  return 1
}

# The shared review prompt. Names the single change under review so each agent
# reads its artifacts dir and locates the related code itself, rather than being
# handed a precomputed diff. $1 is the change name (may be empty); $2 is its
# resolved artifacts dir (defaults to openspec/changes/<name>/ when omitted).
review_prompt() {
  local change="$1" dir="${2:-}"
  [ -n "$dir" ] || dir="openspec/changes/$change"
  if [ -n "$change" ]; then
    printf 'You are the code-review gate for this repository. Verify that the OpenSpec change "%s" is implemented correctly.\n\n' "$change"
    printf 'First read the artifacts for this change under %s/ — proposal.md, design.md, tasks.md, and specs/**/*.md — to understand exactly what it is supposed to do. Then review this repository and judge whether the implementation of that change is correct, complete, and faithful to those specs. Whether the relevant code is already committed or still uncommitted does not matter — review the implementation as it now stands.\n\n' "$dir"
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

# Render every finding as one human-readable line each (blocking tagged [BLOCK],
# sub-threshold tagged [note]) to stdout. Shared by report_findings (→ stderr, the
# session log) and append_history (→ the retained entry file), so the stored report
# is byte-identical to what the session saw. $1 = all-findings JSON array.
render_findings() {
  printf '%s' "${1:-[]}" | jq -r --argjson t "$REVIEW_THRESHOLD" \
    '.[] | "  \(if (.priority | ltrimstr("P") | tonumber) <= $t then "[BLOCK]" else "[note] " end) [\(.priority)] \(.file):\(.line) — \(.issue) → \(.fix)"'
}

# Print EVERY finding from a stage (blocking and sub-threshold) to stderr so the
# full review stays in the session log. $1 stage label, $2 all-findings JSON.
report_findings() {
  local n; n="$(blocking_count "$2")"
  if [ "$n" -eq 0 ]; then
    echo "review-gate: [$1] review returned no findings." >&2
    return 0
  fi
  echo "review-gate: [$1] review findings ($n) — [BLOCK] = priority at/above P$REVIEW_THRESHOLD:" >&2
  render_findings "$2" >&2
}

# Human-readable timestamp for a history header (with seconds).
hist_timestamp() {
  date -r "$1" '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
    || date -d "@$1" '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
    || echo "epoch $1"
}

# Append a durable, committable history entry for a stage outcome. One file per
# entry under $HISTORY_DIR, named <epoch>-<seq>-<change>-<stage>.md — the change
# name is in the filename (as well as the header) for easy browsing of the committed
# store, sanitized for a filename. Durability, per the spec's "SHALL be retained as
# durable history" and the gate's "never silently pass on its own failure":
#   - the full entry is written to a private temp file FIRST, then atomically renamed
#     into place, so a reader never sees a partial entry and a mid-write crash leaves
#     no half-written file behind;
#   - the final name is claimed with `ln` — an atomic create-if-absent (like
#     O_EXCL) — so two concurrent runs in the same second can never overwrite each
#     other's entry (a collision just bumps <seq> and retries), honouring "none is
#     overwritten";
#   - <seq> is seeded from the count of entries already in this epoch-second, across
#     ALL stages, so entries created in the same second sort in creation order
#     (cheap before codex, etc.) rather than colliding on seq 0;
#   - a genuine write failure calls die (exit 1): the gate must not report a
#     successful review it could not retain.
#   $1 = stage label   $2 = verdict (clean|blocking|skipped)   $3 = all-findings JSON
append_history() {
  local stage="$1" verdict="$2" findings="${3:-[]}" epoch seq change_sane fname tmp n e
  epoch="$(now_epoch)"
  n="$(printf '%s' "$findings" | jq 'length' 2>/dev/null || echo 0)"
  # Sanitize the change name for a filename: keep [A-Za-z0-9._-], map the rest to '_'.
  change_sane="$(printf '%s' "${CHANGE:-ad-hoc}" | tr -c 'A-Za-z0-9._-' '_')"
  [ -n "$change_sane" ] || change_sane="ad-hoc"
  mkdir -p "$HISTORY_DIR" || die "could not create the review-history store $HISTORY_DIR."
  # Stage the full entry in a temp file (same dir → same filesystem, so the rename
  # below is atomic). The '.'-prefixed name is hidden from the *.md globs the store
  # is browsed and counted by.
  tmp="$(mktemp "$HISTORY_DIR/.tmp.XXXXXX")" || die "could not create a temp file in $HISTORY_DIR."
  {
    printf '# review-gate history entry\n\n'
    printf -- '- change: %s\n'    "${CHANGE:-<ad-hoc>}"
    printf -- '- stage: %s\n'     "$stage"
    printf -- '- verdict: %s\n'   "$verdict"
    printf -- '- timestamp: %s\n' "$(hist_timestamp "$epoch")"
    printf -- '- findings: %s\n\n' "$n"
    printf '## Findings\n\n'
    if [ "${n:-0}" -eq 0 ]; then
      printf '  (no findings)\n'
    else
      render_findings "$findings"
    fi
  } > "$tmp" || { rm -f "$tmp"; die "could not write the review-history entry for stage '$stage'."; }
  # Seed <seq> from the number of entries already stamped this same second (any
  # stage/change), so appends within one second keep creation order. Glob-safe under
  # set -e: iterate and test existence rather than parsing `ls`.
  seq=0
  for e in "$HISTORY_DIR/${epoch}-"*.md; do [ -e "$e" ] && seq=$(( seq + 1 )); done
  # Claim a unique name atomically; a collision (another run took it) bumps <seq>.
  while :; do
    fname="$HISTORY_DIR/${epoch}-${seq}-${change_sane}-${stage}.md"
    if ln "$tmp" "$fname" 2>/dev/null; then break; fi
    [ -e "$fname" ] || { rm -f "$tmp"; die "could not link the review-history entry into $HISTORY_DIR."; }
    seq=$(( seq + 1 ))
    [ "$seq" -gt 100000 ] && { rm -f "$tmp"; die "could not find a free review-history slot in $HISTORY_DIR."; }
  done
  rm -f "$tmp"
}

# Emit a completed stage's findings to the session log, append a durable history
# entry, and map the result to the stage return code (0 clean, 2 blocking). Shared
# by every stage runner so history is recorded uniformly on both verdicts.
#   $1 = stage label   $2 = all-findings JSON   $3 = blocking-findings JSON
finish_stage() {
  report_findings "$1" "$2"
  if [ "$(blocking_count "$3")" -eq 0 ]; then
    append_history "$1" clean "$2"; return 0
  fi
  append_history "$1" blocking "$2"; return 2
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
    sid="$(session_id_for "$stage" "$CHANGE")"
    if [ -n "$sid" ]; then
      use_session=1; sflag=(--resume "$sid")
    else
      # gen_uuid may fail (no uuidgen AND no python3) — never fatal: fall back to a
      # plain no-session call rather than aborting the gate under set -e.
      sid="$(gen_uuid || true)"
      if [ -n "$sid" ]; then use_session=1; sflag=(--session-id "$sid"); fi
    fi
  fi
  set +e
  raw="$(review_prompt "$CHANGE" "$CHANGE_DIR" | SKIP_REVIEW_GATE=1 claude "${cflags[@]}" ${sflag[@]+"${sflag[@]}"} 2>"$err")"
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
      raw="$(review_prompt "$CHANGE" "$CHANGE_DIR" | SKIP_REVIEW_GATE=1 claude "${cflags[@]}" 2>"$err")"
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
  finish_stage "$stage" "$ALL" "$BLOCKING"
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
    --sandbox read-only --skip-git-repo-check -o "$msg" "$(review_prompt "$CHANGE" "$CHANGE_DIR")" 2>"$err"; }
  # `codex exec resume` takes no --sandbox flag, so force read-only via config —
  # otherwise a resumed review could inherit a workspace-write policy and mutate the repo.
  _codex_resume() { codex exec resume "$1" -m "$REVIEW_MODEL" -c sandbox_mode='"read-only"' \
    --skip-git-repo-check -o "$msg" "$(review_prompt "$CHANGE" "$CHANGE_DIR")" 2>"$err"; }
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
  finish_stage codex "$ALL" "$BLOCKING"
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
  # Select the single change under review. A manual `review-gate start <change>`
  # sets REVIEW_CHANGE_OVERRIDE: a one-shot target that does NOT read or write the
  # armed marker, resolving its directory whether the change is active or archived.
  # Otherwise: the armed workstream marker (set by `start-auto-review` / the apply
  # workflow) when present; else, for ad-hoc runs, the most recently modified
  # change. Empty → generic "uncommitted changes".
  CHANGE=""; CHANGE_DIR=""
  local full_run=1; [ "$REVIEW_START_STAGE" = 2 ] && full_run=0
  if [ -n "$REVIEW_CHANGE_OVERRIDE" ]; then
    CHANGE="$REVIEW_CHANGE_OVERRIDE"
    local rrc=0; CHANGE_DIR="$(resolve_change_dir "$CHANGE")" || rrc=$?
    if [ "$rrc" = 2 ]; then exit 1; fi   # ambiguous — candidates already listed
    [ "$rrc" = 0 ] || die "change '$CHANGE' not found under openspec/changes/ (active or archived) — cannot review."
  else
    [ -f "$ACTIVE_MARKER" ] && CHANGE="$(tr -d '\n' < "$ACTIVE_MARKER" 2>/dev/null || true)"
    if [ -z "$CHANGE" ]; then
      CHANGE="$(active_change || true)"   # never abort under set -e/pipefail when zero non-archived changes
      local nchanges; nchanges="$(active_changes | grep -c . || true)"
      [ "${nchanges:-0}" -gt 1 ] && echo "review-gate: $nchanges non-archived changes found — reviewing the most recently modified: ${CHANGE:-<none>}." >&2
    fi
    # Resolve the artifacts dir. Normally the armed/most-recent change is active, but
    # a marker that names a change already archived (e.g. a direct run before the
    # guard clears it) must still point the prompt at the archived artifacts (D4).
    # This selection path must never fail closed, so an unresolvable name falls back
    # to naming its (expected) active dir rather than aborting.
    if [ -n "$CHANGE" ]; then
      if [ -d "openspec/changes/$CHANGE" ]; then
        CHANGE_DIR="openspec/changes/$CHANGE"
      else
        local mrc=0; CHANGE_DIR="$(resolve_change_dir "$CHANGE")" || mrc=$?
        if [ "$mrc" = 2 ]; then exit 1; fi                      # ambiguous — candidates already listed
        [ "$mrc" = 0 ] || CHANGE_DIR="openspec/changes/$CHANGE" # not found — name it anyway (non-fatal)
      fi
    fi
  fi

  # ---- Full-review cache: if this exact change passed a full clean review on this
  # exact content, skip the ENTIRE review — no reviewer runs. Keyed by change +
  # fingerprint, so a different change with identical content is not covered. A
  # forced manual run (REVIEW_FORCE, from `review-gate start`) skips this check and
  # always reviews afresh. ----
  if [ -z "$REVIEW_FORCE" ] && final_pass_matches; then
    echo "review-gate: review cached (nothing changed since the last clean pass) — skipping the review."
    exit 0
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
  # cheap_cacheable gates the full-pass cache: a clean cheap verdict OR a
  # rate-limited skip is cacheable (the final reviewer is authoritative), but a
  # max-rounds BAIL is not — its blocking findings are unresolved, so caching over
  # them would permanently hide them on later cached runs.
  # A manual `review-gate start 2` starts at the final stage: skip stage 1 entirely
  # (and, being a partial run, never write the full-pass cache — see below).
  local rc=0 cheap_cacheable=1 cheap_desc="cheap $CHEAP_REVIEW_MODEL"
  if [ "$full_run" = 0 ]; then
    cheap_desc="cheap stage skipped (start 2 — final reviewer only)"
    echo "review-gate: manual start at stage 2 — running only the final reviewer." >&2
    append_history cheap skipped '[]'   # record the deliberately-skipped stage in history
  else
  run_claude_stage "$CHEAP_REVIEW_MODEL" cheap 0 || rc=$?
  if [ "$rc" -eq 4 ]; then
    cheap_desc="cheap stage skipped (rate-limited)"
    append_history cheap skipped '[]'   # rate-limited: no verdict, but record the skipped attempt
    loud_banner \
      "CHEAP ($CHEAP_REVIEW_MODEL) STAGE RATE-LIMITED — skipping it this stop." \
      "Falling through to the final reviewer; Claude should recover by $(human_time "$CLAUDE_RESET_EPOCH")."
  elif [ "$rc" -eq 2 ]; then
    local n; n="$(read_counter cheap)"; n=$(( n + 1 )); write_counter cheap "$n"
    if [ "$n" -gt "$REVIEW_MAX_ROUNDS" ]; then
      write_counter cheap 0
      cheap_cacheable=0   # unresolved blocking findings — never cache over them
      cheap_desc="cheap stage bailed to human review (findings NOT cached)"
      loud_banner "CHEAP ($CHEAP_REVIEW_MODEL) STAGE exceeded max rounds ($REVIEW_MAX_ROUNDS) — the [BLOCK] findings above need human review."
      # bail → fall through to the final reviewer
    else
      echo "review-gate: [cheap] BLOCKING (round $n/$REVIEW_MAX_ROUNDS) — fix the [BLOCK] findings above; exiting 2." >&2
      exit 2
    fi
  else
    write_counter cheap 0
  fi
  fi

  # ---- Stage 2: expensive final reviewer — Codex if it has tokens, else Claude Opus ----
  if [ "$codex_ok" -eq 1 ]; then
    local crc=0; run_codex || crc=$?
    case "$crc" in
      0) write_counter final 0; if [ "$full_run" = 1 ] && [ "$cheap_cacheable" = 1 ]; then write_final_pass; fi; echo "Review clean ($cheap_desc + Codex)."; exit 0 ;;
      2) final_block codex ;;
      3) append_history codex skipped '[]'   # Codex rate-limited: record the skipped attempt before falling back to Claude
         mkdir -p "$CLAUDE_DIR"; printf '%s\n' "$RESET_EPOCH" > "$CODEX_COOLDOWN"
         codex_ok=0 ;;
    esac
  fi

  # Codex unavailable (cooldown or just limited) → final reviewer is Claude Opus.
  # Announce the fallback on EVERY such run (not just when the limit was first hit).
  if [ "$codex_ok" -eq 0 ]; then
    announce_codex_fallback
    local orc=0; run_claude_stage "$FINAL_CLAUDE_MODEL" final 1 || orc=$?
    case "$orc" in
      0) write_counter final 0; if [ "$full_run" = 1 ] && [ "$cheap_cacheable" = 1 ]; then write_final_pass; fi; echo "Review clean ($cheap_desc + $FINAL_CLAUDE_MODEL)."; exit 0 ;;
      2) final_block "$FINAL_CLAUDE_MODEL" ;;
      4) append_history final skipped '[]'   # no reviewer available: record the skipped final stage
         loud_banner \
           "NO FINAL REVIEW PERFORMED — Codex and Claude are both rate-limited." \
           "The cheap stage ran, but no expensive reviewer is available this stop." \
           "This stop is allowed to proceed; please review the change by hand before merging."
         notify "All reviewers rate-limited — no final review this stop"
         exit 0 ;;
    esac
  fi
}

main "$@"
