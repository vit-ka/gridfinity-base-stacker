#!/usr/bin/env bash
#
# codex-gate.sh -- run a Codex code review over the uncommitted changes and
# block on actionable defects.
#
# Env vars (with defaults):
#   REVIEW_THRESHOLD   fail on findings P0..P<THRESHOLD>          (default 3)
#   REVIEW_MODEL       codex model to review with                 (default gpt-5.6-sol)
#   REVIEW_EFFORT      model_reasoning_effort                     (default high)
#   REVIEW_MAX_ROUNDS  after this many blocking rounds, escape    (default 5)
#
# Exit codes: 0 = clean (or escaped after max rounds), 2 = blocking findings,
# 1 = the gate itself could not run (missing tool, unparseable output).

set -euo pipefail

REVIEW_THRESHOLD="${REVIEW_THRESHOLD:-3}"
REVIEW_MODEL="${REVIEW_MODEL:-gpt-5.6-sol}"
REVIEW_EFFORT="${REVIEW_EFFORT:-high}"
REVIEW_MAX_ROUNDS="${REVIEW_MAX_ROUNDS:-5}"

ROUNDS_FILE=".claude/.codex-gate-rounds"

die() { echo "codex-gate: $*" >&2; exit 1; }

# --- preconditions: fail loudly, never silently pass ------------------------
for tool in codex jq python3; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool '$tool' not found on PATH"
done

case "$REVIEW_THRESHOLD" in ''|*[!0-9]*) die "REVIEW_THRESHOLD must be an integer, got '$REVIEW_THRESHOLD'";; esac
case "$REVIEW_MAX_ROUNDS" in ''|*[!0-9]*) die "REVIEW_MAX_ROUNDS must be an integer, got '$REVIEW_MAX_ROUNDS'";; esac

# --- the review prompt ------------------------------------------------------
read -r -d '' PROMPT <<'EOF' || true
Review the uncommitted changes in this repository. Inspect the tracked changes
with `git diff HEAD` and include untracked files (`git ls-files --others
--exclude-standard`).

Report ONLY actionable defects: correctness, security, reliability, or
violations of the specs and rules under openspec/. Ignore style nits, naming
preferences, and formatting.

Assign each finding a priority: P0 (critical), P1 (high), P2 (medium), P3 (minor).

Output ONLY a single JSON object, no prose before or after, of exactly this
shape:
{"findings":[{"priority":"P1","file":"path/to/file","line":0,"issue":"what is wrong","fix":"what to do"}]}
Use an empty array when there are no defects: {"findings":[]}
EOF

# --- run codex --------------------------------------------------------------
set +e
RAW="$(codex exec -m "$REVIEW_MODEL" -c model_reasoning_effort="\"$REVIEW_EFFORT\"" --sandbox read-only --skip-git-repo-check "$PROMPT" 2>/dev/null)"
codex_status=$?
set -e
[ "$codex_status" -eq 0 ] || die "codex exec failed (exit $codex_status)"

# --- extract the last top-level JSON object from stdout ---------------------
# A real JSON parser is used because issue/fix strings can contain braces, which
# defeats naive brace counting.
JSON="$(printf '%s' "$RAW" | python3 -c '
import sys, json
data = sys.stdin.read()
dec = json.JSONDecoder()
last, i, n = None, 0, len(data)
while i < n:
    if data[i] == "{":
        try:
            obj, end = dec.raw_decode(data, i)
            if isinstance(obj, dict):
                last = json.dumps(obj)
                i = end
                continue
        except json.JSONDecodeError:
            pass
    i += 1
if last is None:
    sys.exit(3)
sys.stdout.write(last)
')" || die "could not find a parseable JSON object in codex output"

# Require the documented shape rather than treating anything parseable as clean.
printf '%s' "$JSON" | jq -e 'has("findings") and (.findings | type == "array")' >/dev/null 2>&1 \
  || die "codex output JSON has no 'findings' array -- refusing to pass"

# --- filter to blocking findings (priority number <= THRESHOLD) -------------
BLOCKING="$(printf '%s' "$JSON" | jq -c --argjson t "$REVIEW_THRESHOLD" \
  '[.findings[] | select((.priority | ltrimstr("P") | tonumber) <= $t)]')" \
  || die "failed to filter findings with jq"
BLOCKING_COUNT="$(printf '%s' "$BLOCKING" | jq 'length')"

# --- round counter ----------------------------------------------------------
mkdir -p "$(dirname "$ROUNDS_FILE")"
rounds=0
if [ -f "$ROUNDS_FILE" ]; then
  rounds="$(cat "$ROUNDS_FILE" 2>/dev/null || echo 0)"
  case "$rounds" in ''|*[!0-9]*) rounds=0;; esac
fi

if [ "$BLOCKING_COUNT" -eq 0 ]; then
  printf '0\n' > "$ROUNDS_FILE"
  echo "Codex review clean."
  exit 0
fi

rounds=$((rounds + 1))
printf '%s\n' "$rounds" > "$ROUNDS_FILE"

findings_txt="$(printf '%s' "$BLOCKING" | jq -r \
  '.[] | "- [\(.priority)] \(.file):\(.line) \(.issue) → \(.fix)"')"

if [ "$rounds" -gt "$REVIEW_MAX_ROUNDS" ]; then
  {
    echo "codex-gate: gate exceeded max rounds ($REVIEW_MAX_ROUNDS) -- remaining findings need human review"
    echo "$findings_txt"
  } >&2
  exit 0
fi

{
  echo "codex-gate: blocking findings (round $rounds of $REVIEW_MAX_ROUNDS):"
  echo "$findings_txt"
} >&2
exit 2
